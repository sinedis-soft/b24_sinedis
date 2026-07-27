# Production deployment checklist

These templates are a starting point and have not been applied to a production host.

1. Point DNS for `bitrix24.sinedis.pl` at the Ubuntu server.
2. Run `deployment/scripts/install-ubuntu.sh` as root to create `sinedis-bitrix` and the application directory.
3. Place a reviewed checkout in `/opt/bitrix24-automation`; do not use destructive Git reset commands.
4. Create `/opt/bitrix24-automation/.env` from `.env.example`, restrict it to the service user, and never commit it.
5. Generate `ENCRYPTION_KEY` with `make generate-encryption-key`; store its backup separately from PostgreSQL.
6. Provision PostgreSQL, a dedicated database/user, backups, and a restricted `DATABASE_URL`.
7. Run `deployment/scripts/deploy.sh` to create the virtual environment and install dependencies.
8. Confirm `deployment/scripts/migrate.sh` successfully applies `alembic upgrade head`.
9. Review and install both files from `deployment/systemd/`, then enable the API and worker services.
10. Copy only this site's Nginx template into `sites-available` and enable its own symlink.
11. Run `nginx -t` before reloading Nginx.
12. Obtain TLS with Certbot; the referenced certificate paths do not exist before issuance.
13. Run `curl https://bitrix24.sinedis.pl/health`.
14. Run `curl https://bitrix24.sinedis.pl/ready` and investigate database failures.
15. Create an API-only local application in Bitrix24 with the documented install URL and required `bizproc` permission.
16. Put its `BITRIX_CLIENT_ID` and `BITRIX_CLIENT_SECRET` in `.env` without printing them.
17. Restart services and reinstall the local application so robot/event registration runs.
18. Confirm `sinedis.short_pause.v1` appears with the expected localized properties.
19. Execute a short-pause test and verify a job progresses from pending to completed.
20. During another pause, restart the worker and verify recovery safely resumes processing.

After deployment, also confirm the business process continues, inspect only redacted logs, test backups and key recovery, and monitor `failed`, `expired`, and stale jobs. External event delivery is not exactly-once: a transport timeout can require manual review.
