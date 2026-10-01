"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import type { Dictionaries, Facet } from "@/lib/api";

type Props = { dictionaries: Dictionaries | null; facets: Record<string, Facet[]>; hideBrand?: boolean };

// Все фильтры живут в URL; изменение фильтра сбрасывает cursor.
export default function FilterPanel({ dictionaries, facets, hideBrand }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const [open, setOpen] = useState(false);
  const [size, setSize] = useState(sp.get("size") || "");
  const [priceMin, setPriceMin] = useState(sp.get("price_min") || "");
  const [priceMax, setPriceMax] = useState(sp.get("price_max") || "");

  const update = (key: string, values: string[]) => {
    const next = new URLSearchParams(sp.toString());
    next.delete("cursor");
    next.delete(key);
    values.filter(Boolean).forEach((v) => next.append(key, v));
    router.push(`${pathname}?${next.toString()}`);
  };
  const toggle = (key: string, value: string) => {
    const current = sp.getAll(key);
    update(key, current.includes(value) ? current.filter((v) => v !== value) : [...current, value]);
  };
  const applyRange = (e: React.FormEvent) => {
    e.preventDefault();
    const next = new URLSearchParams(sp.toString());
    next.delete("cursor");
    for (const [k, v] of [["price_min", priceMin], ["price_max", priceMax], ["size", size]] as const) {
      next.delete(k);
      if (v.trim()) next.set(k, v.trim());
    }
    router.push(`${pathname}?${next.toString()}`);
    setOpen(false);
  };

  const title = (kind: string, code: string) => {
    if (kind === "colors") return dictionaries?.colors.find((c) => c.code === code)?.title ?? code;
    if (kind === "categories") return dictionaries?.categories.find((c) => c.code === code)?.title ?? code;
    return code;
  };
  const urlKey: Record<string, string> = { colors: "color", categories: "category", brands: "brand", merchants: "merchant" };
  const groups: [string, string][] = [
    ["categories", "Категория"],
    ["colors", "Цвет"],
    ...(hideBrand ? [] : ([["brands", "Бренд"]] as [string, string][])),
    ["merchants", "Магазин"],
  ];

  return (
    <>
      <button className="filters-toggle" type="button" onClick={() => setOpen(true)}>
        Фильтры
      </button>
      <aside className={`filters ${open ? "open" : ""}`} aria-label="Фильтры">
        <div className="filters-head">
          <strong>Фильтры</strong>
          <button type="button" className="close" onClick={() => setOpen(false)} aria-label="Закрыть фильтры">
            ×
          </button>
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={sp.get("availability") === "in_stock"}
            onChange={(e) => update("availability", e.target.checked ? ["in_stock"] : [])}
          />
          Только в наличии
        </label>
        <form onSubmit={applyRange} className="range">
          <label>
            Размер (система:размер)
            <input value={size} onChange={(e) => setSize(e.target.value)} placeholder="EU:38 или INT:M" />
          </label>
          <div className="row">
            <label>
              Цена от, ₽
              <input inputMode="numeric" value={priceMin} onChange={(e) => setPriceMin(e.target.value.replace(/\D/g, ""))} />
            </label>
            <label>
              до, ₽
              <input inputMode="numeric" value={priceMax} onChange={(e) => setPriceMax(e.target.value.replace(/\D/g, ""))} />
            </label>
          </div>
          <button type="submit">Применить</button>
        </form>
        {groups.map(([kind, label]) =>
          (facets[kind] || []).length ? (
            <fieldset key={kind}>
              <legend>{label}</legend>
              {facets[kind].map((f) => (
                <label key={f.value} className="check">
                  <input
                    type="checkbox"
                    checked={sp.getAll(urlKey[kind]).includes(f.value)}
                    onChange={() => toggle(urlKey[kind], f.value)}
                  />
                  {title(kind, f.value)} <span className="muted">{f.count}</span>
                </label>
              ))}
            </fieldset>
          ) : null,
        )}
      </aside>
    </>
  );
}
