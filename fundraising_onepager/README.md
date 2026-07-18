# Fundraising Readiness One-Pager

Reads a pitch deck (and optionally a company website), evaluates it from an
investor's perspective with Claude, and renders a single-page PDF assessment.

## Install

Python 3.11 or newer.

```bash
cd fundraising_onepager
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## API key

The tool reads the key from the `ANTHROPIC_API_KEY` environment variable. It is
never written to disk or passed on the command line.

```powershell
# PowerShell (current session)
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

```bash
# bash / zsh
export ANTHROPIC_API_KEY="sk-ant-..."
```

Get a key at <https://platform.claude.com>.

## Usage

```
python main.py --deck PATH [--url URL] [--company NAME] [--out DIR]
```

| Flag        | Required | Description                                                      |
| ----------- | :------: | ---------------------------------------------------------------- |
| `--deck`    |   yes    | Pitch deck to analyze. `.pdf` or `.pptx`.                        |
| `--url`     |    no    | Company website. Adds the site review to the analysis.           |
| `--company` |    no    | Company name. Inferred from the deck or site when omitted.       |
| `--out`     |    no    | Output directory. Defaults to `./output`.                        |

### Examples

```bash
# Smoke test with the bundled 3-slide sample
python main.py --deck sample_deck.pptx

# Deck only
python main.py --deck decks/acme.pdf

# Deck plus website
python main.py --deck decks/acme.pptx --url https://www.gridmatrix.com/

# Explicit name and output location
python main.py --deck decks/acme.pdf --company "Acme Bio" --out ~/reports
```

Progress goes to stderr; the final PDF path is printed to stdout, so this works:

```bash
open "$(python main.py --deck decks/acme.pdf)"
```

Output lands at `<out>/<Company_Name>_readiness_onepager.pdf`.

## What the PDF contains

- **Header** — company name, title, date, and a color-coded readiness score badge (0–100).
- **Left column** — What's Working, Presentation & UX.
- **Right column** — Gaps & Weaknesses (severity color-coded HIGH / MED / LOW),
  Actionable Improvements.
- **Footer** — SEC / Regulation D read, plus a not-legal-advice disclaimer.

The page is always exactly one page. Content is measured before it is drawn, and
lowest-priority items are dropped until it fits; the footer notes how many were
omitted.

## How it works

```
main.py      CLI, validation, pipeline wiring, exit codes
  ingest.py    .pdf → pdfplumber, .pptx → python-pptx, URL → requests + bs4
  analyze.py   Anthropic API call, schema-constrained JSON response
  render.py    reportlab, two-pass measure-then-draw one-page layout
  rubric.py    the assessment rubric (system prompt)
  schema.py    JSON Schema + dataclasses for the analysis result
```

The model is `claude-sonnet-5`. The response is constrained with
`output_config.format` (structured outputs), so the JSON is schema-valid on
arrival — there is no "please return JSON" prompting or defensive parsing.
The rubric is cached with `cache_control`, so repeat runs pay ~10% for that
prefix.

## Exit codes

| Code | Meaning                                                            |
| ---: | ------------------------------------------------------------------ |
|    0 | Success. PDF path on stdout.                                        |
|    2 | Deck problem — missing file, unsupported type, no extractable text. |
|    3 | Analysis problem — missing/invalid API key, API error, refusal.     |
|    4 | Render problem — could not write the PDF.                           |
|  130 | Interrupted.                                                        |

## Limitations

- **Text only.** An image-only deck (scanned, or exported as flat images) yields
  no text and exits with code 2. Export a text-based PDF or use the PPTX.
- **No JavaScript.** `--url` fetches server-rendered HTML. A fully client-side
  rendered site returns little text; the run warns and continues deck-only.
- **A single page.** `--url` fetches the given URL only, not the whole site.
- **Not legal advice.** The Reg D section is a starting point for a conversation
  with securities counsel, nothing more.
