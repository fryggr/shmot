import Link from "next/link";

import type { SearchItem } from "@/lib/api";
import { AVAILABILITY, price, sizeLabel } from "@/lib/format";

import ProductImage from "./ProductImage";

export default function ProductCard({ item }: { item: SearchItem }) {
  return (
    <article className="card">
      <Link href={`/product/${item.product_id}`} className="card-link">
        <div className="card-img">
          <ProductImage src={item.image_url} alt={item.title} />
        </div>
        <div className="card-body">
          {item.brand && <div className="brand">{item.brand}</div>}
          <h3>{item.title}</h3>
          <div className="price">
            {price(item.price_minor, item.currency)}
            {item.old_price_minor && <s>{price(item.old_price_minor, item.currency)}</s>}
          </div>
          <div className="muted">
            {item.merchant} · {AVAILABILITY[item.availability]}
          </div>
          {item.available_sizes.length > 0 && (
            <div className="sizes">{item.available_sizes.map(sizeLabel).join(", ")}</div>
          )}
          {item.freshness === "stale" && <div className="warn">Данные магазина устарели</div>}
        </div>
      </Link>
    </article>
  );
}
