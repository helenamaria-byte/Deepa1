# Runbook

For the IT team. Day-to-day use of the dashboard needs none of this.

## Where things run

| Piece | Where |
|---|---|
| Dashboard + cron endpoints | Vercel project `freshroots-demand-planning` → https://freshroots-demand-planning-omega.vercel.app |
| Scheduled plan | Vercel Cron, `30 0 * * 1,4` (Mon/Thu 06:00 IST, within the hour on Hobby) → `GET /api/cron/run` |
| Weekly summary | Vercel Cron, `30 0 * * 5` (Fri) → `GET /api/cron/weekly` |
| Database | Neon project `freshroots-demand-planning` (Postgres 16) |
| Email | Resend |
| Code | https://github.com/helenamaria-byte/Deepa1 (CI on every push) |

## Environment variables (Vercel → Project → Settings → Environment Variables)

`DATABASE_URL`, `RESEND_API_KEY`, `EMAIL_PROVIDER`, `EMAIL_FROM`, `EMAIL_TO_RAGHAV`,
`EMAIL_TO_PROCUREMENT`, `EMAIL_TO_DEEPA`, `APP_URL`, `SECRET_KEY`, `CRON_SECRET`,
`ANTHROPIC_MODEL`, optionally `ANTHROPIC_API_KEY`, `DATA_SOURCE_URL`, `DATA_SOURCE_CREDENTIALS`,
`ZOHO_ENABLED`. `.env.example` describes each one. Changes apply on the next deployment.

## Common tasks

| Task | How |
|---|---|
| Run a plan now | Dashboard → Runs → **Run the plan now**, or `python -m src.jobs.run_plan` |
| A run failed | Runs page → **Error details** shows the stack trace. Raghav and Deepa get a failure email. The previous good plan stays on the dashboard. Fix the cause, then run again. |
| "Sales data ends at ..." warning | The SAP export for the last week has not arrived. Check the export drop, then run again. |
| Everything is in Exceptions with STALE_INVENTORY | The inventory export is more than 10 days old. Same as above. |
| Change a threshold | Dashboard → Settings. Saved as a new version; used from the next run. |
| Connect real SAP exports | Set `DATA_SOURCE_URL` (`https://...`, `s3://bucket/prefix`, `sftp://user@host/folder`, or `file:///path`) and `DATA_SOURCE_CREDENTIALS`. The folder must contain the 5 files with the same names and columns. For S3/SFTP add `boto3` / `paramiko` to `requirements.txt`. |
| Turn on Claude explanations | Set `ANTHROPIC_API_KEY`. Replies are validated; any failure falls back to templates. |
| Send email from a company address | Verify the domain in Resend, then set `EMAIL_FROM` and the `EMAIL_TO_*` addresses. |
| Add or reset a login | `DASHBOARD_USERS=email:password,...` then `python -m src.jobs.bootstrap` with `force=True` from a Python shell, or create the user with `Store().create_user(...)`. |
| Use GitHub Actions as the scheduler instead | Add repository secrets `DATABASE_URL`, `RESEND_API_KEY` and variables for the emails, then set variable `USE_GITHUB_SCHEDULER=true` and remove the `crons` block from `vercel.json`. |
| Self-host without Vercel | `docker compose up -d` runs the dashboard on port 8000 and the same schedule via cron (TZ Asia/Kolkata). |
| Check for leaked secrets | `python scripts/secret_scan.py` (also runs in CI). |

## Retries and failure handling

Connectors, database writes and email retry 3 times (2 s, then 4 s backoff). After the third
failure the run is stored as `FAILED` with the stack trace (secrets removed), and a failure notice
goes to Raghav and Deepa. If email itself fails, the message is logged as FAILED and saved to the
outbox folder.

## Database tables

`runs`, `recommendations`, `overrides` (reason required by a database constraint), `rules_versions`,
`users` (password hashes only), `emails`, `ai_cache`, `schema_info`. Migrations:
`python -m src.store.migrations` (also runs automatically at start-up).
