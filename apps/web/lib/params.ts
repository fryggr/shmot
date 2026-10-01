// Состояние поиска хранится в URL. Цены в URL — в рублях, в API — в копейках.
import type { SearchRequest } from "./api";

export type UrlParams = Record<string, string | string[] | undefined>;

const list = (v: string | string[] | undefined) => (v === undefined ? [] : Array.isArray(v) ? v : [v]).filter(Boolean);
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) || "";

function rubles(v: string): number | undefined {
  if (!/^\d{1,9}$/.test(v)) return undefined;
  return Number(v) * 100;
}

export function toSearchRequest(p: UrlParams, extra?: { brands?: string[] }): SearchRequest {
  const size = one(p.size);
  // Размер без системы не угадываем: такой запрос API отклонит с понятной ошибкой
  const [system, label] = size.includes(":") ? size.split(":", 2) : ["", size];
  const sort = one(p.sort);
  return {
    q: one(p.q).slice(0, 500),
    filters: {
      ...(size ? { size: { system: system.toUpperCase(), label } } : {}),
      availability: one(p.availability) === "in_stock" ? "in_stock" : "any",
      price_min_minor: rubles(one(p.price_min)),
      price_max_minor: rubles(one(p.price_max)),
      categories: list(p.category),
      colors: list(p.color),
      brands: extra?.brands ?? list(p.brand),
      merchants: list(p.merchant),
    },
    sort: sort === "price_asc" || sort === "price_desc" ? sort : "relevance",
    limit: 24,
    cursor: one(p.cursor) || null,
  };
}

// URL с заменой/удалением параметров; cursor всегда сбрасывается при изменении фильтров
export function withParams(base: string, p: UrlParams, changes: Record<string, string | string[] | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) {
    if (k === "cursor" || k in changes) continue;
    for (const item of list(v)) sp.append(k, item);
  }
  for (const [k, v] of Object.entries(changes)) {
    if (v === null) continue;
    for (const item of Array.isArray(v) ? v : [v]) if (item) sp.append(k, item);
  }
  const qs = sp.toString();
  return qs ? `${base}?${qs}` : base;
}
