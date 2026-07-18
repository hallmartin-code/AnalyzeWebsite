# AnalyzeWebsite

Two tools sharing one repo.

| | What it does | Output |
|---|---|---|
| **`analyzewebsite_web/`** | Web app. Enter a company URL → crawls the site → Claude analysis → TEN Capital Website Analysis document. **This is what deploys to Railway.** | `.docx` |
| **`fundraising_onepager/`** | CLI. Takes a pitch deck (`.pdf`/`.pptx`) and optional URL → one-page readiness assessment. | `.pdf` |

The document format is defined by [website_analysis_template.md](website_analysis_template.md).
[website_analysis_schema.json](website_analysis_schema.json) is the human-readable copy of the
data contract; the authoritative version the code sends to the API is
[`analyzewebsite_web/analyzer/schema.py`](analyzewebsite_web/analyzer/schema.py), which differs
because structured outputs forbid `minItems`/`maxItems` and require every field to be required.

---

## Deploying to Railway

The repo root is the deploy root — `Procfile`, `railway.json`, `requirements.txt`, and
`.python-version` all live there, matching your LinkedIn Profile Analyzer setup.

1. **Push this folder to a Git repo** (GitHub, GitLab).
2. **Railway → New Project → Deploy from GitHub repo**, pick it. Nixpacks detects Python
   from the root `requirements.txt`, which re-includes `analyzewebsite_web/requirements.txt`.
3. **Set the API key.** Railway → your service → **Variables** → New Variable:

   | Name | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | `sk-ant-...` from <https://console.anthropic.com/settings/keys> |

   Never commit the key. `.env` is gitignored; `analyzewebsite_web/.env.example` shows the shape.
4. **Deploy.** Railway runs the start command from `railway.json`:
   ```
   gunicorn --chdir analyzewebsite_web app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 300
   ```
   The 300s timeout matters — a crawl plus analysis takes 60–120s and the default 30s would
   kill every request.
5. **Verify.** `GET /healthz` returns `{"status":"ok","key_configured":true}`. If
   `key_configured` is `false`, the variable did not land — check it and redeploy.
6. **Settings → Networking → Generate Domain** for a public URL.

### Changing the key later

Update the variable in Railway and redeploy. The key is read at request time from the
environment; it is never written to disk or baked into the image.

---

## Running locally

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows
source .venv/bin/activate          # macOS / Linux
pip install -r requirements.txt

# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# bash / zsh
export ANTHROPIC_API_KEY="sk-ant-..."

python analyzewebsite_web/app.py   # http://localhost:5000
```

`gunicorn` does not run on Windows (it needs `fcntl`). Use `app.py` directly for local
development; Railway runs Linux, where the Procfile command works.

---

## How the web app works

```
app.py                     Flask: GET / form, POST /analyze -> .docx download
  analyzer/site_fetcher.py   bounded same-domain crawl (requests + BeautifulSoup)
  analyzer/rubric.py         system prompt, derived from website_analysis_template.md
  analyzer/schema.py         JSON Schema sent as output_config.format
  analyzer/claude_analyzer.py Anthropic call + response normalization
  generator/docx_builder.py  python-docx render, TEN Capital footer
  webtemplates/index.html    the single page
  assets/                    TEN_Capital_logo_footer.png
```

**The crawl.** The homepage alone cannot answer "is there an Investor Relations section?"
or "are there leadership bios?", so the fetcher pulls the homepage plus up to eight internal
pages, ranked by path token — `investor`, `about`, `team`, `science`, `product`, `news`,
`contact` first. It is capped at 9 pages, 75 seconds, and 60k characters, with a 0.3s delay
between requests. Same-domain only; no PDFs, images, or other binaries. Every page fetched is
listed in a "Pages Reviewed" section at the end of the document.

**The analysis.** Model is `claude-sonnet-5`. The response is constrained with
`output_config.format`, so the JSON is schema-valid on arrival — no "please return JSON"
prompting and no defensive parsing. The rubric is cached with `cache_control`, so repeat runs
pay roughly 10% for that prefix. The rubric forbids inventing any figure, and
`commercial_proof_points` deliberately emits format masks (`XX`, `XX,XXX`) rather than values —
that table tells the company what to publish, it does not report what they have.

**The document.** Eight top-level sections in template order, five tables, and the TEN Capital
footer from `CLAUDE.md`: Open Sans 7pt, centered, live `PAGE` field, logo at 0.67in × 0.25in.

---

## Limitations

- **No JavaScript.** The crawler reads server-rendered HTML. A site rendered entirely in the
  browser returns almost no text and the app reports that rather than guessing.
- **A sample, not the whole site.** Nine pages maximum. The rubric instructs the model to say
  "not found in the pages reviewed" rather than "does not exist".
- **Not legal advice.** The Regulation D section is a starting point for a conversation with
  securities counsel.
