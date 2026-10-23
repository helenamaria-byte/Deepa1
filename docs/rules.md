# Planning rules

All numbers below are defaults from `config/rules.yaml`. The Settings page saves changes as a new
numbered version in the database; every run records which version it used.

## Definitions

| Term | Meaning |
|---|---|
| Complete week | An ISO week (Mon-Sun) whose Sunday is before the run date |
| avg12 | Mean weekly units sold over the last 12 complete weeks |
| avg4 | Mean weekly units sold over the last 4 complete weeks |
| Festival run | The run date's month is marked `Is_Festival_Month = TRUE` in `calendar.csv` (Oct, Nov, Mar) |
| Weekly demand | avg12 × 1.25 in a festival run, otherwise avg12 |
| Cover (weeks) | (Units on hand + units on open purchase orders in Zoho that arrive before stock on hand runs out) ÷ weekly demand. Stock across locations is summed. Later or undated orders are shown but not counted |
| Perishable | `sku_master.Perishability = High` |

If the newest sales week in the export is older than the last complete week (the SAP export is
late), the run uses the newest available week and shows a warning on the dashboard and in the email.

## The five rules

| # | Rule | Condition | Recommendation |
|---|---|---|---|
| 1 | High demand | avg4 > 1.20 × avg12 | Reorder 1.5 × Standard_Reorder_Qty |
| 2 | Stockout risk | cover < 6 weeks | Reorder Standard_Reorder_Qty now |
| 3 | Overstock risk | cover > 14 weeks | Pause the next order (quantity 0) |
| 4 | Festival uplift | festival month | Weekly demand × 1.25, applied before every cover check |
| 5 | Perishable SKUs | Perishability = High | Window of 3 to 4 weeks replaces rules 2 and 3 |

Rule 1 does not use the uplift: it would multiply both averages, so it cancels out of the ratio.

## Boundaries

Thresholds are strict: exactly 6.00 weeks is not a stockout, exactly 14.00 is not an overstock (3.00 and 4.00 for
perishables), and recent sales exactly 20% above the 12-week average are not "high demand". Comparisons are made
at 6 decimal places so float rounding cannot flip a value that sits on a threshold.

## When flags combine

| Flags | Result |
|---|---|
| STOCKOUT_RISK + HIGH_DEMAND | Reorder 1.5 × standard (status STOCKOUT_RISK) |
| STOCKOUT_RISK only | Reorder standard |
| HIGH_DEMAND only | Reorder 1.5 × standard (status HIGH_DEMAND) |
| OVERSTOCK_RISK only | Pause (0) |
| OVERSTOCK_RISK + HIGH_DEMAND | Conflict → exception queue (CONFLICTING_FLAGS) |
| None | OK, no action |

## Exception queue

A SKU with any of these reason codes gets **no automatic quantity**. A person decides on the
Exceptions page.

| Code | Trigger (default) |
|---|---|
| NEW_LAUNCH | Fewer than 12 weeks of sales history |
| VOLATILE_DEMAND | avg4 differs from avg12 by more than 40% in either direction |
| ZERO_SALES_WEEK | A zero-sales week in the last 12 weeks |
| MISSING_WEEKS | A week missing from the sales data after the SKU's first sale |
| MISSING_INVENTORY | No inventory row |
| STALE_INVENTORY | Inventory As_Of_Date more than 10 days before the run |
| NEGATIVE_STOCK | Units on hand below zero |
| INACTIVE_DISTRIBUTOR | Distributor status is not `Active` |
| UNMAPPED_DISTRIBUTOR | Distributor ID not in the distributor master |
| CONFLICTING_FLAGS | Overstocked but demand rising |
| ORPHAN_SKU | Sales or stock for a SKU with no master record |
| DUPLICATE_ROWS | Duplicate rows for the SKU in any table |
| INVALID_VALUE | A number or date that cannot be read |

## Golden result (sample data, run date 2026-10-22, festival month)

| Result | SKUs |
|---|---|
| STOCKOUT_RISK | SKU001 (3.35 wks, also HIGH_DEMAND → 750), SKU003 (4.36), SKU004 (1.80, perishable), SKU011 (1.80, perishable), SKU013 (3.17) |
| OVERSTOCK_RISK | SKU006 (22.2), SKU007 (4.08 vs the perishable max of 4) |
| OK | SKU002, 005, 008, 009, 010, 012, 014, 015 |

With the uplift off, SKU002 (14.33 weeks) becomes OVERSTOCK_RISK. Both are checked by
`tests/test_golden.py` against an independent pandas calculation.
