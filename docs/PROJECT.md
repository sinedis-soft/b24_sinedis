# Полная документация проекта

## 1. Назначение и границы

SINEDIS Bitrix24 Automation — локальное серверное приложение Bitrix24 без собственного UI.
Оно регистрирует подписные роботы, принимает их callbacks, надёжно сохраняет задания и позже
продолжает ожидающий процесс методом `bizproc.event.send`.

Система предназначена для коротких отложенных действий, универсальных REST-вызовов и ожидания
поля CRM. Она не заменяет Bitrix24, не хранит пользовательские пароли и не принимает произвольный
URL: исходящие вызовы всегда направляются на сохранённый и проверенный endpoint портала.

## 2. Компоненты и поток данных

```text
Портал Bitrix24
  │ POST callback + EVENT_TOKEN
  ▼
FastAPI (app/api)
  │ проверка application_token, шифрование, идемпотентный INSERT
  ▼
PostgreSQL (bitrix_portals, automation_jobs)
  │ SELECT ... FOR UPDATE SKIP LOCKED
  ▼
Worker (app/jobs)
  │ OAuth refresh при необходимости, REST-вызов
  ▼
Bitrix24 (bizproc.event.send)
```

Основные модули:

| Каталог | Ответственность |
|---|---|
| `app/api` | health/readiness, lifecycle callbacks, callbacks роботов |
| `app/bitrix` | REST-клиент, OAuth, проверка payload, регистрация расширений |
| `app/robots` | определения, входные и выходные параметры роботов |
| `app/jobs` | очередь, claim, обработка, retry и recovery |
| `app/models` | модели порталов и заданий |
| `app/security` | Fernet-шифрование, HMAC и очистка логов |
| `migrations` | схема Alembic |
| `deployment` | Ubuntu/systemd/Nginx scripts и шаблоны |
| `tests` | unit- и PostgreSQL integration-тесты |

API не выполняет `sleep()`. Время следующего запуска хранится в БД, поэтому паузы и ожидания
переживают перезапуск процессов. Несколько worker могут безопасно разбирать задания благодаря
`FOR UPDATE SKIP LOCKED`.

## 3. Жизненный цикл портала

### Установка

`POST /api/bitrix/install` принимает `ONAPPINSTALL` в JSON, form-urlencoded или multipart.
Обязательны OAuth credentials, `member_id`, HTTPS endpoints и `application_token`. Приложение:

1. подтверждает доступ вызовом `app.info`;
2. добавляет или обновляет три робота;
3. удаляет известные устаревшие дубли classic activities;
4. привязывает `ONAPPUNINSTALL`;
5. шифрует access/refresh tokens и выполняет upsert портала по `member_id`.

Переустановка идемпотентна и обновляет существующую запись.

### Удаление

`POST /api/bitrix/uninstall` проверяет `application_token` и переводит портал в `inactive`.
Портал, токены и история заданий не удаляются, включая callback с `CLEAN=1`. Такая политика
сохраняет аудит; физическое удаление требует отдельной контролируемой процедуры.

Статусы портала: `active`, `inactive`, `auth_error`. Последний означает необратимый отказ OAuth
refresh или повторное отклонение обновлённого токена.

## 4. HTTP API

| Метод и путь | Назначение | Успех |
|---|---|---|
| `GET /health` | liveness процесса, без обращения к БД | HTTP 200 |
| `GET /ready` | `SELECT 1` в PostgreSQL | HTTP 200 либо безопасный 503 |
| `POST /api/bitrix/install` | установка/переустановка | состояние портала и регистрации |
| `POST /api/bitrix/uninstall` | деактивация портала | `status=inactive` |
| `POST /api/bitrix/robots/short-pause` | постановка паузы в очередь | `job_id`, `run_at`, `existing` |
| `POST /api/bitrix/robots/rest-request` | постановка REST-запроса | `job_id`, `run_at`, `existing` |
| `POST /api/bitrix/robots/wait-field` | постановка ожидания поля | `job_id`, `run_at`, `existing` |

Production отключает OpenAPI endpoints. В development доступны `/docs`, `/redoc` и
`/openapi.json`. Callback endpoints предназначены для Bitrix24, а не для ручного вызова:
подделанный или неизвестный `application_token` отклоняется.

Типовые безопасные ответы: 400 — некорректный payload, 403 — credentials робота отклонены,
503 — временно недоступны БД или Bitrix24. Секреты и подробности внутренней ошибки наружу не
возвращаются.

## 5. Роботы

### Короткая пауза (`sinedis.short_pause.v1`)

Вход: обязательное целое `delay_seconds` и необязательный `comment` до 1000 символов. Диапазон
определяют `SHORT_PAUSE_MIN_SECONDS`/`SHORT_PAUSE_MAX_SECONDS` (по умолчанию 1–3600).

Выход: `status`, `job_id`, `scheduled_at`, `resumed_at`, `requested_delay_seconds`,
`actual_delay_seconds`.

### REST-запрос (`sinedis.rest_request.robot.v1`)

Вход: `rest_method`, JSON object в `request_params_json` (до 100 000 символов), `jsonpath` и
необязательные `error_recipients`. Метод должен состоять из допустимого REST-имени; URL и ключ
`auth` передать нельзя. JSONPath применяется к полю `result` ответа.

Выход: `status`, `job_id`, `result_text`, `result_json`, `matches_count`, `error_code`,
`error_message`. Нет совпадений — успешный пустой `result_text`, `result_json="null"` и 0.

### Ожидать заполнения поля CRM (`sinedis.wait_field.robot.v1`)

Вход: положительные `entity_type_id` и `entity_id`, безопасный код `field_name`, интервал
`poll_interval_seconds` (не более 86400), timeout `timeout_seconds` (не более 31536000), а также
необязательные `error_recipients`. Проверка выполняется через `crm.item.get`. Пустыми считаются
`null`, пустая/пробельная строка, `[]` и `{}`.

Выход: `status`, `job_id`, `field_value`, `checks_count`, `completed_at`, `error_code`,
`error_message`. Истечение срока возвращает `status=timeout` и `field_wait_timeout`, после чего
процесс продолжается и не остаётся подписанным навсегда.

Подробные сценарии настройки приведены в [руководстве Bitrix24](BITRIX24_USER_GUIDE.md).

## 6. Очередь, идемпотентность и ошибки

`automation_jobs` хранит payload, результат, расписание, попытки и lease. Пара
`portal_id + event_token_hash` уникальна: повторный callback получает исходные `job_id` и
`run_at`, а не создаёт второе действие.

Worker короткой транзакцией забирает наступившие `pending`/`retry`, фиксирует `processing` и
делает сетевой вызов уже вне claim-транзакции. Временные ошибки получают ограниченный
exponential backoff с jitter и учитывают `Retry-After`; просроченные lease возвращает recovery.
Проверка пустого поля переносит `run_at` ровно на заданный интервал и не расходует лимит
технических retry.

Успешный результат REST-запроса сохраняется до отправки события, поэтому повтор доставки не
повторяет прикладной вызов. Но exactly-once для внешнего POST принципиально не гарантируется:
при transport timeout запрос мог выполниться. В таком случае возвращается
`rest_request_outcome_unknown` без автоматического повтора прикладного вызова. Аналогичный
неоднозначный исход доставки события может потребовать ручной проверки.

При финальной ошибке или timeout выбранные пользователи однократно уведомляются через
`im.notify.system.add`. Ошибка уведомления не меняет основной результат и не мешает продолжить
процесс.

## 7. Данные и безопасность

`bitrix_portals` хранит идентификатор установки, проверенные endpoints, зашифрованные OAuth
tokens, HMAC application token и статус. `automation_jobs` хранит зашифрованный `EVENT_TOKEN`,
его SHA-256 fingerprint, payload/результат и состояние обработки. PostgreSQL выбран из-за UUID,
JSONB, ограничений и частичного индекса; SQLite не поддерживается.

- ciphertext: `fernet:v1:<token>`;
- application token: `hmac-sha256:v1:<64 hex>`;
- ключи не генерируются приложением автоматически;
- `ENCRYPTION_KEY_PREVIOUS` позволяет читать данные во время ротации;
- logging filter маскирует известные credentials, DSN, headers и structured extras;
- redirects REST-клиента отключены, endpoint/метод валидируются, access token передаётся в body.

Redaction — защита от случайной утечки, а не DLP. Никогда не журналируйте payload, заголовки,
`.env`, ciphertext или ответы с персональными данными целиком.

## 8. Конфигурация

Все значения читаются из окружения или `.env`.

| Переменная | Назначение / значение по умолчанию |
|---|---|
| `APP_ENV` | `development`; для production задайте `production` |
| `APP_NAME`, `APP_VERSION` | метаданные сервиса |
| `APP_HOST`, `APP_PORT` | bind приложения; systemd явно использует `127.0.0.1:8010` |
| `APP_BASE_URL` | внешний HTTPS origin/base path для callbacks |
| `LOG_LEVEL` | уровень логов, обычно `INFO` |
| `DATABASE_URL` | только `postgresql+asyncpg://...` |
| `BITRIX_CLIENT_ID` | client ID локального приложения |
| `BITRIX_CLIENT_SECRET` | client secret локального приложения |
| `ENCRYPTION_KEY` | текущий обязательный Fernet key |
| `ENCRYPTION_KEY_PREVIOUS` | предыдущий ключ на время ротации |
| `ADMIN_API_TOKEN` | зарезервирован; административного HTTP API сейчас нет |
| `WORKER_POLL_INTERVAL_SECONDS` | пауза polling loop, 1 |
| `WORKER_BATCH_SIZE` | размер claim batch, 50 |
| `WORKER_LOCK_TIMEOUT_SECONDS` | срок lease до recovery, 120 |
| `WORKER_RECOVERY_INTERVAL_SECONDS` | период recovery, 60 |
| `WORKER_RETRY_BASE_SECONDS` | база backoff, 5 |
| `WORKER_RETRY_MAX_SECONDS` | максимум backoff, 300 |
| `WORKER_RETRY_JITTER_SECONDS` | jitter, 2 |
| `SHORT_PAUSE_MIN_SECONDS` | минимум паузы, 1 |
| `SHORT_PAUSE_MAX_SECONDS` | максимум паузы, 3600 |
| `BITRIX_HTTP_*` | connect/read/write/pool timeouts и pool size REST-клиента |
| `BITRIX_OAUTH_EXPIRY_SKEW_SECONDS` | запас до refresh, 60 |
| `BITRIX_OAUTH_CONNECT_TIMEOUT_SECONDS` | connect timeout OAuth, 5 |
| `BITRIX_OAUTH_READ_TIMEOUT_SECONDS` | read timeout OAuth, 20 |

`WORKER_RETRY_MAX_SECONDS` не может быть меньше base, максимум короткой паузы — меньше минимума.
Пустые optional secrets считаются не настроенными.

## 9. Разработка, миграции и тесты

```bash
cp .env.example .env
uv sync --dev
make generate-encryption-key
make migrate
make api
make worker
```

Полезные команды:

| Команда | Назначение |
|---|---|
| `make format` | форматировать Ruff |
| `make format-check` | проверить формат без изменения |
| `make lint` | статический lint |
| `make test` | все доступные pytest tests |
| `make worker-once` | recovery и один batch |
| `make revision MESSAGE="..."` | создать Alembic migration |
| `make downgrade` | откатить одну migration |

Integration-тесты используют только `TEST_DATABASE_URL`:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:5432/bitrix_automation_test \
  uv run pytest tests/integration
```

База должна быть отдельной и мигрированной. Тесты выполняются в rollback-only транзакциях.

## 10. Эксплуатация и ограничения

Полная production-процедура, обновления, backup/restore и runbook находятся в
[OPERATIONS.md](OPERATIONS.md).

Текущие ограничения:

- встроенного UI и административного HTTP API нет;
- произвольный REST-метод работает лишь в пределах scopes установленного приложения;
- физическая очистка данных удалённого портала не автоматизирована;
- метрики/дашборд не встроены — состояние контролируется health checks, systemd, логами и БД;
- шаблон deployment нужно адаптировать под домен, ОС, PostgreSQL и политику секретов организации;
- production Bitrix24 необходимо пройти приёмочные тесты из руководства после каждого значимого
  обновления.
