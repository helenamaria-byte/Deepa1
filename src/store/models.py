"""Database tables. Same schema on Postgres (Neon) and SQLite.

List-valued fields (flags, reason codes, warnings) are stored as JSON text so the
schema stays portable."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_VERSION = 3  # v2: recommendations.on_order (Zoho POs); v3: recommendations.sales_history


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class SchemaInfo(Base):
    __tablename__ = "schema_info"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True)
    email: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RulesVersion(Base):
    __tablename__ = "rules_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # the version number
    yaml_text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("run_date", "seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_date: Mapped[date] = mapped_column(Date)
    seq: Mapped[int] = mapped_column(Integer)  # 1st, 2nd... run for this date (reruns never overwrite)
    status: Mapped[str] = mapped_column(String(20))  # RUNNING | SUCCESS | FAILED
    trigger: Mapped[str] = mapped_column(String(40), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rules_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    festival: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    data_week: Mapped[str] = mapped_column(String(20), default="")
    connector: Mapped[str] = mapped_column(String(40), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[str] = mapped_column(Text, default="[]")
    counts: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    sku_id: Mapped[str] = mapped_column(String(60))
    sku_name: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[str] = mapped_column(String(120), default="")
    distributor_id: Mapped[str] = mapped_column(String(60), default="")
    distributor_name: Mapped[str] = mapped_column(String(300), default="")
    perishable: Mapped[bool] = mapped_column(Boolean, default=False)
    standard_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    on_hand: Mapped[float | None] = mapped_column(Float, nullable=True)
    on_order: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)  # schema v2
    inventory_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    history_weeks: Mapped[int] = mapped_column(Integer, default=0)
    avg4: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg12: Mapped[float | None] = mapped_column(Float, nullable=True)
    weekly_demand: Mapped[float | None] = mapped_column(Float, nullable=True)
    cover_weeks: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_cover: Mapped[float] = mapped_column(Float)
    max_cover: Mapped[float] = mapped_column(Float)
    flags: Mapped[str] = mapped_column(Text, default="[]")
    exception_codes: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(60))
    recommended_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, default="")
    explanation_source: Mapped[str] = mapped_column(String(20), default="template")
    sales_history: Mapped[str] = mapped_column(Text, default="[]")  # schema v3: [[week, units], ...]


class Override(Base):
    __tablename__ = "overrides"
    __table_args__ = (CheckConstraint("length(trim(reason)) > 0", name="override_reason_required"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    sku_id: Mapped[str] = mapped_column(String(60))
    decision: Mapped[str] = mapped_column(String(20))  # APPROVED | CHANGED
    recommended_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    username: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_by: Mapped[str] = mapped_column(String(120), default="")


class EmailLog(Base):
    __tablename__ = "emails"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(40))  # alert | weekly_summary | failure | credentials | test
    recipients: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))  # SENT | OUTBOX | FAILED
    detail: Mapped[str] = mapped_column(Text, default="")
    html: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AiCacheEntry(Base):
    __tablename__ = "ai_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
