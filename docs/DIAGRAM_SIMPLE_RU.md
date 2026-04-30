# Простая схема проекта (B -> C)

```mermaid
flowchart LR
    A[Сборщик видео] --> B[Хранилище артефактов]
    B --> C[Анализ треков и колец]
    C --> D[API слой]
    D --> E[Веб-интерфейс]

    F[Очередь задач] --> D
    D --> F

    G[PostgreSQL как основная БД] <--> D
    H[SQLite/JSON как fallback и staging] --> D
```

Кратко:
- `videos_collector` собирает матчи и готовит исходные артефакты.
- `services/analysis` строит треки команд и таймлайн.
- `apps/api` отдает единый контракт данных.
- `apps/web` визуализирует турниры/матчи/карты.
- PostgreSQL — целевой SoT; SQLite/JSON оставлены как fallback.
