"""Flask web app: website URL -> Claude analysis -> TEN Capital Website Analysis .docx.

Deployed on Railway. The Claude API key is read from the ANTHROPIC_API_KEY
environment variable (set it in Railway -> your service -> Variables).
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import threading
import time

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import jobs
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

# Seconds one analysis may spend before it must give up. It no longer runs
# inside the HTTP request (see jobs.py), so this is not a race against the
# worker timeout any more — it is the point past which a run is not worth
# waiting for. Kept under jobs.STALE_SECONDS so a job that overruns is reported
# by the run itself rather than by the staleness fallback.
REQUEST_BUDGET = 240.0
DOCUMENT_RESERVE = 20.0

# Below this there is no point starting: the analysis is two calls, neither of
# which can finish in much less than half of it. Saying so beats spending the
# rest of the worker's life on an attempt that cannot land.
MIN_ANALYSIS_BUDGET = 70.0


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
    """The form, and — with ?job= — the progress view for a running analysis."""
    job_id = (request.args.get("job") or "").strip()
    state = jobs.read(job_id) if job_id else None
    if not state:
        return render_template("index.html", error=None)

    return render_template(
        "index.html",
        error=state["message"] if state["state"] == jobs.FAILED else None,
        url=state.get("url", ""),
        company_name=state.get("company_name", ""),
        job_id=job_id if state["state"] == jobs.RUNNING else "",
    )


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
    """Start an analysis and hand back a page that waits for it.

    This returns in milliseconds. The work runs on a daemon thread and the
    document is collected from /download — see jobs.py for why it no longer
    travels back as the body of this request.
    """
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

    job_id = jobs.create(url, company_name)
    threading.Thread(
        target=_run_analysis,
        args=(job_id, url, company_name),
        name=f"analysis-{job_id[:8]}",
        daemon=True,
    ).start()
    log.info("job %s started url=%s", job_id, url)

    # Redirect rather than render: a reload of the progress page then re-reads
    # the job instead of re-posting the form and starting a second analysis.
    return redirect(url_for("index", job=job_id), code=303)


@app.route("/status/<job_id>", methods=["GET"])
def status(job_id: str):
    """Polled by the progress page. Deliberately cheap — no work happens here."""
    state = jobs.read(job_id)
    if not state:
        return {"state": "unknown"}, 404
    return {
        "state": state["state"],
        "message": state.get("message", ""),
        "download": url_for("download", job_id=job_id) if state["state"] == jobs.DONE else "",
    }, 200


@app.route("/download/<job_id>", methods=["GET"])
def download(job_id: str):
    """Hand over the finished document.

    Separate from the run that produced it, so a dropped connection costs a
    retry of this one short request rather than the whole analysis.
    """
    found = jobs.document(job_id)
    if not found:
        abort(404)
    docx_bytes, filename = found

    response = make_response(docx_bytes)
    response.headers["Content-Type"] = DOCX_MIMETYPE
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Length"] = str(len(docx_bytes))
    return response


def _run_analysis(job_id: str, url: str, company_name: str) -> None:
    """The full pipeline, off the request thread.

    Every exit records a message on the job; the progress page shows it in the
    same place the synchronous version used to render its error page.
    """
    started = time.monotonic()

    def fail(message: str):
        log.warning("job %s failed: %s", job_id, message)
        jobs.fail(job_id, message)

    try:
        log.info("crawl start url=%s", url)
        site = fetch_site(url)
        log.info("crawl done pages=%d", len(site.pages))
    except FetchError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the user
        log.exception("unexpected crawl failure")
        return fail(f"Unexpected error fetching the site: {exc}")

    # What is left after the crawl, minus room to build the document. The crawl
    # has its own 75s budget but its final fetch can overshoot it, so measure
    # rather than assume.
    analysis_budget = REQUEST_BUDGET - (time.monotonic() - started) - DOCUMENT_RESERVE
    log.info("analysis budget=%.0fs", analysis_budget)

    if analysis_budget < MIN_ANALYSIS_BUDGET:
        log.warning("crawl left only %.0fs — refusing to start", analysis_budget)
        return fail(
            f"Reading {site.root_url or url} took so long that there was no time "
            "left to analyze it before the request had to return. The site's "
            "pages are responding slowly — try again, or point the analyzer at a "
            "smaller section of the site."
        )

    try:
        data = analyze_site(
            site,
            company_name=company_name or None,
            budget=analysis_budget,
        )
        log.info(
            "analysis done company=%s score=%s",
            data.get("company_name"),
            (data.get("executive_summary") or {}).get("readiness_score"),
        )
    except AnalyzerError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("unexpected analysis failure")
        return fail(f"Unexpected error during analysis: {exc}")

    analysis_date = _today()
    try:
        docx_bytes = build_analysis_docx(data, analysis_date=analysis_date)
    except DocumentError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("unexpected document failure")
        return fail(f"Unexpected error building the document: {exc}")

    filename = f"{_safe_filename(data.get('company_name') or company_name)}_Website_Analysis.docx"

    # Store the document before e-mailing it: the download is what the person
    # is waiting on, and a Resend outage must not be able to hold it up.
    jobs.finish(job_id, docx_bytes=docx_bytes, filename=filename)
    log.info("job %s done file=%s (%d bytes)", job_id, filename, len(docx_bytes))

    # Fire-and-forget on its own thread, as before, so a slow send does not
    # keep this one alive.
    send_analysis_email_async(
        data,
        docx_bytes,
        filename=filename,
        source_url=site.root_url or url,
        analysis_date=analysis_date,
        pages_reviewed=len(site.pages),
    )


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
