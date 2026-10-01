import Link from "next/link";

export default function NotFound() {
  return (
    <div className="state">
      <p>Страница не найдена.</p>
      <Link href="/">На главную</Link>
    </div>
  );
}
