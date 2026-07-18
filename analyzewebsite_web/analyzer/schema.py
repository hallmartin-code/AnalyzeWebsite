"""API-side schema for the Website Analysis.

This mirrors website_analysis_template.md section-for-section and is the
authoritative contract for the code. It is passed to the Anthropic API via
output_config.format, so the model is constrained to emit exactly this shape.

Structured outputs impose two rules that ../../website_analysis_schema.json
(the human-facing documentation copy) does not follow, which is why this is a
separate literal rather than a load of that file:
  1. every object needs additionalProperties: false and must list every
     property in `required` — there are no optional fields;
  2. minItems / maxItems / pattern / minimum are rejected.
Counts and lengths are therefore expressed in the rubric prose, and ranges are
clamped in Python after the response arrives.
"""

from __future__ import annotations

from typing import Any

RATINGS = ["Strong", "Good", "Moderate", "Weak-Moderate", "Weak"]
COVERAGE = [
    "Addressed",
    "Partially addressed",
    "Not addressed",
    "Not quantified",
    "Missing",
]
CALLOUT_LABELS = ["Investor Takeaway", "Investor Signal", "None"]
REG_D_RULES = ["506(b)", "506(c)", "both", "n/a"]

SCORECARD_CATEGORIES = [
    ("scientific_technical_credibility", "Scientific / Technical Credibility"),
    ("founder_credibility", "Founder Credibility"),
    ("product_positioning", "Product Positioning"),
    ("market_opportunity_communication", "Market Opportunity Communication"),
    ("commercialization_story", "Commercialization Story"),
    ("traction_evidence", "Traction Evidence"),
    ("investor_readiness", "Investor Readiness"),
    ("fundraising_supportiveness", "Fundraising Supportiveness"),
]

NARRATIVE_PROBES = [
    ("why_now", "Why Now?"),
    ("why_this_team", "Why This Team?"),
    ("why_this_market", "Why This Market?"),
    ("why_this_product_wins", "Why This Product Wins?"),
    ("why_this_becomes_large", "Why This Becomes Large?"),
]


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    """Object node with the two structured-output requirements applied."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(props),
        "properties": props,
    }


def _str(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


def _strlist(desc: str) -> dict[str, Any]:
    return {"type": "array", "description": desc, "items": {"type": "string"}}


def _enum(values: list[str], desc: str) -> dict[str, Any]:
    return {"type": "string", "enum": values, "description": desc}


ANALYSIS_SCHEMA: dict[str, Any] = _obj(
    {
        "company_name": _str("Company name exactly as the site presents it."),
        "sector": _str("Sector and sub-sector, e.g. 'Life sciences — molecular diagnostics'."),
        "executive_summary": _obj(
            {
                "what_the_site_does_well": _str(
                    "2-4 sentences on what the site currently communicates well, "
                    "from an investor's point of view."
                ),
                "who_the_site_serves": _str(
                    "2-4 sentences on who the site is actually optimized for versus "
                    "who it must serve for fundraising. States the central tension."
                ),
                "readiness_score": {
                    "type": "number",
                    "description": "Investor readiness, 1.0-10.0, one decimal place.",
                },
            }
        ),
        "scorecard": _obj(
            {key: _enum(RATINGS, label) for key, label in SCORECARD_CATEGORIES}
        ),
        "whats_working": {
            "type": "array",
            "description": "3-6 strengths, strongest first.",
            "items": _obj(
                {
                    "title": _str("Short strength title, e.g. 'Strong Founder Credibility'."),
                    "body": _str("1-3 sentences on what the site does and why it lands."),
                    "supporting_points": _strlist(
                        "0-6 specific elements the site surfaces. May be empty."
                    ),
                    "callout": _obj(
                        {
                            "label": _enum(
                                CALLOUT_LABELS,
                                "Use 'None' when the investor read does not differ "
                                "from the surface read; then leave the other fields empty.",
                            ),
                            "lead_in": _str("One line introducing the signals. May be empty."),
                            "points": _strlist("2-4 signals. May be empty."),
                            "close": _str("One line on why it matters. May be empty."),
                        }
                    ),
                }
            ),
        },
        "gaps": {
            "type": "array",
            "description": "4-8 gaps, ordered by investor materiality, most material first.",
            "items": _obj(
                {
                    "title": _str("Short gap title, e.g. 'Missing Commercial Traction'."),
                    "framing": _str("1-2 sentences framing the gap."),
                    "is_largest_concern": {
                        "type": "boolean",
                        "description": "True for exactly one gap — the single largest concern.",
                    },
                    "missing_items": _strlist("3-8 specific things the site does not communicate."),
                    "investor_question": _str(
                        "The question an investor would ask, in quotes-worthy form."
                    ),
                    "resolution_note": _str("One line on how well the site currently answers it."),
                }
            ),
        },
        "narrative_probes": _obj(
            {key: _enum(COVERAGE, label) for key, label in NARRATIVE_PROBES}
        ),
        "improvements": {
            "type": "array",
            "description": "3-6 priorities, ordered by impact. Rendered as 'Priority #N'.",
            "items": _obj(
                {
                    "title": _str("Recommendation title, e.g. 'Create an Investor Relations Section'."),
                    "intro": _str("1-2 sentences introducing the recommendation."),
                    "suggested_sections": {
                        "type": "array",
                        "description": "Nav items and their subsections. May be empty.",
                        "items": _obj(
                            {
                                "name": _str("Section name."),
                                "items": _strlist("Subsection bullets."),
                            }
                        ),
                    },
                    "example_block": _strlist(
                        "Example copy lines shown under an 'Example:' label. May be empty."
                    ),
                }
            ),
        },
        "commercial_proof_points": {
            "type": "array",
            "description": (
                "6-8 sector-appropriate metrics the company should publish. "
                "example_format is ALWAYS a mask such as XX or XX,XXX — never a real figure."
            ),
            "items": _obj(
                {
                    "metric": _str("Metric name, e.g. 'Patients evaluated'."),
                    "example_format": _str("Format mask only: XX, XX,XXX, $XXM, XX%."),
                }
            ),
        },
        "financial_storytelling": _obj(
            {
                "framing": _str(
                    "One line: the site can communicate growth without discussing an offering."
                ),
                "commercial_milestones": _strlist("3-6 milestone types worth publishing."),
                "market_opportunity_points": _strlist("3-5 market data points worth presenting."),
            }
        ),
        "reg_d": _obj(
            {
                "applicable_rule": _enum(
                    REG_D_RULES, "Which Regulation D exemption the site's posture implies."
                ),
                "jurisdiction_note": _str(
                    "One line. Empty string for a straightforward U.S. issuer."
                ),
            }
        ),
        "ux_enhancements": _obj(
            {
                "above_the_fold": _obj(
                    {
                        "current": _str("What is above the fold now."),
                        "proposed_headline": _str("A concrete one-sentence value proposition."),
                        "sub_claims": _strlist("3 short proof claims to sit beneath it."),
                    }
                ),
                "visual_hierarchy_observation": _str("One line on density and layout."),
                "leadership_section_observation": _str("One line on the leadership section."),
                "milestone_timeline": {
                    "type": "array",
                    "description": (
                        "4-6 rows, founding year to current or next-year target. "
                        "ONLY dates verifiable from the site — never invented."
                    ),
                    "items": _obj(
                        {
                            "year": _str("Year, e.g. '2017'."),
                            "milestone": _str("Short milestone description."),
                        }
                    ),
                },
                "cta_observation": _str("One line on the current calls-to-action."),
                "recommended_ctas": _strlist("3-5 relational CTAs to add."),
            }
        ),
        "overall_assessment": _obj(
            {
                "current_position": _str(
                    "What the company credibly looks like today and what foundation that gives."
                ),
                "primary_weakness": _str(
                    "The primary weakness in one sentence, then the dimensions it breaks into."
                ),
                "required_fixes": _strlist("3-6 fixes, phrased as noun clauses."),
                "target_score_range": _str("Achievable range after the fixes, e.g. '8.5-9'."),
                "target_investor_types": _strlist(
                    "3-5 investor types, e.g. angels, family offices, strategic investors."
                ),
            }
        ),
    }
)
