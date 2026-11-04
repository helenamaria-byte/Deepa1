"""The Analytics tab's two charts, rendered server-side as inline SVG (night theme).

1. stock_overview: weeks of stock for every SKU against its own target range.
2. sales_chart: weekly units sold for one SKU with its 4- and 12-week averages.

Colours are the reference chart palette's dark-mode steps, validated against the
card surface (scripts/validate_palette.js: all checks pass). Text always uses
ink colours, never a series colour."""
from __future__ import annotations

import math
from html import escape

from markupsafe import Markup

SERIES = "#3987e5"        # stock on hand / weekly sales (slot 1, dark step)
SERIES_DIM = "#2b5f9e"    # stock on order: same blue, a step toward the surface
AVG_SHORT = "#d95926"     # recent (4-week) average (slot 2)
AVG_LONG = "#199e70"      # long (12-week) average (slot 3)
SURFACE, TRACK, BAND, GRID = "#131c2a", "#1b2636", "#2a3a52", "#223044"
INK, MUTED = "#e6edf5", "#93a1b3"
STATUS_TEXT = {"STOCKOUT_RISK": "Reorder now", "HIGH_DEMAND": "Demand rising", "OVERSTOCK_RISK": "Pause order",
               "EXCEPTION": "Needs judgment", "OK": "OK"}


def _nice_ceiling(v: float) -> float:
    if v <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(v))
    for step in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= step * mag:
            return step * mag
    return 10 * mag


def _bar(x0: float, x1: float, y: float, h: float, fill: str) -> str:
    """Horizontal bar, square at the baseline, 4px rounded data end."""
    w = x1 - x0
    if w <= 0.5:
        return ""
    r = min(4, w, h / 2)
    return (f'<path d="M{x0:.1f},{y:.1f} H{x1 - r:.1f} Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f} '
            f'V{y + h - r:.1f} Q{x1:.1f},{y + h:.1f} {x1 - r:.1f},{y + h:.1f} H{x0:.1f} Z" fill="{fill}"/>')


def stock_overview(recs: list[dict]) -> Markup:
    """One row per SKU, lowest cover first: bar = weeks of stock (on hand, then on
    order), faint band = that SKU's target range, value at the bar tip."""
    rows = sorted([r for r in recs if r.get("cover_weeks") is not None], key=lambda r: r["cover_weeks"])
    if not rows:
        return Markup("")
    W, label_w, right_w, row_h, top, bottom = 760, 200, 120, 30, 6, 26
    H = top + row_h * len(rows) + bottom
    vmax = _nice_ceiling(max(max(r["cover_weeks"], r["max_cover"]) for r in rows) * 1.05)
    x0, x1 = label_w, W - right_w
    x = lambda v: x0 + (x1 - x0) * max(0.0, min(v, vmax)) / vmax
    out = []
    step = vmax / 5
    for k in range(6):
        v = step * k
        out.append(f'<line x1="{x(v):.1f}" x2="{x(v):.1f}" y1="{top}" y2="{H - bottom}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{x(v):.1f}" y="{H - 8}" text-anchor="middle" font-size="11" fill="{MUTED}">{v:g}</text>')
    for i, r in enumerate(rows):
        y = top + i * row_h
        cy = y + row_h / 2
        cover, on_order, demand = r["cover_weeks"], r.get("on_order") or 0, r.get("weekly_demand") or 0
        hand = r["on_hand"] / demand if (on_order and demand and r.get("on_hand") is not None) else cover
        name = r["sku_name"].replace("Freshroots ", "")
        name = name if len(name) <= 22 else name[:21] + "…"
        tip = (f"{r['sku_id']} {r['sku_name']}: {cover} weeks of stock"
               + (f" ({hand:.2f} on hand + {cover - hand:.2f} on order)" if on_order else "")
               + f"; target {r['min_cover']:g}-{r['max_cover']:g} weeks; {STATUS_TEXT.get(r['status'], r['status'])}")
        out.append(f'<g><title>{escape(tip)}</title>'
                   f'<rect x="0" y="{y}" width="{W}" height="{row_h}" fill="transparent"/>'
                   f'<text x="0" y="{cy + 4:.1f}" font-size="12" fill="{INK}"><tspan font-weight="600">{escape(r["sku_id"])}</tspan>'
                   f'<tspan fill="{MUTED}"> {escape(name)}</tspan></text>'
                   f'<rect x="{x(r["min_cover"]):.1f}" y="{cy - 9:.1f}" width="{x(r["max_cover"]) - x(r["min_cover"]):.1f}" '
                   f'height="18" rx="3" fill="{BAND}"/>'
                   + _bar(x0, x(hand), cy - 6, 12, SERIES)
                   + (_bar(x(hand) + 2, x(cover), cy - 6, 12, SERIES_DIM) if on_order else "")
                   + f'<text x="{min(x(cover), x1) + 6:.1f}" y="{cy + 4:.1f}" font-size="12" fill="{INK}">{cover:g}</text>'
                   f'<text x="{W}" y="{cy + 4:.1f}" text-anchor="end" font-size="12" fill="{MUTED}">'
                   f'{escape(STATUS_TEXT.get(r["status"], r["status"]))}</text></g>')
    return Markup(f'<svg class="stock-chart" viewBox="0 0 {W} {H}" width="100%" role="img" '
                  f'aria-label="Weeks of stock for {len(rows)} SKUs against their target ranges">{"".join(out)}</svg>')


def sales_chart(history: list, avg4: float | None, avg12: float | None, short_weeks: int,
                long_weeks: int) -> Markup:
    """Weekly units sold (line) with the recent and long averages drawn over the
    weeks they cover. Hover shows each week's value; a table view sits below."""
    pts = [(i, w, u) for i, (w, u) in enumerate(history) if u is not None]
    if len(pts) < 2:
        return Markup("")
    W, H, ml, mr, mt, mb = 640, 230, 44, 128, 12, 28
    n = len(history)
    ymax = _nice_ceiling(max([u for _, _, u in pts] + [avg4 or 0, avg12 or 0]) * 1.08)
    x = lambda i: ml + (W - ml - mr) * i / max(n - 1, 1)
    yy = lambda v: mt + (H - mt - mb) * (1 - v / ymax)
    out = []
    for k in range(5):  # recessive hairline grid + clean ticks
        v = ymax * k / 4
        out.append(f'<line x1="{ml}" x2="{W - mr}" y1="{yy(v):.1f}" y2="{yy(v):.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{ml - 6}" y="{yy(v) + 4:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">{v:,.0f}</text>')
    for i, (w, _) in enumerate(history):
        if i % 2 == (n - 1) % 2:
            out.append(f'<text x="{x(i):.1f}" y="{H - 8}" text-anchor="middle" font-size="11" fill="{MUTED}">'
                       f'{escape(w.split("-")[-1])}</text>')

    # Sales line first, so the average lines stay visible where they overlap it.
    path = " ".join(f"{'M' if k == 0 else 'L'}{x(i):.1f},{yy(u):.1f}" for k, (i, _, u) in enumerate(pts))
    out.append(f'<path d="{path}" fill="none" stroke="{SERIES}" stroke-width="2" stroke-linejoin="round" '
               f'stroke-linecap="round"/>')

    labels = []
    for avg, weeks, colour, name in ((avg12, long_weeks, AVG_LONG, f"{long_weeks}-wk avg"),
                                     (avg4, short_weeks, AVG_SHORT, f"{short_weeks}-wk avg")):
        if avg is None:
            continue
        i0 = max(n - weeks, 0)
        out.append(f'<line x1="{x(i0):.1f}" x2="{x(n - 1):.1f}" y1="{yy(avg):.1f}" y2="{yy(avg):.1f}" '
                   f'stroke="{colour}" stroke-width="2" stroke-linecap="round"/>')
        labels.append([yy(avg), f"{name} {avg:,.0f}", colour])
    # Two end labels: keep them apart; a short leader ties each to its line.
    labels.sort()
    if len(labels) == 2 and labels[1][0] - labels[0][0] < 15:
        mid = (labels[0][0] + labels[1][0]) / 2
        targets = [mid - 8, mid + 8]
    else:
        targets = [lab[0] for lab in labels]
    for (ly, text, colour), ty in zip(labels, targets):
        lx = x(n - 1) + 8
        out.append(f'<path d="M{x(n - 1) + 2:.1f},{ly:.1f} L{lx:.1f},{ty:.1f} H{lx + 6:.1f}" fill="none" '
                   f'stroke="{colour}" stroke-width="1.5"/>')
        out.append(f'<text x="{lx + 10:.1f}" y="{ty + 4:.1f}" font-size="12" fill="{INK}">{escape(text)}</text>')

    li, _, lu = pts[-1]
    out.append(f'<circle cx="{x(li):.1f}" cy="{yy(lu):.1f}" r="4" fill="{SERIES}" stroke="{SURFACE}" stroke-width="2"/>')

    # Hover layer: crosshair + dot + tooltip, with hit areas wider than the marks.
    step = (W - ml - mr) / max(n - 1, 1)
    out.append(f'<line class="xh" x1="0" x2="0" y1="{mt}" y2="{H - mb}" stroke="{MUTED}" stroke-width="1" '
               'visibility="hidden"/>')
    out.append(f'<circle class="hd" r="5" fill="{SERIES}" stroke="{SURFACE}" stroke-width="2" visibility="hidden"/>')
    for i, w, u in pts:
        out.append(f'<rect x="{x(i) - step / 2:.1f}" y="{mt}" width="{step:.1f}" height="{H - mt - mb}" '
                   f'fill="transparent" data-x="{x(i):.1f}" data-y="{yy(u):.1f}" '
                   f'data-tip="{escape(w)}: {u:,} units sold"/>')
    return Markup(f'<svg class="sales-chart" viewBox="0 0 {W} {H}" width="100%" role="img" '
                  f'aria-label="Weekly units sold for the last {n} weeks">{"".join(out)}</svg>')


BELOW, ABOVE = "#f87171", "#fbbf24"  # status colours (always with a legend label): below minimum / above maximum


def projection_chart(rows: list[dict]) -> Markup:
    """Projected stock at the end of each week (bars) against the minimum and maximum
    stock for that week (lines). Red bar: below the minimum. Amber bar: above the maximum."""
    if not rows:
        return Markup("")
    W, H, ml, mr, mt, mb = 640, 250, 52, 92, 22, 30
    n = len(rows)
    # Scale to the stock and the minimum; the maximum is drawn only when it falls inside
    # that range (otherwise it flattens every bar). The legend says when it is off the chart.
    ymax = _nice_ceiling(max(max(r["stock"], r["min"]) for r in rows) * 1.25)
    slot = (W - ml - mr) / n
    bw = min(24, slot * 0.62)
    yy = lambda v: mt + (H - mt - mb) * (1 - v / ymax)
    cx = lambda i: ml + slot * (i + 0.5)
    out = []
    for k in range(5):
        v = ymax * k / 4
        out.append(f'<line x1="{ml}" x2="{W - mr}" y1="{yy(v):.1f}" y2="{yy(v):.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{ml - 6}" y="{yy(v) + 4:.1f}" text-anchor="end" font-size="11" fill="{MUTED}">{v:,.0f}</text>')
    for i, r in enumerate(rows):
        x0, base = cx(i) - bw / 2, yy(0)
        top = yy(r["stock"])
        colour = BELOW if r["stock"] < r["min"] else (ABOVE if r["stock"] > r["max"] else SERIES)
        h = base - top
        tip = (f"Week of {r['starts']:%d %b}: {r['stock']:,.0f} units at the end of the week "
               f"(demand {r['demand']:,.0f}{', festival month' if r['festival'] else ''}; "
               f"minimum {r['min']:,.0f}, maximum {r['max']:,.0f})"
               + (f"; arriving: {', '.join(r['arriving'])}" if r["arriving"] else "")
               + ("; runs out this week" if r["runs_out"] else ""))
        bar = ""
        if h > 0.5:
            rad = min(4, h, bw / 2)
            bar = (f'<path d="M{x0:.1f},{base:.1f} V{top + rad:.1f} Q{x0:.1f},{top:.1f} {x0 + rad:.1f},{top:.1f} '
                   f'H{x0 + bw - rad:.1f} Q{x0 + bw:.1f},{top:.1f} {x0 + bw:.1f},{top + rad:.1f} V{base:.1f} Z" '
                   f'fill="{colour}"/>')
        out.append(f'<g><title>{escape(tip)}</title><rect x="{cx(i) - slot / 2:.1f}" y="{mt}" width="{slot:.1f}" '
                   f'height="{H - mt - mb}" fill="transparent"/>{bar}</g>')
        if r["arriving"]:
            out.append(f'<text x="{cx(i):.1f}" y="{max(top - 6, 11):.1f}" text-anchor="middle" font-size="10" '
                       f'fill="{INK}">+{r["arrivals"]:,.0f}</text>')
        if i % 2 == 0 or n <= 8:
            out.append(f'<text x="{cx(i):.1f}" y="{H - 10}" text-anchor="middle" font-size="11" fill="{MUTED}">'
                       f'{r["starts"]:%d %b}</text>')
    # Minimum and maximum: step lines that follow each week's demand (festival months raise them).
    for key, colour, name in (("min", BELOW, "Minimum"), ("max", MUTED, "Maximum")):
        if key == "max" and any(r["max"] > ymax for r in rows):
            continue
        pts = []
        for i, r in enumerate(rows):
            pts += [(cx(i) - slot / 2, yy(r[key])), (cx(i) + slot / 2, yy(r[key]))]
        d = " ".join(f"{'M' if j == 0 else 'L'}{x:.1f},{y:.1f}" for j, (x, y) in enumerate(pts))
        out.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.5" stroke-opacity=".9"/>')
        out.append(f'<text x="{W - mr + 6}" y="{pts[-1][1] + 4:.1f}" font-size="12" fill="{INK}">{name}</text>')
    return Markup(f'<svg class="projection-chart" viewBox="0 0 {W} {H}" width="100%" role="img" '
                  f'aria-label="Projected stock for the next {n} weeks against the minimum and maximum">'
                  f'{"".join(out)}</svg>')
