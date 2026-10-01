import type { Metadata } from "next";
import { Suspense } from "react";

import Results from "@/components/Results";
import SearchBar from "@/components/SearchBar";
import { toSearchRequest, type UrlParams } from "@/lib/params";

// Экспериментальные страницы поиска закрыты от индексации
export const metadata: Metadata = { title: "Поиск — Fashion Search POC", robots: { index: false, follow: false } };

export default async function SearchPage({ searchParams }: { searchParams: Promise<UrlParams> }) {
  const params = await searchParams;
  const request = toSearchRequest(params);
  return (
    <>
      <SearchBar initial={request.q} key={request.q} />
      <Suspense key={JSON.stringify(params)} fallback={<div className="state">Ищем…</div>}>
        <Results basePath="/search" params={params} request={request} />
      </Suspense>
    </>
  );
}
