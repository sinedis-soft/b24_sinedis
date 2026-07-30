# Развёртывание и эксплуатация

## 1. Production-топология и требования

Рекомендуется Ubuntu host с Python 3.12/3.13, PostgreSQL, Nginx и TLS. API слушает только
`127.0.0.1:8010`, Nginx завершает TLS, worker запускается отдельным systemd service. DNS и
HTTPS должны быть готовы до установки приложения в Bitrix24.

Шаблоны в `deployment/` — отправная точка: замените домен `bitrix24.sinedis.pl`, пути,
пользователя, параметры PostgreSQL и сертификаты под окружение. Не включайте TLS-конфигурацию с
несуществующими файлами сертификата; сначала выпустите сертификат и выполните `nginx -t`.

## 2. Подготовка Ubuntu

```bash
sudo deployment/scripts/install-ubuntu.sh
```

Скрипт устанавливает базовые пакеты, создаёт системного пользователя `sinedis-bitrix` и каталог
`/opt/bitrix24-automation`, но намеренно не генерирует секреты и не создаёт `.env`.

Разместите проверенный checkout в каталоге приложения и назначьте владельца:

```bash
sudo chown -R sinedis-bitrix:sinedis-bitrix /opt/bitrix24-automation
sudo -u sinedis-bitrix cp .env.example /opt/bitrix24-automation/.env
sudo chmod 600 /opt/bitrix24-automation/.env
```

Создайте отдельные PostgreSQL database/user с минимальными правами, настройте TLS/сетевой доступ,
backup и мониторинг. Не используйте `change_me` из Compose.

## 3. Секреты и `.env`

Сгенерируйте ключ от имени service user:

```bash
cd /opt/bitrix24-automation
sudo -u sinedis-bitrix python3 scripts/generate_encryption_key.py
```

Команда только печатает Fernet key. Перенесите его в secret manager и `ENCRYPTION_KEY`, не
сохраняйте в shell history/тикете. Минимальная production-конфигурация:

```dotenv
APP_ENV=production
APP_BASE_URL=https://automation.example.com
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://SERVICE_USER:SECRET@DB_HOST:5432/SERVICE_DB
BITRIX_CLIENT_ID=...
BITRIX_CLIENT_SECRET=...
ENCRYPTION_KEY=...
ENCRYPTION_KEY_PREVIOUS=
```

Добавьте worker/HTTP параметры из `.env.example` при необходимости. Резервную копию ключа
храните отдельно от дампа БД; потеря всех ключей делает tokens невосстановимыми.

## 4. Установка и первый запуск

```bash
cd /opt/bitrix24-automation
sudo deployment/scripts/deploy.sh
```

Скрипт создаёт `.venv`, устанавливает package, выполняет миграции, устанавливает systemd units и
перезапускает API/worker. Затем адаптируйте и установите Nginx template, выпустите TLS, проверьте:

```bash
sudo nginx -t
curl --fail https://automation.example.com/health
curl --fail https://automation.example.com/ready
sudo systemctl --no-pager --full status sinedis-bitrix-api sinedis-bitrix-worker
```

Только после готовности создавайте/устанавливайте локальное приложение по
[руководству Bitrix24](BITRIX24_USER_GUIDE.md).

## 5. Обновление

1. Сделайте backup БД и проверьте наличие резервного ключа.
2. Получите проверенную версию кода без destructive reset.
3. Просмотрите миграции и release notes/diff.
4. Выполните deployment script или вручную установите package и `alembic upgrade head`.
5. Перезапустите оба сервиса и проверьте health/readiness/status/logs.
6. Для существующих порталов синхронизируйте роботы:

   ```bash
   sudo -u sinedis-bitrix .venv/bin/python -m app.bitrix.register_extensions
   ```

7. Выполните короткий приёмочный сценарий в тестовой CRM-сущности.

Не запускайте новую версию worker со старой несовместимой схемой. Downgrade делайте только после
проверки обратимости миграции и совместимости кода; обычная команда разработки —
`.venv/bin/alembic downgrade -1`.

## 6. Мониторинг

Проверки:

```bash
deployment/scripts/health-check.sh
journalctl -u sinedis-bitrix-api -u sinedis-bitrix-worker --since '30 min ago'
systemctl is-active sinedis-bitrix-api sinedis-bitrix-worker
curl --fail --silent --show-error https://automation.example.com/ready
```

- `/health` подтверждает только процесс API;
- `/ready` подтверждает доступ API к БД;
- успешный `/ready` ничего не говорит о worker или Bitrix24, поэтому проверяйте systemd и
  периодические тестовые задания;
- контролируйте рост `pending`, `retry`, `processing`, `failed`, `expired`, старые `run_at` и
  просроченные lease запросами/средствами мониторинга БД;
- алертируйте на рестарты services, 5xx Nginx, недоступность БД, ошибки OAuth и terminal jobs.

Логи считаются чувствительными даже после redaction. Ограничьте доступ и срок хранения; не
публикуйте их целиком.

## 7. Backup и восстановление

Резервируйте:

1. PostgreSQL database;
2. текущий и, во время ротации, предыдущий encryption key в отдельном secret storage;
3. `.env`/secret definitions безопасным способом;
4. точную версию приложения и миграций.

Регулярно проверяйте restore в изолированном окружении. После восстановления согласуйте DNS и
`APP_BASE_URL`, примените миграции, запустите API/worker, проверьте `/ready`, затем безопасное
тестовое задание. Не подключайте копию одновременно к production-порталу: два активных worker
набора могут конкурировать за внешние callbacks/события.

## 8. Ротация encryption key

1. Сохраните текущий `ENCRYPTION_KEY` как `ENCRYPTION_KEY_PREVIOUS`.
2. Создайте новый key и установите его как `ENCRYPTION_KEY`.
3. Перезапустите API и worker одновременно в коротком окне.
4. Проверьте старые порталы и новые задания.
5. Перешифруйте старые значения контролируемой процедурой или дождитесь их предусмотренного
   обновления; автоматической массовой команды в проекте нет.
6. Удаляйте previous key только после доказательства, что старых ciphertext не осталось, и после
   свежего backup.

Application-token HMAC также проверяется предыдущим ключом во время ротации. Преждевременное
удаление previous key нарушит callbacks старых установок.

## 9. Runbook инцидентов

### API или `/ready` недоступен

Проверьте Nginx → API service → `.env`/DATABASE_URL → PostgreSQL → миграции. Не переустанавливайте
приложение, пока callback нестабилен. После восстановления проверьте worker: сохранённые задания
должны продолжить обработку.

### Worker остановлен или очередь растёт

Проверьте service/logs/БД/доступ к Bitrix24. Безопасно перезапустите worker; recovery заберёт
просроченные `processing` lease. Не меняйте строки заданий вручную без backup и анализа статуса.

### OAuth `auth_error`

Проверьте client ID/secret и состояние приложения. Исправьте credentials и переустановите
приложение, чтобы получить новую пару tokens и вернуть портал в `active`. Никогда не просите
пользователя прислать token.

### Неизвестный исход REST-вызова

Для `rest_request_outcome_unknown` найдите задание по `job_id`, определите метод и проверьте
целевую сущность в Bitrix24. Не повторяйте изменяющий метод до проверки побочного эффекта.
Зафиксируйте принятое бизнес-решение вручную.

### Утерян encryption key

Остановите API/worker, чтобы не множить terminal errors. Восстановите key из отдельного хранилища.
Если ни текущего, ни подходящего previous key нет, ciphertext не расшифровывается: потребуется
переустановка порталов/получение новых tokens, а ожидающие события могут быть невосстановимы.

## 10. Docker Compose для разработки

Compose публикует API на 8010, поднимает PostgreSQL и worker. Credentials и volume предназначены
только для локальной разработки:

```bash
cp .env.example .env
# заполните ENCRYPTION_KEY
docker compose up -d postgres
docker compose run --rm api uv run alembic upgrade head
docker compose up -d api worker
docker compose logs --follow api worker
```

Не используйте Compose defaults в production и не публикуйте PostgreSQL наружу без необходимости.

## 11. Production checklist

- [ ] DNS, публичный HTTPS и сертификат проверены.
- [ ] `.env` имеет mode 600; defaults/`change_me` заменены.
- [ ] PostgreSQL user/database, backup, restore test и мониторинг готовы.
- [ ] encryption key сохранён отдельно от дампа.
- [ ] `APP_ENV=production`, `APP_BASE_URL` совпадает с публичным адресом.
- [ ] миграции применены; API и worker активны.
- [ ] `/health` и `/ready` отвечают 200 через внешний HTTPS.
- [ ] локальное приложение имеет необходимые scopes и установлено.
- [ ] три робота появились; тесты pause, REST read, field completion и timeout пройдены.
- [ ] перезапуск worker во время паузы успешно проверен.
- [ ] алерты, доступ к логам и политика хранения данных согласованы.
