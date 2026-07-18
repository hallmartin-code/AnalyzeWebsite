"""Analysis result schema — the contract between analyze.py and render.py.

ANALYSIS_SCHEMA is a JSON Schema passed to the Anthropic API via
output_config.format, so the model is constrained to emit exactly this shape.
Structured outputs require additionalProperties: false on every object and
reject numeric/string constraints (minimum, maxLength, ...), so bounds are
expressed in the rubric prose instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SEVERITIES = ("high", "med", "low")
SEVERITY_RANK = {"high": 0, "med": 1, "low": 2}

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "company",
        "one_line_summary",
        "readiness_score",
        "whats_working",
        "gaps_weaknesses",
        "actionable_improvements",
        "sec_considerations",
        "presentation_ux",
    ],
    "properties": {
        "company": {
            "type": "string",
            "description": "Company name as it appears in the deck or on the site.",
        },
        "one_line_summary": {
            "type": "string",
            "description": "One sentence: what the company does and what stage it is at.",
        },
        "readiness_score": {
            "type": "integer",
            "description": "Overall fundraising readiness, 0-100.",
        },
        "whats_working": {
            "type": "array",
            "description": "Strengths. 3-6 items, strongest first.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["area", "point"],
                "properties": {
                    "area": {"type": "string", "description": "Short label, 1-4 words."},
                    "point": {
                        "type": "string",
                        "description": "One or two sentences citing deck/site evidence.",
                    },
                },
            },
        },
        "gaps_weaknesses": {
            "type": "array",
            "description": "Gaps. 3-8 items, most material first.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["area", "point", "severity"],
                "properties": {
                    "area": {"type": "string", "description": "Short label, 1-4 words."},
                    "point": {
                        "type": "string",
                        "description": "One or two sentences on what is missing and why it matters.",
                    },
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                },
            },
        },
        "actionable_improvements": {
            "type": "array",
            "description": "Fixes. 3-6 items, highest impact first.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["recommendation", "why_it_matters"],
                "properties": {
                    "recommendation": {
                        "type": "string",
                        "description": "Concrete action, imperative voice.",
                    },
                    "why_it_matters": {
                        "type": "string",
                        "description": "One sentence on the investor-facing effect.",
                    },
                },
            },
        },
        "sec_considerations": {
            "type": "object",
            "additionalProperties": False,
            "required": ["applicable_reg", "notes"],
            "properties": {
                "applicable_reg": {
                    "type": "string",
                    "enum": ["506(b)", "506(c)", "unclear"],
                },
                "notes": {
                    "type": "array",
                    "description": "2-4 short notes. Flag items to confirm with counsel.",
                    "items": {"type": "string"},
                },
            },
        },
        "presentation_ux": {
            "type": "array",
            "description": "Presentation and UX fixes. 3-6 items.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["element", "suggestion"],
                "properties": {
                    "element": {"type": "string", "description": "Short label, 1-4 words."},
                    "suggestion": {"type": "string", "description": "One sentence."},
                },
            },
        },
    },
}


@dataclass
class Strength:
    area: str
    point: str


@dataclass
class Gap:
    area: str
    point: str
    severity: str

    @property
    def rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, len(SEVERITY_RANK))


@dataclass
class Improvement:
    recommendation: str
    why_it_matters: str


@dataclass
class UXNote:
    element: str
    suggestion: str


@dataclass
class SECConsiderations:
    applicable_reg: str
    notes: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    company: str
    one_line_summary: str
    readiness_score: int
    whats_working: list[Strength]
    gaps_weaknesses: list[Gap]
    actionable_improvements: list[Improvement]
    sec_considerations: SECConsiderations
    presentation_ux: list[UXNote]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Analysis":
        """Build from the API's validated JSON. Schema enforcement means the
        keys are present; we still clamp the score, which the schema cannot."""
        sec = d["sec_considerations"]
        return cls(
            company=d["company"],
            one_line_summary=d["one_line_summary"],
            readiness_score=max(0, min(100, int(d["readiness_score"]))),
            whats_working=[Strength(**s) for s in d["whats_working"]],
            gaps_weaknesses=[Gap(**g) for g in d["gaps_weaknesses"]],
            actionable_improvements=[
                Improvement(**i) for i in d["actionable_improvements"]
            ],
            sec_considerations=SECConsiderations(
                applicable_reg=sec["applicable_reg"], notes=list(sec["notes"])
            ),
            presentation_ux=[UXNote(**u) for u in d["presentation_ux"]],
        )
