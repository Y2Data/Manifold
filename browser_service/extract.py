"""Main-content extraction from raw page HTML.

trafilatura over readability-lxml/justext: best accuracy/robustness in
published boilerplate-removal benchmarks, with zero manual heuristic tuning
needed — returns clean text directly, matching this codebase's preference
for using what works out of the box over hand-rolled extraction rules.
"""

from __future__ import annotations

import trafilatura

from browser_service.models import ExtractedPage


def extract_text(html: str, url: str) -> ExtractedPage | None:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_recall=True)
    if not text:
        return None
    metadata = trafilatura.extract_metadata(html)
    title = (metadata.title if metadata and metadata.title else None) or url
    return ExtractedPage(url=url, title=title, text=text)
