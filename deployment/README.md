# Deployment templates

Каталог содержит стартовые шаблоны для production-развёртывания на Ubuntu:

- `scripts/install-ubuntu.sh` — базовые пакеты, service user и каталог приложения;
- `scripts/deploy.sh` — virtual environment, установка, миграции и systemd services;
- `scripts/migrate.sh` — `alembic upgrade head` с production `.env`;
- `scripts/health-check.sh` — API/readiness и состояния services;
- `systemd/` — отдельные units API и worker;
- `nginx/` — HTTPS reverse proxy для демонстрационного домена.

Полная пошаговая инструкция, требования, процедура обновления, мониторинг, backup, ротация
ключей и runbook находятся в [`docs/OPERATIONS.md`](../docs/OPERATIONS.md). Настройка локального
приложения и роботов на портале описана в
[`docs/BITRIX24_USER_GUIDE.md`](../docs/BITRIX24_USER_GUIDE.md).

Перед применением обязательно замените домен, пути и параметры окружения, проверьте каждый
скрипт и шаблон. Файлы не создают production secrets и не заменяют политику эксплуатации
организации.
