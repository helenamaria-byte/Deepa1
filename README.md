# Freshroots demand planning

A twice-weekly stock-planning recommendation engine that replaces the manual monthly
process. It is a **rules engine with a human review loop**, not a forecasting model.

- **Dashboard:** https://freshroots-demand-planning-omega.vercel.app (no login; changes ask for your name. `AUTH_REQUIRED=true` turns logins on)
- **Schedule:** Monday and Thursday, 06:00 IST (Vercel Cron). Weekly summary for Deepa every Friday.
- **Database:** Neon Postgres (project `freshroots-demand-planning`)
- **Email:** Resend
- **Stock on order:** Zoho Inventory open purchase orders, counted in weeks of stock

## How it works

```
SAP CSV exports ──► Connector ──► Validation ──► Rules engine ──► Explanations ──► Database
 (5 files)          (csv/https/    (reason codes)  (5 rules +       (Claude or        │
                     s3/sftp)                       exception queue)  template text)    ▼
                                                                         Dashboard · Excel · Email
```

1. **Input:** weekly sales, current inventory, SKU master, distributor master, calendar (`data/`).
2. **Validation** types every table and turns per-SKU problems into reason codes. Only a missing
   file or column stops a run.
3. **Rules** (all thresholds in `config/rules.yaml`, editable on the Settings page). Full detail in
   [docs/rules.md](docs/rules.md):
   1. High demand: avg4 > 1.20 × avg12 → reorder 1.5 × standard quantity
   2. Stockout risk: cover < 6 weeks → reorder standard quantity now
   3. Overstock risk: cover > 14 weeks → pause the next order
   4. Festival uplift: weekly demand × 1.25 in festival months
   5. Perishable SKUs: 3 to 4 week window replaces rules 2 and 3
4. **Exception queue:** SKUs with unclear data or signals get no automatic quantity and go to
   the Exceptions page with reason codes.
5. **Explanations:** one or two sentences per SKU. Claude writes them when an API key is set; any
   reply containing a number not in the facts is replaced by template text. The AI never changes a
   flag or quantity.
6. **Outputs:** dashboard, Excel workbook (5 sheets), "N SKUs need review" email with the workbook
   attached, Friday summary for Deepa, failure notices.

## Dashboard pages

| Page | What it is for |
|---|---|
| Today | Raghav's to-do list: decisions made so far and how many are due before the next plan, SKUs grouped by action with Approve / Change / Undo |
| Recommendations | Every SKU with its suggestion, filters, and an Approve-all option |
| Decisions | Every approval/change with reason, user and time; Excel upload |
| Analytics | The only page with charts: weeks of stock for all SKUs, weekly sales for any SKU |
| Run history (/runs, not in the menu) | For the IT team: every run, errors, emails sent, "Run the plan now" |
| Settings | The rules; saving creates a new version used from the next run |

## Repository layout

```
config/rules.yaml        every threshold
data/                    the 5 sample CSVs
src/connectors/          Connector interface + csv_folder, https, s3, sftp; zoho + zoho_api (open POs)
src/ingest/validate.py   typing, validation, reason codes
src/engine/              weeks, features, rules, exceptions, sizing, pipeline
src/ai/explain.py        Claude explanations + validation + template fallback
src/store/               SQLAlchemy models, repository, migrations
src/outputs/             workbook, email, judgment log, email templates
src/app/                 FastAPI dashboard + templates
src/jobs/                run_plan (single entry point), weekly_summary, bootstrap, backtest
api/index.py, vercel.json  Vercel deployment and cron
tests/                   golden, exceptions, validation, AI, store/retry, outputs, e2e, backtest
```

## Commands (for the IT team)

```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # full test suite (uses SQLite, no secrets)
python -m src.jobs.run_plan               # run a plan now (today, India time)
python -m src.jobs.run_plan --date 2026-10-22 --no-email
python -m src.jobs.backtest               # regenerate docs/backtest.md
uvicorn src.app.main:app --reload         # dashboard on http://localhost:8000
python scripts/secret_scan.py             # check for committed secrets
```

See [RUNBOOK.md](RUNBOOK.md) for operations, [DECISIONS.md](DECISIONS.md) for design choices,
[BLOCKERS.md](BLOCKERS.md) for what is not connected yet, and [docs/backtest.md](docs/backtest.md)
for the backtest.
