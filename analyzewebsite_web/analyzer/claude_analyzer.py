"""Anthropic API call: prompt assembly, schema-constrained JSON, error mapping."""

from __future__ import annotations

import json
import os

import anthropic

from .rubric import SYSTEM_PROMPT
from .schema import ANALYSIS_SCHEMA
from .site_fetcher import SiteContent

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16_000
EFFORT = "medium"


class AnalyzerError(Exception):
    """Raised when the analysis call fails or returns unusable output."""


def analyze_site(site: SiteContent, company_name: str | None = None) -> dict:
    """Send the crawled pages to Claude; return validated, normalized analysis."""
    client = _client()

    if company_name:
        opening = (
            f"The company is called {company_name}. Use this exact name in `company_name`."
        )
    else:
        opening = (
            "The company name is not supplied — infer it from the site and put it in "
            "`company_name`."
        )

    prompt = (
        f"{opening}\n\n"
        "Review the following pages from the company's website and produce the "
        "Website Analysis.\n\n"
        f"{site.as_prompt_text()}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # Identical on every run — cache it.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA},
            },
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as exc:
        raise AnalyzerError(
            "The Anthropic API key was rejected. Check the ANTHROPIC_API_KEY "
            "variable in Railway."
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise AnalyzerError(
            f"This API key does not have access to {MODEL}."
        ) from exc
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
        raise AnalyzerError("The model declined to analyze this site.")
    if response.stop_reason == "max_tokens":
        raise AnalyzerError(
            "The analysis was cut off before it finished. Try a site with fewer pages."
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text.strip():
        raise AnalyzerError("The Anthropic API returned an empty response.")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnalyzerError(f"The model returned text that is not valid JSON: {exc}") from exc

    return _normalize(data, site)


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

    # Exactly one gap carries the largest-concern flag.
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
