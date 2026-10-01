"""Проверка и нормализация записей YML (mapping_version yml-v1).

Каждая запись staging получает статус valid / quarantined (с кодами причин) / deleted
и нормализованное представление. Сырые значения не изменяются и остаются в payload.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from fashion_domain import normalization as N
from fashion_domain.urls import is_allowed_merchant_url

MAPPING_VERSION = "yml-v1"

PARAM_SIZE = ("размер",)
PARAM_COLOR = ("цвет",)
PARAM_COLORWAY = ("код цвета", "артикул цвета", "colorway")
PARAM_MATERIAL = ("материал",)
PARAM_COMPOSITION = ("состав",)
PARAM_GENDER = ("пол",)
PARAM_FIT = ("посадка", "силуэт")


def _param(payload: dict, names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for p in payload.get("params", []):
        if (p.get("name") or "").strip().lower() in names and p.get("value"):
            return p["value"], p.get("unit")
    return None, None


def _field(payload: dict, name: str) -> str | None:
    value = payload.get("fields", {}).get(name)
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    return value


def _field_list(payload: dict, name: str) -> list[str]:
    value = payload.get("fields", {}).get(name)
    if value is None:
        return []
    return [v for v in (value if isinstance(value, list) else [value]) if v]


@dataclass
class SourceContext:
    allowed_domains: list[str]
    allowed_currencies: tuple[str, ...]
    category_paths: dict[str, str]          # source category id → путь
    category_codes: dict[str, str | None]   # source category id → code
    is_full_snapshot: bool


@dataclass
class Validated:
    status: str
    errors: list[str] = field(default_factory=list)
    normalized: dict | None = None
    external_id: str | None = None
    group_key: str | None = None
    warnings: list[str] = field(default_factory=list)


def group_key_for(payload: dict) -> str | None:
    """Ключ offer внутри источника: явный group_id (или id) + колорвей/цвет.

    Карточка = модель в цвете, поэтому group_id с несколькими цветами разбивается.
    """
    base = payload.get("attrs", {}).get("group_id") or payload.get("attrs", {}).get("id")
    if not base:
        return None
    colorway, _ = _param(payload, PARAM_COLORWAY)
    color, _ = _param(payload, PARAM_COLOR)
    suffix = (colorway or (N._norm(color) if color else "")).strip()
    return f"{base}|{suffix}" if suffix else base


def validate_record(payload: dict, ctx: SourceContext, d: N.Dictionaries) -> Validated:
    attrs = payload.get("attrs", {})
    external_id = (attrs.get("id") or "").strip() or None
    errors: list[str] = []
    warnings: list[str] = []

    if not external_id:
        errors.append("missing_id")
    if attrs.get("deleted", "").lower() == "true":
        if ctx.is_full_snapshot:
            errors.append("delete_marker_in_full_snapshot")
        else:
            return Validated("deleted", external_id=external_id, group_key=group_key_for(payload))

    title = _field(payload, "name") or _field(payload, "model")
    if not title:
        errors.append("missing_name")

    url = _field(payload, "url")
    if not url:
        errors.append("missing_url")
    elif not is_allowed_merchant_url(url, ctx.allowed_domains):
        errors.append("url_not_allowed")

    price_minor = N.parse_price_minor(_field(payload, "price"))
    if price_minor is None:
        errors.append("invalid_price")

    currency = N.normalize_currency(_field(payload, "currencyId"))
    if currency is None or currency not in ctx.allowed_currencies:
        errors.append("unsupported_currency")

    pictures = [p for p in payload.get("pictures", []) if is_allowed_merchant_url(p, ctx.allowed_domains)]
    if len(pictures) != len(payload.get("pictures", [])):
        warnings.append("picture_dropped")
    if not pictures:
        errors.append("missing_image")

    if errors:
        return Validated("quarantined", errors=errors, external_id=external_id,
                         group_key=group_key_for(payload), warnings=warnings)

    old_price_minor = N.parse_price_minor(_field(payload, "oldprice"))
    if _field(payload, "oldprice") is not None and (old_price_minor is None or old_price_minor <= price_minor):
        # Некорректная «старая цена» остаётся в raw, но не публикуется как скидка
        warnings.append("invalid_old_price")
        old_price_minor = None

    source_category_id = _field(payload, "categoryId")
    category_code = ctx.category_codes.get(source_category_id) if source_category_id else None
    node = d.category(category_code) if category_code else None
    size_kind = node.size_kind if node else "unknown"

    size_raw, size_unit = _param(payload, PARAM_SIZE)
    color_raw, _ = _param(payload, PARAM_COLOR)
    colorway, _ = _param(payload, PARAM_COLORWAY)
    material_raw, _ = _param(payload, PARAM_MATERIAL)
    composition_raw, _ = _param(payload, PARAM_COMPOSITION)
    gender_raw, _ = _param(payload, PARAM_GENDER)
    fit_raw, _ = _param(payload, PARAM_FIT)

    materials = N.normalize_materials(material_raw, d)
    composition = N.parse_composition(composition_raw, d)
    if not materials:
        materials = [c["material"] for c in composition if c["material"]]

    barcodes = [b for b in _field_list(payload, "barcode") if re.fullmatch(r"\d{8,14}", b)]

    normalized = {
        "external_variant_id": external_id,
        "title": title.strip(),
        "description": _field(payload, "description"),
        "brand": (_field(payload, "vendor") or "").strip() or None,
        "model_code": (_field(payload, "vendorCode") or "").strip() or None,
        "colorway_code": colorway.strip() if colorway else None,
        "color_raw": color_raw,
        "color": N.normalize_color(color_raw, d),
        "material_raw": material_raw,
        "materials": materials,
        "composition_raw": composition_raw,
        "composition": composition,
        "gender": N.normalize_gender(gender_raw),
        "fit_raw": fit_raw,
        "source_category_id": source_category_id,
        "source_category_path": ctx.category_paths.get(source_category_id) if source_category_id else None,
        "category_code": category_code,
        "size": N.normalize_size(size_raw, size_unit, size_kind),
        "price_minor": price_minor,
        "old_price_minor": old_price_minor,
        "currency": currency,
        "availability": N.normalize_availability(attrs.get("available")),
        "url": url.strip(),
        "pictures": pictures,
        "gtin": barcodes[0] if barcodes else None,
    }
    return Validated("valid", normalized=normalized, external_id=external_id,
                     group_key=group_key_for(payload), warnings=warnings)
