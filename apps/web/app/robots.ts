import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  // POC: индексация закрыта целиком до проверки страниц
  return { rules: [{ userAgent: "*", disallow: "/" }] };
}
