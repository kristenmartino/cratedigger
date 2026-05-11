"""Shared diagnostic helper for selector-based scrapers.

When a scraper gets 200 OK but finds zero product cards, the most useful
question is "what does the HTML actually look like?" — not "did the request
succeed?" (we already know it did). This helper logs a compact snapshot of
the response body so we can iterate selectors from the workflow output
without having to re-fetch.

Kept private to the sources package; never log full bodies or anything that
could include user-attributable data — these are public retail listing pages,
but caution is cheap.
"""
from __future__ import annotations

import logging

from selectolax.parser import HTMLParser

_SNIPPET_BYTES = 2000


def log_no_cards_diagnostic(
    logger: logging.Logger, slug: str, html: str
) -> None:
    """Log a structured snapshot to help iterate selectors after a no-cards run.

    Output:
      - body length (bytes)
      - <title> text (proves we got the right page, not a CDN block-page)
      - counts of the common card-container tags (article / li / div)
      - first ~2KB of the <body> HTML (skips <head> noise)
    """
    body_len = len(html)
    tree = HTMLParser(html)

    title_el = tree.css_first("title")
    title = title_el.text(strip=True) if title_el else "(no <title>)"

    counts = {tag: len(tree.css(tag)) for tag in ("article", "li", "div")}

    body_el = tree.css_first("body")
    body_html = (body_el.html if body_el else html) or ""
    snippet = body_html[:_SNIPPET_BYTES]

    logger.warning(
        "%s: no cards diagnostic — body_len=%d title=%r elem_counts=%s "
        "first_%dB_of_body=\n%s",
        slug,
        body_len,
        title,
        counts,
        _SNIPPET_BYTES,
        snippet,
    )
