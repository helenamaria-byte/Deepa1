# Blockers and fallbacks

Intake items that were blank, and what the system does instead. Each one is a configuration
change away from the full version: set the value in Vercel's environment variables and redeploy.

| Intake item | Status | Fallback in use |
|---|---|---|
| `ANTHROPIC_API_KEY` | Blank | Deterministic template explanations. The Claude path is built and tested. |
| `GITHUB_TOKEN` | Supplied after the build (the machine's saved login had no access to `Deepa1`) | Resolved: code pushed to https://github.com/helenamaria-byte/Deepa1 and CI passes on GitHub. The token was used only for the push and not stored. GitHub Actions secrets were not created, so Vercel Cron stays the scheduler and the backup workflow stays off. |
| `RENDER_API_KEY` | Not used | Vercel token supplied instead (see DECISIONS #1). |
| `EMAIL_FROM` | Blank | `onboarding@resend.dev` (Resend's test sender). |
| `EMAIL_TO_RAGHAV`, `EMAIL_TO_PROCUREMENT`, `EMAIL_TO_DEEPA` | Blank | All three set to the Resend account owner's address, the only address the test sender can reach. |
| `DATA_SOURCE_URL`, `DATA_SOURCE_CREDENTIALS` | Blank | The 5 CSVs in `data/`, read through `CsvFolderConnector`. **The sample inventory is dated 2026-10-19; from 2026-10-30 every SKU will be flagged STALE_INVENTORY, and sales will show as "behind" from 2026-10-26, until real SAP exports are connected.** |
| `ZOHO_*` | Supplied after the build | Resolved: connected to **Zoho Inventory** (organization `Freshroots`, India data centre). Open purchase orders are read before every plan and counted in weeks of stock. The organization holds demo data loaded by `python -m src.jobs.zoho_seed` (4 vendors, 15 items, 3 issued POs and 1 draft). |
| `DASHBOARD_USERS` | Blank | Logins generated and emailed (DECISIONS #6). |
