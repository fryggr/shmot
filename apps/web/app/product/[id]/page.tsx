import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import OfferList from "@/components/OfferList";
import ProductImage from "@/components/ProductImage";
import { ApiError, api, type Product } from "@/lib/api";

export const metadata: Metadata = { robots: { index: false, follow: true } };

const ATTR_TITLE: Record<string, string> = {
  color: "Цвет",
  materials: "Материал",
  composition: "Состав",
  fit: "Посадка",
};

function attrText(value: Record<string, unknown>): string {
  return String(value.raw ?? "");
}

export default async function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let product: Product;
  try {
    product = await api.product(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  // Показываем только значения, переданные продавцом (provenance=merchant/manual)
  const attrs = product.attributes.filter((a) => a.key in ATTR_TITLE && ["merchant", "manual"].includes(a.provenance));

  return (
    <article className="product">
      <div className="gallery">
        {(product.images.length ? product.images : [null]).slice(0, 4).map((src, i) => (
          <ProductImage key={src ?? i} src={src} alt={product.title} />
        ))}
      </div>
      <div className="product-info">
        {product.is_demo && <span className="badge">демо-товар</span>}
        {product.brand && (
          <Link className="brand" href={`/brand/${product.brand.slug}`}>
            {product.brand.name}
          </Link>
        )}
        <h1>{product.title}</h1>
        {product.status === "discontinued" && (
          <div className="state warn">Товар снят с продажи у всех подключённых продавцов.</div>
        )}
        {product.description && <p>{product.description}</p>}
        {attrs.length > 0 && (
          <dl className="attrs">
            {attrs.map((a) => (
              <div key={`${a.key}-${a.merchant}`}>
                <dt>{ATTR_TITLE[a.key]}</dt>
                <dd>
                  {attrText(a.value)}
                  {a.merchant && <span className="muted small"> — по данным {a.merchant}</span>}
                </dd>
              </div>
            ))}
          </dl>
        )}
        <h2>Продавцы</h2>
        <OfferList offers={product.offers} />
      </div>
    </article>
  );
}
