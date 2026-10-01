"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function SearchBar({ initial = "", autoFocus = false }: { initial?: string; autoFocus?: boolean }) {
  const router = useRouter();
  const [q, setQ] = useState(initial);
  return (
    <form
      className="searchbar"
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        const query = q.trim();
        router.push(query ? `/search?q=${encodeURIComponent(query)}` : "/search");
      }}
    >
      <input
        type="search"
        name="q"
        value={q}
        maxLength={500}
        autoFocus={autoFocus}
        placeholder="Например: коричневая замшевая куртка"
        aria-label="Поисковый запрос"
        onChange={(e) => setQ(e.target.value)}
      />
      <button type="submit">Найти</button>
    </form>
  );
}
