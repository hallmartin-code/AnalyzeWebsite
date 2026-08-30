"""Resend email notification for every completed analysis.

Each run of /analyze e-mails a summary of the analysis plus the generated .docx
to ANALYSIS_EMAIL_TO (default Info@tencapital.group). The send is a side effect,
never a dependency: send_analysis_email_async() runs on a daemon thread so a
Resend outage can neither delay nor fail the user's document download.

Environment variables (set these in Railway -> your service -> Variables):

  RESEND_API_KEY      Required. Without it, notification is silently disabled.
  ANALYSIS_EMAIL_TO   Comma-separated recipients. Default Info@tencapital.group.
  ANALYSIS_EMAIL_FROM Sender. Must be an address on a domain verified at
                      resend.com/domains; tencapital.group is verified, so the
                      default sends as analyzer@tencapital.group.
  ANALYSIS_EMAIL_CC   Optional, comma-separated.
"""

from __future__ import annotations

import base64
import html
import logging
import os
import threading

import requests

RESEND_ENDPOINT = "https://api.resend.com/emails"
SEND_TIMEOUT = 20  # seconds

DEFAULT_TO = "Info@tencapital.group"
DEFAULT_FROM = "TEN Capital Website Analyzer <analyzer@tencapital.group>"

DOCX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

# Scorecard keys are snake_case on the merged payload; these are the labels the
# document uses, kept in the document's order.
SCORECARD_LABELS = [
    ("scientific_technical_credibility", "Scientific / Technical Credibility"),
    ("founder_credibility", "Founder Credibility"),
    ("product_positioning", "Product Positioning"),
    ("market_opportunity_communication", "Market Opportunity Communication"),
    ("commercialization_story", "Commercialization Story"),
    ("traction_evidence", "Traction Evidence"),
    ("investor_readiness", "Investor Readiness"),
    ("fundraising_supportiveness", "Fundraising Supportiveness"),
]

PROBE_LABELS = [
    ("why_now", "Why Now?"),
    ("why_this_team", "Why This Team?"),
    ("why_this_market", "Why This Market?"),
    ("why_this_product_wins", "Why This Product Wins?"),
    ("why_this_becomes_large", "Why This Becomes Large?"),
]

# Rating -> accent colour, so the scorecard reads at a glance in the inbox.
RATING_COLORS = {
    "Strong": "#1a7f4b",
    "Good": "#2f8f5b",
    "Moderate": "#b07d10",
    "Weak-Moderate": "#c2600f",
    "Weak": "#b3261e",
}

FONT = "'Open Sans',Arial,sans-serif"

log = logging.getLogger("analyzewebsite.email")


class EmailError(Exception):
    """Raised when Resend rejects or cannot receive the notification."""


# -------------------------------------------------------------------- config


def _recipients(var: str, default: str = "") -> list[str]:
    raw = os.getenv(var) or default
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def analysis_recipients() -> list[str]:
    """The addresses a completed analysis is mailed to."""
    return _recipients("ANALYSIS_EMAIL_TO", DEFAULT_TO)


def email_configured() -> bool:
    """True when a Resend key is present and at least one recipient is set."""
    return bool(os.getenv("RESEND_API_KEY")) and bool(analysis_recipients())


# --------------------------------------------------------------- html helpers


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _score_color(score) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "#57534e"
    if value >= 8:
        return "#1a7f4b"
    if value >= 6.5:
        return "#2f8f5b"
    if value >= 5:
        return "#b07d10"
    return "#b3261e"


def _section(title: str, body: str) -> str:
    return (
        '<tr><td style="padding:22px 28px 0 28px;">'
        f'<div style="font:600 11px/1.4 {FONT};letter-spacing:.09em;'
        'text-transform:uppercase;color:#78716c;border-bottom:1px solid #e7e5e4;'
        f'padding-bottom:6px;margin-bottom:10px;">{_esc(title)}</div>'
        f"{body}</td></tr>"
    )


def _paragraph(text: str) -> str:
    return (
        f'<p style="margin:0 0 10px 0;font:400 13px/1.6 {FONT};color:#292524;">'
        f"{_esc(text)}</p>"
    )


def _bullets(items, limit: int = 8) -> str:
    rows = [i for i in (items or []) if str(i).strip()][:limit]
    if not rows:
        return ""
    lis = "".join(f'<li style="margin:0 0 5px 0;">{_esc(item)}</li>' for item in rows)
    return (
        f'<ul style="margin:0 0 10px 0;padding-left:18px;font:400 13px/1.6 {FONT};'
        f'color:#292524;">{lis}</ul>'
    )


def _kv_table(rows: list[tuple[str, str, str]]) -> str:
    """rows = (label, value, value colour)."""
    trs = []
    for label, value, color in rows:
        trs.append(
            "<tr>"
            f'<td style="padding:5px 12px 5px 0;font:400 13px/1.5 {FONT};color:#44403c;'
            f'border-bottom:1px solid #f5f5f4;">{_esc(label)}</td>'
            f'<td style="padding:5px 0;font:600 13px/1.5 {FONT};color:{color};'
            'border-bottom:1px solid #f5f5f4;text-align:right;white-space:nowrap;">'
            f"{_esc(value)}</td>"
            "</tr>"
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;border-collapse:collapse;">{"".join(trs)}</table>'
    )


# -------------------------------------------------------------- body assembly


def build_summary_html(
    data: dict,
    *,
    source_url: str,
    analysis_date: str,
    pages_reviewed: int | None = None,
    filename: str = "",
) -> str:
    """Render the merged analysis payload as a self-contained HTML email body."""
    company = data.get("company_name") or "Unknown company"
    sector = data.get("sector") or ""
    summary = data.get("executive_summary") or {}
    score = summary.get("readiness_score")
    overall = data.get("overall_assessment") or {}

    try:
        score_text = f"{float(score):.1f} / 10"
    except (TypeError, ValueError):
        score_text = "—"

    meta_bits = [f"Analyzed {_esc(analysis_date)}"]
    if pages_reviewed:
        plural = "s" if pages_reviewed != 1 else ""
        meta_bits.append(f"{pages_reviewed} page{plural} reviewed")

    parts: list[str] = []

    # Header block: company, sector, readiness score.
    sector_line = (
        f'<div style="font:400 13px/1.5 {FONT};color:#d6d3d1;margin-top:3px;">'
        f"{_esc(sector)}</div>"
        if sector
        else ""
    )
    parts.append(
        '<tr><td style="padding:28px 28px 4px 28px;background:#1c1917;">'
        f'<div style="font:600 10px/1.4 {FONT};letter-spacing:.14em;'
        'text-transform:uppercase;color:#a8a29e;">'
        "TEN Capital · Website Analysis</div>"
        f'<div style="font:700 24px/1.3 {FONT};color:#ffffff;margin-top:8px;">'
        f"{_esc(company)}</div>"
        f"{sector_line}"
        '<div style="margin-top:16px;padding-bottom:24px;">'
        '<span style="display:inline-block;background:#ffffff;border-radius:4px;'
        f'padding:8px 14px;font:700 18px/1 {FONT};color:{_score_color(score)};">'
        f"{_esc(score_text)}</span>"
        f'<span style="display:inline-block;font:400 11px/1.4 {FONT};color:#a8a29e;'
        'padding-left:10px;">Investor readiness</span>'
        "</div></td></tr>"
    )

    # Source line.
    parts.append(
        '<tr><td style="padding:14px 28px;background:#fafaf9;'
        f'border-bottom:1px solid #e7e5e4;font:400 12px/1.6 {FONT};color:#57534e;">'
        f'<a href="{_esc(source_url)}" style="color:#1c1917;">{_esc(source_url)}</a><br>'
        f'{" · ".join(meta_bits)}'
        "</td></tr>"
    )

    if summary.get("what_the_site_does_well"):
        parts.append(
            _section(
                "What the site does well", _paragraph(summary["what_the_site_does_well"])
            )
        )
    if summary.get("who_the_site_serves"):
        parts.append(
            _section("Who the site serves", _paragraph(summary["who_the_site_serves"]))
        )

    # Scorecard.
    scorecard = data.get("scorecard") or {}
    score_rows = [
        (
            label,
            scorecard.get(key, "—"),
            RATING_COLORS.get(scorecard.get(key, ""), "#57534e"),
        )
        for key, label in SCORECARD_LABELS
    ]
    parts.append(_section("Scorecard", _kv_table(score_rows)))

    # Narrative probes.
    probes = data.get("narrative_probes") or {}
    probe_rows = [
        (label, probes.get(key, "—"), "#44403c") for key, label in PROBE_LABELS
    ]
    parts.append(_section("Narrative coverage", _kv_table(probe_rows)))

    # Gaps, largest concern first.
    gaps = data.get("gaps") or []
    if gaps:
        ordered = sorted(gaps, key=lambda g: not g.get("is_largest_concern"))
        blocks = []
        for gap in ordered[:5]:
            flag = (
                '<span style="display:inline-block;background:#fef2f2;color:#b3261e;'
                f'border-radius:3px;padding:1px 6px;margin-left:6px;font:600 10px/1.6 {FONT};'
                'letter-spacing:.06em;text-transform:uppercase;">Largest concern</span>'
                if gap.get("is_largest_concern")
                else ""
            )
            question = (
                f'<div style="font:400 12px/1.6 {FONT};color:#78716c;margin-top:3px;">'
                f"<em>Investor question:</em> {_esc(gap.get('investor_question'))}</div>"
                if gap.get("investor_question")
                else ""
            )
            blocks.append(
                '<div style="margin-bottom:12px;">'
                f'<div style="font:600 13px/1.5 {FONT};color:#1c1917;">'
                f"{_esc(gap.get('title'))}{flag}</div>"
                f'<div style="font:400 13px/1.6 {FONT};color:#44403c;">'
                f"{_esc(gap.get('framing'))}</div>"
                f"{question}</div>"
            )
        parts.append(_section(f"Gaps ({len(gaps)})", "".join(blocks)))

    # Priority recommendations.
    improvements = data.get("improvements") or []
    if improvements:
        blocks = []
        for index, item in enumerate(improvements[:6], start=1):
            blocks.append(
                '<div style="margin-bottom:10px;">'
                f'<div style="font:600 13px/1.5 {FONT};color:#1c1917;">'
                f"Priority #{index} — {_esc(item.get('title'))}</div>"
                f'<div style="font:400 13px/1.6 {FONT};color:#44403c;">'
                f"{_esc(item.get('intro'))}</div></div>"
            )
        parts.append(_section("Recommended priorities", "".join(blocks)))

    # Overall assessment.
    overall_body = ""
    if overall.get("primary_weakness"):
        overall_body += _paragraph(overall["primary_weakness"])
    if overall.get("required_fixes"):
        overall_body += _bullets(overall["required_fixes"])
    if overall.get("target_score_range"):
        overall_body += _paragraph(
            f"Achievable range after these fixes: {overall['target_score_range']}."
        )
    if overall.get("target_investor_types"):
        overall_body += _paragraph(
            "Target investors: " + ", ".join(overall["target_investor_types"])
        )
    if overall_body:
        parts.append(_section("Overall assessment", overall_body))

    # Footer.
    attachment_note = (
        f"The full analysis is attached as <strong>{_esc(filename)}</strong>."
        if filename
        else "The full analysis is attached."
    )
    parts.append(
        '<tr><td style="padding:22px 28px 26px 28px;">'
        '<div style="border-top:1px solid #e7e5e4;padding-top:14px;'
        f'font:400 12px/1.6 {FONT};color:#78716c;">'
        f"{attachment_note}<br>"
        f"Compiled on {_esc(analysis_date)} by TEN Capital Network."
        "</div></td></tr>"
    )

    return (
        '<div style="background:#f5f5f4;padding:24px 12px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:640px;margin:0 auto;width:100%;background:#ffffff;'
        'border:1px solid #e7e5e4;border-radius:6px;border-collapse:separate;'
        'overflow:hidden;">'
        + "".join(parts)
        + "</table></div>"
    )


def build_summary_text(data: dict, *, source_url: str, analysis_date: str) -> str:
    """Plain-text alternative for clients that will not render HTML."""
    summary = data.get("executive_summary") or {}
    scorecard = data.get("scorecard") or {}
    overall = data.get("overall_assessment") or {}
    try:
        score_text = f"{float(summary.get('readiness_score')):.1f} / 10"
    except (TypeError, ValueError):
        score_text = "—"

    lines = [
        f"TEN CAPITAL - WEBSITE ANALYSIS: {data.get('company_name') or 'Unknown company'}",
        f"Sector: {data.get('sector') or '-'}",
        f"Site: {source_url}",
        f"Investor readiness: {score_text}",
        f"Analyzed: {analysis_date}",
        "",
        "SCORECARD",
    ]
    lines += [f"  {label}: {scorecard.get(key, '-')}" for key, label in SCORECARD_LABELS]

    gaps = data.get("gaps") or []
    if gaps:
        lines += ["", f"GAPS ({len(gaps)})"]
        for gap in sorted(gaps, key=lambda g: not g.get("is_largest_concern"))[:5]:
            marker = " [LARGEST CONCERN]" if gap.get("is_largest_concern") else ""
            lines.append(f"  - {gap.get('title')}{marker}")

    improvements = data.get("improvements") or []
    if improvements:
        lines += ["", "RECOMMENDED PRIORITIES"]
        for index, item in enumerate(improvements[:6], start=1):
            lines.append(f"  {index}. {item.get('title')}")

    if overall.get("target_score_range"):
        lines += ["", f"Achievable range after fixes: {overall['target_score_range']}"]

    lines += [
        "",
        "The full analysis is attached as a .docx.",
        f"Compiled on {analysis_date} by TEN Capital Network.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------- sending


def send_analysis_email(
    data: dict,
    docx_bytes: bytes,
    *,
    filename: str,
    source_url: str,
    analysis_date: str,
    pages_reviewed: int | None = None,
) -> str:
    """Send the summary plus the .docx through Resend; return the message id.

    Raises EmailError on any failure, including missing configuration.
    """
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        raise EmailError("RESEND_API_KEY is not set; analysis email not sent.")

    to = _recipients("ANALYSIS_EMAIL_TO", DEFAULT_TO)
    if not to:
        raise EmailError("ANALYSIS_EMAIL_TO is empty; analysis email not sent.")

    company = data.get("company_name") or "Unknown company"
    summary = data.get("executive_summary") or {}
    try:
        score_text = f"{float(summary.get('readiness_score')):.1f}/10"
    except (TypeError, ValueError):
        score_text = "no score"

    payload = {
        "from": os.getenv("ANALYSIS_EMAIL_FROM") or DEFAULT_FROM,
        "to": to,
        "subject": f"Website Analysis — {company} ({score_text})",
        "html": build_summary_html(
            data,
            source_url=source_url,
            analysis_date=analysis_date,
            pages_reviewed=pages_reviewed,
            filename=filename,
        ),
        "text": build_summary_text(
            data, source_url=source_url, analysis_date=analysis_date
        ),
        "attachments": [
            {
                "filename": filename,
                "content": base64.b64encode(docx_bytes).decode("ascii"),
                "content_type": DOCX_MIMETYPE,
            }
        ],
    }
    cc = _recipients("ANALYSIS_EMAIL_CC")
    if cc:
        payload["cc"] = cc

    try:
        response = requests.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=SEND_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise EmailError(f"Could not reach Resend: {exc}") from exc

    if response.status_code >= 400:
        # Resend returns {"name": ..., "message": ...} on error. Log only the
        # response — the request body carries the whole base64 document.
        raise EmailError(f"Resend returned {response.status_code}: {response.text[:400]}")

    try:
        return (response.json() or {}).get("id", "")
    except ValueError:
        return ""


def send_analysis_email_async(*args, **kwargs) -> None:
    """Fire-and-forget wrapper. Never raises; failures are logged only.

    The .docx download is the user's deliverable, so a Resend problem must not
    delay the response or surface as a request error.
    """
    if not email_configured():
        log.info("analysis email skipped: RESEND_API_KEY or ANALYSIS_EMAIL_TO not set")
        return

    def run() -> None:
        try:
            message_id = send_analysis_email(*args, **kwargs)
            log.info("analysis email sent id=%s", message_id or "(none)")
        except EmailError as exc:
            log.warning("analysis email failed: %s", exc)
        except Exception:  # noqa: BLE001 - a background thread must not die loudly
            log.exception("unexpected failure sending analysis email")

    threading.Thread(target=run, name="resend-analysis-email", daemon=True).start()
