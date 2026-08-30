"""Flask web app: website URL -> Claude analysis -> TEN Capital Website Analysis .docx.

Deployed on Railway. The Claude API key is read from the ANTHROPIC_API_KEY
environment variable (set it in Railway -> your service -> Variables).
"""

from __future__ import annotations

import datetime
import logging
import os
import re

from dotenv import load_dotenv
from flask import Flask, make_response, render_template, request, send_from_directory

from analyzer import AnalyzerError, FetchError, analyze_site, fetch_site
from generator import DocumentError, build_analysis_docx
from notifier import analysis_recipients, email_configured, send_analysis_email_async

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("analyzewebsite")

app = Flask(__name__, template_folder="webtemplates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024  # form posts only; no uploads

DOCX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _safe_filename(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip()).strip("_")
    return stem[:80] or "Company"


def _today() -> str:
    d = datetime.date.today()
    return f"{d.month}/{d.day}/{d.year}"


def _key_configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


@app.context_processor
def _server_state():
    """Configuration every render of index.html needs.

    Read per request rather than at import: Railway variables can change under
    a running container, and the template's warning banner should follow.
    """
    return {
        "key_configured": _key_configured(),
        "email_configured": email_configured(),
        "email_to": ", ".join(analysis_recipients()),
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", error=None)


@app.route("/favicon.ico")
def favicon():
    """Serve the icon from the site root as well as /static.

    index.html links the icon explicitly, but browsers still probe
    /favicon.ico directly — for bookmarks, and on any page that is a bare
    error response. Answering here keeps those out of the logs as 404s.
    """
    return send_from_directory(app.static_folder, "favicon.ico")


@app.route("/healthz", methods=["GET"])
def healthz():
    return {
        "status": "ok",
        "key_configured": _key_configured(),
        "email_configured": email_configured(),
    }, 200


@app.route("/analyze", methods=["POST"])
def analyze():
    url = (request.form.get("url") or "").strip()
    company_name = (request.form.get("company_name") or "").strip()

    def fail(message: str, status: int = 400):
        return make_response(
            render_template(
                "index.html",
                error=message,
                url=url,
                company_name=company_name,
            ),
            status,
        )

    if not url:
        return fail("Please enter the company's website URL.")
    if not _key_configured():
        return fail(
            "The server has no Anthropic API key configured. Add ANTHROPIC_API_KEY "
            "in Railway → your service → Variables, then redeploy.",
            503,
        )

    try:
        log.info("crawl start url=%s", url)
        site = fetch_site(url)
        log.info("crawl done pages=%d", len(site.pages))
    except FetchError as exc:
        return fail(str(exc), 400)
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the user
        log.exception("unexpected crawl failure")
        return fail(f"Unexpected error fetching the site: {exc}", 500)

    try:
        data = analyze_site(site, company_name=company_name or None)
        log.info(
            "analysis done company=%s score=%s",
            data.get("company_name"),
            (data.get("executive_summary") or {}).get("readiness_score"),
        )
    except AnalyzerError as exc:
        return fail(str(exc), 502)
    except Exception as exc:  # noqa: BLE001
        log.exception("unexpected analysis failure")
        return fail(f"Unexpected error during analysis: {exc}", 500)

    analysis_date = _today()
    try:
        docx_bytes = build_analysis_docx(data, analysis_date=analysis_date)
    except DocumentError as exc:
        return fail(str(exc), 500)
    except Exception as exc:  # noqa: BLE001
        log.exception("unexpected document failure")
        return fail(f"Unexpected error building the document: {exc}", 500)

    filename = f"{_safe_filename(data.get('company_name') or company_name)}_Website_Analysis.docx"

    # Fire-and-forget: the notification runs on a daemon thread, so a Resend
    # outage can neither delay this download nor turn a good analysis into an
    # error page.
    send_analysis_email_async(
        data,
        docx_bytes,
        filename=filename,
        source_url=site.root_url or url,
        analysis_date=analysis_date,
        pages_reviewed=len(site.pages),
    )

    response = make_response(docx_bytes)
    response.headers["Content-Type"] = DOCX_MIMETYPE
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Length"] = str(len(docx_bytes))
    return response


@app.errorhandler(413)
def too_large(_exc):
    return make_response(
        render_template(
            "index.html",
            error="That request was too large.",
        ),
        413,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
