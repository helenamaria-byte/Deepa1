# Freshroots Demand Planning System: Build Rubric for Claude Code

You are building this system end to end. This file is your contract. Read all of it before doing anything.

---

## 0. The prime directive (read twice)

**The user will do NOTHING manually. Not one step.**

- You ask the user for inputs **exactly once**, using the intake message in Section 1. That is the only time you may ask them for anything.
- After the intake reply, you do **everything else yourself**: create the repo, install dependencies, write code, write tests, run tests, fix failures, provision the database, set secrets, deploy, schedule the job, send a test email, run the backtest, write the docs, and verify the deployed system.
- You never tell the user to run a command, click a button, install a package, edit a file, create an account, copy a value, open a terminal, set an environment variable, or "just check that...". You do it.
- If something fails, **you debug it and fix it**. Try at least 5 distinct approaches before treating anything as blocked. Reading logs, searching docs, changing libraries and rewriting the approach all count as your job.
- If a credential from the intake is missing or invalid, **do not ask again**. Use the fallback defined for it in Section 1, log the gap in `BLOCKERS.md`, and keep building.
- The only message the user sees after intake is the final report (Section 9). No progress questions, no "should I proceed?", no "which do you prefer?". Decide, using the defaults in this file, and record the decision in `DECISIONS.md`.

**Banned phrases in anything you say to the user:** "please run", "please install", "please click", "please set", "you will need to", "you'll need to", "can you confirm", "which would you prefer", "let me know if", "once you have done".

---

## 1. The single intake (the only time you ask)

Send this message **once**, verbatim, as your first and only question. Then wait for the reply and do not ask anything further.

> **Intake: reply with this block filled in. Leave a line blank to use the fallback.**
>
> ```
> ANTHROPIC_API_KEY=
> ANTHROPIC_MODEL=                      # blank = claude-haiku-5-5
>
> GITHUB_TOKEN=                         # scopes: repo, workflow. Blank = local git only
> GITHUB_OWNER=                         # user or org for the new repo
>
> DATABASE_URL=                         # Postgres URL (Neon etc.)
> NEON_API_KEY=                         # alternative: I will create the database myself
>                                       # both blank = SQLite file
>
> EMAIL_PROVIDER=                       # resend | smtp
> RESEND_API_KEY=
> SMTP_HOST= / SMTP_PORT= / SMTP_USER= / SMTP_PASSWORD=
> EMAIL_FROM=                           # must be a sender the provider already allows
> EMAIL_TO_RAGHAV=
> EMAIL_TO_PROCUREMENT=
> EMAIL_TO_DEEPA=
>
> RENDER_API_KEY=                       # to host the review dashboard. Blank = Docker only
>
> DATA_SOURCE_URL=                      # where the twice-weekly SAP CSV exports land
> DATA_SOURCE_CREDENTIALS=              # (S3, SFTP, Drive, HTTPS...). Blank = use the 5 CSVs in /data
>
> ZOHO_CLIENT_ID= / ZOHO_CLIENT_SECRET= / ZOHO_REFRESH_TOKEN= / ZOHO_ORG_ID=
>                                       # blank = Zoho stays a stub (phase 2)
>
> DASHBOARD_USERS=                      # email:password pairs, comma separated. Blank = generate and
>                                       # email them to the three recipients above
> ```

### Fallbacks (use silently, never ask again)

| Missing | You do this instead |
|---|---|
| `ANTHROPIC_API_KEY` | Deterministic template explanations. The system must work fully without any LLM. |
| `GITHUB_TOKEN` | Local git repo; scheduling via a cron entry in `docker-compose.yml`. |
| `DATABASE_URL` and `NEON_API_KEY` | SQLite file in `./var/`. Same schema, same code path via SQLAlchemy. |
| Email credentials | Write emails to `./var/outbox/*.eml` and `*.html`; the code path is identical so enabling credentials later is a config change only. |
| `RENDER_API_KEY` | Ship `Dockerfile` + `docker-compose.yml` and run the dashboard locally in the container; verify it with HTTP checks. |
| `DATA_SOURCE_*` | Ingest from `./data/` (the 5 provided CSVs) through the same `Connector` interface. |
| Zoho credentials | `ZohoConnector` stub returning an empty open-PO table, behind a feature flag. |
| `DASHBOARD_USERS` | Generate credentials, store only hashes, deliver them by email (or outbox). |

All secrets go in `.env` (git-ignored) and in the platform's secret store. **Never print, log or commit a secret.**

---

## 2. What you are building

A twice-weekly (Mon and Thu, 06:00 IST) demand-planning recommendation engine for Freshroots, replacing Raghav Kulkarni's manual monthly process. It is a **rules engine with a human review loop, not a forecasting model.**

Flow, matching the components map:

1. **Trigger:** scheduled run (GitHub Actions cron `30 0 * * 1,4` = 06:00 IST, or container cron).
2. **Input:** SAP CSV exports (weekly sales, current inventory, SKU master, distributor master, calendar).
3. **Context:** validated SKU-week table joined with perishability, standard reorder qty and festival flag.
4. **Processing:** Raghav's 5 rules produce a flag and a recommended order quantity per SKU. Unclear SKUs go to an exception queue.
5. **AI:** Claude writes a one- or two-sentence reason per flag. It never changes a flag or a quantity.
6. **Data:** every run, flag, quantity, explanation and override is stored. Retries 3 times; any failure is flagged.
7. **Output:** review dashboard plus Excel workbook; email alert to Raghav and procurement; Deepa gets the weekly metrics summary.

Input files (already in `/mnt/user-data/uploads` or `./data`; copy to `./data`):

| File | Columns |
|---|---|
| `weekly_sales.csv` | SKU_ID, Week (e.g. 2026-W42), Units_Sold |
| `current_inventory.csv` | SKU_ID, Location, Units_On_Hand, As_Of_Date |
| `sku_master.csv` | SKU_ID, SKU_Name, Category, Perishability (High/Low), Standard_Reorder_Qty, Unit_of_Measure, Distributor_ID |
| `distributor_master.csv` | Distributor_ID, Distributor_Name, Region, Status |
| `calendar.csv` | Year, Month, Year_Month, Is_Festival_Month |

The data is a sample (15 SKUs, 4 distributors, 16 weeks, one warehouse). The system must be built for the real scale (about 400 SKUs, 1,200 distributors, 3 years) with nothing hard-coded to the sample.

---

## 3. Business rules (implement exactly; thresholds live in `config/rules.yaml`)

**Definitions (defaults; decided for you, so do not ask):**

- `avg12` = mean weekly units over the last 12 complete weeks. `avg4` = mean over the last 4 complete weeks.
- A week is complete if its Sunday is before the run date.
- Festival run: the run date's month has `Is_Festival_Month = TRUE` (October, November, March).
- `weekly_demand` = `avg12` × 1.25 in a festival month, otherwise `avg12`. (The uplift applies before every threshold check.)
- `cover_weeks` = `Units_On_Hand` / `weekly_demand`.
- Perishability comes from `sku_master.Perishability`.

**Rules:**

1. **High-demand:** `avg4 > 1.20 × avg12` → flag HIGH_DEMAND, recommend reorder at **1.5 × Standard_Reorder_Qty**. (Uplift cancels out in this ratio, so it is not applied here. Document this.)
2. **Stockout risk:** `cover_weeks < 6` → flag STOCKOUT_RISK, recommend immediate reorder at **Standard_Reorder_Qty**.
3. **Overstock risk:** `cover_weeks > 14` → flag OVERSTOCK_RISK, recommend **pausing** the next order (quantity 0).
4. **Festival uplift:** +25% on demand in festival months, applied as above.
5. **High-perishability SKUs:** replace rules 2 and 3 with min **3** and max **4** weeks of cover (`cover < 3` → STOCKOUT_RISK, `cover > 4` → OVERSTOCK_RISK). Perishable windows override the general windows.

**Precedence when flags combine:** STOCKOUT_RISK and HIGH_DEMAND can coexist (quantity = 1.5 × standard). OVERSTOCK_RISK with HIGH_DEMAND coexisting is a conflict and goes to the exception queue. A SKU with none of the flags is `OK` (no action).

**Exception queue (the 15-20% needing judgment). A SKU goes here, with no auto-recommendation, if any is true:**

- fewer than 12 weeks of sales history (new launch);
- `avg4` deviates from `avg12` by more than 40% in either direction;
- any zero-sales week in the last 12;
- missing or stale (older than 10 days) inventory;
- its distributor is not `Active`;
- conflicting flags (see precedence);
- orphaned SKU (no master record) or duplicate rows.

Thresholds are configurable. Each exception carries a reason code.

**Golden test, from the provided data** (run date 2026-10-22, October is a festival month). The engine must reproduce this, verified by an independent pandas calculation in the tests:

| Result | SKUs |
|---|---|
| STOCKOUT_RISK | SKU001 (3.35 wks, also HIGH_DEMAND so 750 units), SKU003 (4.36), SKU004 (1.80, perishable), SKU011 (1.80, perishable), SKU013 (3.17) |
| OVERSTOCK_RISK (pause) | SKU006 (22.2 wks), SKU007 (4.08 wks vs the perishable max of 4) |
| OK | SKU002, 005, 008, 009, 010, 012, 014, 015 |

With the uplift disabled in a non-festival test month, SKU002 (14.33 wks) must flip to OVERSTOCK_RISK. This proves the uplift is wired in.

---

## 4. Architecture and stack (decided; do not ask)

- **Language:** Python 3.11+. pandas, pydantic, SQLAlchemy, pytest, openpyxl, Streamlit, anthropic SDK, PyYAML.
- **Repo layout:**

```
freshroots-demand-planning/
  CLAUDE.md                    # this file (keep it)
  README.md  DECISIONS.md  BLOCKERS.md  RUNBOOK.md
  config/rules.yaml            # every threshold, festival months, exception limits
  data/                        # the 5 CSVs
  src/connectors/              # base.py, csv_folder.py, s3.py, sftp.py, https.py, zoho.py
  src/ingest/                  # loaders + validation (pandera or pydantic)
  src/engine/                  # features.py, rules.py, exceptions.py, sizing.py
  src/ai/explain.py            # Claude explanations + deterministic fallback
  src/store/                   # models.py, repo.py, migrations
  src/outputs/                 # workbook.py, email.py, templates/
  src/app/                     # Streamlit dashboard
  src/jobs/run_plan.py         # single entry point, with retry and failure flagging
  tests/                       # unit, golden, backtest, e2e
  .github/workflows/           # run_plan.yml (cron), ci.yml
  Dockerfile  docker-compose.yml  .env.example
```

- **Connector interface:** every source implements `fetch() -> dict[str, DataFrame]`. Switching from CSV to real SAP exports is config only.
- **Idempotency:** a run is keyed by run date; rerunning replaces nothing, it creates a new versioned run.
- **Retries:** 3 attempts with backoff on connectors, email and database. After the third failure, record `FAILED` with the stack trace, and email Raghav and Deepa a failure notice (or write it to the outbox).
- **AI layer rules:** the prompt receives only the computed facts (SKU, cover, thresholds, flags, festival status) and returns a 1-2 sentence reason. Validate that the reply contains no number that is absent from the input facts; if it does, or the call fails, use the template text. The model must never alter a flag or a quantity. Cache by input hash.
- **Dashboard (Streamlit, authenticated):** Home (this run's summary and counts), Recommendations (filter by flag, distributor, category), Exceptions (with reason codes), Override (approve or override with a **mandatory reason**; stored with the user and timestamp), Runs (history and health), Settings (view rules; edits are versioned in the database and take effect on the next run). Excel upload to import the Override sheet is supported.
- **Excel workbook** per run: Recommendations, Exceptions, Override log, Rules (documentation), Run info. Attached to the alert email.
- **Emails:** (1) "N SKUs need review" to Raghav and procurement, with the workbook attached and the dashboard link; (2) weekly Friday summary to Deepa; (3) failure notices.
- **Documentation by override:** the override log is exported to `docs/judgment_log.md` automatically on each run, so Raghav's undocumented logic gets written down over time.

---

## 5. Autonomy checklist (how you work)

Do these yourself, in this order, without asking:

1. Read this file. Send the intake (Section 1). Receive the reply.
2. Write `.env`, `.env.example` and the secret-store entries from the reply.
3. Create the repo (GitHub API if the token is present). Push regularly.
4. Provision the database (Neon API if the key is present). Run the migrations.
5. Build the connectors, ingest, engine, AI layer, store, outputs, dashboard and job runner.
6. Write the tests first for the rules (golden table), then the code. Run the whole suite until it is green.
7. Run the **backtest** (Section 6) and save the report.
8. Deploy the dashboard (Render API if the key is present; Docker otherwise). Create the GitHub Actions secrets and schedule via the API.
9. Trigger one real run on the provided data. Send a real test email (or outbox file). Verify the dashboard over HTTP: log in, load each page, submit a test override and delete it.
10. Run the final rubric (Section 7), fix anything under threshold, run it again.
11. Write the final report (Section 9).

You may read the web and install any package you need. If a tool is missing, install it. If a command fails, fix it. If an API errors, read the docs and adapt.

---

## 6. Backtest (required)

The data has only 16 weeks and one inventory snapshot, so a full historical replay is limited. You must still:

- Replay the rules weekly across W39-W42 using sales available at each point, and report the flags that would have fired and how they changed over time. The SKU001 jump from about 186 to about 260 units a week at W39 is the key case to show.
- State plainly what cannot be validated (festival uplift accuracy, stock history), in `docs/backtest.md`. Do not invent results.
- Produce a sensitivity table: flag counts under avg4 vs avg12 baselines and uplift on vs off.

---

## 7. Acceptance rubric (self-grade before you finish)

Score yourself with evidence (test names, file paths, command output). **Every MUST item has to pass. Total must be 90 or above out of 100.** If not, fix and rerun. Do not hand over a failing build.

| # | Criterion | Pts | MUST |
|---|---|---|---|
| 1 | Intake sent once; no other questions asked of the user | 5 | Y |
| 2 | Zero manual steps requested of the user anywhere (transcript and docs checked for banned phrases) | 10 | Y |
| 3 | All 5 rules implemented exactly; config-driven; no magic numbers in code | 10 | Y |
| 4 | Golden test passes and matches an independent pandas calculation; uplift on/off flip test passes | 10 | Y |
| 5 | Exception queue works, each reason code covered by a test; exceptions never get auto-recommendations | 8 | Y |
| 6 | Perishable windows override general rules; conflicting-flag handling tested | 4 | Y |
| 7 | Data validation catches missing weeks, negative stock, unmapped SKUs, duplicates, stale inventory | 6 | Y |
| 8 | AI explanations: numbers validated against inputs; fallback works with the API key removed; flags never altered by AI | 6 | Y |
| 9 | Store persists runs, flags, explanations, overrides with a mandatory reason, user and timestamp | 6 | Y |
| 10 | Dashboard deployed (or running in Docker), authenticated, all pages load, override round-trip tested over HTTP | 8 | Y |
| 11 | Excel workbook generated with the 5 sheets and opens without errors | 4 | N |
| 12 | Emails sent in a real test (or outbox files rendered) for alert, weekly summary and failure notice | 5 | Y |
| 13 | Scheduler configured and verified (workflow file valid; cron correct for 06:00 IST; one triggered run succeeded) | 5 | Y |
| 14 | Retry (3x) and failure flagging proven by a fault-injection test | 4 | Y |
| 15 | Connector interface proven by swapping CSV for a second connector in a test; Zoho stub behind a flag | 3 | N |
| 16 | Scale test: 400 SKUs × 156 weeks synthetic run completes in under 60 seconds | 3 | N |
| 17 | Backtest and sensitivity report written, with an honest limitations section | 3 | N |
| 18 | Docs complete: README, RUNBOOK, DECISIONS, BLOCKERS, rules documentation; no secrets in the repo (secret scan passes) | 3 | Y |
| 19 | CI green on a clean checkout (fresh clone, install, tests) | 3 | Y |
| | **Total** | **100** | |

---

## 8. Quality rules

- No secrets in code, logs, commits or error messages. Run a secret scan before every push.
- Tests must hit real code paths. No mocking the engine in the golden test.
- Never fabricate numbers, test results or deployment status. If something wasn't verified, say so in the report.
- Do not add ML forecasting. Sixteen weeks of history cannot support it. Mention it as a future option only.
- Keep rule changes versioned; every run records the rules version it used.
- Keep code readable for a 3-person IT team: small modules, docstrings, one entry point.

---

## 9. Final report (the only thing the user reads)

One message, under 300 words, containing:

1. **Status:** rubric score with the table, and every MUST passing.
2. **Live links:** dashboard URL, repo URL, next scheduled run time, where the workbook and emails go.
3. **Logins:** how the dashboard credentials were delivered (never paste passwords in chat).
4. **What ran:** the result of the first real run (counts per flag, number of exceptions).
5. **Fallbacks used:** which intake items were blank and what you did instead (from `BLOCKERS.md`).
6. **Known limits:** from the backtest (e.g. no open POs until Zoho credentials are supplied).

Do not include instructions for the user. There is nothing left for them to do.
