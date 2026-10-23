"""One- or two-sentence reasons for each recommendation.

Claude only writes the sentence. It receives the computed facts and never
changes a flag or a quantity: its reply is used as text only. A reply is
rejected (and the deterministic template used instead) if it contains any
number not present in the facts, is too long, or the call fails. Results are
cached by a hash of the facts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Protocol

from src.ingest.validate import reason_text
from src.rules_config import RulesConfig

log = logging.getLogger(__name__)

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
MAX_CHARS = 400
MAX_SENTENCES = 3  # the template can use 3 short sentences; the model is asked for 1-2

SYSTEM_PROMPT = (
    "You explain stock-planning recommendations to a busy planner at an Indian FMCG company. "
    "Write one or two short, plain sentences saying why this SKU got its status. "
    "Use only numbers that appear in the facts, exactly as given; never calculate new numbers. "
    "Do not suggest a different action or quantity. No preamble, no bullet points."
)


class Cache(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...


class MemoryCache:
    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value


def build_facts(rec: dict, festival: bool, rules: RulesConfig) -> dict:
    """The only information the model sees. All numbers are already rounded."""
    avg4, avg12 = rec.get("avg4"), rec.get("avg12")
    pct = round((avg4 / avg12 - 1) * 100) if avg4 is not None and avg12 else None
    return {
        "sku_id": rec["sku_id"],
        "sku_name": rec["sku_name"],
        "status": rec["status"],
        "flags": list(rec["flags"]),
        "perishable": bool(rec["perishable"]),
        "festival_month": bool(festival and rules.festival.enabled),
        "festival_uplift_pct": round((rules.festival.uplift - 1) * 100),
        "avg_last_short_weeks": _int_if_whole(avg4),
        "avg_last_long_weeks": _int_if_whole(avg12),
        "short_weeks": rules.windows.short_weeks,
        "long_weeks": rules.windows.long_weeks,
        "pct_change_short_vs_long": pct,
        "weekly_demand_used": rec.get("weekly_demand"),
        "units_on_hand": _int_if_whole(rec.get("on_hand")),
        "units_on_order": _int_if_whole(rec.get("on_order") or 0),
        "on_order_counted_in_cover": bool(rules.open_orders.include_in_cover),
        "cover_weeks": rec.get("cover_weeks"),
        "min_cover_weeks": _int_if_whole(rec.get("min_cover")),
        "max_cover_weeks": _int_if_whole(rec.get("max_cover")),
        "standard_reorder_qty": _int_if_whole(rec.get("standard_qty")),
        "reorder_multiplier": rules.high_demand.reorder_multiplier,
        "recommended_qty": rec.get("recommended_qty"),
        "exception_reasons": [reason_text(c, rules) for c in rec.get("exception_codes", [])],
    }


def _int_if_whole(v):
    if v is None:
        return None
    return int(v) if float(v).is_integer() else v


def template_explanation(f: dict) -> str:
    """Deterministic explanation built only from the facts. Always available."""
    status = f["status"]
    perish = " for a perishable item" if f["perishable"] else ""
    po = (f", counting {f['units_on_order']} units already on order"
          if f.get("units_on_order") and f.get("on_order_counted_in_cover") else "")
    fest = f" (demand includes the {f['festival_uplift_pct']}% festival uplift)" if f["festival_month"] else ""
    trend = (f"Sales over the last {f['short_weeks']} weeks average {f['avg_last_short_weeks']} a week, "
             f"{f['pct_change_short_vs_long']}% above the {f['long_weeks']}-week average of "
             f"{f['avg_last_long_weeks']}. ")
    if status == "EXCEPTION":
        return "Needs your judgment: " + "; ".join(f["exception_reasons"]) + "."
    if status == "STOCKOUT_RISK":
        lead = trend if "HIGH_DEMAND" in f["flags"] else ""
        return (f"{lead}Only {f['cover_weeks']} weeks of stock left{po}, below the {f['min_cover_weeks']}-week "
                f"minimum{perish}{fest}. Reorder {f['recommended_qty']} units now.")
    if status == "OVERSTOCK_RISK":
        return (f"{f['cover_weeks']} weeks of stock{po} is above the {f['max_cover_weeks']}-week maximum"
                f"{perish}{fest}. Pause the next order.")
    if status == "HIGH_DEMAND":
        return trend + (f"There are {f['cover_weeks']} weeks of stock{po}, but reorder "
                        f"{f['recommended_qty']} units to keep up.")
    return (f"{f['cover_weeks']} weeks of stock{po} is within the {f['min_cover_weeks']} to "
            f"{f['max_cover_weeks']}-week range{fest}. No action needed.")


def _allowed_numbers(facts: dict) -> set[float]:
    allowed = set()
    for n in NUMBER_RE.findall(json.dumps(facts)):
        v = float(n)
        allowed.update({v, round(v), round(v, 1)})
    return allowed


def is_valid_reply(reply: str, facts: dict) -> bool:
    """Reject empty/long replies and any number that is not in the facts."""
    if not reply or len(reply) > MAX_CHARS or len(SENTENCE_END_RE.findall(reply)) > MAX_SENTENCES:
        return False
    allowed = _allowed_numbers(facts)
    for n in NUMBER_RE.findall(reply.replace(",", "")):
        if not any(abs(float(n) - a) < 0.005 for a in allowed):
            return False
    return True


def facts_hash(facts: dict, model: str) -> str:
    return hashlib.sha256((model + json.dumps(facts, sort_keys=True, default=str)).encode()).hexdigest()


class Explainer:
    """Produces (text, source) where source is 'ai', 'cache' or 'template'."""

    def __init__(self, api_key: str = "", model: str = "claude-haiku-5-5",
                 cache: Cache | None = None, client=None):
        self.model = model
        self.cache = cache or MemoryCache()
        self.client = client
        if self.client is None and api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=20)
            except Exception:  # pragma: no cover - SDK import problems fall back to templates
                self.client = None

    def explain(self, facts: dict) -> tuple[str, str]:
        if self.client is None:
            return template_explanation(facts), "template"
        key = facts_hash(facts, self.model)
        cached = self.cache.get(key)
        if cached:
            return cached, "cache"
        try:
            msg = self.client.messages.create(
                model=self.model, max_tokens=160, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": "Facts:\n" + json.dumps(facts, indent=1)}],
            )
            reply = "".join(getattr(b, "text", "") for b in msg.content).strip()
        except Exception as exc:
            log.warning("AI explanation failed for %s (%s); using template", facts["sku_id"], type(exc).__name__)
            return template_explanation(facts), "template"
        if not is_valid_reply(reply, facts):
            log.info("AI reply for %s rejected by validation; using template", facts["sku_id"])
            return template_explanation(facts), "template"
        self.cache.set(key, reply)
        return reply, "ai"
