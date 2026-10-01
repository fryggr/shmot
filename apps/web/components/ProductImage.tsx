"use client";

import { useState } from "react";

// Картинки берутся только из фида согласованного источника. Недоступное изображение —
// явная заглушка, без подмены чужими картинками и без image proxy.
export default function ProductImage({ src, alt }: { src: string | null; alt: string }) {
  const [broken, setBroken] = useState(!src);
  if (broken || !src) {
    return (
      <div className="img-placeholder" role="img" aria-label={`${alt}: изображение недоступно`}>
        Изображение недоступно
      </div>
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt} loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} />;
}
