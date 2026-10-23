"""The dashboard's two charts, rendered server-side as inline SVG.

1. cover_gauge: weeks of stock against the SKU's target range (landing page rows).
2. sales_chart: weekly units sold with the 4- and 12-week averages (SKU page).

Colours are the validated reference chart palette (slot 1 blue, slot 2 orange,
slot 3 aqua); text always uses ink colours, never the series colour."""
from __future__ import annotations

import math
from html import escape

from markupsafe import Markup

SERIES = "#2a78d6"        # weekly sales / stock on hand
SERIES_LIGHT = "#a9c9f0"  # lighter step of the same blue: stock on order
AVG_SHORT = "#eb6834"     # recent (4-week) average
AVG_LONG = "#1baf7a"      # long (12-week) average
TRACK, BAND, GRID = "#eef1ec", "#d3dccf", "#e6ebe4"
INK, MUTED, SURFACE = "#1c2a20", "#5d6b60", "#ffffff"


def _bar(x0: float, x1: float, y: float, h: float, fill: str) -> str:
    """Horizontal bar, square at the baseline, 4px rounded data end."""
    w = max(x1 - x0, 0)
    if w <= 0:
        return ""
    r = min(4, w, h / 2)
    return (f'<path d="M{x0:.1f},{y:.1f} H{x1 - r:.1f} Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f} '
            f'V{y + h - r:.1f} Q{x1:.1f},{y + h:.1f} {x1 - r:.1f},{y + h:.1f} H{x0:.1f} Z" fill="{fill}"/>')


def cover_gauge(r: dict) -> Markup:
    cover = r.get("cover_weeks")
    if cover is None:
        return Markup("")
    lo, hi = r["min_cover"], r["max_cover"]
    on_order, demand = r.get("on_order") or 0, r.get("weekly_demand") or 0
    on_hand_cover = (r["on_hand"] / demand) if (on_order and demand and r.get("on_hand") is not None) else cover
    W, pad, y, h = 240, 1, 3, 10
    top = max(hi * 1.5, cover * 1.12, lo + 1)
    x = lambda v: pad + (W - 2 * pad) * max(0.0, min(v, top)) / top
    title = f"{cover} weeks of stock; target {lo:g} to {hi:g} weeks"
    parts = [
        f'<rect x="{pad}" y="{y}" width="{W - 2 * pad}" height="{h}" rx="5" fill="{TRACK}"/>',
        f'<rect x="{x(lo):.1f}" y="{y}" width="{x(hi) - x(lo):.1f}" height="{h}" fill="{BAND}">'
        f'<title>Target range {lo:g} to {hi:g} weeks</title></rect>',
    ]
    if on_order:
        parts.append(_bar(x(0), x(on_hand_cover), y + 2, h - 4, SERIES))
        parts.append(_bar(x(on_hand_cover) + 2, x(cover), y + 2, h - 4, SERIES_LIGHT))
        title += f" ({on_hand_cover:.2f} from stock on hand, the rest on order)"
    else:
        parts.append(_bar(x(0), x(cover), y + 2, h - 4, SERIES))
    for v in (lo, hi):
        parts.append(f'<text x="{x(v):.1f}" y="26" text-anchor="middle" font-size="10" fill="{MUTED}">{v:g}</text>')
    parts.append(f'<text x="{W - pad}" y="26" text-anchor="end" font-size="10" fill="{MUTED}">wks</text>')
    svg = (f'<svg class="gauge" viewBox="0 0 {W} 30" width="100%" style="max-width:{W}px" role="img" '
           f'aria-label="{escape(title)}"><title>{escape(title)}</title>{"".join(parts)}</svg>')
    return Markup(svg)


def _nice_ceiling(v: float) -> float:
    if v <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(v))
    for step in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= step * mag:
            return step * mag
    return 10 * mag


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
    svg = (f'<svg class="sales-chart" viewBox="0 0 {W} {H}" width="100%" role="img" '
           f'aria-label="Weekly units sold for the last {n} weeks">{"".join(out)}</svg>')
    return Markup(svg)
