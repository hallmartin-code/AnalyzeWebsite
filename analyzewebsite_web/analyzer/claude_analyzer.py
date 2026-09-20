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
# Three tiers, because they cover different failure shapes:
#
#   1. The SDK retries 408/409/429/5xx itself, fast and with exponential
#      backoff, honouring `retry-after`. That handles a single bad routing
#      attempt. The default is 2 retries; 3 costs nothing when calls succeed.
#   2. _call adds slower whole-call attempts on top, for a blip that outlives
#      the SDK's burst — the case that produced "API error 500" here.
#   3. _call's last attempt drops the structured-output grammar (see
#      _request). A 5xx that survives every attempt above is often the schema
#      failing to compile server-side rather than a blip, and that shape of
#      failure never clears no matter how long we wait.
#
# Tier 2 is bounded by the clock, not by an attempt count. A 500 comes back
# almost instantly, so the previous fixed ladder (3 attempts, 3s then 8s)
# surrendered after ~15s with ~170s of the budget below still unspent — the
# user saw "did not recover after 3 attempts" while there was ample room to
# keep waiting. Attempts now continue for as long as one more call could still
# finish, with the pause growing each round and capped so the gap between tries
# stays useful.
#
# Tier 1 is deliberately shallow. The SDK retries a timeout as readily as a
# 500, so with its own ceiling the worst case is timeout x (max_retries + 1) —
# at the SDK's default 10-minute timeout that is forty minutes inside one
# `create()` call, long after gunicorn has killed the worker and the proxy has
# given up ("upstream error"). One fast double-tap is all tier 1 needs to be
# worth having; tier 2 owns the long game and watches the clock while it does.
SDK_MAX_RETRIES = 1
MAX_TRANSIENT_ATTEMPTS = 6
BACKOFF_SECONDS = (3.0, 8.0, 15.0, 25.0, 40.0)

# Wall-clock ceiling for both analysis calls together, used when the caller does
# not supply one. gunicorn kills the worker at 300s, so retrying past this trades
# a useful error page for a dead connection. app.py passes what is actually left
# of the request instead of this default — the crawl's own budget is 75s but its
# last fetch can overshoot, and guessing low here is what left no margin.
#
# The budget is a deadline, not a hint: every HTTP attempt is given a timeout cut
# from the time still remaining (see _attempt_timeout), so the analysis cannot
# outlive it no matter how the API behaves. A call needs roughly MIN_CALL_SECONDS
# to have any chance of finishing, so we never start one without room for it.
ANALYSIS_BUDGET = 190.0
MIN_CALL_SECONDS = 35.0

# Ceiling on any single HTTP attempt. Well under the SDK's 10-minute default: a
# schema-constrained Sonnet call on a 60k-char crawl lands in tens of seconds,
# so a request still running after this is stuck, not slow.
MAX_CALL_SECONDS = 120.0

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


def analyze_site(
    site: SiteContent,
    company_name: str | None = None,
    budget: float | None = None,
) -> dict:
    """Run both calls and return the merged, normalized analysis.

    `budget` is the seconds available for both calls together; the caller
    passes what is left of the HTTP request so the two agree on the clock.
    """
    client = _client()
    site_text = site.as_prompt_text()
    deadline = time.monotonic() + (ANALYSIS_BUDGET if budget is None else budget)

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

    Attempts stop when the clock runs out rather than at a fixed count, and the
    last resort drops the response grammar entirely; see the retry policy notes
    at the top of this module.
    """
    last: Exception | None = None
    attempts = 0

    for attempt in range(1, MAX_TRANSIENT_ATTEMPTS + 1):
        # Checked before the first attempt too, not just between retries. The
        # second call inherits whatever the first left of the shared deadline,
        # and _attempt_timeout's floor would otherwise let a doomed attempt run
        # past it — the overshoot the worker timeout has no patience for.
        if time.monotonic() > deadline - MIN_CALL_SECONDS:
            if attempt == 1:
                raise AnalyzerError(_exhausted_message(label))
            break

        attempts = attempt
        try:
            return _request(
                client,
                system=system,
                schema=schema,
                site_text=site_text,
                instruction=instruction,
                label=label,
                timeout=_attempt_timeout(deadline),
            )
        except _TRANSIENT as exc:
            last = exc
            pause = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            pause += random.uniform(0, 1)  # de-sync concurrent workers
            remaining = deadline - time.monotonic()

            if attempt == MAX_TRANSIENT_ATTEMPTS:
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
                MAX_TRANSIENT_ATTEMPTS,
            )
            time.sleep(pause)

    # Every constrained attempt failed the same way. A 5xx that survives all of
    # them is more often the response grammar failing to compile server-side
    # than a blip — and that shape never clears on its own, so waiting longer
    # would not have helped. Ask once more without the grammar, spelling the
    # schema out in the prompt instead. merge_analysis() reads every field
    # through .get(), so a slightly loose shape degrades rather than breaks.
    if deadline - time.monotonic() >= MIN_CALL_SECONDS:
        log.warning("%s: retrying once without the response grammar", label)
        try:
            return _request(
                client,
                system=system,
                schema=schema,
                site_text=site_text,
                instruction=instruction,
                label=label,
                constrained=False,
                timeout=_attempt_timeout(deadline),
            )
        except Exception as exc:  # noqa: BLE001 - best effort; report the 5xx
            log.warning("%s: ungrammared retry also failed — %s", label, _describe(exc))

    raise AnalyzerError(_transient_message(last, label, attempts)) from last


def _exhausted_message(label: str) -> str:
    """When the clock ran out before the step could even be attempted."""
    return (
        f"There was not enough time left to run the {label} step before the "
        "request had to return. The site was slow to read, or an earlier step "
        "spent the time waiting on the Anthropic API. Please try again."
    )


def _attempt_timeout(deadline: float) -> float:
    """Seconds one HTTP attempt may take.

    The SDK gets `max_retries` tries of its own inside a single `create()`, so
    the wall clock for the call is this value times SDK_MAX_RETRIES + 1 — divide
    the remaining time by that, or the outer deadline means nothing. Floored at
    MIN_CALL_SECONDS: the caller already refuses to start an attempt without
    that much room, so a smaller number here could only cut short a call that
    was going to fit.
    """
    remaining = deadline - time.monotonic()
    return max(MIN_CALL_SECONDS, min(MAX_CALL_SECONDS, remaining / (SDK_MAX_RETRIES + 1)))


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


def _transient_message(exc: Exception | None, label: str, attempts: int) -> str:
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
    tries = "1 attempt" if attempts == 1 else f"{attempts} attempts"
    return (
        f"The Anthropic API {busy} (HTTP {status}) and did not recover after "
        f"{tries}, so the {label} step could not finish. This is a fault on "
        f"Anthropic's side, not a problem with the site you entered. Please try "
        f"again in a few minutes.{detail}"
    )


def _request(
    client,
    *,
    system: str,
    schema: dict,
    site_text: str,
    instruction: str,
    label: str,
    timeout: float,
    constrained: bool = True,
) -> dict:
    """One analysis request. Site content is cached across calls.

    `constrained` picks how the JSON shape is enforced. Normally the schema
    goes in `output_config.format`, so the API guarantees conforming output.
    The fallback in _call passes False: effort is kept, the grammar is dropped,
    and the schema is spelled out in the prompt instead — weaker, but it is the
    only form left when the grammar itself is what the API is choking on.
    """
    output_config: dict = {"effort": EFFORT}
    if constrained:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    else:
        instruction = (
            f"{instruction}\n\nReply with a single JSON object and nothing else — "
            "no prose, no explanation, no markdown code fence. It must match this "
            f"JSON Schema exactly:\n\n{json.dumps(schema)}"
        )

    try:
        # Per-request, so each attempt is cut from the time actually left rather
        # than inheriting the SDK's 10-minute default.
        response = client.with_options(timeout=timeout).messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system}],
            output_config=output_config,
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
        return json.loads(_unfence(text))
    except json.JSONDecodeError as exc:
        raise AnalyzerError(f"The {label} step returned invalid JSON: {exc}") from exc


def _unfence(text: str) -> str:
    """Strip a ```json fence, if one is there.

    The grammar guarantees bare JSON, so this only ever matters on the
    ungrammared fallback, where the instruction asks for no fence but nothing
    enforces it.
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body[:4].lower() == "json":
        body = body[4:]
    return body.rsplit("```", 1)[0].strip() if "```" in body else body.strip()


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
