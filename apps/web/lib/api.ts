// Типы и серверный клиент API v1. Вызывается из серверных компонентов.
import { headers } from "next/headers";

export type Size = { system: string | null; label: string | null };

export type SearchItem = {
  product_id: string;
  title: string;
  brand: string | null;
  brand_slug: string | null;
  category: string | null;
  color: string | null;
  image_url: string | null;
  matched_offer_id: string;
  price_minor: number;
  old_price_minor: number | null;
  currency: string;
  merchant: string;
  merchant_slug: string;
  availability: "in_stock" | "out_of_stock" | "unknown";
  available_sizes: Size[];
  freshness: "fresh" | "stale";
  outbound_url: string;
  is_demo: boolean;
};

export type Facet = { value: string; count: number };

export type SearchResponse = {
  search_id: string;
  experiment_arm: string;
  parsed: Record<string, unknown>;
  items: SearchItem[];
  total: { value: number; relation: "eq" | "gte" };
  window_limit: number;
  facets: Record<string, Facet[]>;
  relaxations: { filter: string; count: number }[];
  next_cursor: string | null;
  degraded_mode: boolean;
  ranking_version: string;
  demo_data: boolean;
};

export type SearchFilters = {
  size?: { system: string; label: string };
  availability?: "in_stock" | "any";
  price_min_minor?: number;
  price_max_minor?: number;
  categories?: string[];
  colors?: string[];
  brands?: string[];
  merchants?: string[];
};

export type SearchRequest = {
  q: string;
  filters: SearchFilters;
  sort: "relevance" | "price_asc" | "price_desc";
  limit: number;
  cursor: string | null;
};

export type OfferSize = Size & { raw: string | null; availability: string; price_minor: number };

export type Offer = {
  offer_id: string;
  merchant: string;
  merchant_slug: string;
  title: string;
  price_minor: number;
  old_price_minor: number | null;
  currency: string;
  availability: string;
  active: boolean;
  freshness: "fresh" | "stale";
  last_seen_at: string;
  imported_at: string;
  sizes: OfferSize[];
  outbound_url: string | null;
};

export type Product = {
  product_id: string;
  title: string;
  description: string | null;
  brand: { name: string; slug: string } | null;
  category: { code: string; title: string } | null;
  gender: string;
  color: string | null;
  model_code: string | null;
  is_demo: boolean;
  status: "active" | "discontinued";
  images: string[];
  attributes: { key: string; value: Record<string, unknown>; provenance: string; merchant: string | null }[];
  offers: Offer[];
};

export type Dictionaries = {
  categories: { code: string; title: string; parent: string | null }[];
  colors: { code: string; title: string }[];
  materials: { code: string; title: string }[];
  size_systems: string[];
};

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

const API = process.env.API_INTERNAL_URL || "http://localhost:8000";

// IP пользователя передаётся API для rate limit (иначе все запросы идут с адреса web-сервера)
async function clientHeaders(): Promise<Record<string, string>> {
  try {
    const h = await headers();
    const ip = h.get("x-forwarded-for") || h.get("x-real-ip");
    return ip ? { "x-forwarded-for": ip } : {};
  } catch {
    return {};
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    const extra = await clientHeaders();
    res = await fetch(`${API}${path}`, {
      ...init,
      headers: { ...(init?.headers as Record<string, string> | undefined), ...extra },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(503, "api_unreachable", "API недоступен");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.code || "http_error", body.message || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  search: (req: SearchRequest) =>
    call<SearchResponse>("/api/v1/search", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(req),
    }),
  product: (id: string) => call<Product>(`/api/v1/products/${encodeURIComponent(id)}`),
  brand: (slug: string) =>
    call<{ name: string; slug: string; product_count: number; is_demo: boolean }>(
      `/api/v1/brands/${encodeURIComponent(slug)}`,
    ),
  dictionaries: () => call<Dictionaries>("/api/v1/dictionaries"),
};
