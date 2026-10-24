# Эксплуатация Synchronisica

Рабочий checkout на VPS находится в `/opt/synchronisica/repo`, Compose
проект называется `synchronisica-prod`. Все команды ниже выполняются из checkout
на Ubuntu. Скрипт `scripts.stack` выбирает конфигурации и env-файлы в одном месте.
Для локального запуска передайте `--dev`.
На текущем VPS uv не установлен на хосте: серверные скрипты управления используют
системный Python 3 и стандартную библиотеку. Зависимости сервисов устанавливает uv
внутри Docker; локальная разработка и проверки также используют uv.

## Настройки и данные

| Файл или хранилище | Содержимое |
| --- | --- |
| `.env` | Домен и настройки визитки, образец `.env.example` |
| `.env.integration` | Шаблон `.env.integration.example`; `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `BOT_TOKEN`, `WEBHOOK_SECRET`, `MONITORING_EMAIL`, `MONITORING_PASSWORD`, `BESZEL_KEY`, `BESZEL_TOKEN` |
| `.env.monitor` | Существующий `WATCH_MONITOR_TOKEN` ESP32 |
| `synchronisica-prod_mysql_data` | MySQL и очередь Telegram |
| `synchronisica-prod_beszel_data` | Аккаунты, история и настройки Beszel |
| `synchronisica-prod_beszel_agent_data` | Постоянное состояние агента |
| `synchronisica-prod_caddy_data` | Сертификаты Caddy |
| `/var/lib/synch-monitor/status.json` | Последние метрики ESP32 |

Секреты находятся вне архивов исходников и Git. На Ubuntu установите им права 0600.
Не выводите `docker compose config` без `--quiet`. Локальные данные входа в панель
находятся в `.local/monitoring-access.txt`; браузерная проверка читает
`.local/integration-secrets.json` с ключами `monitoring_email` и `monitoring_password`.

## Проверки и обновление

На локальной машине:

```sh
uv sync --locked
uv run --locked python scripts/check.py --audit
```

На VPS:

```sh
python3 -m scripts.stack config --quiet
python3 -m scripts.stack ps
```

Перед изменением бота сохраните SQL и предыдущий образ:

```sh
python3 -m scripts.backup_database --restore-check
docker tag synchronisica-prod-bot synchronisica-rollback:before-bot-update
python3 -m scripts.stack build bot
python3 -m scripts.stack run --rm --no-deps bot \
  alembic -c services/random_telegram_bot/alembic.ini upgrade head
python3 -m scripts.stack up -d --no-deps --wait bot
```

Перед повторным использованием тега `before-bot-update` убедитесь, что предыдущая
точка восстановления больше не нужна; для отдельных поставок используйте новый тег.
Смена схемы требует отдельного плана совместимости. Откат образа не откатывает SQL.
Исходники для поставки берите из проверенного и опубликованного Git-коммита с помощью
`git archive`. Сохраните его хеш вместе с резервной копией исходников и образов.
Сверьте исходники сервера перед копированием: рабочий каталог VPS может содержать
изменения вне его Git-истории. Приватные env-файлы сохраняются отдельно.

После обновления проверяйте `/`, `/api/health`, `/api/random/channel`,
`/api/random/ready`, `/api/random/db`, вход в `/monitoring/` и свежесть `/monitor/status`.
Обычный рестарт не требует повторной регистрации webhook. Команда
регистрация webhook не входит в обычное обновление сервиса. Сохраняйте ожидающие
Telegram-обновления при отдельном изменении webhook.

Локальная браузерная проверка:

```sh
uv run --locked --group browser python scripts/check_monitoring_browser.py --access-file .local/integration-secrets.json
```

Браузерная проверка требует Chrome и приватного файла аккаунта. Для проверки задержек
каталога измеряйте запросы дольше одного интервала обновления, включая время фоновой
загрузки. Отдельно измеряйте обработку внутри сервиса и полный HTTPS-запрос.

## Резервирование и восстановление

`scripts.backup_database` сохраняет согласованный SQL-снимок в
`/var/backups/synchronisica/mysql/` с правами 0600. `--restore-check` восстанавливает
его в новую БД `synch_restore_*`, повторно экспортирует и сравнивает SQL побайтово,
после чего удаляет только созданную тестовую БД. Исходная база не изменяется.
Автоматическое расписание и копия вне VPS пока не настроены.

Для полного восстановления нужны также приватные env-файлы, исходники и образы,
volume Beszel, состояние агента и сертификаты Caddy. SQL-бэкап не заменяет их копии.
Резервная копия перед объединением находится в
`/var/backups/synchronisica/implementation-20261011T112223Z`.
История основного репозитория хранится в Git. История прежнего бота сохранена
в приватном `.local/backups/original-bot.bundle`, исходные данные старой MySQL —
в `.local/backups/legacy-mysql-data.tar.gz`. Эти копии не входят в поставку.

Для отката обновления бота восстановите его исходники из выбранной точки резервирования,
переназначьте сохранённый исправный образ на `synchronisica-prod-bot`, затем запустите
`scripts.stack up -d --no-deps --wait bot` без `--build` и проверьте HTTPS/readiness.
Выбирайте образ, проверенный до обновления; откат не должен возвращать известную ошибку.

## Мониторинг

Beszel находится в `/monitoring/`, вход по паролю, IP-ограничений нет.
Hub и служебные порты доступны только через loopback; MySQL не публикуется наружу.
`python3 -m scripts.bootstrap_monitoring --checks` привязывает пять
HTTP-проверок к единственному работающему агенту, сохраняя существующие пороги.
При смене агента Beszel назначает проверкам новые ID и начинает новую историю проверок.
Архивный `rockery-lips (initial setup)` приостановлен с сохранением истории.

Пороги: CPU и RAM >90%, диск >85% на протяжении 5 минут; потеря связи и unhealthy
контейнер на протяжении 1 минуты; доля ошибок HTTP >5% в окне мониторинга Beszel.
Внешние уведомления отключены по выбору пользователя. SMTP-восстановление пароля
не проверялось; обычный аккаунт Beszel и административный аккаунт PocketBase различаются.
Zabbix Agent сохранён; чтение systemd-журналов агентом Beszel выключено.
Сборщик метрик находится в `services/monitoring/src/server_monitor/`, прошивка и шаблоны
настроек ESP32 в `esp_software/`. Проверка HTTP и свежести метрик не подтверждает работу
физического дисплея.

События Telegram подтверждаются после записи в SQL. Повтор update_id не повторяет
изменения статистики. Авария после отправки ответа в Telegram и до SQL-подтверждения
может повторить ответ: для отправки используется гарантия at-least-once.
Готовность проверяет SQL, работу обработчиков, ошибки доставки и зависшую очередь.
