import type { Offer } from "@/lib/api";
import { AVAILABILITY, dateTime, price, sizeLabel } from "@/lib/format";

export default function OfferList({ offers }: { offers: Offer[] }) {
  return (
    <ul className="offers">
      {offers.map((o) => (
        <li key={o.offer_id} className={o.active ? "" : "inactive"}>
          <div className="offer-head">
            <strong>{o.merchant}</strong>
            <span className="price">
              {o.sizes.length > 1 ? "от " : ""}
              {price(o.price_minor, o.currency)}
              {o.old_price_minor && <s>{price(o.old_price_minor, o.currency)}</s>}
            </span>
          </div>
          {!o.active && <div className="warn">Снято с продажи у этого продавца</div>}
          {o.active && o.freshness === "stale" && (
            <div className="warn">Данные магазина устарели — наличие и цену уточняйте у продавца</div>
          )}
          {o.sizes.length > 0 ? (
            <div className="size-list">
              {o.sizes.map((s) => (
                <span key={`${s.system}-${s.label}`} className={`size ${s.availability}`} title={AVAILABILITY[s.availability]}>
                  {sizeLabel(s)} · {price(s.price_minor, o.currency)}
                </span>
              ))}
            </div>
          ) : (
            <div className="muted">Размеры не переданы магазином</div>
          )}
          <div className="muted small">
            {AVAILABILITY[o.availability]} · данные получены {dateTime(o.imported_at)}. Наличие в конкретном
            городе не гарантируется.
          </div>
          {o.outbound_url && (
            <a className="button" href={o.outbound_url} rel="nofollow noopener" target="_blank">
              В магазин
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}
