"""Anthropic API call: prompt assembly, structured JSON output, error mapping."""

from __future__ import annotations

import json
import os

import anthropic

from ingest import DeckContent, SiteContent
from rubric import LENGTH_GUIDANCE, SYSTEM_PROMPT
from schema import ANALYSIS_SCHEMA, Analysis

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16_000
EFFORT = "medium"


class AnalysisError(Exception):
    """Raised when the analysis call fails or returns unusable output."""


def analyze(
    deck: DeckContent,
    site: SiteContent | None = None,
    company: str | None = None,
) -> Analysis:
    """Send deck (and optional site) text to Claude, return a validated Analysis."""
    client = _client()
    prompt = _build_prompt(deck, site, company)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT + LENGTH_GUIDANCE,
                    # Stable across every run — cache it.
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
        raise AnalysisError(
            "Anthropic rejected the API key. Check ANTHROPIC_API_KEY."
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise AnalysisError(
            f"API key lacks access to {MODEL}. Check your workspace's model permissions."
        ) from exc
    except anthropic.NotFoundError as exc:
        raise AnalysisError(f"Model '{MODEL}' not found for this account: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise AnalysisError(
            "Rate limited by the Anthropic API after retries. Wait and try again."
        ) from exc
    except anthropic.APIStatusError as exc:
        raise AnalysisError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AnalysisError(f"Could not reach the Anthropic API: {exc}") from exc

    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", None) or "no detail given"
        raise AnalysisError(f"The model declined to analyze this deck ({detail}).")
    if response.stop_reason == "max_tokens":
        raise AnalysisError(
            "The response hit the token limit and the JSON is incomplete. "
            "Try a shorter deck or raise MAX_TOKENS in analyze.py."
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text.strip():
        raise AnalysisError("The API returned an empty response.")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"Model returned text that is not valid JSON: {exc}") from exc

    try:
        return Analysis.from_dict(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalysisError(f"Analysis JSON did not match the expected shape: {exc}") from exc


def _client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AnalysisError(
            "ANTHROPIC_API_KEY is not set.\n"
            "  PowerShell:  $env:ANTHROPIC_API_KEY = 'sk-ant-...'\n"
            "  bash/zsh:    export ANTHROPIC_API_KEY='sk-ant-...'"
        )
    return anthropic.Anthropic()


def _build_prompt(
    deck: DeckContent, site: SiteContent | None, company: str | None
) -> str:
    sections: list[str] = []

    if company:
        sections.append(
            f"The company is called {company}. Use this exact name in the `company` field."
        )
    else:
        sections.append(
            "The company name is not supplied — infer it from the deck or site "
            "and put it in the `company` field."
        )

    sections.append(
        f"=== PITCH DECK ({deck.source.name}, {deck.unit_count} "
        f"{deck.unit_label}s) ===\n{deck.text}"
    )

    if site is not None:
        sections.append(f"=== COMPANY WEBSITE ===\n{site.as_prompt_text()}")
        sections.append(
            "Assess the deck and the website together. Where the website is the "
            "weaker artifact, say so explicitly in gaps and presentation_ux."
        )
    else:
        sections.append(
            "No website was supplied. Base every finding on the deck alone. Do not "
            "speculate about what the website does or does not contain, and do not "
            "recommend website changes you cannot ground in the deck — except the "
            "Investor Relations section, which the rubric requires you to address."
        )

    return "\n\n".join(sections)
