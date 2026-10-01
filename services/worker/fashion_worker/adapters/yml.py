"""Потоковый разбор YML (Yandex Market Language).

Безопасность: DTD и сущности запрещены (defusedxml, forbid_dtd=True), сетевые обращения
невозможны, ограничены объём после распаковки, глубина вложенности и число записей.
Элементы <offer> удаляются из дерева сразу после обработки — весь файл в памяти не держится.
"""
from __future__ import annotations

import gzip
import io
from dataclasses import dataclass, field
from typing import BinaryIO, Iterator

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import iterparse
from xml.etree.ElementTree import ParseError


class FeedRejected(Exception):
    """Снимок отклонён целиком (повреждён, небезопасен или превышает лимиты)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _LimitedReader(io.RawIOBase):
    def __init__(self, raw: BinaryIO, limit: int):
        self.raw = raw
        self.limit = limit
        self.consumed = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self.raw.read(len(buffer))
        self.consumed += len(data)
        if self.consumed > self.limit:
            raise FeedRejected("snapshot_too_large", f"snapshot exceeds {self.limit} bytes after decompression")
        buffer[: len(data)] = data
        return len(data)


def open_limited(stream: BinaryIO, max_bytes: int) -> io.BufferedReader:
    """Прозрачно распаковывает gzip и ограничивает объём распакованных данных."""
    buffered = io.BufferedReader(stream) if not hasattr(stream, "peek") else stream
    head = buffered.peek(2)[:2]
    if head == b"\x1f\x8b":
        buffered = gzip.GzipFile(fileobj=buffered, mode="rb")
    return io.BufferedReader(_LimitedReader(buffered, max_bytes), buffer_size=64 * 1024)


@dataclass
class ParsedCategory:
    id: str
    parent_id: str | None
    title: str


@dataclass
class ParseLimits:
    max_bytes: int
    max_records: int
    max_depth: int


@dataclass
class YmlParseResult:
    categories: dict[str, ParsedCategory] = field(default_factory=dict)
    shop: dict[str, str] = field(default_factory=dict)
    records: int = 0


def _text(el) -> str | None:
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value or None


def offer_to_payload(el) -> dict:
    """Переводит <offer> в JSON-совместимый payload без потери исходных значений."""
    fields: dict[str, str] = {}
    pictures: list[str] = []
    params: list[dict] = []
    for child in el:
        tag = child.tag
        if tag == "picture":
            if (t := _text(child)) is not None:
                pictures.append(t)
        elif tag == "param":
            params.append(
                {"name": child.get("name"), "unit": child.get("unit"), "value": _text(child)}
            )
        elif len(child) == 0:
            # повторяющиеся простые поля (например, несколько barcode) сохраняем списком
            value = _text(child)
            if tag in fields:
                prev = fields[tag]
                fields[tag] = prev + [value] if isinstance(prev, list) else [prev, value]
            else:
                fields[tag] = value
    return {
        "attrs": dict(el.attrib),
        "fields": fields,
        "pictures": pictures,
        "params": params,
    }


def iter_yml_offers(stream: BinaryIO, limits: ParseLimits, result: YmlParseResult) -> Iterator[dict]:
    """Генерирует payload каждой записи <offer>; категории и сведения о магазине
    накапливаются в result (в YML они обычно идут до offers, но порядок не важен)."""
    reader = open_limited(stream, limits.max_bytes)
    stack: list = []
    try:
        for event, el in iterparse(reader, events=("start", "end"), forbid_dtd=True,
                                   forbid_entities=True, forbid_external=True):
            if event == "start":
                stack.append(el)
                if len(stack) > limits.max_depth:
                    raise FeedRejected("xml_too_deep", f"XML depth exceeds {limits.max_depth}")
                continue
            stack.pop()
            parent = stack[-1] if stack else None
            if el.tag == "offer":
                result.records += 1
                if result.records > limits.max_records:
                    raise FeedRejected("too_many_records", f"more than {limits.max_records} offers")
                yield offer_to_payload(el)
                if parent is not None:
                    parent.remove(el)
            elif el.tag == "category" and parent is not None and parent.tag == "categories":
                cid = el.get("id")
                if cid:
                    result.categories[cid] = ParsedCategory(cid, el.get("parentId"), _text(el) or "")
            elif parent is not None and parent.tag == "shop" and el.tag in ("name", "company", "url"):
                result.shop[el.tag] = _text(el) or ""
    except DefusedXmlException as exc:
        raise FeedRejected("xml_forbidden_construct", f"forbidden XML construct: {type(exc).__name__}") from exc
    except ParseError as exc:
        raise FeedRejected("xml_malformed", f"malformed XML: {exc}") from exc
    if result.records == 0 and not result.shop:
        # Корень не yml_catalog/shop — скорее всего не тот файл
        raise FeedRejected("not_a_yml_feed", "no <shop> section found")


def category_path(categories: dict[str, ParsedCategory], cid: str | None) -> str | None:
    """'Женщинам / Верхняя одежда / Куртки'; защита от циклов parentId."""
    if not cid or cid not in categories:
        return None
    parts, seen = [], set()
    current: str | None = cid
    while current and current in categories and current not in seen:
        seen.add(current)
        parts.append(categories[current].title)
        current = categories[current].parent_id
    return " / ".join(reversed(parts))
