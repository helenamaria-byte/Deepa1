# Decisions

Choices made during the build, and why.

| # | Decision | Why |
|---|---|---|
| 1 | **Vercel hosts the dashboard instead of Render.** | The intake supplied a Vercel token, not a Render key. |
| 2 | **Dashboard is FastAPI with server-rendered HTML, not Streamlit.** | Streamlit needs a long-running server with websockets, which Vercel's short-lived functions do not provide. FastAPI pages run as one Vercel function. It also allowed a deliberately simple UI: plain pages, big buttons, plain-English labels, almost no JavaScript. |
| 3 | **Scheduler is Vercel Cron** (`vercel.json`: `30 0 * * 1,4` run, `30 0 * * 5` weekly summary). | No GitHub token was supplied, so GitHub Actions secrets and schedules cannot be created through the API. Vercel Cron calls `/api/cron/run`, which is protected by `CRON_SECRET`. On the Hobby plan Vercel starts the job somewhere in the scheduled hour (05:30-06:29 IST). `.github/workflows/run_plan.yml` holds the same cron as a backup that runs only when the repository variable `USE_GITHUB_SCHEDULER` is `true`, so the plan never runs twice. |
| 4 | **Database is Neon Postgres**, created through the Neon API (project `freshroots-demand-planning`, region ap-southeast-1, pooled connection). | The intake supplied a Neon API key. The same SQLAlchemy code runs on SQLite for tests and local work. Serverless connections use `NullPool`; Neon's pooler reuses connections. |
| 5 | **Emails go through Resend from `onboarding@resend.dev`.** All three recipient roles currently point at the Resend account owner's address. | No `EMAIL_FROM` or recipient addresses were supplied, and the Resend account has no verified domain. Resend's test sender only delivers to the account owner, so that is the only address that can receive mail today. A test send succeeded. Changing `EMAIL_TO_*` and `EMAIL_FROM` in Vercel settings is all that is needed later. |
| 6 | **Dashboard logins were generated** (raghav, procurement, deepa); only PBKDF2 hashes are stored. Passwords were delivered once by email and the body was not stored. | `DASHBOARD_USERS` was blank. |
| 7 | **Explanations use templates.** | No Anthropic key was supplied. The Claude path (model `claude-haiku-5-5`, number validation, cache) is built and tested with a stub client, and switches on when `ANTHROPIC_API_KEY` is set. |
| 8 | **The public repo contains the 5 sample CSVs.** | The brief calls them sample data. Real SAP exports should come through `DATA_SOURCE_URL`, not the repo. |
| 9 | Order quantities are rounded **up** to whole units. | Never under-order because of rounding. |
| 10 | Stock in several locations is **summed per SKU**; the **oldest** As_Of_Date decides staleness. | The real data will have more than one warehouse. |
| 11 | If the SAP export is behind (no row for the last complete week), the run uses the newest week available and shows a warning. Gaps inside a SKU's history raise MISSING_WEEKS. | A late export should be visible, not silently treated as zero sales. |
| 12 | Every decision needs a reason, including approvals. The Recommendations page offers **Approve all** with one shared reason. | The brief requires a mandatory reason. Approve-all keeps review quick. |
| 13 | Approving is not allowed on an exception (there is nothing to approve); the reviewer enters a quantity instead. | Exceptions never get an automatic recommendation. |
| 14 | Decisions are **soft-deleted** (marked removed, kept in the log). | Keeps the audit trail. |
| 15 | Reruns on the same date create run #2, #3... and never overwrite. | Idempotency requirement. |
| 16 | `windows.demand_baseline` (default `long` = avg12) was added to the rules file. | Needed for the backtest sensitivity table (avg4 vs avg12). The production rule is unchanged. |
| 17 | `docs/judgment_log.md` is regenerated on each local run. On Vercel the filesystem is read-only, so the same file is served live at `/judgment-log.md`. | Requirement to document overrides over time. |
| 18 | Retries: 3 attempts with 2 s then 4 s backoff for connectors, database writes and email. Validation errors are not retried (they will not fix themselves). | Requirement, kept short enough for a 60 s serverless limit. |
| 19 | No ML forecasting. | Sixteen weeks of history cannot support it. Listed as a future option. |
