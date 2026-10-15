"""`ci-agent ask "question"`: answer questions from everything the agent has collected."""

from __future__ import annotations

import json

from .config import Config
from .llm import LLM
from .store import Store

SNAPSHOT_BUDGET = 400_000  # characters of raw snapshots to include, on top of reports and digests


def ask(cfg: Config, llm: LLM, question: str) -> str:
    store = Store(cfg.data_path)
    reports = store.all_reports()
    digests = store.recent_digests(8)
    changes = store.read_changes()
    if not (reports or digests or changes):
        return "No data yet — run `ci-agent run` first."

    sections = ["## Reports (battle cards and analyses)"]
    sections += [f"### {name}\n{text}" for name, text in reports.items()]
    sections.append("## Recent digests")
    sections += [f"### {day}\n{text}" for day, text in digests.items()]
    sections.append("## Change log (oldest first)")
    sections += [json.dumps(c, ensure_ascii=False) for c in changes]

    used = 0
    snapshot_parts = []
    for competitor in cfg.competitors:
        for source_id, text in store.all_snapshots(competitor.slug).items():
            if used + len(text) > SNAPSHOT_BUDGET:
                break
            snapshot_parts.append(f"### {competitor.name} / {source_id}\n{text}")
            used += len(text)
    if snapshot_parts:
        sections.append("## Raw source snapshots (latest)")
        sections += snapshot_parts

    knowledge = "\n\n".join(sections)
    prompt = f"""{cfg.company_context()}

Here is everything our competitive intelligence agent has collected:

{knowledge}

Question: {question}

Answer using only the material above. Cite which report, digest date or source you are relying on.
If the material doesn't contain the answer, say so and suggest which source to add to the config."""
    return llm.text(
        f"You are a competitive intelligence analyst answering questions from a product marketing team. Write in {cfg.language}.",
        prompt,
    )
