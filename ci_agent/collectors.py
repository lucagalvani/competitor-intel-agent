"""Collectors: one per kind of source. Each turns a Source into a text snapshot.

To support a new kind of source, write a function `collect_x(source, competitor, llm) -> str`
and register it in COLLECTORS below.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import Callable

from .config import PAGE_TYPES, Competitor, Source
from .fetch import fetch_html, fetch_page_text
from .llm import LLM

MAX_FEED_ITEMS = 25


def collect_page(source: Source, competitor: Competitor, llm: LLM | None) -> str:
    return fetch_page_text(source.url, render=source.render)


def collect_rss(source: Source, competitor: Competitor, llm: LLM | None) -> str:
    """RSS or Atom feed -> one line per item, e.g. '- 2026-10-01 | Title | https://…'."""
    root = ET.fromstring(fetch_html(source.url))
    atom = "{http://www.w3.org/2005/Atom}"
    lines = []
    for item in root.iter("item"):  # RSS
        lines.append(_feed_line(item.findtext("pubDate"), item.findtext("title"), item.findtext("link")))
    for entry in root.iter(f"{atom}entry"):  # Atom
        link = entry.find(f"{atom}link")
        lines.append(
            _feed_line(
                entry.findtext(f"{atom}updated") or entry.findtext(f"{atom}published"),
                entry.findtext(f"{atom}title"),
                link.get("href") if link is not None else "",
            )
        )
    return "\n".join(lines[:MAX_FEED_ITEMS])


def _feed_line(when: str | None, title: str | None, link: str | None) -> str:
    return f"- {(when or '').strip()} | {(title or '').strip()} | {(link or '').strip()}"


SEARCH_PROMPTS = {
    "news": (
        "Find recent news about the company {name}{site}: funding, acquisitions, product launches, "
        "partnerships, new markets, leadership changes, layoffs."
    ),
    "reviews": (
        "Find recent customer reviews and opinions about {name}{site} on review sites (G2, Capterra, "
        "Trustpilot, Reddit, forums). Focus on recurring praise and complaints."
    ),
    "search": "{query}",
}


def collect_search(source: Source, competitor: Competitor, llm: LLM | None) -> str:
    """Use Claude's web search to collect items, returned as one stable line per item."""
    if llm is None:
        raise RuntimeError("search sources need the Claude API (they can't run with --no-llm)")
    site = f" ({competitor.website})" if competitor.website else ""
    task = SEARCH_PROMPTS[source.type].format(name=competitor.name, site=site, query=source.query or "")
    prompt = (
        f"Today is {date.today().isoformat()}.\n{task}\n\n"
        "Return ONLY a markdown list, one item per line, newest first, at most 15 items, in this exact format:\n"
        "- YYYY-MM-DD | short factual headline | source URL\n"
        "Use the publication date. Only include items you actually found, about this specific company. "
        "If you find nothing, return an empty answer."
    )
    answer = llm.research("You are a meticulous market research assistant.", prompt)
    return "\n".join(line.strip() for line in answer.splitlines() if line.strip().startswith("- "))


Collector = Callable[[Source, Competitor, "LLM | None"], str]

COLLECTORS: dict[str, Collector] = {
    **{t: collect_page for t in PAGE_TYPES},
    "rss": collect_rss,
    "news": collect_search,
    "reviews": collect_search,
    "search": collect_search,
}


def collect(source: Source, competitor: Competitor, llm: LLM | None) -> str:
    return COLLECTORS[source.type](source, competitor, llm)
