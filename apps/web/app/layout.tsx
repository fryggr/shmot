import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "Fashion Search POC",
  description: "Поиск одежды, обуви и аксессуаров с переходом к продавцу (демонстрационный прототип).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <header className="site-header">
          <Link href="/" className="logo">Fashion Search</Link>
          <span className="badge">POC · демо-данные</span>
        </header>
        <main>{children}</main>
        <footer className="site-footer">
          Покупка и оплата происходят на сайте продавца. Цены и наличие берутся из фидов магазинов и могут
          отличаться на момент перехода.
        </footer>
      </body>
    </html>
  );
}
