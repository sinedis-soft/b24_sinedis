# SINEDIS Bitrix24 Automation

Серверное приложение для Bitrix24, которое добавляет подписные роботы, сохраняет ожидающие
действия в PostgreSQL и продолжает бизнес-процессы через REST API Bitrix24. Приложение не имеет
собственного пользовательского интерфейса: сотрудники работают с ним в редакторе роботов и
бизнес-процессов Bitrix24.

```text
Bitrix24 → FastAPI → PostgreSQL → worker → Bitrix24 REST API
```

## Документация

- [Полная документация проекта](docs/PROJECT.md) — назначение, архитектура, API, модель данных,
  безопасность, разработка и тестирование.
- [Руководство пользователя Bitrix24](docs/BITRIX24_USER_GUIDE.md) — установка приложения,
  добавление и настройка роботов, примеры и диагностика.
- [Развёртывание и эксплуатация](docs/OPERATIONS.md) — production-конфигурация, systemd, Nginx,
  миграции, обновление, мониторинг, резервное копирование и аварийные процедуры.

## Возможности

- **Короткая пауза** — продолжает автоматизацию через заданное число секунд.
- **REST-запрос** — вызывает разрешённый приложению REST-метод и извлекает данные по JSONPath.
- **Ожидать заполнения поля CRM** — опрашивает поле CRM до заполнения или timeout.
- Идемпотентный приём повторных callbacks, надёжная PostgreSQL-очередь, retry и восстановление
  зависших заданий.
- Безопасное хранение OAuth- и `EVENT_TOKEN`, автоматическое обновление OAuth-токенов и
  маскирование секретов в логах.

## Быстрый локальный запуск

Нужны Python 3.12/3.13, `uv` и PostgreSQL:

```bash
cp .env.example .env
make generate-encryption-key       # перенесите вывод в ENCRYPTION_KEY файла .env
uv sync --dev
make migrate
make api                           # отдельный терминал
make worker                        # отдельный терминал
```

API слушает `http://localhost:8010`. В development доступны `/docs`, `/redoc` и
`/openapi.json`; проверки процесса и базы — `/health` и `/ready`.

Локальный вариант с контейнерами:

```bash
cp .env.example .env
# заполните ENCRYPTION_KEY
docker compose up -d postgres
docker compose run --rm api uv run alembic upgrade head
docker compose up -d api worker
```

## Проверки

```bash
make format-check
make lint
make test
```

Integration-тестам нужна отдельная, заранее созданная PostgreSQL-база в
`TEST_DATABASE_URL`; без неё они пропускаются. Не направляйте тесты в production-базу.

## Важные условия production

- публичный `APP_BASE_URL` должен быть HTTPS и доступен серверам Bitrix24;
- API и worker должны работать одновременно;
- перед запуском применяются Alembic-миграции;
- `.env`, ключи, OAuth credentials и дампы базы не коммитятся;
- ключ шифрования резервируется отдельно от дампа PostgreSQL;
- приложение в Bitrix24 создаётся как локальное серверное приложение «только API».

Пошаговая инструкция находится в [руководстве по эксплуатации](docs/OPERATIONS.md), а действия
администратора портала — в [руководстве Bitrix24](docs/BITRIX24_USER_GUIDE.md).
