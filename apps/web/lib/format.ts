export function price(minor: number, currency = "RUB"): string {
  return new Intl.NumberFormat("ru-RU", { style: "currency", currency, maximumFractionDigits: 0 }).format(minor / 100);
}

export function dateTime(iso: string): string {
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(
    new Date(iso),
  ) + " UTC";
}

export const AVAILABILITY: Record<string, string> = {
  in_stock: "В наличии",
  out_of_stock: "Нет в наличии",
  unknown: "Наличие не подтверждено",
};

export const SIZE_SYSTEM_TITLE: Record<string, string> = {
  RU: "RU", EU: "EU", US: "US", UK: "UK", INT: "INT", ONE: "", UNKNOWN: "система не указана",
};

export function sizeLabel(s: { system: string | null; label: string | null }): string {
  if (!s.label) return "";
  if (s.system === "ONE") return "One size";
  if (s.system === "UNKNOWN" || !s.system) return `${s.label} (система не указана)`;
  return `${s.label} ${s.system}`;
}

export const FILTER_TITLE: Record<string, string> = {
  size: "размер",
  availability: "только в наличии",
  price: "цена",
  categories: "категория",
  colors: "цвет",
  materials: "материал",
  brands: "бренд",
  merchants: "магазин",
  gender: "пол",
};

export const FILTER_URL_KEYS: Record<string, string[]> = {
  size: ["size"],
  availability: ["availability"],
  price: ["price_min", "price_max"],
  categories: ["category"],
  colors: ["color"],
  brands: ["brand"],
  merchants: ["merchant"],
};
