"""API-side schemas for the Website Analysis.

The document format is website_analysis_template.md. Getting there in one
API call produced "The compiled grammar is too large" — a single schema with
16 object shapes, 72 properties, 72 enum values and depth 6 exceeds what
structured outputs will compile. Three changes fix it, and merge_analysis()
reassembles the original nested shape so generator/docx_builder.py is
unaffected:

  1. Split into two calls. ASSESSMENT_SCHEMA (what the site is) and
     RECOMMENDATIONS_SCHEMA (what to do about it) compile independently.
  2. Keyed objects become arrays. An 8-property scorecard where every value is
     a 5-way enum expands into a branch per key; one array of
     {category, rating} does not.
  3. Flatten nested shapes. `callout` and `above_the_fold` were objects nested
     inside arrays/objects; their fields are now siblings.

Structured outputs also require additionalProperties: false plus every
property listed in `required` (there are no optional fields), and reject
minItems / maxItems / pattern / minimum. Counts live in the rubric prose;
ranges are clamped in Python after the response arrives.
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

# (payload key, document label). The label is the enum value on the wire —
# merge_analysis maps it back to the key the document builder expects.
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

_SCORECARD_LABELS = [label for _, label in SCORECARD_CATEGORIES]
_PROBE_LABELS = [label for _, label in NARRATIVE_PROBES]


def _obj(props: dict[str, Any]) -> dict[str, Any]:
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


def _arr(desc: str, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "description": desc, "items": _obj(props)}


# --------------------------------------------------------------- call 1 of 2

ASSESSMENT_SCHEMA: dict[str, Any] = _obj(
    {
        "company_name": _str("Company name exactly as the site presents it."),
        "sector": _str("Sector and sub-sector, e.g. 'Life sciences — molecular diagnostics'."),
        "what_the_site_does_well": _str(
            "2-4 sentences on what the site currently communicates well, from an "
            "investor's point of view."
        ),
        "who_the_site_serves": _str(
            "2-4 sentences on who the site is actually optimized for versus who it "
            "must serve for fundraising. States the central tension."
        ),
        "readiness_score": {
            "type": "number",
            "description": "Investor readiness, 1.0-10.0, one decimal place.",
        },
        "scorecard": _arr(
            "Exactly one entry for each of the eight categories, in the listed order.",
            {
                "category": _enum(_SCORECARD_LABELS, "One of the eight fixed categories."),
                "rating": _enum(RATINGS, "Fixed rating vocabulary."),
            },
        ),
        "whats_working": _arr(
            "3-6 strengths, strongest first.",
            {
                "title": _str("Short strength title, e.g. 'Strong Founder Credibility'."),
                "body": _str("1-3 sentences on what the site does and why it lands."),
                "supporting_points": _strlist(
                    "0-6 specific elements the site surfaces. May be empty."
                ),
                "callout_label": _enum(
                    CALLOUT_LABELS,
                    "Use 'None' unless the investor read genuinely differs from the "
                    "surface read; then leave callout_lead_in and callout_points empty.",
                ),
                "callout_lead_in": _str("One line introducing the signals. May be empty."),
                "callout_points": _strlist("2-4 signals. May be empty."),
            },
        ),
        "gaps": _arr(
            "4-8 gaps, ordered by investor materiality, most material first.",
            {
                "title": _str("Short gap title, e.g. 'Missing Commercial Traction'."),
                "framing": _str("1-2 sentences framing the gap."),
                "is_largest_concern": {
                    "type": "boolean",
                    "description": "True for exactly one gap — the single largest concern.",
                },
                "missing_items": _strlist("3-8 specific things the site does not communicate."),
                "investor_question": _str("The question an investor would ask."),
                "resolution_note": _str("One line on how well the site currently answers it."),
            },
        ),
        "narrative_probes": _arr(
            "Exactly one entry for each of the five questions, in the listed order.",
            {
                "question": _enum(_PROBE_LABELS, "One of the five fixed questions."),
                "coverage": _enum(COVERAGE, "How well the site answers it."),
            },
        ),
    }
)


# --------------------------------------------------------------- call 2 of 2

RECOMMENDATIONS_SCHEMA: dict[str, Any] = _obj(
    {
        "improvements": _arr(
            "3-6 priorities ordered by impact. Rendered as 'Priority #N'.",
            {
                "title": _str("Recommendation title, e.g. 'Create an Investor Relations Section'."),
                "intro": _str("1-2 sentences introducing the recommendation."),
                "section_name": _str(
                    "The site section this creates or changes, e.g. 'Investors'. "
                    "Empty when the recommendation is not a site section."
                ),
                "section_items": _strlist(
                    "Subsections or contents of section_name. May be empty."
                ),
                "example_block": _strlist(
                    "Example copy lines shown under an 'Example:' label. May be empty."
                ),
            },
        ),
        "commercial_proof_points": _arr(
            "6-8 sector-appropriate metrics the company should publish.",
            {
                "metric": _str("Metric name, e.g. 'Patients evaluated'."),
                "example_format": _str(
                    "Format mask ONLY — XX, XX,XXX, $XXM, XX%. Never a real figure."
                ),
            },
        ),
        "financial_framing": _str(
            "One line: the site can communicate growth without discussing an offering."
        ),
        "commercial_milestones": _strlist("3-6 milestone types worth publishing."),
        "market_opportunity_points": _strlist("3-5 market data points worth presenting."),
        "reg_d_rule": _enum(
            REG_D_RULES, "Which Regulation D exemption the site's posture implies."
        ),
        "reg_d_jurisdiction_note": _str(
            "One line. Empty string for a straightforward U.S. issuer."
        ),
        "above_the_fold_current": _str("What is above the fold now."),
        "proposed_headline": _str("A concrete one-sentence value proposition."),
        "proposed_sub_claims": _strlist("3 short proof claims to sit beneath the headline."),
        "visual_hierarchy_observation": _str("One line on density and layout."),
        "leadership_section_observation": _str("One line on the leadership section."),
        "milestone_timeline": _arr(
            "4-6 rows, founding year to current or next-year target. ONLY dates "
            "verifiable from the supplied pages. Return fewer rows rather than "
            "inventing one.",
            {
                "year": _str("Year, e.g. '2017'."),
                "milestone": _str("Short milestone description."),
            },
        ),
        "cta_observation": _str("One line on the current calls-to-action."),
        "recommended_ctas": _strlist("3-5 relational CTAs to add."),
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
)


# ------------------------------------------------------------------- assembly


def merge_analysis(assessment: dict, recs: dict) -> dict:
    """Rebuild the nested shape generator/docx_builder.py consumes.

    The wire format is flat and array-based to keep each grammar small; the
    document builder wants keyed objects. This is the only place that
    translation lives.
    """
    label_to_key = {label: key for key, label in SCORECARD_CATEGORIES}
    scorecard = {key: "—" for key, _ in SCORECARD_CATEGORIES}
    for row in assessment.get("scorecard") or []:
        key = label_to_key.get(row.get("category", ""))
        if key and row.get("rating"):
            scorecard[key] = row["rating"]

    probe_to_key = {label: key for key, label in NARRATIVE_PROBES}
    probes = {key: "—" for key, _ in NARRATIVE_PROBES}
    for row in assessment.get("narrative_probes") or []:
        key = probe_to_key.get(row.get("question", ""))
        if key and row.get("coverage"):
            probes[key] = row["coverage"]

    whats_working = [
        {
            "title": s.get("title", ""),
            "body": s.get("body", ""),
            "supporting_points": s.get("supporting_points") or [],
            "callout": {
                "label": s.get("callout_label") or "None",
                "lead_in": s.get("callout_lead_in") or "",
                "points": s.get("callout_points") or [],
                "close": "",
            },
        }
        for s in assessment.get("whats_working") or []
    ]

    improvements = []
    for item in recs.get("improvements") or []:
        sections = []
        if (item.get("section_name") or "").strip():
            sections.append(
                {"name": item["section_name"], "items": item.get("section_items") or []}
            )
        improvements.append(
            {
                "title": item.get("title", ""),
                "intro": item.get("intro", ""),
                "suggested_sections": sections,
                "example_block": item.get("example_block") or [],
            }
        )

    return {
        "company_name": assessment.get("company_name", ""),
        "sector": assessment.get("sector", ""),
        "executive_summary": {
            "what_the_site_does_well": assessment.get("what_the_site_does_well", ""),
            "who_the_site_serves": assessment.get("who_the_site_serves", ""),
            "readiness_score": assessment.get("readiness_score", 0),
        },
        "scorecard": scorecard,
        "whats_working": whats_working,
        "gaps": assessment.get("gaps") or [],
        "narrative_probes": probes,
        "improvements": improvements,
        "commercial_proof_points": recs.get("commercial_proof_points") or [],
        "financial_storytelling": {
            "framing": recs.get("financial_framing", ""),
            "commercial_milestones": recs.get("commercial_milestones") or [],
            "market_opportunity_points": recs.get("market_opportunity_points") or [],
        },
        "reg_d": {
            "applicable_rule": recs.get("reg_d_rule", "n/a"),
            "jurisdiction_note": recs.get("reg_d_jurisdiction_note", ""),
        },
        "ux_enhancements": {
            "above_the_fold": {
                "current": recs.get("above_the_fold_current", ""),
                "proposed_headline": recs.get("proposed_headline", ""),
                "sub_claims": recs.get("proposed_sub_claims") or [],
            },
            "visual_hierarchy_observation": recs.get("visual_hierarchy_observation", ""),
            "leadership_section_observation": recs.get("leadership_section_observation", ""),
            "milestone_timeline": recs.get("milestone_timeline") or [],
            "cta_observation": recs.get("cta_observation", ""),
            "recommended_ctas": recs.get("recommended_ctas") or [],
        },
        "overall_assessment": {
            "current_position": recs.get("current_position", ""),
            "primary_weakness": recs.get("primary_weakness", ""),
            "required_fixes": recs.get("required_fixes") or [],
            "target_score_range": recs.get("target_score_range", ""),
            "target_investor_types": recs.get("target_investor_types") or [],
        },
    }
