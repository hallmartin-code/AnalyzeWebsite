"""Bounded same-domain crawl.

The analysis template asks whether an Investor Relations section, leadership
bios, or a news page exist. A homepage alone cannot answer that, so we fetch
the homepage plus a small number of internal pages, prioritising the paths that
carry investor-relevant content. The crawl is deliberately shallow and capped:
this is a page sample, not a site mirror, and the rubric tells the model to
qualify its findings accordingly.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (compatible; TENCapitalSiteReview/1.0; "
    "+https://tencapital.group/)"
)
FETCH_TIMEOUT = 15          # seconds per page
TOTAL_BUDGET = 75.0         # seconds for the whole crawl
MAX_PAGES = 9               # homepage + 8
CRAWL_DELAY = 0.3           # seconds between requests, politeness
MAX_CHARS_PER_PAGE = 9_000
MAX_TOTAL_CHARS = 60_000

# Paths worth spending a fetch on, highest value first. Matched against path
# *tokens*, never as raw substrings — "ir" as a substring hits /airports and
# /texas-dir-contract, which pushed the real About and Team pages out of the
# crawl budget.
PRIORITY_PATHS = [
    "investor", "ir", "about", "our-story", "story", "team", "leadership",
    "people", "science", "technology", "platform", "product", "solution",
    "pipeline", "research", "publication", "evidence", "clinical", "data",
    "news", "press", "media", "blog", "milestone", "partner", "contact",
]

_STRIP_TAGS = ("script", "style", "noscript", "svg", "iframe", "template")
_SKIP_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip",
    ".mp4", ".mp3", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
)


class FetchError(Exception):
    """Raised when the site cannot be turned into usable text."""


@dataclass
class Page:
    url: str
    title: str
    headings: list[str]
    body: str


@dataclass
class SiteContent:
    root_url: str
    domain: str
    pages: list[Page] = field(default_factory=list)
    ctas: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_prompt_text(self) -> str:
        parts = [
            f"SITE: {self.root_url}",
            f"PAGES REVIEWED: {len(self.pages)}"
            + (f" (crawl stopped early: {'; '.join(self.skipped)})" if self.skipped else ""),
        ]
        if self.notes:
            parts.append("CRAWL NOTES:\n" + "\n".join(f"- {n}" for n in self.notes))
        if self.ctas:
            parts.append(
                "NAVIGATION AND CALLS-TO-ACTION (from the homepage chrome):\n"
                + "\n".join(f"- {c}" for c in self.ctas)
            )
        for page in self.pages:
            block = [f"===== PAGE: {page.url} =====", f"TITLE: {page.title or '(none)'}"]
            if page.headings:
                block.append("HEADINGS: " + " | ".join(page.headings))
            block.append(page.body)
            parts.append("\n".join(block))
        return "\n\n".join(parts)


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise FetchError("Please enter a website URL.")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc or "." not in parsed.netloc:
        raise FetchError(f"'{raw}' does not look like a website address.")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def fetch_site(root_url: str, max_pages: int = MAX_PAGES) -> SiteContent:
    """Fetch the homepage and a prioritised sample of internal pages."""
    root_url = normalize_url(root_url)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    started = time.monotonic()

    # A dead homepage is fatal, but "dead" is worth a second opinion first: the
    # apex and the www host are routinely configured differently.
    root_url, html, note = _fetch_homepage(session, root_url)
    domain = urlparse(root_url).netloc.lower().removeprefix("www.")
    site = SiteContent(root_url=root_url, domain=domain)
    if note:
        site.notes.append(note)

    soup = BeautifulSoup(html, "lxml")

    site.ctas = _dedupe(
        el.get_text(" ", strip=True)
        for el in soup.select("nav a, header a, a, button, [role='button']")
        if 2 <= len(el.get_text(" ", strip=True)) <= 60
    )[:30]

    home = _to_page(root_url, soup)
    if not home.body and not home.headings:
        raise FetchError(
            f"Almost no readable text was found at {root_url}. The site is likely "
            "rendered entirely in the browser with JavaScript, which this tool "
            "cannot execute. Try a specific content page, or paste the copy manually."
        )
    site.pages.append(home)

    queue = _rank_links(soup, root_url, domain)
    seen = {_key(root_url)}
    total_chars = len(home.body)

    for url in queue:
        if len(site.pages) >= max_pages:
            break
        if time.monotonic() - started > TOTAL_BUDGET:
            site.skipped.append("time budget reached")
            break
        if total_chars >= MAX_TOTAL_CHARS:
            site.skipped.append("content budget reached")
            break
        if _key(url) in seen:
            continue
        seen.add(_key(url))

        time.sleep(CRAWL_DELAY)
        try:
            page_html = _get(session, url)
        except FetchError:
            continue  # a broken internal link is not fatal
        page = _to_page(url, BeautifulSoup(page_html, "lxml"))
        # Stub pages (empty templates, duplicated drafts) waste a crawl slot.
        if len(page.body) < 120 and len(page.headings) < 3:
            continue
        site.pages.append(page)
        total_chars += len(page.body)

    return site


# --------------------------------------------------------------------------- internals


def _host_variants(url: str) -> list[str]:
    """The URL as given, then its www / apex counterpart.

    People type the bare domain, but the two hosts are separate DNS names and
    are often configured separately — a lapsed certificate or a parking page on
    one says nothing about the other. accubreath.com is the case that prompted
    this: an expired certificate in front of a Wix "domain not connected" 404,
    while www.accubreath.com served the real site with a valid certificate.
    """
    parsed = urlparse(url)
    host = parsed.netloc
    if host.lower().startswith("www."):
        other = host[4:]
    else:
        other = f"www.{host}"
    return [url, urlunparse(parsed._replace(netloc=other))]


def _fetch_homepage(session: requests.Session, root_url: str) -> tuple[str, str, str]:
    """Resolve the homepage, trying the www / apex counterpart before failing.

    Returns (url that worked, its HTML, a note when it was not the one asked
    for). Certificate verification stays on for every attempt: a site whose
    only working host has a bad certificate is a finding to report, not one to
    quietly route around.
    """
    failures: list[str] = []
    for candidate in _host_variants(root_url):
        try:
            html = _get(session, candidate)
        except FetchError as exc:
            failures.append(str(exc))
            continue

        if candidate == root_url:
            return candidate, html, ""
        return candidate, html, f"{failures[0]} The analysis used {candidate} instead."

    # Lead with the error for the host the user actually asked for.
    detail = failures[0]
    if len(failures) > 1:
        detail += f" The {'apex' if 'www.' in root_url else 'www'} host also failed: {failures[1]}"
    raise FetchError(detail)


def _tls_reason(exc: Exception) -> str:
    """Plain-English cause of a certificate failure, from the SDK's chained text."""
    text = str(exc).lower()
    if "certificate has expired" in text:
        return "its TLS certificate has expired"
    if "self signed" in text or "self-signed" in text:
        return "its TLS certificate is self-signed"
    if "hostname mismatch" in text or "doesn't match" in text:
        return "its TLS certificate was issued for a different hostname"
    if "unable to get local issuer" in text:
        return "its TLS certificate chain is incomplete"
    return "its TLS certificate could not be verified"


def _connection_reason(exc: Exception) -> str:
    """Plain-English cause of a failed connection, from urllib3's chained text."""
    text = str(exc).lower()
    if "nameresolutionerror" in text or "getaddrinfo" in text or "name or service not known" in text:
        return "the domain name does not resolve"
    if "connection refused" in text or "newconnectionerror" in text:
        return "the server refused the connection"
    if "connection reset" in text:
        return "the server closed the connection"
    return "the connection failed"


def _get(session: requests.Session, url: str) -> str:
    try:
        resp = session.get(url, timeout=FETCH_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
    # SSLError and ConnectTimeout both subclass ConnectionError, so they have to
    # be matched before it or their specific wording is lost.
    except requests.exceptions.SSLError as exc:
        raise FetchError(f"{url} could not be reached securely — {_tls_reason(exc)}.") from exc
    except requests.exceptions.Timeout as exc:
        raise FetchError(f"{url} did not respond within {FETCH_TIMEOUT}s.") from exc
    except requests.exceptions.ConnectionError as exc:
        raise FetchError(f"{url} could not be reached — {_connection_reason(exc)}.") from exc
    except requests.exceptions.HTTPError as exc:
        raise FetchError(
            f"{url} returned HTTP {resp.status_code}."
            + (" The site may be blocking automated clients." if resp.status_code in (403, 429) else "")
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise FetchError(f"Could not reach {url}: {exc}") from exc

    if "html" not in resp.headers.get("content-type", "").lower():
        raise FetchError(f"{url} did not return HTML.")
    return resp.text


def _to_page(url: str, soup: BeautifulSoup) -> Page:
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()

    headings = _dedupe(h.get_text(" ", strip=True) for h in soup.select("h1, h2, h3"))[:20]

    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    for tag in main(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    body = _collapse(main.get_text("\n", strip=True))[:MAX_CHARS_PER_PAGE]
    return Page(url=url, title=title, headings=headings, body=body)


def _rank_links(soup: BeautifulSoup, root_url: str, domain: str) -> list[str]:
    """Internal links, priority paths first, then the rest in document order."""
    candidates: list[str] = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(root_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.lower().removeprefix("www.") != domain:
            continue
        if parsed.path.lower().endswith(_SKIP_EXTENSIONS):
            continue
        clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        if clean not in candidates:
            candidates.append(clean)

    return sorted(candidates, key=_priority)


def _priority(url: str) -> int:
    """Rank by path token. Short terms must match a whole token; longer terms
    may match a token prefix so /investors hits on "investor"."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", urlparse(url).path.lower()) if t]
    for i, term in enumerate(PRIORITY_PATHS):
        for token in tokens:
            if token == term or (len(term) >= 4 and token.startswith(term)):
                return i
    return len(PRIORITY_PATHS)


def _key(url: str) -> str:
    return urlparse(url).path.rstrip("/").lower() or "/"


def _collapse(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out
