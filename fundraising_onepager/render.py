"""Single-page PDF layout via reportlab.

One page is a hard constraint, so layout runs in two passes: measure every
block, then drop lowest-priority items until the content fits the available
height. Nothing is ever drawn past the page boundary and showPage() is called
exactly once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas

from schema import Analysis, Gap

# --- geometry ---------------------------------------------------------------
PAGE_W, PAGE_H = LETTER
MARGIN = 0.5 * inch
CONTENT_W = PAGE_W - 2 * MARGIN
COL_GUTTER = 0.28 * inch
COL_W = (CONTENT_W - COL_GUTTER) / 2

HEADER_H = 0.86 * inch
SUMMARY_GAP = 0.20 * inch
FOOTER_H = 1.02 * inch
FOOTER_GAP = 0.16 * inch

# --- type -------------------------------------------------------------------
F_REG, F_BOLD = "Helvetica", "Helvetica-Bold"
SZ_TITLE, SZ_SUBTITLE = 17, 9.5
SZ_SECTION, SZ_LABEL, SZ_BODY, SZ_FINE = 10.5, 9, 9, 7.5
LEAD_BODY = 10.8
LEAD_FINE = 9.0
ITEM_GAP = 5.0
SECTION_GAP = 11.0

# --- color ------------------------------------------------------------------
INK = HexColor("#12161C")
MUTED = HexColor("#5A6472")
RULE = HexColor("#D8DEE6")
PANEL = HexColor("#F4F6F9")
ACCENT = HexColor("#1F4E79")
SEVERITY_COLORS = {
    "high": HexColor("#C0392B"),
    "med": HexColor("#C8860D"),
    "low": HexColor("#5A6472"),
}
SEVERITY_LABEL = {"high": "HIGH", "med": "MED", "low": "LOW"}

MIN_ITEMS_PER_SECTION = 2


class RenderError(Exception):
    """Raised when the PDF cannot be written."""


# --------------------------------------------------------------------------- text


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap. Over-long words are hard-split so nothing overflows."""
    words = re.sub(r"\s+", " ", text or "").strip().split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if not word:
            continue
        trial = f"{current} {word}".strip()
        if pdfmetrics.stringWidth(trial, font, size) <= width:
            current = trial
            continue
        if current:
            lines.append(current)
        while pdfmetrics.stringWidth(word, font, size) > width:
            cut = len(word)
            while cut > 1 and pdfmetrics.stringWidth(word[:cut], font, size) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines or [""]


@dataclass
class Item:
    """One measured, drawable bullet: a bold label plus wrapped body text."""

    label: str
    lines: list[str]
    label_color: object = INK
    tag: str = ""          # right-aligned severity chip, e.g. "HIGH"
    tag_color: object = MUTED

    @property
    def height(self) -> float:
        return LEAD_BODY + len(self.lines) * LEAD_BODY + ITEM_GAP


def _item(label: str, body: str, width: float, **kw) -> Item:
    return Item(label=label, lines=_wrap(body, F_REG, SZ_BODY, width), **kw)


# --------------------------------------------------------------------------- fitting


@dataclass
class Section:
    title: str
    items: list[Item]

    def height(self) -> float:
        if not self.items:
            return 0.0
        return SECTION_GAP + LEAD_BODY + sum(i.height for i in self.items)


def _fit(sections: list[Section], budget: float) -> int:
    """Drop lowest-priority items until the sections fit `budget`.

    Items are ordered most-important-first by the rubric, so dropping from the
    tail drops the least important. Each section keeps MIN_ITEMS_PER_SECTION
    while any other section still has surplus, so no section is starved.
    Returns the number of items dropped.
    """
    dropped = 0
    while sum(s.height() for s in sections) > budget:
        surplus = [s for s in sections if len(s.items) > MIN_ITEMS_PER_SECTION]
        pool = surplus or [s for s in sections if len(s.items) > 1]
        if not pool:
            # Every section is down to one item and it still overflows. Drop
            # body lines from the tallest item rather than spilling to page 2.
            tallest = max(
                (i for s in sections for i in s.items),
                key=lambda i: len(i.lines),
                default=None,
            )
            if tallest is None or len(tallest.lines) <= 1:
                break
            tallest.lines.pop()
            continue
        pool.sort(key=lambda s: len(s.items), reverse=True)
        pool[0].items.pop()
        dropped += 1
    return dropped


# --------------------------------------------------------------------------- draw


def render(analysis: Analysis, out_path: Path) -> Path:
    """Write the one-page assessment. Returns the path written."""
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        c = rl_canvas.Canvas(str(out_path), pagesize=LETTER)
        c.setTitle(f"{analysis.company} — Fundraising Readiness Assessment")
        c.setAuthor("Fundraising Readiness One-Pager")

        summary_h = _draw_header(c, analysis)
        body_top = PAGE_H - MARGIN - HEADER_H - summary_h - SUMMARY_GAP
        body_bottom = MARGIN + FOOTER_H + FOOTER_GAP
        budget = body_top - body_bottom

        left = [
            Section("WHAT'S WORKING", [
                _item(s.area, s.point, COL_W) for s in analysis.whats_working
            ]),
            Section("PRESENTATION & UX", [
                _item(u.element, u.suggestion, COL_W) for u in analysis.presentation_ux
            ]),
        ]
        right = [
            Section("GAPS & WEAKNESSES", [
                _gap_item(g) for g in sorted(analysis.gaps_weaknesses, key=lambda g: g.rank)
            ]),
            Section("ACTIONABLE IMPROVEMENTS", [
                _item(i.recommendation, i.why_it_matters, COL_W)
                for i in analysis.actionable_improvements
            ]),
        ]

        dropped = _fit(left, budget) + _fit(right, budget)

        _draw_column(c, MARGIN, body_top, left)
        _draw_column(c, MARGIN + COL_W + COL_GUTTER, body_top, right)
        _draw_footer(c, analysis, dropped)

        c.showPage()
        c.save()
    except OSError as exc:
        raise RenderError(f"Could not write {out_path}: {exc}") from exc
    return out_path


def _gap_item(gap: Gap) -> Item:
    color = SEVERITY_COLORS.get(gap.severity, MUTED)
    # Reserve room on the label line for the severity chip.
    return Item(
        label=gap.area,
        lines=_wrap(gap.point, F_REG, SZ_BODY, COL_W),
        label_color=color,
        tag=SEVERITY_LABEL.get(gap.severity, gap.severity.upper()),
        tag_color=color,
    )


def _draw_header(c: rl_canvas.Canvas, a: Analysis) -> float:
    top = PAGE_H - MARGIN
    badge_w, badge_h = 1.12 * inch, 0.62 * inch
    badge_x = PAGE_W - MARGIN - badge_w

    c.setFillColor(INK)
    c.setFont(F_BOLD, SZ_TITLE)
    name_w = badge_x - MARGIN - 0.15 * inch
    name = _truncate(a.company, F_BOLD, SZ_TITLE, name_w)
    c.drawString(MARGIN, top - 16, name)

    c.setFillColor(MUTED)
    c.setFont(F_REG, SZ_SUBTITLE)
    c.drawString(MARGIN, top - 30, "Fundraising Readiness Assessment")
    c.setFont(F_REG, SZ_FINE)
    c.drawString(MARGIN, top - 42, _today())

    # Score badge
    c.setFillColor(_score_color(a.readiness_score))
    c.roundRect(badge_x, top - badge_h, badge_w, badge_h, 5, stroke=0, fill=1)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(F_BOLD, 24)
    c.drawCentredString(badge_x + badge_w / 2, top - badge_h + 22, str(a.readiness_score))
    c.setFont(F_REG, 6.8)
    c.drawCentredString(badge_x + badge_w / 2, top - badge_h + 10, "READINESS / 100")

    # Summary line(s)
    y = top - HEADER_H
    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.line(MARGIN, y + 8, PAGE_W - MARGIN, y + 8)

    c.setFillColor(INK)
    c.setFont(F_REG, 9.5)
    lines = _wrap(a.one_line_summary, F_REG, 9.5, CONTENT_W)[:2]
    for i, line in enumerate(lines):
        c.drawString(MARGIN, y - 4 - i * 11.5, line)
    return len(lines) * 11.5


def _draw_column(c: rl_canvas.Canvas, x: float, top: float, sections: list[Section]) -> None:
    y = top
    for section in sections:
        if not section.items:
            continue
        y -= SECTION_GAP
        c.setFillColor(ACCENT)
        c.setFont(F_BOLD, SZ_SECTION)
        c.drawString(x, y, section.title)
        c.setStrokeColor(ACCENT)
        c.setLineWidth(0.6)
        c.line(x, y - 3.5, x + COL_W, y - 3.5)
        y -= LEAD_BODY

        for item in section.items:
            y -= LEAD_BODY
            c.setFillColor(item.label_color)
            c.setFont(F_BOLD, SZ_LABEL)
            tag_w = 0.0
            if item.tag:
                tag_w = pdfmetrics.stringWidth(item.tag, F_BOLD, SZ_FINE) + 4
            c.drawString(x, y, _truncate(item.label, F_BOLD, SZ_LABEL, COL_W - tag_w))
            if item.tag:
                c.setFillColor(item.tag_color)
                c.setFont(F_BOLD, SZ_FINE)
                c.drawRightString(x + COL_W, y, item.tag)

            c.setFillColor(INK)
            c.setFont(F_REG, SZ_BODY)
            for line in item.lines:
                y -= LEAD_BODY
                c.drawString(x, y, line)
            y -= ITEM_GAP


def _draw_footer(c: rl_canvas.Canvas, a: Analysis, dropped: int) -> None:
    x, y = MARGIN, MARGIN
    c.setFillColor(PANEL)
    c.roundRect(x, y, CONTENT_W, FOOTER_H, 4, stroke=0, fill=1)

    pad = 8.0
    inner_w = CONTENT_W - 2 * pad
    cursor = y + FOOTER_H - pad - 2

    c.setFillColor(ACCENT)
    c.setFont(F_BOLD, 9)
    heading = f"SEC / REGULATION D  —  reads as {a.sec_considerations.applicable_reg}"
    c.drawString(x + pad, cursor, heading)
    cursor -= 11

    # One line per note, ellipsised. Wrapping here would silently swallow the
    # tail of a note when the strip runs out of vertical room.
    disclaimer_h = LEAD_FINE + 2
    c.setFillColor(INK)
    c.setFont(F_REG, 8)
    for note in a.sec_considerations.notes:
        if cursor - LEAD_FINE < y + pad + disclaimer_h:
            break
        cursor -= LEAD_FINE
        c.drawString(x + pad, cursor, _truncate(f"• {note}", F_REG, 8, inner_w))

    c.setFillColor(MUTED)
    c.setFont(F_REG, 6.8)
    note = (
        "Generated analysis. Not legal, financial, or investment advice — "
        "confirm all securities matters with qualified counsel."
    )
    if dropped:
        note += f"  ({dropped} lower-priority item{'s' if dropped != 1 else ''} omitted to fit one page.)"
    c.drawString(x + pad, y + pad, _truncate(note, F_REG, 6.8, inner_w))


def _score_color(score: int):
    if score >= 70:
        return HexColor("#1E7A46")
    if score >= 45:
        return HexColor("#C8860D")
    return HexColor("#C0392B")


def _truncate(text: str, font: str, size: float, width: float) -> str:
    text = (text or "").strip()
    if pdfmetrics.stringWidth(text, font, size) <= width:
        return text
    while text and pdfmetrics.stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def _today() -> str:
    d = date.today()
    return f"{d.strftime('%B')} {d.day}, {d.year}"
