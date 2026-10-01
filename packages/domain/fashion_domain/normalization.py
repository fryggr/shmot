"""Детерминированные правила нормализации: категории, цвета, материалы, размеры, цены, пол.

Правила только распознают явно переданные значения. Ничего не выдумывается: неизвестное
остаётся неизвестным (None / 'unknown' / 'UNKNOWN').
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

import yaml

from .config import get_settings

AVAILABILITY_VALUES = ("in_stock", "out_of_stock", "unknown")
GENDER_VALUES = ("women", "men", "unisex", "kids", "unknown")
SIZE_SYSTEMS = ("RU", "EU", "US", "UK", "INT", "ONE", "UNKNOWN")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("ё", "е").replace("Ё", "Е")).strip().lower()


@dataclass(frozen=True)
class CategoryNode:
    code: str
    title: str
    parent_code: str | None
    size_kind: str
    keywords: tuple[str, ...]
    depth: int


@dataclass(frozen=True)
class Dictionaries:
    taxonomy_version: str
    categories: tuple[CategoryNode, ...]
    colors: dict[str, tuple[str, ...]]
    materials: dict[str, tuple[str, ...]]
    versions: dict[str, str]

    def category(self, code: str) -> CategoryNode | None:
        return next((c for c in self.categories if c.code == code), None)


def _walk(nodes, parent: str | None, inherited_kind: str | None, depth: int, out: list[CategoryNode]) -> None:
    for node in nodes:
        kind = node.get("size_kind", inherited_kind) or "unknown"
        out.append(
            CategoryNode(
                code=node["code"],
                title=node["title"],
                parent_code=parent,
                size_kind=kind,
                keywords=tuple(_norm(k) for k in node.get("keywords", [])),
                depth=depth,
            )
        )
        _walk(node.get("children", []), node["code"], kind, depth + 1, out)


@lru_cache
def load_dictionaries(config_dir: Path | None = None) -> Dictionaries:
    config_dir = config_dir or get_settings().config_dir
    tax = yaml.safe_load((config_dir / "taxonomy.yaml").read_text(encoding="utf-8"))
    colors = yaml.safe_load((config_dir / "color_aliases.yaml").read_text(encoding="utf-8"))
    materials = yaml.safe_load((config_dir / "material_aliases.yaml").read_text(encoding="utf-8"))
    nodes: list[CategoryNode] = []
    _walk(tax["categories"], None, None, 0, nodes)
    return Dictionaries(
        taxonomy_version=tax["version"],
        categories=tuple(nodes),
        colors={k: tuple(_norm(a) for a in v) for k, v in colors["colors"].items()},
        materials={k: tuple(_norm(a) for a in v) for k, v in materials["materials"].items()},
        versions={"taxonomy": tax["version"], "colors": colors["version"], "materials": materials["version"]},
    )


# ------------------------------------------------------------------ категории
def map_category(source_path: str, d: Dictionaries | None = None) -> str | None:
    """Сопоставляет путь категории источника ('Женщинам / Одежда / Куртки') с code.

    Сначала ищется совпадение у самого глубокого узла таксономии, проверяя сегменты пути
    от листа к корню. Ничего не нашлось — None (категория 'unmapped', товар не теряется).
    """
    d = d or load_dictionaries()
    segments = [_norm(s) for s in source_path.split("/") if s.strip()]
    ordered = sorted(d.categories, key=lambda c: -c.depth)
    for segment in reversed(segments):
        for node in ordered:
            if any(k in segment for k in node.keywords):
                return node.code
    return None


# ---------------------------------------------------------------------- цвета
def normalize_color(raw: str | None, d: Dictionaries | None = None) -> str | None:
    if not raw:
        return None
    d = d or load_dictionaries()
    value = _norm(raw)
    for code, aliases in d.colors.items():
        if value == code or value in aliases:
            return code
    # «темно-коричневый», «коричневый меланж»: ищем алиас как отдельное слово
    best: tuple[int, str] | None = None
    for code, aliases in d.colors.items():
        for alias in aliases:
            m = re.search(rf"(?<![а-яa-z]){re.escape(alias)}(?![а-яa-z])", value)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), code)
    return best[1] if best else None


# ------------------------------------------------------------------- материалы
_COMPOSITION_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%\s*([^\d,;%]+)")


def normalize_materials(raw: str | None, d: Dictionaries | None = None) -> list[str]:
    """Возвращает коды материалов по алиасам. Самые длинные формы ищутся первыми и
    «вырезаются», поэтому «экокожа» не превращается в «кожа», а «под замшу» — в «замша»."""
    if not raw:
        return []
    d = d or load_dictionaries()
    text = f" {_norm(raw)} "
    pairs = sorted(
        ((alias, code) for code, aliases in d.materials.items() for alias in aliases),
        key=lambda p: -len(p[0]),
    )
    found: list[tuple[int, str]] = []
    for alias, code in pairs:
        pattern = re.compile(rf"(?<![а-яa-z]){re.escape(alias)}[а-яa-z]{{0,3}}(?![а-яa-z])")
        while (m := pattern.search(text)) is not None:
            found.append((m.start(), code))
            text = text[: m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    out: list[str] = []
    for _, code in sorted(found):
        if code not in out:
            out.append(code)
    return out


def parse_composition(raw: str | None, d: Dictionaries | None = None) -> list[dict]:
    """«80% шерсть, 20% полиамид» → [{'material':'wool','percent':80}, ...]. Неизвестный
    материал сохраняется с material=None и исходным текстом."""
    if not raw:
        return []
    out = []
    for pct, name in _COMPOSITION_RE.findall(raw):
        codes = normalize_materials(name, d)
        out.append(
            {
                "material": codes[0] if codes else None,
                "raw": name.strip(),
                "percent": float(pct.replace(",", ".")),
            }
        )
    return out


# --------------------------------------------------------------------- размеры
_SYSTEM_ALIASES = {
    "ru": "RU", "рос": "RU", "российский": "RU",
    "eu": "EU", "eur": "EU", "европейский": "EU",
    "us": "US", "usa": "US",
    "uk": "UK",
    "int": "INT", "международный": "INT",
    "one": "ONE", "one size": "ONE",
}
_INT_LABELS = ("XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL")


def normalize_size(raw: str | None, unit: str | None, size_kind: str) -> dict | None:
    """Возвращает {size_raw, size_system, size_label, size_kind} или None, если размер не передан.

    Система берётся только из явного атрибута unit либо однозначной метки (XS..XXL → INT,
    one size → ONE). Число без системы получает UNKNOWN: «46» без unit — не обязательно RU,
    «38» без unit — не обязательно EU. Конвертации между системами здесь нет.
    """
    if raw is None or not str(raw).strip():
        return None
    label = re.sub(r"\s+", " ", str(raw)).strip()
    system = _SYSTEM_ALIASES.get(_norm(unit)) if unit else None
    upper = label.upper()
    if system is None:
        if upper in ("ONE SIZE", "ONESIZE", "OS", "БЕЗРАЗМЕРНЫЙ", "ЕДИНЫЙ"):
            system, upper = "ONE", "ONE SIZE"
        elif upper in _INT_LABELS:
            system = "INT"
        else:
            system = "UNKNOWN"
    if system == "INT":
        upper = {"2XL": "XXL", "3XL": "XXXL"}.get(upper, upper)
    if system == "ONE":
        upper = "ONE SIZE"
    return {"size_raw": str(raw), "size_system": system, "size_label": upper, "size_kind": size_kind}


# ----------------------------------------------------------------------- прочее
_GENDER = {
    "женский": "women", "женская": "women", "женщинам": "women", "women": "women", "female": "women",
    "мужской": "men", "мужская": "men", "мужчинам": "men", "men": "men", "male": "men",
    "унисекс": "unisex", "unisex": "unisex",
    "детский": "kids", "детям": "kids", "kids": "kids",
}


def normalize_gender(raw: str | None) -> str:
    return _GENDER.get(_norm(raw), "unknown") if raw else "unknown"


def parse_price_minor(raw: str | None) -> int | None:
    """'24990.00' → 2499000. Некорректное или неположительное значение → None."""
    if raw is None:
        return None
    text = str(raw).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    minor = int(value * 100)
    return minor if minor > 0 else None


def normalize_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    code = raw.strip().upper()
    return "RUB" if code in ("RUR", "RUB") else code


def normalize_availability(raw: str | None) -> str:
    if raw is None:
        return "unknown"
    value = raw.strip().lower()
    if value in ("true", "1", "yes", "in_stock"):
        return "in_stock"
    if value in ("false", "0", "no", "out_of_stock"):
        return "out_of_stock"
    return "unknown"


def slugify(text: str) -> str:
    table = str.maketrans(
        {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
         "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
         "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh",
         "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}
    )
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().translate(table)).strip("-")
    return slug or "brand"
