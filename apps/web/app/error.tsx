"use client";

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="state error">
      <p>Не удалось загрузить данные. Сервис поиска может быть временно недоступен.</p>
      <button type="button" onClick={reset}>Повторить</button>
    </div>
  );
}
