import Link from "next/link";

import { ApiError, api, type SearchRequest } from "@/lib/api";
import { FILTER_TITLE, FILTER_URL_KEYS } from "@/lib/format";
import { withParams, type UrlParams } from "@/lib/params";

import FilterPanel from "./FilterPanel";
import ProductCard from "./ProductCard";

const SORTS: [string, string][] = [
  ["relevance", "По релевантности"],
  ["price_asc", "Сначала дешевле"],
  ["price_desc", "Сначала дороже"],
];

export default async function Results({
  basePath, params, request, hideBrand,
}: { basePath: string; params: UrlParams; request: SearchRequest; hideBrand?: boolean }) {
  let data;
  try {
    data = await api.search(request);
  } catch (e) {
    const err = e as ApiError;
    if (err.code === "cursor_expired" || err.code === "cursor_mismatch") {
      return (
        <div className="state">
          <p>Результаты обновились, и продолжить листание нельзя.</p>
          <Link className="button" href={withParams(basePath, params, {})}>Начать поиск заново</Link>
        </div>
      );
    }
    if (err.status === 422) {
      const hint = err.message.includes("size")
        ? "Укажите размер вместе с системой, например EU:38 или INT:M."
        : err.message;
      return <div className="state error">Некорректные параметры поиска. {hint}</div>;
    }
    return (
      <div className="state error">
        Поиск временно недоступен. Попробуйте ещё раз через минуту.
      </div>
    );
  }
  const dictionaries = await api.dictionaries().catch(() => null);
  const { total } = data;

  return (
    <div className="results-layout">
      <FilterPanel dictionaries={dictionaries} facets={data.facets} hideBrand={hideBrand} />
      <section className="results">
        <div className="results-head">
          <span>
            {total.relation === "gte" ? `Больше ${total.value}` : `Найдено: ${total.value}`}
            {data.demo_data && <span className="badge">демо-каталог</span>}
          </span>
          <nav className="sorts" aria-label="Сортировка">
            {SORTS.map(([value, label]) => (
              <Link
                key={value}
                className={request.sort === value ? "active" : ""}
                href={withParams(basePath, params, { sort: value === "relevance" ? null : value })}
              >
                {label}
              </Link>
            ))}
          </nav>
        </div>
        {total.value === 0 ? (
          <div className="state">
            <p>Ничего не найдено с текущими условиями.</p>
            {data.relaxations.length > 0 && (
              <ul className="relax">
                {data.relaxations.map((r) => (
                  <li key={r.filter}>
                    <Link href={withParams(basePath, params, Object.fromEntries((FILTER_URL_KEYS[r.filter] || []).map((k) => [k, null])))}>
                      Снять фильтр «{FILTER_TITLE[r.filter] ?? r.filter}»
                    </Link>{" "}
                    — найдётся {r.count}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <div className="grid">
            {data.items.map((item) => (
              <ProductCard key={item.product_id} item={item} />
            ))}
          </div>
        )}
        {data.next_cursor && (
          <Link className="button more" href={withParams(basePath, params, { cursor: data.next_cursor })}>
            Следующая страница
          </Link>
        )}
        {total.relation === "gte" && (
          <p className="muted small">Показываются первые {data.window_limit} результатов — уточните запрос.</p>
        )}
      </section>
    </div>
  );
}
