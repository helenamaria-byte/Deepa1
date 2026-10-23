"""Typed model of config/rules.yaml. The engine reads every threshold from here."""
from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from src.settings import ROOT

RULES_PATH = ROOT / "config" / "rules.yaml"


class Windows(BaseModel):
    long_weeks: int = Field(gt=0)
    short_weeks: int = Field(gt=0)
    demand_baseline: Literal["long", "short"] = "long"


class Festival(BaseModel):
    enabled: bool
    uplift: float = Field(gt=0)
    fallback_months: list[int]


class HighDemand(BaseModel):
    ratio: float = Field(gt=0)
    reorder_multiplier: float = Field(gt=0)


class CoverWindow(BaseModel):
    min_weeks: float = Field(ge=0)
    max_weeks: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.min_weeks >= self.max_weeks:
            raise ValueError("min_weeks must be below max_weeks")
        return self


class Cover(BaseModel):
    general: CoverWindow
    perishable: CoverWindow
    perishable_values: list[str]


class Exceptions(BaseModel):
    min_history_weeks: int = Field(gt=0)
    max_deviation: float = Field(gt=0)
    zero_sales_lookback_weeks: int = Field(gt=0)
    stale_inventory_days: int = Field(ge=0)
    active_status: str


class Rounding(BaseModel):
    cover_decimals: int = Field(ge=0)


class RulesConfig(BaseModel):
    windows: Windows
    festival: Festival
    high_demand: HighDemand
    cover: Cover
    exceptions: Exceptions
    rounding: Rounding

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(), sort_keys=False)


def rules_from_yaml(text: str) -> RulesConfig:
    return RulesConfig.model_validate(yaml.safe_load(text))


def default_rules_yaml() -> str:
    return RULES_PATH.read_text(encoding="utf-8")


def load_default_rules() -> RulesConfig:
    return rules_from_yaml(default_rules_yaml())
