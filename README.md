# Synchronisica

Сайт, Telegram webhook-бот с MySQL, Beszel и мониторинг ESP32 в одном uv workspace.
Сайт получает случайный канал через API бота; таблицы пользователей и оценки изменяет только бот.
Сборщик ESP32 работает независимо и сохраняет компактный контракт `/monitor/status`.

| Каталог | Назначение |
| --- | --- |
| `services/website/` | Страница, API визитки и кэш публикаций Telegram |
| `services/random_telegram_bot/` | Каталог, команды, очередь webhook, миграции, импорт и проверка каналов |
| `services/monitoring/` | Метрики хоста, проверки свежести и установка systemd-службы |
| `esp_software/` | Прошивка и шаблоны настроек ESP32 |
| `deploy/` | Caddy, MySQL и отдельная ER-диаграмма БД |
| `scripts/` | Общая команда Compose, резервное копирование и проверки |
| `tests/` | Контракты импорта, фоновой проверки и доступа к данным мониторинга |
| `.local/` | Приватные данные, CSV и результаты проверок; исключён из Git |

## Разработка и проверки

Нужны uv и Python 3.13; для синтаксической проверки JavaScript — Node.js.

```powershell
uv sync --locked
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8080
uv run --locked python scripts/check.py
uv run --locked python scripts/check.py --audit
```

Сайт доступен на `http://127.0.0.1:8080`. Настройки — в `.env.example`.
Для полного стека скопируйте `.env.integration.example` в `.env.integration`, задайте секреты
и выполните `uv run --locked python -m scripts.stack --dev up -d --build --wait`.
В production `scripts.stack` подключает `.env`, `.env.integration`, `.env.monitor` и все overlays.
Не выводите полную конфигурацию Compose в общий лог: она содержит секреты.

## Каталог каналов

После оценки обе кнопки под сообщением исчезают. `/suggest` проверяет публичную страницу
`https://t.me/s/<username>`: заголовок с числом подписчиков подтверждает публичный канал.
Принимается любой размер аудитории; точный размер, отображаемое значение и дата последнего
поста сохраняются. Давность публикации не ограничивает подтверждение. Временная ошибка
не подтверждает и не отклоняет предложение.

Массовый импорт использует тот же HTML-валидатор без Bot API и личного аккаунта Telegram.
CSV должен содержать уникальные `username`, `source_urls`, `attribution`.
Полный файл проверяется до записи; новые строки добавляются **неактивными** порциями по 1000.
Существующие идентификаторы, статусы, оценки и статистика пользователей сохраняются.
Импорт можно повторять: канал и запись очереди создаются в одной транзакции.

```powershell
uv run --locked python -m random_telegram_bot.catalogue_jobs inspect .local/discovery/all-public-channels.csv
# DATABASE_URL должен быть задан в окружении:
uv run --locked python -m random_telegram_bot.catalogue_jobs import .local/discovery/all-public-channels.csv
uv run --locked python -m random_telegram_bot.catalogue_jobs verify --limit 100
```

На сервере работает отдельный контейнер `catalogue-checker`: до 100 адресов за порцию,
интервал запросов 1,5 секунды, затем пауза 300 секунд. Публичный канал активируется,
недоступный публично адрес остаётся неактивным. Транспортные ошибки откладываются с увеличением
паузы; HTTP 429 сохраняет общий cooldown в БД. Перезапуск сохраняет очередь и паузу.
MySQL advisory lock исключает одновременный импорт и проверку. Проверенные адреса повторно
проверяются через 30 дней; пока остаются непроверенные, они идут первыми.
Полный первый проход 266 тысяч адресов занимает минимум около 14 дней без ошибок и ограничений.

Источники и атрибуция каждого импортированного адреса хранятся в `channel_checks` вместе
с SHA-256 входного файла. Сам CSV остаётся вне Git. Отдельный инструмент исследования
источников доступен через `uv run --locked python -m random_telegram_bot.discovery --help`;
его ledger и входные файлы также приватные.

## Мониторинг и ER-диаграмма

Beszel доступен по `/monitoring/`; его штатная вкладка Containers показывает сведения и логи.
Страница `/monitoring/operations/` дополняет его: первые 10 каналов и пользователей по первичному
ключу, прогресс проверки, ER текущей БД и последние 10 строк каждого контейнера проекта.
Войдите в Beszel как администратор, затем откройте страницу данных в том же браузере.
Каждый запрос данных проверяет сессию через Beszel; анонимные запросы получают 401,
пользователи без роли admin — 403. Ответы данных не кэшируются. В логах скрываются известные
секреты и типичные токены, но текст логов всё равно предназначен только администратору.
Произвольные SQL-запросы и команды контейнеров через страницу не выполняются.

Отдельные файлы: [ER в SVG](deploy/database.er.svg) и [исходник Mermaid](deploy/database.er.mmd).
На странице диаграмма строится из текущей схемы БД и доступна для скачивания в обоих форматах.
Сплошные связи обозначают реальные FK, пунктир — логические связи старых таблиц без FK.

## Обновление сервера

До миграций сохраните БД и проверьте восстановление в изолированную временную базу:

```sh
python3 scripts/backup_database.py --restore-check
python3 -m scripts.stack config --quiet
python3 -m scripts.stack build bot catalogue-checker
python3 -m scripts.stack run --rm --no-deps bot alembic -c services/random_telegram_bot/alembic.ini upgrade head
python3 -m scripts.stack up -d --no-deps --wait bot catalogue-checker
```

Для CSV, размещённого на сервере по абсолютному пути `/srv/private/all-public-channels.csv`:

```sh
python3 -m scripts.stack stop catalogue-checker
python3 -m scripts.stack run --rm --no-deps -v /srv/private/all-public-channels.csv:/import/channels.csv:ro bot python -m random_telegram_bot.catalogue_jobs import /import/channels.csv
python3 -m scripts.stack up -d --no-deps catalogue-checker
```

Сохраните старые образы и исходники перед обновлением. При проблеме остановите checker,
верните предыдущие образы/исходники и перезапустите сервисы. Миграции добавляют таблицы и поля;
для отката кода их удалять не требуется. После импорта не восстанавливайте старый дамп поверх
новых действий пользователей без отдельного решения: это потеряет новые оценки и сообщения.
Для исключения конкретного импорта можно деактивировать только каналы, связанные с его SHA-256
в `channel_checks`, сохранив строки и пользовательские данные.

Секреты `.env*`, приватные заголовки ESP32 и `.local/` не входят в коммит.
Настройка ESP32 и системного сборщика описана в [MONITORING.md](MONITORING.md).
