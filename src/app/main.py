"""Review dashboard (FastAPI + server-rendered HTML; runs locally or on Vercel).

Pages: Home, Recommendations, Exceptions, Decisions (override log + Excel upload),
Runs, Settings. Pages are open by default; anyone making a change gives their
name, which is stored with the decision. AUTH_REQUIRED=true turns logins back
on. Cron endpoints are protected by CRON_SECRET (Vercel sends it automatically
as a bearer token)."""
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

from src.app import charts
from src.ingest.validate import reason_text
from src.app.status import plan_status
from src.engine.sizing import is_material_change
from src.jobs.run_plan import STATUS_ORDER, last_scheduled_run, next_scheduled_run, now_ist, run_plan, today_ist
from src.jobs.weekly_summary import send_weekly_summary
from src.outputs.judgment_log import judgment_log_markdown
from src.outputs.workbook import build_workbook, read_import
from src.rules_config import RulesConfig, rules_from_yaml
from src.settings import settings
from src.store.repo import Store

SESSION_COOKIE = "fr_session"
NAME_COOKIE = "fr_name"
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
templates.env.globals.update(STATUS_LABELS=STATUS_LABELS, STATUS_ORDER=STATUS_ORDER,
                             CHART=charts)


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


def viewer(request: Request) -> None:
    """Gate for viewing pages: open unless AUTH_REQUIRED=true."""
    if settings.auth_required:
        require_user(request)


def display_name(request: Request) -> str:
    """Logged-in username, else the name this browser last entered."""
    return current_user(request) or request.cookies.get(NAME_COOKIE, "")


def actor(request: Request, who: str = "", required: bool = True) -> str:
    """Who is making a change. With logins on, the logged-in user. Without, the
    name typed in the form (remembered in a cookie). Raises ValueError if a
    required name is missing."""
    if settings.auth_required:
        return require_user(request)
    name = " ".join((who or "").split())[:60] or display_name(request)
    if not name and required:
        raise ValueError("Please enter your name so the log shows who made this decision.")
    return name or "anonymous"


def remember(resp, name: str):
    """Remember the name for this browser so it is pre-filled next time."""
    if name and name != "anonymous" and not settings.auth_required:
        resp.set_cookie(NAME_COOKIE, name, max_age=365 * 86400, samesite="lax",
                        secure=settings.app_url.startswith("https"))
    return resp


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("user", display_name(request))
    ctx.setdefault("logged_in", bool(current_user(request)))
    ctx.setdefault("auth_required", settings.auth_required)
    ctx.setdefault("msg", request.query_params.get("msg", ""))
    ctx.setdefault("err", request.query_params.get("err", ""))
    return templates.TemplateResponse(request, name, ctx)


def back(path: str, **params) -> RedirectResponse:
    path, _, fragment = path.partition("#")
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    sep = "&" if "?" in path else "?"
    url = (f"{path}{sep}{query}" if query else path) + (f"#{fragment}" if fragment else "")
    return RedirectResponse(url, status_code=303)


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


def _safe_next(next_url: str, default: str) -> str:
    return next_url if next_url.startswith("/") and not next_url.startswith("//") else default


APPROVE_REASON = "Approved as recommended"
CHANGE_REASON = "Quantity changed (no reason given)"

# Landing-page sections, in the order Raghav should act on them.
ACTION_GROUPS = [
    ("STOCKOUT_RISK", "Reorder now", "red", "Stock will run out soon."),
    ("HIGH_DEMAND", "Demand rising", "blue", "Sales are climbing, so order more than usual."),
    ("OVERSTOCK_RISK", "Pause the next order", "amber", "There is too much stock, so skip the next order."),
    ("EXCEPTION", "Needs your judgment", "purple", "No automatic suggestion. You decide the quantity."),
    ("OK", "No order needed", "green", "Stock is within range. Approve to confirm no order, or change it."),
]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def rule_chips(r: dict, rules: RulesConfig) -> list[str]:
    """The rule(s) behind a SKU's status, in plain words with its own numbers."""
    if r["status"] == "EXCEPTION":
        return [reason_text(c, rules) for c in r["exception_codes"]]
    chips = []
    if "HIGH_DEMAND" in r["flags"] and r["avg12"]:
        chips.append(f"Sales +{round((r['avg4'] / r['avg12'] - 1) * 100)}% vs {rules.windows.long_weeks}-week "
                     f"average (trigger: over +{round((rules.high_demand.ratio - 1) * 100)}%)")
    cover, window = r["cover_weeks"], "perishable " if r["perishable"] else ""
    if "STOCKOUT_RISK" in r["flags"]:
        chips.append(f"{cover} weeks of stock, below the {r['min_cover']:g}-week {window}minimum")
    elif "OVERSTOCK_RISK" in r["flags"]:
        chips.append(f"{cover} weeks of stock, above the {r['max_cover']:g}-week {window}maximum")
    elif cover is not None:
        chips.append(f"{cover} weeks of stock, within the {r['min_cover']:g} to {r['max_cover']:g}-week {window}range")
    if r.get("on_order"):
        chips.append(f"Includes {r['on_order']:,.0f} units on order that arrive before stock runs out")
    excluded = (r.get("calc") or {}).get("on_order_excluded")
    if excluded:
        chips.append(f"{excluded:,.0f} units on order not counted (arrive too late or no delivery date)")
    return chips


def urgency_key(r: dict):
    """Within a group: least stock first (soonest to run out), except Pause (most excess first)."""
    cover = r["cover_weeks"] if r["cover_weeks"] is not None else -1
    return (-cover if r["status"] == "OVERSTOCK_RISK" else cover, r["sku_id"])


def rules_summary(rules: RulesConfig) -> list[tuple[str, str]]:
    pct = lambda x: round((x - 1) * 100)
    g, p = rules.cover.general, rules.cover.perishable
    return [
        ("Demand rising", f"Sales over the last {rules.windows.short_weeks} weeks are more than "
                          f"{pct(rules.high_demand.ratio)}% above the {rules.windows.long_weeks}-week average. "
                          f"Order {rules.high_demand.reorder_multiplier:g}× the standard quantity."),
        ("Reorder now", f"Stock covers less than {g.min_weeks:g} weeks of average weekly sales. "
                        "Order the standard quantity now."),
        ("Pause the next order", f"Stock covers more than {g.max_weeks:g} weeks. Skip the next order."),
        ("Festival months", f"{', '.join(MONTHS[m - 1] for m in rules.festival.fallback_months)}: demand "
                            f"+{pct(rules.festival.uplift)}% before every check"
                            + ("" if rules.festival.enabled else " (switched off)") + "."),
        ("Perishable SKUs", f"Tighter window: minimum {p.min_weeks:g}, maximum {p.max_weeks:g} weeks of stock."),
        ("Stock counted", "Units on hand" + ((" plus units on order in Zoho that arrive before stock on hand runs out"
                                              if rules.open_orders.count_only_if_arrives_before_stockout
                                              else " plus all units on order in Zoho")
                                             if rules.open_orders.include_in_cover else "") + "."),
        ("When rules overlap", "Reorder now + Demand rising: order the larger, high-demand quantity. "
                               "Pause + Demand rising: conflicting signals, so it goes to your judgment."),
        ("Needs your judgment", "New launches, big swings, zero-sales weeks, missing or old stock data, inactive "
                                "distributors, or conflicting signals."),
    ]


# ---- pages -----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Raghav's landing page: what needs a decision today, grouped by action."""
    viewer(request)
    store = get_store()
    run = store.latest_run()
    last_any = store.latest_run(status=None)
    # Show the rules this plan was made with, not whatever was saved since.
    rules = store.rules_for_version(run["rules_version"]) if run else store.current_rules()[1]
    recs = _with_decisions(run) if run else []
    for r in recs:
        r["chips"] = rule_chips(r, rules)
    groups = [{"status": s, "title": t, "color": c, "hint": h,
               "rows": sorted((r for r in recs if r["status"] == s), key=urgency_key)}
              for s, t, c, h in ACTION_GROUPS]
    groups = [g for g in groups if g["rows"]]
    todo = recs  # every SKU needs a decision, including "no order needed"
    progress = store.decision_progress(run["id"]) if run else {"total": 0, "decided": 0, "pending": 0}
    pending_approvable = [r for r in todo if not r["decision"] and r["status"] != "EXCEPTION"]
    now = now_ist()
    status = plan_status(run, recs, rules, now, last_scheduled_run(now)) if run else None
    return page(request, "home.html", run=run, last_any=last_any, groups=groups, todo=todo,
                decided=progress["decided"], pending_approvable=pending_approvable, plan=status,
                rules_summary=rules_summary(rules), uplift_pct=round((rules.festival.uplift - 1) * 100),
                now_ist=now, next_run=next_scheduled_run(now))


@app.post("/me")
def set_name(request: Request, who: str = Form(""), next: str = Form("/")):
    try:
        name = actor(request, who)
    except ValueError as exc:
        return back("/", err=str(exc))
    return remember(RedirectResponse(_safe_next(next, "/"), status_code=303), name)


@app.get("/review")
def review_shortcut():
    return RedirectResponse("/recommendations?show=todo", status_code=303)


@app.get("/recommendations", response_class=HTMLResponse)
def recommendations(request: Request, run: int | None = None, status: str = "", distributor: str = "",
                    category: str = "", show: str = ""):
    viewer(request)
    r = _run_or_latest(run)
    recs = [x for x in _with_decisions(r) if x["status"] != "EXCEPTION"] if r else []
    distributors = sorted({x["distributor_name"] for x in recs if x["distributor_name"]})
    categories = sorted({x["category"] for x in recs if x["category"]})
    rows = [x for x in recs
            if (not status or x["status"] == status)
            and (not distributor or x["distributor_name"] == distributor)
            and (not category or x["category"] == category)
            and (show != "todo" or not x["decision"])]
    pending = [x for x in recs if not x["decision"]]
    return page(request, "recommendations.html", run=r, rows=rows, distributors=distributors,
                categories=categories, f={"status": status, "distributor": distributor, "category": category,
                                          "show": show}, pending=pending)


@app.post("/recommendations/approve-all")
def approve_all(request: Request, run_id: int = Form(...), reason: str = Form(""), who: str = Form(""),
                next: str = Form("/recommendations")):
    target = _safe_next(next, "/recommendations")
    try:
        user = actor(request, who)
    except ValueError as exc:
        return back(target, err=str(exc))
    store = get_store()
    run = store.get_run(run_id)
    n = 0
    for r in _with_decisions(run):
        if r["status"] != "EXCEPTION" and not r["decision"]:
            store.add_override(run_id, r["sku_id"], "APPROVED", None, reason.strip() or APPROVE_REASON, user)
            n += 1
    return remember(back(target, msg=f"Approved {n} suggestions."), user)


@app.get("/exceptions")
def exceptions_moved():
    """Exceptions are shown on Today ("Needs your judgment"); the old tab forwards there."""
    return RedirectResponse("/", status_code=303)


@app.get("/sku/{run_id}/{sku_id}", response_class=HTMLResponse)
def sku_detail(request: Request, run_id: int, sku_id: str):
    viewer(request)
    store = get_store()
    rec = store.recommendation(run_id, sku_id)
    if rec is None:
        return back("/recommendations", err=f"{sku_id} was not found in that run.")
    _, rules = store.current_rules()
    rec["reasons"] = [(c, reason_text(c, rules)) for c in rec["exception_codes"]]
    history = [o for o in store.overrides(run_id, include_deleted=True) if o["sku_id"] == sku_id]
    return page(request, "sku.html", run=store.get_run(run_id), r=rec, history=history,
                short_weeks=rules.windows.short_weeks, long_weeks=rules.windows.long_weeks)


@app.post("/sku/{run_id}/{sku_id}")
def decide(request: Request, run_id: int, sku_id: str, decision: str = Form(...),
           final_qty: str = Form(""), reason: str = Form(""), who: str = Form(""), next: str = Form("")):
    """Approvals and small changes need no reason (logged as "Approved as recommended"
    or "Quantity changed (no reason given)"). A material change, more than
    review.material_change_pct_of_standard of the standard order, needs one."""
    store = get_store()
    target = _safe_next(next, f"/sku/{run_id}/{sku_id}")
    qty = int(final_qty) if final_qty.strip().lstrip("-").isdigit() else None
    try:
        user = actor(request, who)
        reason = reason_or_default(store, run_id, sku_id, decision, qty, reason)
        store.add_override(run_id, sku_id, decision, qty, reason, user)
    except ValueError as exc:
        return back(target, err=f"{sku_id}: {exc}")
    rec = store.recommendation(run_id, sku_id)
    default = "/" if rec["status"] == "EXCEPTION" else "/recommendations"
    return remember(back(_safe_next(next, default), msg=f"Saved your decision for {sku_id}."), user)


def reason_or_default(store: Store, run_id: int, sku_id: str, decision: str, qty: int | None, reason: str) -> str:
    """Return the reason to store, or raise if a material change has none."""
    reason = (reason or "").strip()
    if reason:
        return reason
    if decision == "APPROVED":
        return APPROVE_REASON
    rec = store.recommendation(run_id, sku_id)
    run = store.get_run(run_id)
    rules = store.rules_for_version(run["rules_version"] if run else None)
    if rec and is_material_change(rec["recommended_qty"], qty, rec["standard_qty"], rules):
        pct = rules.review.material_change_pct_of_standard
        raise ValueError(f"Please add a reason: this changes the order by more than {pct:g}% of the "
                         f"standard quantity ({rec['standard_qty']:,.0f}).")
    return CHANGE_REASON


@app.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request, sku: str = ""):
    """All charts live here, away from the decision page: weeks of stock for every
    SKU, and weekly sales for one SKU."""
    viewer(request)
    store = get_store()
    run = store.latest_run()
    if run is None:
        return page(request, "analytics.html", run=None)
    rules = store.rules_for_version(run["rules_version"])
    recs = _with_decisions(run)
    progress = store.decision_progress(run["id"])
    todo = recs
    chosen = next((r for r in recs if r["sku_id"] == sku), None) or recs[0]
    tiles = [("SKUs in this plan", len(recs)), ("Need action (order or pause)",
                                                sum(1 for r in recs if r["status"] != "OK")),
             ("Decisions made", f"{progress['decided']} of {progress['total']}"),
             ("Units already on order", f"{sum((r.get('on_order') or 0) for r in recs):,.0f}")]
    return page(request, "analytics.html", run=run, recs=recs, chosen=chosen, tiles=tiles,
                stock_svg=charts.stock_overview(recs),
                sales_svg=charts.sales_chart(chosen["sales_history"], chosen["avg4"], chosen["avg12"],
                                             rules.windows.short_weeks, rules.windows.long_weeks),
                short_weeks=rules.windows.short_weeks, long_weeks=rules.windows.long_weeks,
                hd_pct=round((rules.high_demand.ratio - 1) * 100))


@app.get("/decisions", response_class=HTMLResponse)
def decisions(request: Request):
    """Current state for the latest plan (one row per SKU), then every decision event."""
    viewer(request)
    store = get_store()
    run = store.latest_run()
    current = _with_decisions(run) if run else []
    current.sort(key=lambda r: (r["decision"] is not None, r["sku_id"]))  # waiting first
    progress = store.decision_progress(run["id"]) if run else None
    return page(request, "decisions.html", run=run, current=current, progress=progress,
                rows=store.overrides(include_deleted=True)[:500])


@app.post("/decisions/{override_id}/delete")
def delete_decision(request: Request, override_id: int, who: str = Form(""), next: str = Form("/decisions")):
    user = actor(request, who, required=False)
    ok = get_store().delete_override(override_id, user)
    return back(_safe_next(next, "/decisions"),
                msg="Undone. The SKU is waiting for a decision again; the history keeps the undone entry." if ok else "",
                err="" if ok else "That decision was already undone or replaced by a later one.")


@app.post("/decisions/upload")
async def upload_decisions(request: Request, file: UploadFile = File(...), who: str = Form("")):
    """Every row is checked first; if any row is wrong nothing is applied."""
    try:
        user = actor(request, who)
    except ValueError as exc:
        return back("/decisions", err=str(exc))
    store = get_store()
    run = store.latest_run()
    if run is None:
        return back("/decisions", err="There is no plan to attach these decisions to yet.")
    try:
        parsed = read_import(await file.read())
    except Exception as exc:  # noqa: BLE001 - any unreadable file
        return back("/decisions", err=f"Could not read that file: {exc}")
    to_apply, errors, unchanged = validate_import(store, run, parsed)
    if errors:
        shown = "; ".join(errors[:8]) + (f"; and {len(errors) - 8} more" if len(errors) > 8 else "")
        return back("/decisions", err=f"Nothing was imported. Fix these rows and upload again: {shown}")
    for row in to_apply:
        store.add_override(run["id"], row["sku_id"], row["decision"], row["final_qty"], row["reason"], user)
    note = f" {unchanged} rows matched decisions already recorded and were skipped." if unchanged else ""
    return remember(back("/decisions", msg=f"Imported {len(to_apply)} decisions.{note}"), user)


def validate_import(store: Store, run: dict, parsed: dict) -> tuple[list[dict], list[str], int]:
    """Check an uploaded sheet against the current plan. Returns (rows to apply, errors, unchanged)."""
    errors, to_apply, seen, unchanged = [], [], set(), 0
    if parsed["run_id"] not in (None, "") and str(parsed["run_id"]) != str(run["id"]):
        return [], [f"this file is for plan #{parsed['run_id']}, but the current plan is #{run['id']}. "
                    "Download the current Excel file and fill that in"], 0
    recs = {r["sku_id"]: r for r in store.recommendations(run["id"])}
    current = store.latest_decisions(run["id"])
    rules = store.rules_for_version(run["rules_version"])
    for r in parsed["rows"]:
        where = f"row {r['row']} ({r['sku_id']})"
        rec = recs.get(r["sku_id"])
        word = r["decision"].lower()
        if rec is None:
            errors.append(f"{where}: SKU is not in this plan")
            continue
        if r["sku_id"] in seen:
            errors.append(f"{where}: SKU appears more than once in the file")
            continue
        seen.add(r["sku_id"])
        if word.startswith("approve"):
            if rec["status"] == "EXCEPTION":
                errors.append(f"{where}: needs a quantity, there is no suggestion to approve")
                continue
            decision, qty = "APPROVED", rec["recommended_qty"]
        elif word.startswith("change"):
            try:
                qty = int(str(r["qty"]).strip())
                if qty < 0 or float(str(r["qty"]).strip()) != qty:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{where}: Your_Qty must be a whole number of 0 or more")
                continue
            decision = "CHANGED"
        else:
            errors.append(f"{where}: Decision must be Approve or Change")
            continue
        try:
            reason = reason_or_default(store, run["id"], r["sku_id"], decision, qty, r["reason"])
        except ValueError as exc:
            errors.append(f"{where}: {exc}")
            continue
        cur = current.get(r["sku_id"])
        if cur and cur["decision"] == decision and cur["final_qty"] == qty:
            unchanged += 1
            continue
        to_apply.append({"sku_id": r["sku_id"], "decision": decision, "final_qty": qty, "reason": reason})
    return to_apply, errors, unchanged


@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request):
    viewer(request)
    store = get_store()
    return page(request, "runs.html", runs=store.list_runs(50), emails=store.emails(30))


@app.post("/runs/new")
def run_now(request: Request, who: str = Form("")):
    user = actor(request, who, required=False)
    run = run_plan(today_ist(), trigger=f"dashboard:{user}", store=get_store())
    if run["status"] == "SUCCESS":
        return back("/", msg=f"New plan created (run {run['id']}).")
    return back("/runs", err=f"Run {run['id']} failed. See the details below.")


@app.get("/runs/{run_id}/workbook")
def workbook(request: Request, run_id: int):
    viewer(request)
    store = get_store()
    run = store.get_run(run_id)
    if not run or run["status"] != "SUCCESS":
        return back("/runs", err="That run has no plan to download.")
    version = run["rules_version"] or 0
    rules = next((rules_from_yaml(v.yaml_text) for v in store.rules_history() if v.id == version),
                 store.current_rules()[1])
    data = build_workbook(run, store.recommendations(run_id), store.overrides(run_id, current_only=True), rules, version)
    name = f"freshroots_plan_{run['run_date']}_run{run_id}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/emails/{email_id}", response_class=HTMLResponse)
def view_email(request: Request, email_id: int):
    viewer(request)
    e = get_store().email(email_id)
    if not e:
        return back("/runs", err="Email not found.")
    return HTMLResponse(e["html"] or "<p>This email's content is not stored (it contained logins).</p>")


@app.get("/judgment-log.md", response_class=PlainTextResponse)
def judgment_log(request: Request):
    viewer(request)
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
    viewer(request)
    store = get_store()
    version, rules = store.current_rules()
    data = rules.model_dump()
    fields = [(n, label, help_, _get(data, path)) for n, label, help_, path in SETTINGS_FIELDS]
    return page(request, "settings.html", version=version, fields=fields, history=store.rules_history()[:20],
                rules_yaml=rules.to_yaml())


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    try:
        user = actor(request, str(form.get("who", "")))
    except ValueError as exc:
        return back("/settings", err=str(exc))
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
    return remember(back("/settings", msg=f"Saved as version {version}. It applies from the next run."), user)


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
