"""CLI entrypoint for the Fundraising Readiness One-Pager generator.

    python main.py --deck deck.pdf [--url https://example.com] \
                   [--company "Name"] [--out output/]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from analyze import AnalysisError, analyze
from ingest import SUPPORTED_DECK_SUFFIXES, IngestError, extract_deck, fetch_site
from render import RenderError, render

DEFAULT_OUT = Path(__file__).resolve().parent / "output"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Generate a one-page Fundraising Readiness Assessment PDF from a "
            "pitch deck and, optionally, the company's website."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python main.py --deck decks/acme.pdf\n"
            "  python main.py --deck decks/acme.pptx --url https://www.gridmatrix.com/\n"
            '  python main.py --deck decks/acme.pdf --company "Acme Bio" --out ~/reports\n'
        ),
    )
    p.add_argument(
        "--deck",
        required=True,
        type=Path,
        metavar="PATH",
        help="Pitch deck to analyze (%s)." % ", ".join(sorted(SUPPORTED_DECK_SUFFIXES)),
    )
    p.add_argument(
        "--url",
        metavar="URL",
        help="Company website. When given, the site review is folded into the analysis.",
    )
    p.add_argument(
        "--company",
        metavar="NAME",
        help="Company name. Inferred from the deck or site when omitted.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        metavar="DIR",
        help=f"Output directory (default: {DEFAULT_OUT}).",
    )
    return p


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_")
    return slug[:60] or "company"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        print(f"Reading deck: {args.deck}", file=sys.stderr)
        deck = extract_deck(args.deck)
        print(
            f"  extracted {deck.unit_count} {deck.unit_label}s "
            f"({len(deck.text):,} chars)",
            file=sys.stderr,
        )

        site = None
        if args.url:
            print(f"Fetching site: {args.url}", file=sys.stderr)
            try:
                site = fetch_site(args.url)
                print(
                    f"  extracted {len(site.headings)} headings, "
                    f"{len(site.ctas)} CTAs ({len(site.body):,} chars)",
                    file=sys.stderr,
                )
            except IngestError as exc:
                # An unreachable site degrades the report; it should not kill it.
                print(f"  warning: {exc}", file=sys.stderr)
                print("  continuing with deck-only analysis.", file=sys.stderr)

        print("Analyzing...", file=sys.stderr)
        analysis = analyze(deck, site=site, company=args.company)
        print(f"  readiness score: {analysis.readiness_score}/100", file=sys.stderr)

        out_path = args.out / f"{slugify(analysis.company)}_readiness_onepager.pdf"
        render(analysis, out_path)

    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except RenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
