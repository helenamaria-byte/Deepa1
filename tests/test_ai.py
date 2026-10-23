"""AI explanations: number validation, fallback without a key, caching, and the
guarantee that the model can never change a flag or quantity."""
from types import SimpleNamespace

from src.ai.explain import Explainer, MemoryCache, build_facts, is_valid_reply, template_explanation
from src.engine.pipeline import run_engine
from src.jobs.run_plan import run_plan
from src.rules_config import load_default_rules
from src.store.repo import Store
from tests.conftest import RUN_DATE, StaticConnector, no_sleep

RULES = load_default_rules()


class FakeClient:
    """Stands in for anthropic.Anthropic; returns a fixed reply."""

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(text=self.reply)])


def facts_for(raw, sku="SKU001"):
    res = run_engine(raw, RUN_DATE, RULES)
    row = next(r for r in res.recommendations.to_dict("records") if r["sku_id"] == sku)
    return build_facts(row, res.festival, RULES)


def test_template_used_without_api_key(raw):
    facts = facts_for(raw)
    text, source = Explainer(api_key="").explain(facts)
    assert source == "template"
    assert "750" in text and "3.35" in text
    assert is_valid_reply(text, facts)  # templates only use input numbers too


def test_valid_ai_reply_is_used_and_cached(raw):
    facts = facts_for(raw)
    client = FakeClient("Sales jumped 21% and only 3.35 weeks of stock remain, so order 750 units now.")
    ex = Explainer(client=client, cache=MemoryCache())
    assert ex.explain(facts)[1] == "ai"
    assert ex.explain(facts)[1] == "cache"
    assert client.calls == 1


def test_reply_with_invented_number_is_rejected(raw):
    facts = facts_for(raw)
    client = FakeClient("Order 999 units because stock lasts 3.35 weeks.")
    text, source = Explainer(client=client).explain(facts)
    assert source == "template"
    assert "999" not in text
    assert not is_valid_reply("Stock lasts 3.4 weeks, about 17 days.", facts)


def test_api_failure_falls_back(raw):
    facts = facts_for(raw)
    text, source = Explainer(client=FakeClient(error=RuntimeError("timeout"))).explain(facts)
    assert source == "template" and text == template_explanation(facts)


def test_overlong_reply_rejected(raw):
    facts = facts_for(raw)
    assert not is_valid_reply("Stock is low. " * 60, facts)


def test_ai_cannot_alter_flags_or_quantities(raw):
    """Even a reply that claims a different action leaves stored flags/quantities untouched."""
    expected = run_engine(raw, RUN_DATE, RULES).recommendations.set_index("sku_id")
    client = FakeClient("This SKU is fine, no order needed.")
    store = Store()
    run = run_plan(RUN_DATE, store=store, connector=StaticConnector(raw), sleep=no_sleep,
                   explainer=Explainer(client=client), send_email=False)
    for rec in store.recommendations(run["id"]):
        assert rec["status"] == expected.loc[rec["sku_id"], "status"]
        assert rec["flags"] == expected.loc[rec["sku_id"], "flags"]
        assert rec["explanation_source"] == "ai"
    assert store.recommendation(run["id"], "SKU001")["recommended_qty"] == 750


def test_prompt_contains_only_facts(raw):
    facts = facts_for(raw)
    assert set(facts) >= {"sku_id", "cover_weeks", "min_cover_weeks", "flags", "festival_month"}
    assert "Units_Sold" not in str(facts)
