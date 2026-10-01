import Link from "next/link";

import SearchBar from "@/components/SearchBar";

const EXAMPLES = [
  "коричневая куртка из замши",
  "белое платье",
  "черные лоферы",
  "серая юбка миди",
];

export default function Home() {
  return (
    <section className="hero">
      <h1>Вся одежда — в одном поиске</h1>
      <SearchBar autoFocus />
      <div className="examples">
        {EXAMPLES.map((q) => (
          <Link key={q} href={`/search?q=${encodeURIComponent(q)}`}>
            {q}
          </Link>
        ))}
      </div>
      <p className="muted">
        Мы показываем предложения нескольких магазинов. Кнопка «В магазин» ведёт на сайт продавца — покупка и
        оплата происходят там.
      </p>
      <p className="demo-note">
        Сейчас подключён <strong>демонстрационный каталог</strong>: вымышленные магазины и бренды. Это не поиск по
        реальному рынку.
      </p>
    </section>
  );
}
