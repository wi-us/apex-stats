# Инвентарь материалов для ВКР

Документ фиксирует, какие схемы, скриншоты и примеры данных нужно подготовить для диплома по Apex Stats Parser. Он дополняет `docs/diploma/vkr-detailed-plan.md` и `docs/diploma/diploma-diagrams.md`.

## 1. Введение

### Схемы

- Контекстная схема системы из `docs/diploma/diploma-diagrams.md`.
- Схема ручного и автоматизированного процесса из `docs/diploma/vkr-detailed-plan.md`.

### Скриншоты

- Главный экран viewer: карта, команды слева/справа, таймлайн.
- CAMERA-страница: карта и видео рядом.

### Что показать в тексте

- Проблема ручного анализа: 20 команд по 3 минуты.
- Ценность для тренера: быстрее получить карту ротаций и перейти к тактическим выводам.

## 2. Глава 1. Требования

### Схемы

- Диаграмма вариантов использования из `docs/diploma/vkr-detailed-plan.md`.
- End-to-end поток `video ingest -> detect/parse -> DB -> API -> UI render`.
- Схема метрик качества камеры.

### Скриншоты

- Главная страница с выбранным турниром/матчем/картой.
- Admin HSV как пример настройки распознавания.
- Admin ZONES/POLYGONS как пример подготовки карты.
- CAMERA-графики как пример диагностики качества.

### Примеры данных

- Фрагмент входного VOD/название файла записи.
- Фрагмент JSON трека команды.
- Фрагмент данных камеры.

### Что собрать вручную

- Список 3-5 матчей или карт, на которых можно демонстрировать работу.
- Короткое описание ручного процесса тренера.
- Подтверждение исходной оценки: 3 минуты на команду, 20 команд в матче.

## 3. Глава 2. Проектирование

### Архитектурные схемы

- Контекстная схема системы.
- C4 Container.
- Component frontend.
- Component backend.
- Общая end-to-end блок-схема проекта из `.cursor/plans/project_structure_revamp_54b2ad6b.plan.md`.
- Каркас проекта по директориям из `.cursor/plans/project_structure_revamp_54b2ad6b.plan.md`.
- Целевая архитектура реорганизации: ingestion pipeline, canonical Postgres, modular API, shared contracts.

### Pipeline-схемы

- VOD ingest.
- VPS sync.
- Детекция map start / OCR / rings / camera.
- Team tracking + export.
- ETL SQLite/JSON -> Postgres.
- Storage artifacts.

### Data/API-схемы

- ER-диаграмма доменных сущностей.
- Логические связи map-centric.
- Карта REST endpoint-ов.
- Sequence загрузки CAMERA.
- Sequence fallback-загрузки видео.
- Жизненный цикл данных.

### UI/UX-схемы

- Карта экранов: Main, Admin HSV/ZONES/POLYGONS, CAMERA, DATABASE.
- Карта левого меню.
- State diagram воспроизведения.
- Поток обновления `timeCursor`.

### Скриншоты

- Main viewer.
- CAMERA: верхний блок карта+видео.
- CAMERA: графики.
- CAMERA: настройки камеры слева.
- HSV.
- ZONES.
- POLYGONS.
- Figma layout или captured pages, если есть итоговый файл.

### Фрагменты кода для приложений

- `apps/web/lib/useMatchViewerState.ts`: состояние и загрузка данных.
- `apps/web/components/broadcast/BroadcastViewer.tsx`: композиция интерфейса.
- `apps/web/components/map-player.tsx`: рендер и алгоритмы камеры.
- `apps/web/app/admin/zoom/page.tsx`: синхронизация видео.
- `apps/api/src/modules/catalog/catalog.controller.ts`: REST endpoint-ы.
- `apps/api/src/modules/catalog/catalog.service.ts`: чтение Postgres/SQLite/files.
- `tools/algs-collector/camera_tracker.py`: трекинг камеры.
- `tools/algs-collector/detect_map_start.py`: детекция карты/кольца/OCR.
- `services/analysis/app/batch_analyze.py`: анализ команд.

## 4. Глава 3. Реализация и тестирование

### Схемы реализации

- Sequence CAMERA load.
- Sequence video fallback.
- Component frontend/backend.
- EMA + ring noise tuning.
- Anti-latch.
- Pre-jump lock.
- Step-shift.
- Dev/prod deployment.

### Скриншоты реализации

- Успешная загрузка карты.
- Успешная загрузка видео.
- Графики камеры с текущим курсором времени.
- Включение/выключение camera-shift.
- Пример HUD `map-overlay-hud`.
- Пример admin-config или zones после сохранения.

### API-примеры

- `GET /catalog/tournaments`.
- `GET /catalog/tournaments/:tournamentId/matches`.
- `GET /catalog/matches/:matchId/maps`.
- `GET /catalog/maps/:mapId/teams`.
- `GET /catalog/maps/:mapId/tracks`.
- `GET /catalog/maps/:mapId/rings`.
- `GET /catalog/maps/:mapId/camera`.
- `GET /catalog/maps/:mapId/video`.
- `GET/PUT /catalog/maps/:mapId/admin-config`.
- `GET/PUT /catalog/maps/:mapId/zones`.
- `GET/PUT /catalog/maps/:mapId/text-zones`.

### Тестовые материалы

- Набор тестовых VOD или фрагментов.
- Список проверенных карт.
- Результаты ручной проверки UI.
- Результаты API smoke-checks.
- Сравнение поведения камеры при разных пресетах.

### Минимальный набор ручных тест-кейсов

- Открыть main viewer и проверить загрузку турниров.
- Выбрать матч и карту.
- Проверить отображение команд.
- Переместить timeline и убедиться, что карта обновляется.
- Открыть CAMERA.
- Проверить синхронизацию видео с `timeCursor`.
- Включить/выключить camera-shift.
- Изменить EMA/anti-latch/pre-jump параметры.
- Открыть HSV и изменить пресет.
- Открыть ZONES/POLYGONS и проверить загрузку карты.

## 5. Глава 4. Организационно-экономические аспекты

### Схемы

- Ручной процесс против автоматизированного.
- Roadmap коммерциализации: MVP -> пилот -> heat-map/team pages -> live-analysis -> commercial rollout.

### Скриншоты

- Главный экран как демонстрация продукта для тренера.
- CAMERA как демонстрация технической диагностики.
- Будущая страница команды может быть показана как TODO/roadmap, если она еще не реализована.

### Числа

- 3 минуты на одну команду.
- 20 команд в матче.
- 60 минут ручной первичной разметки на матч.
- 6 карт = 360 минут, около 6 часов.
- Дополнительно можно оценить 5 матчей по 6 карт = 30 часов первичной ручной разметки.

### Что уточнить перед финальным текстом

- Реальное среднее время batch-обработки одной карты.
- Реальное время ручной проверки готового результата.
- Ожидаемый формат продажи: лицензия, SaaS, пилот, кастомная установка.

## 6. Глава 5. Информационная безопасность

### Схемы

- Production deployment.
- Trust boundaries: Browser -> Reverse Proxy -> API -> DB/Files.
- Роли доступа: Coach, Analyst, Operator, Admin.

### Что описать

- Открытые данные: публичные VOD.
- Закрытые данные: выводы тренера, heat-map, настройки анализа, приватная база матчей.
- Угрозы: утечка аналитики, изменение конфигов, подмена артефактов, открытый API.
- Меры: HTTPS, auth, roles, backups, env secrets, закрытый Postgres, аудит изменений.

### Что собрать

- Список endpoint-ов, которые нельзя оставлять публичными.
- Список директорий с media/artifacts, доступ к которым нужно ограничить.
- Описание политики хранения VOD.

## 7. Приложения

### Приложение А. Схемы

Включить полный набор из `docs/diploma/diploma-diagrams.md`, при необходимости экспортировав Mermaid в PNG/SVG.

### Приложение Б. Скриншоты

Собрать 8-12 скриншотов в одном разрешении и стиле.

### Приложение В. API и данные

Добавить фрагменты JSON и endpoint responses.

### Приложение Г. Код

Добавить небольшие фрагменты ключевого кода, не полные файлы:
- состояние frontend;
- REST controller;
- camera smoothing;
- video fallback;
- pipeline CLI.

### Приложение Д. Тестирование

Добавить тест-кейсы, результаты проверки и известные ограничения.
