# SINEDIS Bitrix24 Automation

## Текущий статус

Реализованы каркас API, асинхронный слой SQLAlchemy, PostgreSQL-модели, первая Alembic-миграция
и проверка готовности базы. Целевая production-версия — Python 3.12; допустимый диапазон
зафиксирован как `>=3.12,<3.14`.

## Назначение

Проект станет локальным серверным приложением Bitrix24 без встроенного интерфейса. Оно будет
принимать ожидающие действия автоматизации, надёжно хранить их в PostgreSQL и продолжать
конкретные процессы через Bitrix24 REST API.

## Архитектура

Планируемый поток данных:

```text
Bitrix24 → FastAPI → PostgreSQL → worker → bizproc.event.send
```

HTTP callback только валидирует и шифрует подписной `EVENT_TOKEN`, после чего создаёт
идемпотентное задание в `automation_jobs`. Worker забирает готовые строки через
`FOR UPDATE SKIP LOCKED`, обращается к REST API только доверенного портала и продолжает
ожидающий бизнес-процесс. В HTTP-обработчиках нет `sleep()`.

## REST-запрос из бизнес-процесса

Подписное действие **REST-запрос** принимает имя метода (например,
`lists.element.get`), JSON object с параметрами и JSONPath. URL передать нельзя, `auth`
в параметрах запрещён, а JSON ограничен 100 000 символами. JSONPath применяется именно
к значению `result` ответа Bitrix24.

```json
{
  "IBLOCK_TYPE_ID": "lists",
  "IBLOCK_ID": 53,
  "FILTER": {"NAME": "{{Госномер транспортного средства}}"}
}
```

`$[0].ID` выбирает первый ID, `$[*].ID` — все ID, `$` — весь `result`. Действие
возвращает `status`, `job_id`, `result_text`, `result_json`, `matches_count`,
`error_code` и `error_message`. Отсутствие совпадений успешно возвращает пустой
`result_text`, строку `null` и нулевой счётчик.

Успешный результат произвольного вызова сохраняется до `bizproc.event.send`. Поэтому
повтор доставки события не повторяет прикладной REST-вызов. Однако exactly-once для
произвольного внешнего POST обеспечить невозможно: при transport timeout метод мог
выполниться, и приложение вернёт `rest_request_outcome_unknown`, не повторяя его.

## Ожидание заполнения поля CRM

Действие **Ожидать заполнения поля CRM** проверяет сделку, лид, контакт, компанию,
счёт или смарт-процесс через `crm.item.get`. Настройки задают `entity_type_id`,
`entity_id`, `field_name`, интервал опроса и общий timeout. `None`, пустая или состоящая
только из пробелов строка, `[]` и `{}` считаются незаполненным значением.

Пустое поле планирует следующий запуск на точный интервал без exponential backoff и
без расходования лимита технических попыток. Заполненное поле возвращает `status`,
`job_id`, `field_value`, `checks_count`, `completed_at`, `error_code` и
`error_message`. По timeout процесс обязательно продолжается со статусом `timeout` и
кодом `field_wait_timeout`, поэтому подписка не остаётся вечной.

## Уведомления действий

В обоих действиях можно выбрать нескольких пользователей. При окончательной ошибке
или timeout каждому отправляется одно системное уведомление через
`im.notify.system.add`. Временные ошибки и обычные проверки пустого поля уведомлений
не создают. Ошибка уведомления не заменяет основной результат и не мешает попытке
продолжить процесс.

## Регистрация действий и роботов

При `ONAPPINSTALL` приложение идемпотентно синхронизирует классические activities и
CRM robots: существующие коды обновляются, отсутствующие добавляются без удаления.
Коды activity и robot различаются, все четыре расширения используют подписку и общие
защищённые callbacks `/api/bitrix/robots/rest-request` и
`/api/bitrix/robots/wait-field`.

Для уже установленного production-портала администратор должен после deployment
выполнить в настроенном окружении:

```bash
python -m app.bitrix.register_extensions
```

Команда выбирает активные порталы, безопасно получает/обновляет OAuth token и печатает
только fingerprint портала и фактически подтверждённые Bitrix24 статусы регистрации.

FastAPI использует лениво создаваемые async engine и session factory. `/ready` выполняет
`SELECT 1`, а shutdown приложения освобождает пул соединений. Bitrix24-интеграция и worker пока
обозначены только границами модулей.

## Технологический стек

- Python 3.12, FastAPI и Uvicorn;
- Pydantic Settings;
- PostgreSQL, SQLAlchemy 2, asyncpg и Alembic;
- httpx и cryptography (интеграция запланирована);
- uv для зависимостей;
- pytest, pytest-asyncio, respx и Ruff для контроля качества.

## Структура проекта

- `app/api` — HTTP endpoints;
- `app/bitrix` — будущая REST/OAuth интеграция;
- `app/robots` — будущий расширяемый реестр роботов;
- `app/jobs` — будущая очередь, worker и recovery;
- `app/models` — SQLAlchemy-модели порталов и отложенных заданий;
- `app/security` — Fernet-шифрование и централизованная защита логов;
- `migrations` — async Alembic environment и версионированная схема;
- `tests` — unit- и integration-тесты;
- `deployment` — каталоги для будущих deployment-шаблонов.

## Локальная подготовка

Требуется Python версии 3.12 или 3.13 и установленный `uv`:

```bash
cp .env.example .env
uv sync --dev
```

Значения в `.env.example` предназначены только для локальной разработки.

## Запуск API

```bash
make api
```

API будет доступен по адресу `http://localhost:8010`; документация development-режима — в
`/docs`, `/redoc` и `/openapi.json`.

## Запуск тестов

```bash
make test
```

Текущие unit-тесты не используют PostgreSQL, Docker или сеть.

## PostgreSQL

Приложение принимает только асинхронный PostgreSQL URL вида:

```dotenv
DATABASE_URL=postgresql+asyncpg://bitrix_app:change_me@localhost:5432/bitrix_automation
```

В Docker Compose hostname меняется на `postgres`; открытые credentials из Compose предназначены
только для локальной разработки и непригодны для production. SQLite намеренно не поддерживается:
схема использует PostgreSQL UUID, JSONB и частичный индекс очереди.

## Модели данных

`bitrix_portals` хранит идентификатор установки, endpoints, будущие зашифрованные OAuth-значения
и статус. `automation_jobs` хранит отложенное действие, JSONB payload/return values, расписание,
состояние попыток и блокировки. Уникальная пара `portal_id + event_token_hash` обеспечивает
идемпотентность, а foreign key не использует каскадное удаление истории.

Статусы хранятся строками с check constraints. Это сохраняет проверку на уровне базы, но не
связывает будущие изменения с трудоёмкими операциями PostgreSQL ENUM.

## Миграции Alembic

Перед любой командой явно проверьте `DATABASE_URL`. Применить текущую схему:

```bash
make migrate
# либо внутри локального Compose после запуска сервисов:
docker compose run --rm api uv run alembic upgrade head
```

Создать и откатить миграцию:

```bash
make revision MESSAGE="describe schema change"
make downgrade
```

Alembic получает URL из Settings; пароль не хранится в `alembic.ini`. Эти команды не выбирают
production автоматически.

## Проверка готовности

`GET /health` проверяет только процесс API. `GET /ready` выполняет `SELECT 1`: при успехе он
возвращает HTTP 200 и `database=available`, при любой ошибке — безопасный HTTP 503 и
`database=unavailable` без exception и реквизитов подключения.

## Тестовая база

Integration-тесты никогда не используют `DATABASE_URL`. Они требуют отдельную, заранее
созданную и мигрированную базу:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://test_user:test_password@localhost:5432/bitrix_automation_test \
  uv run pytest tests/integration
```

Без `TEST_DATABASE_URL` тесты пропускаются; любой URL не с `postgresql+asyncpg://` отклоняется.
Тесты выполняются в rollback-only транзакциях и не должны запускаться против неизвестной базы.

## Управление секретами

OAuth-токены и `EVENT_TOKEN` должны сохраняться только после шифрования. `application_token`
сохраняется только как keyed HMAC, а `EVENT_TOKEN` дополнительно получает детерминированный
fingerprint для идемпотентности. Production-секреты не проверялись и отсутствуют в репозитории.

Файл `.env` нельзя коммитить. Резервные копии ключей должны храниться защищённо и отдельно от
резервных копий PostgreSQL. Redaction снижает риск случайной утечки, но не отменяет правило не
логировать входящие payload и секреты целиком.

## ENCRYPTION_KEY

Сгенерировать совместимый с Fernet ключ можно командой:

```bash
make generate-encryption-key
```

Команда только печатает ключ: она не создаёт и не изменяет `.env`. Ключ необходимо передать
приложению через защищённую конфигурацию. Его нельзя коммитить или хранить рядом с дампом базы.
Потеря всех ключей делает зашифрованные токены невосстановимыми. Приложение намеренно не
генерирует ключ автоматически при запуске.

## Ротация ключей

1. Перенести старый основной ключ в `ENCRYPTION_KEY_PREVIOUS`.
2. Установить новый ключ в `ENCRYPTION_KEY`.
3. Старые ciphertext будут читаться через предыдущий ключ, а новые — создаваться текущим.
4. При использовании или отдельной контролируемой процедуре старые значения перешифровываются.
5. После полной ротации удалить предыдущий ключ из конфигурации.

Эта процедура подготовлена в коде, но не применялась к реальным данным.

## Формат зашифрованных данных

Ciphertext хранится в envelope `fernet:v1:<token>`. Алгоритм и версия проверяются до
расшифрования; неизвестные или повреждённые значения дают безопасную ошибку без содержимого
ciphertext. Новые значения всегда шифруются текущим ключом.

## Hash EVENT_TOKEN

`event_token_hash` — полный lowercase SHA-256 fingerprint. Он нужен для детерминированной
идемпотентности в паре с `portal_id`, не содержит средство расшифрования и не заменяет хранение
самого токена в зашифрованном виде.

## Проверка application_token

`application_token_hash` имеет формат `hmac-sha256:v1:<64 hex>`, помещается в существующий
`varchar(128)` и создаётся HMAC-SHA256 с ключом, производным от `ENCRYPTION_KEY`. Проверка
использует `hmac.compare_digest`; открытый токен и digest не логируются. Во время ротации
поддерживается проверка hash с ключом, производным от `ENCRYPTION_KEY_PREVIOUS`.

## Защита логов

Logging filter рекурсивно маскирует известные секретные ключи, Bearer credentials, Cookie,
формы `key=value`, query parameters и пароль в DSN маской `***REDACTED***`. Фильтр также очищает
аргументы, structured extras и текст исключений. Это best-effort защита, а не DLP-система:
прикладной код всё равно не должен передавать секретные payload в logger.

## Bitrix24 REST client

`BitrixClient` выполняет универсальные асинхронные POST-вызовы REST-методов. Он получает
проверенный `client_endpoint` и уже готовый `access_token`, поддерживает повторное использование
одного connection pool и возвращает типизированный `BitrixResponse`.

Все автоматические тесты клиента используют `respx` и HTTPS mock-host `portal.test`. Запросы к
реальному Bitrix24 на этом этапе не выполнялись.

## Граница ответственности REST-клиента

Клиент не читает PostgreSQL, не принимает модель `BitrixPortal`, не расшифровывает сохранённые
токены и не выполняет OAuth refresh. Эти обязанности будут реализованы отдельным OAuth-сервисом.
Клиент также не создаёт задания и не меняет статус портала.

Автоматического retry нет: любой REST-метод выполняется ровно один раз. Универсальный клиент не
может считать каждый POST идемпотентным; решение о повторе принимает будущий registration service
или worker с учётом конкретного метода и ограничения числа попыток.

## Формат запросов

`client_endpoint` должен быть абсолютным HTTPS URL без credentials, query и fragment. Имя метода
может содержать только буквы, цифры, `_` и разделяющие точки. После проверки URL строится в
формате `{client_endpoint}/{method}` без возможности заменить hostname.

Тело отправляется как JSON:

```json
{
  "auth": "<готовый access_token>",
  "parameter": "value"
}
```

Передача `auth` внутри пользовательских `params` запрещена. Исходный mapping параметров не
изменяется.

## Формат ответов

Успешный ответ должен быть JSON object с ключом `result`; значение может быть object, list,
boolean, string, number или `null`. Отдельно сохраняются `next`, `total` и `time`, поскольку они
нужны для пагинации и анализа лимитов. HTML, plain text, повреждённый JSON и object без `result`
считаются некорректным ответом.

## Классификация ошибок

- `expired_token`, `NO_AUTH_FOUND` и эквивалентные коды — authentication error;
- `ACCESS_DENIED`, `INVALID_CREDENTIALS`, `user_access_error`, insufficient scope — permission;
- `QUERY_LIMIT_EXCEEDED`, `OPERATION_TIME_LIMIT` — retryable rate limit;
- `INTERNAL_SERVER_ERROR`, `ERROR_UNEXPECTED_ANSWER` и неструктурированные 5xx — temporary;
- неизвестные 4xx и `OVERLOAD_LIMIT` — permanent.

Клиент анализирует Bitrix `error` даже при HTTP 200. Если структурированной ошибки нет, класс
выбирается консервативно по HTTP status. `error_description` не включается в `str(exception)` и
не предназначен для ответа конечному пользователю.

## Лимиты Bitrix24

Для `QUERY_LIMIT_EXCEEDED` и `OPERATION_TIME_LIMIT` сохраняется `Retry-After`, если он передан как
секунды или HTTP-date. `time` ошибки сохраняется для доступа к `operating_reset_at`. Клиент не
вызывает `sleep()`. `OVERLOAD_LIMIT` не считается обычной кратковременной ошибкой для быстрого
автоматического повтора: требуется отдельное операционное решение.

## Безопасность REST-запросов

Access token находится только в JSON body запроса и не журналируется. Логи содержат только имя
метода, HTTP status, код/категорию ошибки и длительность. Request/response payload, headers,
endpoint и `error_description` в лог не передаются. Redirects отключены, а внутренний httpx
client использует раздельные timeout и ограниченный pool соединений.

## Installation callback

`POST /api/bitrix/install` принимает JSON, form-urlencoded и multipart payload события
`ONAPPINSTALL`. Callback проверяет обязательные OAuth-поля и HTTPS endpoints, вызывает `app.info`
с полученным access token и только после успешной проверки выполняет PostgreSQL upsert по
`member_id`. Access и refresh tokens шифруются, `application_token` сохраняется как HMAC, а
портал получает статус `active`. Повторная установка обновляет ту же запись и не удаляет задания.

Приложение работает без UI, поэтому `BX24.installFinish()` не используется.

## Uninstall callback

`POST /api/bitrix/uninstall` принимает `ONAPPUNINSTALL`, находит портал по `member_id` и проверяет
callback через сохранённый HMAC `application_token`. OAuth token во время удаления может быть уже
недействителен, поэтому REST-вызов не выполняется. Портал становится `inactive`; токены, портал и
история заданий физически не удаляются. `data[CLEAN]=1` фиксируется только как намерение и не
меняет это правило сохранения истории.

## OAuth token lifecycle

Access token имеет ограниченный срок. Если до `token_expires_at` остаётся больше safety window
(`BITRIX_OAUTH_EXPIRY_SKEW_SECONDS`, по умолчанию 60 секунд), возвращается сохранённый token без
refresh. Истёкший или скоро истекающий token обновляется через refresh token. Refresh перед
каждым REST-запросом запрещён.

После явного `BitrixAuthenticationError` portal service принудительно обновляет пару и повторяет
REST-вызов ровно один раз. Permission, transport, rate-limit и остальные ошибки не повторяются,
поскольку POST-метод может иметь побочные эффекты. Вторая authentication error переводит портал
в `auth_error`.

## OAuth refresh

Refresh URL строится из origin сохранённого `server_endpoint`: исходный path отбрасывается и
заменяется фиксированным `/oauth/token/`. Требуются HTTPS, hostname и отсутствие credentials,
query и fragment. Запрос передаёт `grant_type=refresh_token`, `client_id`, `client_secret` и
`refresh_token` как form body; URL с секретной query string не создаётся и payload не логируется.

Authorization server должен вернуть новую пару, совпадающий `member_id` и нормализуемые
`client_endpoint`/`server_endpoint`. `invalid_client`, `invalid_grant`, `PAYMENT_REQUIRED` и
несовпадение портала являются постоянным отказом; timeout и HTTP 5xx — временными.

## Atomic token replacement

При каждом успешном refresh одновременно заменяются access token, refresh token, фактический
expiry, endpoints, domain и status. Оба token шифруются до записи. Частично обновлённая пара не
коммитится.

## Concurrent refresh protection

Refresh выполняется внутри транзакции после `SELECT ... FOR UPDATE` по UUID портала. Второй API
или worker process ждёт row lock, затем повторно проверяет expiry и использует уже обновлённый
token. Блокировка удерживается во время одного authorization request: это осознанный trade-off,
который предотвращает параллельное использование одноразово заменяемого refresh token.

## Application token verification

`application_token` никогда не хранится открыто. Installation сохраняет versioned HMAC, а
uninstall использует constant-time verification. Неизвестный `member_id` и неверный token дают
одинаковый безопасный отказ, чтобы не раскрывать наличие портала.

## Portal statuses

- `active` — разрешено получать и обновлять OAuth token;
- `inactive` — приложение удалено, история сохранена;
- `auth_error` — refresh необратимо отклонён либо повторный REST-вызов снова отверг token.

Временная ошибка authorization server не переводит портал в `auth_error`.

## Manual Bitrix24 setup

Тип приложения: локальное серверное приложение, режим «использует только API», без UI.

```text
Initial installation path:
https://bitrix24.sinedis.pl/api/bitrix/install

Uninstall handler:
https://bitrix24.sinedis.pl/api/bitrix/uninstall
```

Installation callback теперь идемпотентно регистрирует uninstall handler через `event.get` и
`event.bind`. Регистрация на реальном production-портале не проверялась. Production `client_id`,
`client_secret` и tokens в документации не публикуются.

## Линтинг и форматирование

```bash
make format-check
make lint
make format
```

## Ограничения текущего этапа

- OAuth и lifecycle callbacks реализованы; для них подготовлены изолированные mock-тесты.
- PostgreSQL-схема не применялась и integration-тесты не запускались в текущей среде.
- Worker, retry и recovery реализованы, но не проверялись с production Bitrix24.
- Production-развёртывание не реализовано и не проверено.
- В текущей среде Codex отсутствуют Docker и PostgreSQL, поэтому Compose-шаблон не проверялся.
- PyPI недоступен в текущей среде, поэтому зависимости и pytest не удалось запустить.

## Реестр роботов

Реестр содержит типизированные определения роботов приложения и сейчас публикует только
`sinedis.short_pause.v1`. Он не обращается к базе или Bitrix24 и позволяет добавлять новые
версии роботов без изменения механизма регистрации.

## Робот «Короткая пауза»

Робот имеет локализованные имя, описание, входные параметры `delay_seconds` и `comment`, а
также будущие возвращаемые значения. `USE_SUBSCRIPTION=Y` переводит процесс в ожидание.
Handler строится исключительно из проверенного `APP_BASE_URL`; входящий portal endpoint для
этого не используется.

## Регистрация и обновление робота

После успешного `app.info` установка вызывает `bizproc.robot.list`. Отсутствующий робот
добавляется через `bizproc.robot.add`, а существующий при переустановке обновляется через
`bizproc.robot.update` с `CODE` и вложенным `FIELDS`. Это сохраняет актуальные handler и
описание без удаления или дублирования робота.

## Регистрация ONAPPUNINSTALL

Установка проверяет `event.get` на точное совпадение события `ONAPPUNINSTALL` и handler
`{APP_BASE_URL}/api/bitrix/uninstall`. Только отсутствующая привязка создаётся через
`event.bind`; offline event и `auth_type` не задаются.

## Формат callback робота

`POST /api/bitrix/robots/short-pause` принимает JSON, form-urlencoded и multipart payload,
включая bracket notation Bitrix24. Callback подтверждает активный портал и проверяет
`application_token` через HMAC. Продолжительность ограничена настройками
`SHORT_PAUSE_MIN_SECONDS` и `SHORT_PAUSE_MAX_SECONDS`; запрос не выполняет `sleep` и сразу
возвращает идентификатор ожидающего задания.

## Идемпотентность EVENT_TOKEN

`EVENT_TOKEN` шифруется Fernet перед хранением. Полный детерминированный SHA-256 fingerprint
без salt используется только вместе с `portal_id` для идемпотентности. PostgreSQL
`INSERT ... ON CONFLICT DO NOTHING` гарантирует одну строку при конкурентной повторной
доставке. Повтор возвращает исходные `job_id` и `run_at`, поэтому пауза не продлевается.

## Создание AutomationJob

Новое задание получает статус `pending`, `attempts=0`, `max_attempts=10` и `run_at`, равный
UTC-времени приёма плюс запрошенная задержка. JSONB содержит только диагностические данные
задания; OAuth-, application- и event-токены туда не записываются.

## Worker

Запуск: `python -m app.jobs.worker` или `make worker`. Worker не делает `sleep` для
конкретной паузы: расписание хранится в PostgreSQL, поэтому паузы переживают рестарт.
Одноразовый безопасный цикл recovery + одной batch доступен через `make worker-once`.

## Claim заданий

Наступившие `pending`/`retry` строки выбираются по `run_at` через `SELECT FOR UPDATE SKIP
LOCKED`. Claim выполняется короткой транзакцией, повышает `attempts`, записывает lease и
завершается до сетевого вызова. Поэтому одну строку получает ровно один worker, а исчерпавшие
`max_attempts` задания больше не выбираются.

## bizproc.event.send и Return values

Worker расшифровывает актуальный `EVENT_TOKEN` только перед доставкой и вызывает
`bizproc.event.send` с точными ключами `EVENT_TOKEN`, `RETURN_VALUES`, `LOG_MESSAGE`.
Возвращаются только зарегистрированные `status`, `job_id`, `scheduled_at`, `resumed_at`,
`requested_delay_seconds`, `actual_delay_seconds`. Успех подтверждается только `result=true`.
`ACCESS_DENIED` без предшествующего неоднозначного исхода означает invalid/expired event token.

## Retry policy

Временные, rate-limit и transport ошибки назначают новый `run_at`: bounded exponential
backoff плюс jitter, а также более поздний `Retry-After` или `operating_reset_at`. Worker не
ждёт отдельное задание. На последней попытке ошибка становится terminal `failed`.

## Неоднозначный transport timeout

HTTP-запрос мог быть принят, даже если ответ потерян. Первый такой исход сохраняется как
`event_delivery_outcome_unknown` и допускает осторожный retry. Последующий `ACCESS_DENIED`
переводит задание в `failed`/manual review, а не в ложный `completed`. Exactly-once доставка
внешнего HTTP-вызова не гарантируется.

## Recovery и graceful shutdown

При старте и затем по интервалу worker восстанавливает только просроченные `processing` lease:
доступные попытки переходят в `retry`, исчерпанные — в `failed`. SIGINT/SIGTERM останавливают
новые claim, позволяют текущей обработке закончиться и закрывают OAuth/REST clients и engine.
Незавершённый lease не переписывается немедленно и позднее обрабатывается recovery.

## Docker Compose

Compose запускает PostgreSQL, API и worker из одного image и `.env`; worker не публикует
порты и зависит от healthy PostgreSQL. Конфигурация не запускалась в текущей среде.

## Production deployment

Шаблоны systemd, Nginx, безопасные deployment scripts и пошаговый checklist находятся в
`deployment/README.md`. Они требуют ручной проверки, production `.env`, PostgreSQL и TLS.

## Ручная проверка Bitrix24

После установки необходимо проверить появление робота, выполнить тестовую паузу,
перезапустить worker во время ожидания и подтвердить продолжение процесса. Production и
реальный Bitrix24 на этом этапе не проверялись.
