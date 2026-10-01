import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import Results from "@/components/Results";
import { ApiError, api } from "@/lib/api";
import { toSearchRequest, type UrlParams } from "@/lib/params";

// Индексация брендов включается только для проверенных страниц с достаточным ассортиментом
export const metadata: Metadata = { robots: { index: false, follow: true } };

export default async function BrandPage({
  params, searchParams,
}: { params: Promise<{ slug: string }>; searchParams: Promise<UrlParams> }) {
  const { slug } = await params;
  const sp = await searchParams;
  let brand;
  try {
    brand = await api.brand(slug);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const request = toSearchRequest(sp, { brands: [slug] });
  return (
    <>
      <h1>{brand.name}</h1>
      <p className="muted">
        Товаров в каталоге: {brand.product_count}
        {brand.is_demo && <span className="badge">демо</span>}
      </p>
      <Suspense key={JSON.stringify(sp)} fallback={<div className="state">Загружаем…</div>}>
        <Results basePath={`/brand/${slug}`} params={sp} request={request} hideBrand />
      </Suspense>
    </>
  );
}
