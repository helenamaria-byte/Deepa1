"""All database reads and writes go through Store. Keeps SQL out of the app and job."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import date
from functools import lru_cache

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from src.rules_config import RulesConfig, default_rules_yaml, rules_from_yaml
from src.settings import settings
from src.store.models import (SCHEMA_VERSION, AiCacheEntry, Base, EmailLog, Override, Recommendation,
                              RulesVersion, Run, SchemaInfo, User, utcnow)

PBKDF2_ROUNDS = 200_000
# (table, column, DDL): columns added after the first release, applied to older databases.
COLUMN_MIGRATIONS = [("recommendations", "on_order", "FLOAT DEFAULT 0"),        # schema v2
                     ("recommendations", "sales_history", "TEXT DEFAULT '[]'"),  # schema v3
                     ("recommendations", "calc", "TEXT DEFAULT '{}'"),           # schema v4
                     ("runs", "data_info", "TEXT DEFAULT '{}'"),                 # schema v4
                     ("overrides", "previous_qty", "INTEGER"),                   # schema v4
                     ("overrides", "superseded_at", "TIMESTAMP")]                # schema v4


@lru_cache(maxsize=8)
def get_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    # Serverless: no long-lived pool; Neon's pooler handles connection reuse.
    return create_engine(url, poolclass=NullPool, pool_pre_ping=True)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, AttributeError):
        return False


def _rec_to_dict(r: Recommendation) -> dict:
    d = {c.name: getattr(r, c.name) for c in Recommendation.__table__.columns}
    d["flags"] = json.loads(r.flags)
    d["exception_codes"] = json.loads(r.exception_codes)
    d["sales_history"] = json.loads(r.sales_history or "[]")
    d["calc"] = json.loads(r.calc or "{}")
    return d


def _run_to_dict(r: Run) -> dict:
    d = {c.name: getattr(r, c.name) for c in Run.__table__.columns}
    d["warnings"] = json.loads(r.warnings or "[]")
    d["counts"] = json.loads(r.counts or "{}")
    d["data_info"] = json.loads(r.data_info or "{}")
    return d


def _override_to_dict(o: Override) -> dict:
    d = {c.name: getattr(o, c.name) for c in Override.__table__.columns}
    d["state"] = "undone" if o.deleted_at else ("replaced" if o.superseded_at else "current")
    return d


def _active(q):
    """Decisions that are neither undone nor replaced."""
    return q.where(Override.deleted_at.is_(None), Override.superseded_at.is_(None))


class Store:
    def __init__(self, url: str | None = None):
        self.engine = get_engine(url or settings.database_url)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    # ---- schema ----------------------------------------------------------
    def migrate(self) -> None:
        """Create tables (idempotent), apply column migrations, record the schema
        version and seed rules v1."""
        Base.metadata.create_all(self.engine)
        # Add columns to databases created before they existed.
        insp = inspect(self.engine)
        existing = {t: {c["name"] for c in insp.get_columns(t)} for t in {m[0] for m in COLUMN_MIGRATIONS}}
        for table, name, ddl in COLUMN_MIGRATIONS:
            if name not in existing[table]:
                with self.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        self._mark_replaced_decisions()
        with self.Session.begin() as s:
            current = s.scalar(select(func.max(SchemaInfo.version)))
            if current is None or current < SCHEMA_VERSION:
                s.add(SchemaInfo(version=SCHEMA_VERSION))
            if not s.scalar(select(func.count()).select_from(RulesVersion)):
                s.add(RulesVersion(id=1, yaml_text=default_rules_yaml(), created_by="system",
                                   note="Initial rules from config/rules.yaml"))

    def _mark_replaced_decisions(self) -> None:
        """Keep one current decision per (run, SKU). Older active decisions (from before
        v4, e.g. repeated clicks) are marked replaced, never deleted."""
        with self.Session.begin() as s:
            rows = list(s.scalars(_active(select(Override)).order_by(Override.run_id, Override.sku_id,
                                                                     Override.created_at, Override.id)))
            for older, newer in zip(rows, rows[1:]):
                if (older.run_id, older.sku_id) == (newer.run_id, newer.sku_id):
                    older.superseded_at = newer.created_at

    # ---- rules -----------------------------------------------------------
    def current_rules(self) -> tuple[int, RulesConfig]:
        with self.Session() as s:
            rv = s.scalars(select(RulesVersion).order_by(RulesVersion.id.desc())).first()
        if rv is None:
            return 0, rules_from_yaml(default_rules_yaml())
        return rv.id, rules_from_yaml(rv.yaml_text)

    def save_rules(self, rules: RulesConfig, user: str, note: str = "") -> int:
        """Store a new rules version. Takes effect on the next run."""
        RulesConfig.model_validate(rules.model_dump())
        with self.Session.begin() as s:
            next_id = (s.scalar(select(func.max(RulesVersion.id))) or 0) + 1
            s.add(RulesVersion(id=next_id, yaml_text=rules.to_yaml(), created_by=user, note=note))
        return next_id

    def rules_for_version(self, version: int | None) -> RulesConfig:
        """The rules a run actually used (falls back to the current rules)."""
        with self.Session() as s:
            rv = s.get(RulesVersion, version) if version else None
        return rules_from_yaml(rv.yaml_text) if rv else self.current_rules()[1]

    def rules_history(self) -> list[RulesVersion]:
        with self.Session() as s:
            return list(s.scalars(select(RulesVersion).order_by(RulesVersion.id.desc())))

    # ---- runs ------------------------------------------------------------
    def start_run(self, run_date: date, trigger: str) -> int:
        with self.Session.begin() as s:
            seq = (s.scalar(select(func.max(Run.seq)).where(Run.run_date == run_date)) or 0) + 1
            run = Run(run_date=run_date, seq=seq, status="RUNNING", trigger=trigger)
            s.add(run)
            s.flush()
            return run.id

    def update_run(self, run_id: int, **fields) -> None:
        for key in ("warnings", "counts", "data_info"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        with self.Session.begin() as s:
            run = s.get(Run, run_id)
            for k, v in fields.items():
                setattr(run, k, v)

    def save_recommendations(self, run_id: int, rows: list[dict]) -> None:
        cols = {c.name for c in Recommendation.__table__.columns} - {"id", "run_id"}
        with self.Session.begin() as s:
            for row in rows:
                data = {k: v for k, v in row.items() if k in cols}
                data["flags"] = json.dumps(list(row.get("flags", [])))
                data["exception_codes"] = json.dumps(list(row.get("exception_codes", [])))
                data["sales_history"] = json.dumps(row.get("sales_history", []))
                data["calc"] = json.dumps(row.get("calc", {}), default=str)
                s.add(Recommendation(run_id=run_id, **data))

    def get_run(self, run_id: int) -> dict | None:
        with self.Session() as s:
            run = s.get(Run, run_id)
            return _run_to_dict(run) if run else None

    def latest_run(self, status: str | None = "SUCCESS") -> dict | None:
        q = select(Run).order_by(Run.started_at.desc(), Run.id.desc())
        if status:
            q = q.where(Run.status == status)
        with self.Session() as s:
            run = s.scalars(q).first()
            return _run_to_dict(run) if run else None

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self.Session() as s:
            runs = s.scalars(select(Run).order_by(Run.started_at.desc(), Run.id.desc()).limit(limit))
            return [_run_to_dict(r) for r in runs]

    def recommendations(self, run_id: int) -> list[dict]:
        with self.Session() as s:
            recs = s.scalars(select(Recommendation).where(Recommendation.run_id == run_id)
                             .order_by(Recommendation.sku_id))
            return [_rec_to_dict(r) for r in recs]

    def recommendation(self, run_id: int, sku_id: str) -> dict | None:
        with self.Session() as s:
            r = s.scalars(select(Recommendation).where(Recommendation.run_id == run_id,
                                                       Recommendation.sku_id == sku_id)).first()
            return _rec_to_dict(r) if r else None

    # ---- overrides -------------------------------------------------------
    def add_override(self, run_id: int, sku_id: str, decision: str, final_qty: int | None,
                     reason: str, username: str) -> int:
        """Record a decision as an event and make it the SKU's current decision for the run.

        - The same decision again (a repeated click) is not stored twice: the current
          event's id is returned.
        - A different decision replaces the current one (marked replaced, kept in history)
          and records the value it replaced in previous_qty.
        A reason is always stored; the app supplies a default when none is typed."""
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("A reason is required.")
        if decision not in ("APPROVED", "CHANGED"):
            raise ValueError("Decision must be APPROVED or CHANGED.")
        rec = self.recommendation(run_id, sku_id)
        if rec is None:
            raise ValueError(f"{sku_id} is not in run {run_id}.")
        if decision == "APPROVED" and rec["status"] == "EXCEPTION":
            raise ValueError("This SKU has no automatic recommendation. Enter the quantity you decide on.")
        if decision == "CHANGED":
            if final_qty is None or final_qty < 0:
                raise ValueError("Enter the order quantity you want (0 or more).")
        else:
            final_qty = rec["recommended_qty"]
        with self.Session.begin() as s:
            # Lock the recommendation row (Postgres) so two quick clicks are applied one after the other.
            s.execute(select(Recommendation.id).where(Recommendation.run_id == run_id,
                                                      Recommendation.sku_id == sku_id).with_for_update())
            current = s.scalars(_active(select(Override)).where(Override.run_id == run_id, Override.sku_id == sku_id)
                                .order_by(Override.created_at.desc(), Override.id.desc())).first()
            if current and current.decision == decision and current.final_qty == final_qty:
                return current.id
            previous = current.final_qty if current else rec["recommended_qty"]
            if current:
                current.superseded_at = utcnow()
            ov = Override(run_id=run_id, sku_id=sku_id, decision=decision, final_qty=final_qty,
                          recommended_qty=rec["recommended_qty"], previous_qty=previous, reason=reason,
                          username=username)
            s.add(ov)
            s.flush()
            return ov.id

    def delete_override(self, override_id: int, username: str) -> bool:
        """Undo the current decision: the SKU goes back to waiting for a decision. The
        event stays in the history, marked undone with who and when. Replaced or
        already-undone events cannot be undone."""
        with self.Session.begin() as s:
            ov = s.get(Override, override_id)
            if ov is None or ov.deleted_at is not None or ov.superseded_at is not None:
                return False
            ov.deleted_at, ov.deleted_by = utcnow(), username
            return True

    def overrides(self, run_id: int | None = None, include_deleted: bool = False,
                  current_only: bool = False) -> list[dict]:
        """Decision events, newest first. Each carries state: current, replaced or undone."""
        q = select(Override).order_by(Override.created_at.desc(), Override.id.desc())
        if run_id is not None:
            q = q.where(Override.run_id == run_id)
        if current_only:
            q = _active(q)
        elif not include_deleted:
            q = q.where(Override.deleted_at.is_(None))
        with self.Session() as s:
            return [_override_to_dict(o) for o in s.scalars(q)]

    def latest_decisions(self, run_id: int) -> dict[str, dict]:
        """The current decision per SKU for a run (one at most)."""
        out: dict[str, dict] = {}
        for o in self.overrides(run_id, current_only=True):
            out.setdefault(o["sku_id"], o)
        return out

    def decision_progress(self, run_id: int) -> dict:
        """The one place decision counts come from, so every page agrees."""
        with self.Session() as s:
            total = s.scalar(select(func.count()).select_from(Recommendation)
                             .where(Recommendation.run_id == run_id)) or 0
        decided = len(self.latest_decisions(run_id))
        return {"total": total, "decided": decided, "pending": total - decided}

    # ---- emails ----------------------------------------------------------
    def log_email(self, kind: str, recipients: list[str], subject: str, status: str,
                  detail: str = "", html: str = "", run_id: int | None = None) -> None:
        with self.Session.begin() as s:
            s.add(EmailLog(kind=kind, recipients=", ".join(recipients), subject=subject, status=status,
                           detail=detail[:2000], html=html, run_id=run_id))

    def emails(self, limit: int = 30) -> list[dict]:
        with self.Session() as s:
            rows = s.scalars(select(EmailLog).order_by(EmailLog.created_at.desc(), EmailLog.id.desc()).limit(limit))
            return [{c.name: getattr(e, c.name) for c in EmailLog.__table__.columns} for e in rows]

    def email(self, email_id: int) -> dict | None:
        with self.Session() as s:
            e = s.get(EmailLog, email_id)
            return {c.name: getattr(e, c.name) for c in EmailLog.__table__.columns} if e else None

    # ---- users -----------------------------------------------------------
    def create_user(self, username: str, password: str, email: str = "") -> None:
        with self.Session.begin() as s:
            existing = s.scalars(select(User).where(User.username == username)).first()
            if existing:
                existing.password_hash, existing.email = hash_password(password), email
            else:
                s.add(User(username=username, email=email, password_hash=hash_password(password)))

    def authenticate(self, login: str, password: str) -> str | None:
        login = (login or "").strip().lower()
        with self.Session() as s:
            for u in s.scalars(select(User)):
                if login in (u.username.lower(), (u.email or "").lower()) and check_password(password, u.password_hash):
                    return u.username
        return None

    def usernames(self) -> list[str]:
        with self.Session() as s:
            return [u.username for u in s.scalars(select(User).order_by(User.username))]

    # ---- AI cache --------------------------------------------------------
    def ai_cache(self) -> "DbCache":
        return DbCache(self)


class DbCache:
    """Explanation cache keyed by a hash of the facts (see src/ai/explain.py)."""

    def __init__(self, store: Store):
        self.store = store

    def get(self, key: str) -> str | None:
        with self.store.Session() as s:
            e = s.get(AiCacheEntry, key)
            return e.text if e else None

    def set(self, key: str, value: str) -> None:
        with self.store.Session.begin() as s:
            if s.get(AiCacheEntry, key) is None:
                s.add(AiCacheEntry(key=key, text=value))
