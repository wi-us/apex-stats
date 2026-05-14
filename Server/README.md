# Server (VPS deployment)

Минимальный production-контур для VPS с ограниченными ресурсами:
- PostgreSQL
- Redis
- API (`@apex/api`)
- Web (`@apex/web`)

## 1) Подготовка

```bash
cd Server
cp .env.example .env
```

Проверь `.env`:
- `DATABASE_URL`
- `NEXT_PUBLIC_API_URL`
- `CATALOG_SOURCE`, `JOBS_SOURCE` (в C-фазе по умолчанию `postgres`)

## 2) Запуск

```bash
docker compose up -d
docker compose ps
```

## 3) ETL SQLite/JSON -> PostgreSQL

После старта Postgres запусти мост миграции:

```bash
node Server/scripts/bootstrap_local_pg.js
node Server/scripts/apply_pg_schema.js
node Server/scripts/etl_sqlite_json_to_pg.js
```

Для локальной машины с занятым `5432`:
- infra Postgres поднимается на `localhost:5433`.
- ETL автоматически читает `.env` (`DATABASE_URL`).

## 4) Обновление релиза

```bash
docker compose pull || true
docker compose up -d --build
```

## 5) Rollback (быстрый)

Если есть проблемы с Postgres режимом:
- в `.env` временно выставь:
  - `CATALOG_SOURCE=sqlite`
  - `JOBS_SOURCE=sqlite`
- перезапусти API:

```bash
docker compose up -d api
```

## 6) Замечания для слабого VPS

- Держи только нужные артефакты и БД.
- Не храни локальные `.venv`, `.next`, `dist` в production volume как постоянные данные.
- Регулярно чисти устаревшие `output/tracks/*.json` и делай backup Postgres тома.
