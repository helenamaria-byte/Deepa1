"""Review dashboard (FastAPI + server-rendered HTML; runs locally or on Vercel).

Pages: Home, Recommendations, Exceptions, Decisions (override log + Excel upload),
Runs, Settings. Every page needs a login. Cron endpoints are protected by
CRON_SECRET (Vercel sends it automatically as a bearer token)."""
from __future__ import annotations

import hmac
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from src.ingest.validate import reason_text
from src.jobs.run_plan import STATUS_ORDER, run_plan, today_ist
from src.jobs.weekly_summary import send_weekly_summary
from src.outputs.judgment_log import judgment_log_markdown
from src.outputs.workbook import build_workbook, read_override_sheet
from src.rules_config import RulesConfig, rules_from_yaml
from src.settings import settings
from src.store.repo import Store

SESSION_COOKIE = "fr_session"
SESSION_DAYS = 14

app = FastAPI(title="Freshroots demand planning", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

STATUS_LABELS = {
    "STOCKOUT_RISK": ("Reorder now", "red"),
    "HIGH_DEMAND": ("Demand rising", "blue"),
    "OVERSTOCK_RISK": ("Pause order", "amber"),
    "EXCEPTION": ("Needs judgment", "purple"),
    "OK": ("OK", "green"),
}
templates.env.globals.update(STATUS_LABELS=STATUS_LABELS, STATUS_ORDER=STATUS_ORDER)


@lru_cache(maxsize=1)
def get_store() -> Store:
    store = Store()
    store.migrate()
    return store


# ---- auth ----------------------------------------------------------------
def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="session")


def current_user(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        return _signer().loads(token, max_age=SESSION_DAYS * 86400)
    except BadSignature:
        return None


class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
async def _to_login(request: Request, _exc):
    return RedirectResponse(f"/login?{urlencode({'next': request.url.path})}", status_code=303)


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise LoginRequired()
    return user


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("user", current_user(request))
    ctx.setdefault("msg", request.query_params.get("msg", ""))
    ctx.setdefault("err", request.query_params.get("err", ""))
    return templates.TemplateResponse(request, name, ctx)


def back(path: str, **params) -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return page(request, "login.html", next=next)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    user = get_store().authenticate(username, password)
    if not user:
        return page(request, "login.html", next=next, err="That username and password don't match.")
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(SESSION_COOKIE, _signer().dumps(user), max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=settings.app_url.startswith("https"))
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---- helpers ---------------------------------------------------------------
def _run_or_latest(run_id: int | None) -> dict | None:
    store = get_store()
    return store.get_run(run_id) if run_id else store.latest_run()


def _with_decisions(run: dict) -> list[dict]:
    store = get_store()
    recs = store.recommendations(run["id"])
    decisions = store.latest_decisions(run["id"])
    for r in recs:
        r["decision"] = decisions.get(r["sku_id"])
    recs.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), r["sku_id"]))
    return recs


# ---- pages -----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    require_user(request)
    store = get_store()
    run = store.latest_run()
    last_any = store.latest_run(status=None)
    recs = _with_decisions(run) if run else []
    todo = [r for r in recs if r["status"] != "OK"]
    decided = sum(1 for r in todo if r["decision"])
    _, rules = store.current_rules()
    return page(request, "home.html", run=run, last_any=last_any, todo=todo, decided=decided,
                uplift_pct=round((rules.festival.uplift - 1) * 100))


@app.get("/review")
def review_shortcut():
    return RedirectResponse("/recommendations?show=todo", status_code=303)


@app.get("/recommendations", response_class=HTMLResponse)
def recommendations(request: Request, run: int | None = None, status: str = "", distributor: str = "",
                    category: str = "", show: str = ""):
    require_user(request)
    r = _run_or_latest(run)
    recs = [x for x in _with_decisions(r) if x["status"] != "EXCEPTION"] if r else []
    distributors = sorted({x["distributor_name"] for x in recs if x["distributor_name"]})
    categories = sorted({x["category"] for x in recs if x["category"]})
    rows = [x for x in recs
            if (not status or x["status"] == status)
            and (not distributor or x["distributor_name"] == distributor)
            and (not category or x["category"] == category)
            and (show != "todo" or (x["status"] != "OK" and not x["decision"]))]
    pending = [x for x in recs if x["status"] != "OK" and not x["decision"]]
    return page(request, "recommendations.html", run=r, rows=rows, distributors=distributors,
                categories=categories, f={"status": status, "distributor": distributor, "category": category,
                                          "show": show}, pending=pending)


@app.post("/recommendations/approve-all")
def approve_all(request: Request, run_id: int = Form(...), reason: str = Form("")):
    user = require_user(request)
    if not reason.strip():
        return back("/recommendations", show="todo", err="Please write a short reason before approving.")
    store = get_store()
    run = store.get_run(run_id)
    n = 0
    for r in _with_decisions(run):
        if r["status"] not in ("OK", "EXCEPTION") and not r["decision"]:
            store.add_override(run_id, r["sku_id"], "APPROVED", None, reason, user)
            n += 1
    return back("/recommendations", msg=f"Approved {n} suggestions.")


@app.get("/exceptions", response_class=HTMLResponse)
def exceptions(request: Request, run: int | None = None):
    require_user(request)
    r = _run_or_latest(run)
    store = get_store()
    _, rules = store.current_rules()
    rows = [x for x in _with_decisions(r) if x["status"] == "EXCEPTION"] if r else []
    for x in rows:
        x["reasons"] = [(c, reason_text(c, rules)) for c in x["exception_codes"]]
    return page(request, "exceptions.html", run=r, rows=rows)


@app.get("/sku/{run_id}/{sku_id}", response_class=HTMLResponse)
def sku_detail(request: Request, run_id: int, sku_id: str):
    require_user(request)
    store = get_store()
    rec = store.recommendation(run_id, sku_id)
    if rec is None:
        return back("/recommendations", err=f"{sku_id} was not found in that run.")
    _, rules = store.current_rules()
    rec["reasons"] = [(c, reason_text(c, rules)) for c in rec["exception_codes"]]
    history = [o for o in store.overrides(run_id) if o["sku_id"] == sku_id]
    return page(request, "sku.html", run=store.get_run(run_id), r=rec, history=history)


@app.post("/sku/{run_id}/{sku_id}")
def decide(request: Request, run_id: int, sku_id: str, decision: str = Form(...),
           final_qty: str = Form(""), reason: str = Form("")):
    user = require_user(request)
    qty = int(final_qty) if final_qty.strip().lstrip("-").isdigit() else None
    try:
        get_store().add_override(run_id, sku_id, decision, qty, reason, user)
    except ValueError as exc:
        return back(f"/sku/{run_id}/{sku_id}", err=str(exc))
    rec = get_store().recommendation(run_id, sku_id)
    target = "/exceptions" if rec["status"] == "EXCEPTION" else "/recommendations"
    return back(target, msg=f"Saved your decision for {sku_id}.")


@app.get("/decisions", response_class=HTMLResponse)
def decisions(request: Request):
    require_user(request)
    return page(request, "decisions.html", rows=get_store().overrides(include_deleted=True)[:300])


@app.post("/decisions/{override_id}/delete")
def delete_decision(request: Request, override_id: int):
    user = require_user(request)
    ok = get_store().delete_override(override_id, user)
    return back("/decisions", msg="Decision removed." if ok else "", err="" if ok else "Nothing to remove.")


@app.post("/decisions/upload")
async def upload_decisions(request: Request, file: UploadFile = File(...)):
    user = require_user(request)
    store = get_store()
    run = store.latest_run()
    if run is None:
        return back("/decisions", err="There is no plan to attach these decisions to yet.")
    try:
        rows = read_override_sheet(await file.read())
    except Exception as exc:  # noqa: BLE001 - any unreadable file
        return back("/decisions", err=f"Could not read that file: {exc}")
    saved, problems = 0, []
    for row in rows:
        try:
            store.add_override(run["id"], row["sku_id"], row["decision"], row["final_qty"], row["reason"], user)
            saved += 1
        except ValueError as exc:
            problems.append(f"{row['sku_id']}: {exc}")
    return back("/decisions", msg=f"Imported {saved} decisions.", err="; ".join(problems[:5]))


@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    require_user(request)
    store = get_store()
    return page(request, "runs.html", runs=store.list_runs(50), emails=store.emails(30))


@app.post("/runs/new")
def run_now(request: Request):
    require_user(request)
    run = run_plan(today_ist(), trigger=f"dashboard:{current_user(request)}", store=get_store())
    if run["status"] == "SUCCESS":
        return back("/", msg=f"New plan created (run {run['id']}).")
    return back("/runs", err=f"Run {run['id']} failed. See the details below.")


@app.get("/runs/{run_id}/workbook")
def workbook(request: Request, run_id: int):
    require_user(request)
    store = get_store()
    run = store.get_run(run_id)
    if not run or run["status"] != "SUCCESS":
        return back("/runs", err="That run has no plan to download.")
    version = run["rules_version"] or 0
    rules = next((rules_from_yaml(v.yaml_text) for v in store.rules_history() if v.id == version),
                 store.current_rules()[1])
    data = build_workbook(run, store.recommendations(run_id), store.overrides(run_id), rules, version)
    name = f"freshroots_plan_{run['run_date']}_run{run_id}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/emails/{email_id}", response_class=HTMLResponse)
def view_email(request: Request, email_id: int):
    require_user(request)
    e = get_store().email(email_id)
    if not e:
        return back("/runs", err="Email not found.")
    return HTMLResponse(e["html"] or "<p>This email's content is not stored (it contained logins).</p>")


@app.get("/judgment-log.md", response_class=PlainTextResponse)
def judgment_log(request: Request):
    require_user(request)
    return judgment_log_markdown(get_store())


SETTINGS_FIELDS = [
    # (form name, label, help, getter path)
    ("long_weeks", "Long average (weeks)", "Weeks in the long-run average (avg12)", ("windows", "long_weeks")),
    ("short_weeks", "Recent average (weeks)", "Weeks in the recent average (avg4)", ("windows", "short_weeks")),
    ("festival_enabled", "Festival uplift on", "Raise demand in festival months", ("festival", "enabled")),
    ("uplift", "Festival demand multiplier", "1.25 means +25%", ("festival", "uplift")),
    ("hd_ratio", "High-demand trigger", "Recent average above this x long average", ("high_demand", "ratio")),
    ("hd_mult", "High-demand order size", "Multiple of the standard order", ("high_demand", "reorder_multiplier")),
    ("gen_min", "Minimum stock (weeks)", "Below this: reorder now", ("cover", "general", "min_weeks")),
    ("gen_max", "Maximum stock (weeks)", "Above this: pause next order", ("cover", "general", "max_weeks")),
    ("per_min", "Perishable minimum (weeks)", "For high-perishability SKUs", ("cover", "perishable", "min_weeks")),
    ("per_max", "Perishable maximum (weeks)", "For high-perishability SKUs", ("cover", "perishable", "max_weeks")),
    ("min_hist", "Minimum sales history (weeks)", "Less than this: send to review", ("exceptions", "min_history_weeks")),
    ("max_dev", "Maximum swing", "0.40 means 40%: bigger swings go to review", ("exceptions", "max_deviation")),
    ("stale_days", "Stock data too old after (days)", "Older stock figures go to review",
     ("exceptions", "stale_inventory_days")),
]


def _get(data: dict, path: tuple):
    for p in path:
        data = data[p]
    return data


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    require_user(request)
    store = get_store()
    version, rules = store.current_rules()
    data = rules.model_dump()
    fields = [(n, label, help_, _get(data, path)) for n, label, help_, path in SETTINGS_FIELDS]
    return page(request, "settings.html", version=version, fields=fields, history=store.rules_history()[:20],
                rules_yaml=rules.to_yaml())


@app.post("/settings")
async def save_settings(request: Request):
    user = require_user(request)
    form = await request.form()
    store = get_store()
    _, rules = store.current_rules()
    data = rules.model_dump()
    try:
        for name, _, _, path in SETTINGS_FIELDS:
            target = data
            for p in path[:-1]:
                target = target[p]
            old = target[path[-1]]
            if isinstance(old, bool):
                target[path[-1]] = form.get(name) == "on"
            else:
                raw = str(form.get(name, "")).strip()
                target[path[-1]] = type(old)(float(raw)) if isinstance(old, int) else float(raw)
        new_rules = RulesConfig.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - show any validation problem to the user
        return back("/settings", err=f"Not saved: {exc}".splitlines()[0])
    note = str(form.get("note", "")).strip()
    version = store.save_rules(new_rules, user, note)
    return back("/settings", msg=f"Saved as version {version}. It applies from the next run.")


# ---- machine endpoints -------------------------------------------------------
def _cron_ok(request: Request) -> bool:
    secret = settings.cron_secret
    given = request.headers.get("authorization", "")
    return bool(secret) and hmac.compare_digest(given, f"Bearer {secret}")


@app.get("/api/cron/run")
def cron_run(request: Request):
    if not _cron_ok(request):
        return PlainTextResponse("unauthorized", status_code=401)
    run = run_plan(today_ist(), trigger="schedule", store=get_store())
    return {"run_id": run["id"], "status": run["status"], "counts": run.get("counts")}


@app.get("/api/cron/weekly")
def cron_weekly(request: Request):
    if not _cron_ok(request):
        return PlainTextResponse("unauthorized", status_code=401)
    return {"weekly_summary": send_weekly_summary(get_store())}


@app.get("/health")
def health():
    run = get_store().latest_run(status=None)
    return {"ok": True, "last_run": run and {"id": run["id"], "status": run["status"],
                                             "run_date": str(run["run_date"])},
            "today_ist": str(today_ist()), "date": str(date.today())}
