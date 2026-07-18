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

import anthropic

from .rubric import ASSESSMENT_PROMPT, RECOMMENDATIONS_PROMPT
from .schema import ASSESSMENT_SCHEMA, RECOMMENDATIONS_SCHEMA, merge_analysis
from .site_fetcher import SiteContent

MODEL = "claude-sonnet-5"
MAX_TOKENS = 8_000
EFFORT = "medium"

log = logging.getLogger("analyzewebsite.claude")


class AnalyzerError(Exception):
    """Raised when an analysis call fails or returns unusable output."""


def analyze_site(site: SiteContent, company_name: str | None = None) -> dict:
    """Run both calls and return the merged, normalized analysis."""
    client = _client()
    site_text = site.as_prompt_text()

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
    )

    return _normalize(merge_analysis(assessment, recommendations), site)


def _call(client, *, system: str, schema: dict, site_text: str, instruction: str, label: str) -> dict:
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
    except anthropic.APIStatusError as exc:
        raise AnalyzerError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AnalyzerError(f"Could not reach the Anthropic API: {exc}") from exc

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
    return anthropic.Anthropic()


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
