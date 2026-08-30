"""Anthropic API calls: prompt assembly, schema-constrained JSON, error mapping.

The analysis runs as two calls rather than one. A single schema covering the
whole document exceeded the structured-outputs grammar limit ("The compiled
grammar is too large"); see analyzer/schema.py for the full reasoning. The
split also grounds the second call in the first: recommendations are written
against the gaps that were actually found, not inferred a second time.

The site content is identical across both calls and carries a cache breakpoint,
so the second call reads it at roughly a tenth of the input price.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time

import anthropic

from .rubric import ASSESSMENT_PROMPT, RECOMMENDATIONS_PROMPT
from .schema import ASSESSMENT_SCHEMA, RECOMMENDATIONS_SCHEMA, merge_analysis
from .site_fetcher import SiteContent

MODEL = "claude-sonnet-5"
MAX_TOKENS = 8_000
EFFORT = "medium"

# --------------------------------------------------------------- retry policy
#
# A 500 from the API is Anthropic's side, not ours, and it is nearly always
# transient — so it is worth waiting out rather than throwing away a crawl and
# (on the second call) a completed, already-billed first call.
#
# Two tiers, because they cover different failure shapes:
#
#   1. The SDK retries 408/409/429/5xx itself, fast and with exponential
#      backoff, honouring `retry-after`. That handles a single bad routing
#      attempt. The default is 2 retries; 3 costs nothing when calls succeed.
#   2. _call adds slower whole-call attempts on top, for a blip that outlives
#      the SDK's burst — the case that produced "API error 500" here.
SDK_MAX_RETRIES = 3
TRANSIENT_ATTEMPTS = 3
BACKOFF_SECONDS = (3.0, 8.0)

# Wall-clock ceiling for both analysis calls together. gunicorn kills the worker
# at 300s and the crawl may already have spent 75s, so retrying past this trades
# a useful error page for a dead connection. A call needs roughly this long to
# have any chance of finishing, so we do not start one without room for it.
ANALYSIS_BUDGET = 190.0
MIN_CALL_SECONDS = 35.0

# Infrastructure failures worth a second look. RateLimitError is deliberately
# absent: the SDK already waited out `retry-after`, and a 429 that survives that
# needs a human, not a tighter loop. APITimeoutError subclasses
# APIConnectionError, so it is covered.
_TRANSIENT = (
    anthropic.InternalServerError,  # 500-599 other than 529
    anthropic.OverloadedError,      # 529 — sibling of the above, not a subclass
    anthropic.APIConnectionError,
)

log = logging.getLogger("analyzewebsite.claude")


class AnalyzerError(Exception):
    """Raised when an analysis call fails or returns unusable output."""


def analyze_site(site: SiteContent, company_name: str | None = None) -> dict:
    """Run both calls and return the merged, normalized analysis."""
    client = _client()
    site_text = site.as_prompt_text()
    deadline = time.monotonic() + ANALYSIS_BUDGET

    if company_name:
        naming = f"The company is called {company_name}. Use this exact name in `company_name`."
    else:
        naming = (
            "The company name is not supplied — infer it from the site and put it in "
            "`company_name`."
        )

    assessment = _call(
        client,
        system=ASSESSMENT_PROMPT,
        schema=ASSESSMENT_SCHEMA,
        site_text=site_text,
        instruction=(
            f"{naming}\n\nReview the pages below and produce the assessment: what the "
            "site does well, the eight category ratings, the strengths, the gaps, and "
            "the five narrative probes."
        ),
        label="assessment",
        deadline=deadline,
    )

    findings = _findings_digest(assessment)
    recommendations = _call(
        client,
        system=RECOMMENDATIONS_PROMPT,
        schema=RECOMMENDATIONS_SCHEMA,
        site_text=site_text,
        instruction=(
            "An assessment of this site has already been completed. Its findings are "
            "below. Write the recommendations that follow from THESE findings — do not "
            "restate them and do not introduce gaps the assessment did not identify.\n\n"
            f"{findings}"
        ),
        label="recommendations",
        deadline=deadline,
    )

    return _normalize(merge_analysis(assessment, recommendations), site)


def _call(
    client,
    *,
    system: str,
    schema: dict,
    site_text: str,
    instruction: str,
    label: str,
    deadline: float,
) -> dict:
    """One analysis step, retried past a transient failure on Anthropic's side.

    Only the infrastructure failures in _TRANSIENT are retried. Everything else
    — a rejected schema, a bad key, a refusal — fails the same way on attempt
    two as on attempt one, so retrying it would only spend the user's time.
    """
    last: Exception | None = None

    for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
        try:
            return _request(
                client,
                system=system,
                schema=schema,
                site_text=site_text,
                instruction=instruction,
                label=label,
            )
        except _TRANSIENT as exc:
            last = exc
            pause = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            pause += random.uniform(0, 1)  # de-sync concurrent workers
            remaining = deadline - time.monotonic()

            if attempt == TRANSIENT_ATTEMPTS:
                log.warning("%s: %s — out of attempts", label, _describe(exc))
                break
            if remaining < pause + MIN_CALL_SECONDS:
                log.warning(
                    "%s: %s — %.0fs left, not enough to retry",
                    label,
                    _describe(exc),
                    remaining,
                )
                break

            log.warning(
                "%s: %s — retrying in %.1fs (attempt %d of %d)",
                label,
                _describe(exc),
                pause,
                attempt,
                TRANSIENT_ATTEMPTS,
            )
            time.sleep(pause)

    raise AnalyzerError(_transient_message(last, label)) from last


def _describe(exc: Exception) -> str:
    """One-line log form of an API failure, carrying the request id when there is one."""
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    parts = [f"HTTP {status}" if status else type(exc).__name__]
    message = str(getattr(exc, "message", "") or exc).strip()
    if message:
        parts.append(message)
    if request_id:
        parts.append(f"request_id={request_id}")
    return " ".join(parts)


def _transient_message(exc: Exception | None, label: str) -> str:
    """What the user is told when the retries ran out.

    Names Anthropic as the source. The previous wording — "Anthropic API error
    500: Internal server error" — read as though the site being analyzed, or
    this app, had done something wrong.
    """
    if isinstance(exc, anthropic.APITimeoutError):
        return (
            f"The {label} step timed out. The site may be large enough that the "
            "analysis cannot finish in time — try again, or use a smaller site."
        )
    if isinstance(exc, anthropic.APIConnectionError):
        return (
            f"Could not reach the Anthropic API during the {label} step. Check the "
            "server's network connection and try again."
        )

    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    detail = f" Request ID {request_id}." if request_id else ""
    busy = "is temporarily overloaded" if status == 529 else "had an internal error"
    return (
        f"The Anthropic API {busy} (HTTP {status}) and did not recover after "
        f"{TRANSIENT_ATTEMPTS} attempts, so the {label} step could not finish. "
        f"This is a fault on Anthropic's side, not a problem with the site you "
        f"entered. Please try again in a few minutes.{detail}"
    )


def _request(client, *, system: str, schema: dict, site_text: str, instruction: str, label: str) -> dict:
    """One schema-constrained request. Site content is cached across calls."""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system}],
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"COMPANY WEBSITE CONTENT\n\n{site_text}",
                            # Identical in both calls — the second reads it from cache.
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
        )
    except anthropic.BadRequestError as exc:
        message = str(getattr(exc, "message", exc))
        if "grammar" in message.lower():
            raise AnalyzerError(
                f"The {label} response schema is too complex for the API to compile. "
                "Reduce the number of fields in analyzer/schema.py."
            ) from exc
        raise AnalyzerError(f"The {label} request was rejected: {message}") from exc
    except anthropic.AuthenticationError as exc:
        raise AnalyzerError(
            "The Anthropic API key was rejected. Check the ANTHROPIC_API_KEY "
            "variable in Railway."
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise AnalyzerError(f"This API key does not have access to {MODEL}.") from exc
    except anthropic.NotFoundError as exc:
        raise AnalyzerError(f"Model '{MODEL}' is not available to this account.") from exc
    except anthropic.RateLimitError as exc:
        raise AnalyzerError(
            "The Anthropic API rate limit was hit. Wait a minute and try again."
        ) from exc
    except _TRANSIENT:
        # Anthropic's side and probably momentary. _call owns the decision to
        # wait and try again, so this must stay an exception, not a message.
        # Listed before APIStatusError, which would otherwise swallow the 5xx.
        raise
    except anthropic.APIStatusError as exc:
        raise AnalyzerError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc

    if response.stop_reason == "refusal":
        raise AnalyzerError(f"The model declined the {label} step for this site.")
    if response.stop_reason == "max_tokens":
        raise AnalyzerError(
            f"The {label} step was cut off before it finished. Try a site with fewer pages."
        )

    log.info(
        "%s: in=%s cache_read=%s out=%s",
        label,
        response.usage.input_tokens,
        response.usage.cache_read_input_tokens,
        response.usage.output_tokens,
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text.strip():
        raise AnalyzerError(f"The {label} step returned an empty response.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnalyzerError(f"The {label} step returned invalid JSON: {exc}") from exc


def _findings_digest(assessment: dict) -> str:
    """Compact summary of call 1, fed to call 2 as grounding."""
    lines = [
        f"COMPANY: {assessment.get('company_name', '')}",
        f"SECTOR: {assessment.get('sector', '')}",
        f"READINESS SCORE: {assessment.get('readiness_score', '')}/10",
        f"POSITIONING: {assessment.get('who_the_site_serves', '')}",
        "",
        "CATEGORY RATINGS:",
    ]
    lines += [
        f"- {row.get('category', '')}: {row.get('rating', '')}"
        for row in assessment.get("scorecard") or []
    ]
    lines += ["", "STRENGTHS:"]
    lines += [f"- {s.get('title', '')}: {s.get('body', '')}" for s in assessment.get("whats_working") or []]
    lines += ["", "GAPS (most material first):"]
    for gap in assessment.get("gaps") or []:
        missing = ", ".join(gap.get("missing_items") or [])
        flag = " [LARGEST CONCERN]" if gap.get("is_largest_concern") else ""
        lines.append(f"- {gap.get('title', '')}{flag}: {gap.get('framing', '')} Missing: {missing}")
    lines += ["", "NARRATIVE COVERAGE:"]
    lines += [
        f"- {row.get('question', '')} {row.get('coverage', '')}"
        for row in assessment.get("narrative_probes") or []
    ]
    return "\n".join(lines)


def _client() -> anthropic.Anthropic:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise AnalyzerError(
            "ANTHROPIC_API_KEY is not configured on the server. Add it in "
            "Railway → your service → Variables, then redeploy."
        )
    return anthropic.Anthropic(max_retries=SDK_MAX_RETRIES)


def _normalize(data: dict, site: SiteContent) -> dict:
    """Clamp what the schema cannot constrain and attach crawl provenance."""
    summary = data.setdefault("executive_summary", {})
    try:
        score = round(float(summary.get("readiness_score", 0)), 1)
    except (TypeError, ValueError):
        score = 0.0
    summary["readiness_score"] = max(1.0, min(10.0, score))

    gaps = data.get("gaps") or []
    flagged = False
    for gap in gaps:
        if gap.get("is_largest_concern") and not flagged:
            flagged = True
        else:
            gap["is_largest_concern"] = False
    if gaps and not flagged:
        gaps[0]["is_largest_concern"] = True

    data["website_url"] = site.root_url
    data["pages_reviewed"] = [p.url for p in site.pages]
    return data
