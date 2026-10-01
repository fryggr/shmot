"""Контракты публичного API v1."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fashion_domain.config import get_settings
from fashion_domain.normalization import SIZE_SYSTEMS, load_dictionaries

SizeSystem = Literal["RU", "EU", "US", "UK", "INT", "ONE", "UNKNOWN"]
assert set(SizeSystem.__args__) == set(SIZE_SYSTEMS)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SizeFilter(Strict):
    """Размер всегда передаётся вместе с системой: «38» без системы не принимается."""

    system: SizeSystem
    label: str = Field(min_length=1, max_length=20)

    @field_validator("label")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = " ".join(v.split()).upper()
        return {"2XL": "XXL", "3XL": "XXXL"}.get(v, v)


class Filters(Strict):
    size: SizeFilter | None = None
    availability: Literal["in_stock", "any"] = "any"
    price_min_minor: int | None = Field(default=None, ge=0)
    price_max_minor: int | None = Field(default=None, ge=0)
    currency: str = "RUB"
    categories: list[str] = Field(default_factory=list, max_length=20)
    colors: list[str] = Field(default_factory=list, max_length=20)
    materials: list[str] = Field(default_factory=list, max_length=20)
    brands: list[str] = Field(default_factory=list, max_length=50)
    merchants: list[str] = Field(default_factory=list, max_length=20)
    gender: list[Literal["women", "men", "unisex", "kids", "unknown"]] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        v = v.upper()
        if v not in get_settings().allowed_currencies:
            raise ValueError(f"currency must be one of {list(get_settings().allowed_currencies)}")
        return v

    @field_validator("categories")
    @classmethod
    def _cats(cls, v: list[str]) -> list[str]:
        known = {c.code for c in load_dictionaries().categories}
        bad = [x for x in v if x not in known]
        if bad:
            raise ValueError(f"unknown category codes: {bad}")
        return v

    @field_validator("colors")
    @classmethod
    def _colors(cls, v: list[str]) -> list[str]:
        bad = [x for x in v if x not in load_dictionaries().colors]
        if bad:
            raise ValueError(f"unknown color codes: {bad}")
        return v

    @field_validator("materials")
    @classmethod
    def _materials(cls, v: list[str]) -> list[str]:
        bad = [x for x in v if x not in load_dictionaries().materials]
        if bad:
            raise ValueError(f"unknown material codes: {bad}")
        return v

    @model_validator(mode="after")
    def _prices(self):
        if self.price_min_minor is not None and self.price_max_minor is not None \
                and self.price_min_minor > self.price_max_minor:
            raise ValueError("price_min_minor must not exceed price_max_minor")
        return self


class SearchRequest(Strict):
    q: str = Field(default="", max_length=500)
    filters: Filters = Field(default_factory=Filters)
    disabled_inferred_filters: list[str] = Field(default_factory=list, max_length=50)
    sort: Literal["relevance", "price_asc", "price_desc"] = "relevance"
    limit: int = Field(default=24, ge=1, le=48)
    cursor: str | None = Field(default=None, max_length=2000)
    anonymous_session_id: str | None = Field(default=None, max_length=64)


class SizeOut(BaseModel):
    system: str | None
    label: str | None


class SearchItem(BaseModel):
    product_id: str
    title: str
    brand: str | None
    brand_slug: str | None
    category: str | None
    color: str | None
    image_url: str | None
    matched_offer_id: str
    price_minor: int
    old_price_minor: int | None
    currency: str
    merchant: str
    merchant_slug: str
    availability: str
    available_sizes: list[SizeOut]
    freshness: Literal["fresh", "stale"]
    outbound_url: str
    is_demo: bool
    score: float


class Total(BaseModel):
    value: int
    relation: Literal["eq", "gte"]


class FacetValue(BaseModel):
    value: str
    count: int


class Relaxation(BaseModel):
    filter: str
    count: int


class SearchResponse(BaseModel):
    search_id: str
    experiment_arm: str
    parsed: dict
    items: list[SearchItem]
    total: Total
    window_limit: int
    facets: dict[str, list[FacetValue]]
    relaxations: list[Relaxation]
    next_cursor: str | None
    degraded_mode: bool
    ranking_version: str
    demo_data: bool


class ImportRequest(Strict):
    source: str = Field(min_length=1, max_length=100)
    force: bool = False


class PublishRequest(Strict):
    approve: bool = False
