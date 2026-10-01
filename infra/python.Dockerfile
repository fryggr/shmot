# Общий образ API и worker (одна кодовая база, разные команды запуска).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASHION_CONFIG_DIR=/app/config \
    FASHION_MIGRATIONS_DIR=/app/db/migrations \
    FASHION_FIXTURES_DIR=/app/tests/fixtures/feeds

WORKDIR /app
COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY packages/domain packages/domain
COPY services/api services/api
COPY services/worker services/worker
RUN pip install --no-deps ./packages/domain ./services/api ./services/worker

COPY config config
COPY db db
# Только демонстрационные фиды (реальных ссылок фидов в образе нет)
COPY tests/fixtures/feeds tests/fixtures/feeds
COPY infra/init.sh infra/init.sh

RUN useradd --system --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "fashion_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
