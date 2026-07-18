# {{COMPANY_NAME}} — Website Analysis

> **Template notes (remove on render):**
> Document title style: `{{COMPANY_NAME}} - Website Analysis`
> Body font: Open Sans. Footer per TEN Capital standard:
> `{{COMPANY_NAME}} - Website Analysis   [PAGE#]   Compiled on {{DATE}} by TEN Capital Network   [logo]`
> Heading levels: H1 = section, H2 = numbered sub-item, H3 = callout label
> (`Investor Takeaway`, `Investor Signal`, `Investor Question`).
> Placeholders use `{{...}}`. Repeating blocks are marked `[REPEAT]`.

---

## Executive Summary

{{EXEC_SUMMARY_PARA_1}}
<!-- 2–4 sentences: what the site currently communicates well, from an investor's
     point of view. Establishes the positive baseline. -->

{{EXEC_SUMMARY_PARA_2}}
<!-- 2–4 sentences: who the site is actually optimized for vs. who it needs to
     serve for fundraising. States the central tension the report resolves. -->

**Current Investor Readiness Score: {{SCORE}}/10**

### Category Scorecard

| Category | Assessment |
|---|---|
| Scientific / Technical Credibility | {{RATING}} |
| Founder Credibility | {{RATING}} |
| Product Positioning | {{RATING}} |
| Market Opportunity Communication | {{RATING}} |
| Commercialization Story | {{RATING}} |
| Traction Evidence | {{RATING}} |
| Investor Readiness | {{RATING}} |
| Fundraising Supportiveness | {{RATING}} |

<!-- Rating vocabulary (fixed set): Strong | Good | Moderate | Weak-Moderate | Weak
     Categories are fixed. Rename "Scientific / Technical Credibility" to match
     sector (e.g. "Technical Credibility" for software, "Scientific Credibility"
     for life sciences). Do not add or drop rows. -->

---

## What's Working

[REPEAT — 3 to 6 items, ordered strongest first]

### {{N}}. {{STRENGTH_TITLE}}

{{STRENGTH_BODY}}
<!-- 1–3 sentences describing what the site does and why it lands. -->

{{OPTIONAL_BULLET_LIST}}
<!-- Optional: specific elements the site surfaces. -->

**Investor Takeaway** *(or **Investor Signal**)*

{{TAKEAWAY_LEAD_IN}}
- {{SIGNAL_1}}
- {{SIGNAL_2}}
- {{SIGNAL_3}}

{{TAKEAWAY_CLOSE}}
<!-- One line on why this matters to an investor. Not every strength needs a
     takeaway block — include only where the investor read differs from the
     surface read. -->

[/REPEAT]

---

## Gaps and Weaknesses

[REPEAT — 4 to 8 items, ordered by investor materiality, most material first]

### {{N}}. {{GAP_TITLE}}

{{GAP_FRAMING}}
<!-- 1–2 sentences. Flag the single largest gap explicitly as such. -->

The website does not clearly communicate:
- {{MISSING_ITEM_1}}
- {{MISSING_ITEM_2}}
- {{MISSING_ITEM_3}}

**Investor Question**

"{{THE_QUESTION_AN_INVESTOR_WOULD_ASK}}"

{{RESOLUTION_NOTE}}
<!-- One line on how well the site currently answers it. -->

[/REPEAT]

### Standard gap probes to evaluate

<!-- The analyzer should test the site against each of these and report the ones
     that fail. Keep the "Why X?" framing verbatim where it appears. -->

**Investor-facing narrative:**
- Why Now? — {{ADDRESSED | PARTIALLY ADDRESSED | NOT ADDRESSED}}
- Why This Team? — {{...}}
- Why This Market? — {{...}}
- Why This Product Wins? — {{...}}
- Why This Becomes Large? — {{...}}

**Competitive positioning:** competitors named, alternative approaches,
competitive advantages, barriers to entry, IP moat

**Commercialization:** regulatory pathway, distribution strategy, delivery/
fulfillment partners, revenue model, pricing model, reimbursement or
procurement strategy

**Validation metrics:** publications, validation cohorts, performance metrics,
outcomes data, peer review, patent count, regulatory milestones

**Platform vs. point solution:** whether the larger platform opportunity is
visible on the site or buried in secondary channels (LinkedIn, decks, press)

---

## Actionable Improvements

[REPEAT — 3 to 6 priorities, numbered and ordered by impact]

### Priority #{{N}}: {{RECOMMENDATION_TITLE}}

{{RECOMMENDATION_INTRO}}

{{SUGGESTED_STRUCTURE}}
<!-- Where the recommendation is a site section, spell out the nav item and its
     subsections as a nested list. Where it is content, give an example block
     labeled "Example:". -->

[/REPEAT]

### Standard priority set (adapt titles, keep the intent)

1. **Create an Investor Relations Section** — nav item plus subsections:
   Company Overview (mission, market opportunity, investment highlights);
   Leadership (founders, board, advisors); Validation (publications, studies,
   data); Partnerships; News & Milestones; Investor Contact
2. **Add an Investment Highlights Section** — a "Why {{COMPANY_NAME}}" block of
   5–7 one-line proof claims
3. **Add Commercial Proof Points** — the metrics table below
4. **Showcase Validation** — a dedicated evidence section
5. **Explain the Business Model** — a "How We Scale" visual, 5–6 steps
6. **Improve Calls-to-Action** — move CTAs from informational to relational

### Commercial Proof Points table

| Metric | Example Format |
|---|---|
| {{METRIC_1}} | XX,XXX |
| {{METRIC_2}} | XX |
| {{METRIC_3}} | XX |
| {{METRIC_4}} | XX |
| {{METRIC_5}} | XX |
| {{METRIC_6}} | XX |
| {{METRIC_7}} | XX |

*Even modest numbers are better than no numbers.*

<!-- Metrics are sector-dependent. Choose 6–8 that a buyer of this company's
     product would recognize: units/patients served, institutional customers,
     collaborators, publications, cohort or dataset size, geographies, IP count,
     ARR or contract count. Always show format masks (XX), never invented values. -->

---

## Financial Storytelling (Without Violating Securities Rules)

{{FRAMING_LINE}}
<!-- Standard: the website can communicate growth without discussing a
     securities offering. -->

**Recommended content**

*Commercial Milestones*
- {{MILESTONE_TYPE_1}}
- {{MILESTONE_TYPE_2}}
- {{MILESTONE_TYPE_3}}

*Market Opportunity — present:*
- {{MARKET_DATA_POINT_1}}
- {{MARKET_DATA_POINT_2}}
- {{MARKET_DATA_POINT_3}}

*Avoid:*
- Promises of investment returns
- Forecasted investor gains
- Promotional fundraising language

---

## SEC Regulation D Considerations

*(Assuming future U.S. fundraising activity.)*

### If Using Rule 506(b)

Avoid general solicitation. Recommended approach:
- Public website remains informational
- No active fundraising language
- No investment opportunity pages
- Investor materials gated behind password access
- Access only after establishing substantive investor relationships

**Appropriate structure**

| Public Site | Private Investor Portal |
|---|---|
| Company information | Financials |
| Product information | Data room |
| Team | Pitch deck |
| News | Fundraising materials |

### If Using Rule 506(c)

Public discussion is permissible, but:
- Avoid performance claims
- Avoid investment return projections
- Use clear securities disclaimers
- Verify accredited investor status before accepting investments

**Recommended footer language**

> Information presented is for informational purposes only and does not
> constitute an offer to sell or solicitation of an offer to buy securities.

<!-- This entire section is boilerplate and renders identically for every
     company. Only the jurisdiction note changes for non-U.S. issuers. -->

---

## Presentation & UX Enhancements

### Homepage Improvements

**Above-the-Fold**

*Current:* {{WHAT_IS_THERE_NOW}}

*Recommended:*
- One-sentence value proposition
- Product image
- Key metrics
- Partner logos

*Example:*
> {{PROPOSED_HEADLINE}}

Followed by:
- {{SUB_CLAIM_1}}
- {{SUB_CLAIM_2}}
- {{SUB_CLAIM_3}}

### Stronger Visual Hierarchy

*Current:* {{OBSERVATION_ON_DENSITY_AND_LAYOUT}}

*Add:*
- Infographics
- Timeline of milestones
- Product workflow diagrams
- Validation visuals

### Improve Leadership Section

*Add:*
- Professional headshots
- Founder achievements
- Publications
- Prior exits
- Academic or industry affiliations

*Investors back people first.*

### Add Milestone Timeline

| Year | Milestone |
|---|---|
| {{YEAR}} | {{MILESTONE}} |
| {{YEAR}} | {{MILESTONE}} |
| {{YEAR}} | {{MILESTONE}} |
| {{YEAR}} | {{MILESTONE}} |
| {{YEAR}} | {{MILESTONE}} |

<!-- 4–6 rows, founding year through current or next-year target. Only use
     dates verifiable from the website or supplied source material. -->

*Investors love progress visualization.*

### Improve Calls-to-Action

*Current:* {{CTA_OBSERVATION}}

*Add:*
- {{RELATIONAL_CTA_1}}
- {{RELATIONAL_CTA_2}}
- {{RELATIONAL_CTA_3}}
- Contact Business Development
- Investor Inquiries

---

## Overall Assessment

{{ASSESSMENT_PARA_1}}
<!-- What the company credibly looks like today and what foundation that gives. -->

{{ASSESSMENT_PARA_2}}
<!-- The primary weakness, stated in one sentence, then the specific dimensions
     it breaks into. -->

If {{COMPANY_NAME}} adds:
- {{FIX_1}}
- {{FIX_2}}
- {{FIX_3}}
- {{FIX_4}}
- {{FIX_5}}

the website could move from a {{SCORE}}/10 investor-readiness score to
approximately {{TARGET_SCORE_RANGE}}/10, substantially improving first
impressions with {{TARGET_INVESTOR_TYPES}}.

<!-- TARGET_INVESTOR_TYPES draws from: angels, family offices, strategic
     investors, sector-focused venture funds, corporate development teams. -->
