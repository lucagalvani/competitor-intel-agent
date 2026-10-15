"""Offline tests: a fake Claude and fake web pages, so they run for free and without network."""

import pytest
import yaml

from ci_agent import collectors
from ci_agent.config import ConfigError, Source, parse_config
from ci_agent.pipeline import run
from ci_agent.store import Store, list_diff, normalize, page_diff

BASE = {
    "company": {"name": "Us", "description": "We sell widgets."},
    "competitors": [
        {
            "name": "Rival Inc",
            "sources": [
                {"type": "pricing", "url": "https://rival.example/pricing"},
                {"type": "news"},
            ],
        }
    ],
    "frameworks": ["battlecard"],
    "outputs": [],
}


class FakeLLM:
    def __init__(self):
        self.calls = []

    def text(self, system, prompt):
        self.calls.append(("text", prompt))
        return "# Report\nfake content"

    def json(self, system, prompt, schema):
        self.calls.append(("json", prompt))
        return {
            "changes": [
                {
                    "source": "pricing",
                    "category": "pricing",
                    "significance": "high",
                    "summary": "Pro plan went from $10 to $12",
                    "evidence": "+ Pro $12/month",
                    "implication": "We can undercut them",
                }
            ]
        }

    def research(self, system, prompt):
        return "- 2026-10-01 | Rival raises $10M | https://news.example/1"


# ── config ─────────────────────────────────────────────────────


def test_config_parses_and_slugs(tmp_path):
    cfg = parse_config(BASE, base_dir=tmp_path)
    comp = cfg.competitors[0]
    assert comp.slug == "rival-inc"
    assert [s.id for s in comp.sources] == ["pricing", "news"]
    assert "Us" in cfg.company_context()


def test_config_rejects_bad_values():
    with pytest.raises(ConfigError, match="needs a 'url'"):
        parse_config({"competitors": [{"name": "X", "sources": [{"type": "pricing"}]}]})
    with pytest.raises(ConfigError, match="not valid"):
        parse_config({"competitors": [{"name": "X", "sources": [{"type": "tiktok"}]}]})
    with pytest.raises(ConfigError, match="distinct"):
        parse_config(
            {"competitors": [{"name": "X", "sources": [{"type": "news"}, {"type": "news"}]}]}
        )


def test_example_configs_are_valid():
    from pathlib import Path

    root = Path(__file__).parent.parent
    for path in [root / "config.example.yaml", *sorted((root / "examples").glob("*.yaml"))]:
        parse_config(yaml.safe_load(path.read_text()))


# ── diffing ────────────────────────────────────────────────────


def test_normalize_ignores_whitespace_and_copyright():
    assert normalize("Hello   world\n\n© 2026 Rival\n  Pricing ") == "Hello world\nPricing\n"


def test_page_diff_shows_added_and_removed():
    diff = page_diff("Pro $10\nTeam $20\n", "Pro $12\nTeam $20\n")
    assert "-Pro $10" in diff and "+Pro $12" in diff


def test_list_diff_only_reports_new_items():
    old = "- 2026-09-01 | A | https://x/a\n- 2026-09-02 | B | https://x/b\n"
    new = "- 2026-09-02 | B (updated title) | https://x/b\n- 2026-10-01 | C | https://x/c\n"
    assert list_diff(old, new) == "+ - 2026-10-01 | C | https://x/c"


def test_list_sources_remember_old_items(tmp_path):
    store = Store(tmp_path)
    src = Source(type="news")
    store.update_snapshot("rival", src, "- d | A | https://x/a")
    store.update_snapshot("rival", src, "- d | B | https://x/b")
    # A dropped out of the search results but must not come back as "new" later
    d = store.update_snapshot("rival", src, "- d | A | https://x/a")
    assert not d.changed


# ── full pipeline ──────────────────────────────────────────────


def test_pipeline_baseline_then_change(tmp_path, monkeypatch):
    pages = {"https://rival.example/pricing": "Pro $10/month"}
    monkeypatch.setitem(collectors.COLLECTORS, "pricing", lambda s, c, llm: pages[s.url])
    cfg = parse_config(BASE, base_dir=tmp_path)
    llm = FakeLLM()

    # Run 1: everything is new -> baseline, reports created, no changes classified
    r1 = run(cfg, llm, notify=False, log=lambda *a: None)
    assert r1.baseline == ["Rival Inc"]
    assert r1.changes == []
    assert (tmp_path / "data/reports/rival-inc/battlecard.md").exists()
    assert not any(kind == "json" for kind, _ in llm.calls)

    # Run 2: nothing changed -> no Claude calls for classification or reports
    llm.calls.clear()
    r2 = run(cfg, llm, notify=False, log=lambda *a: None)
    assert r2.changes == [] and r2.reports == []

    # Run 3: price changed -> classified, report regenerated, digest written, change logged
    pages["https://rival.example/pricing"] = "Pro $12/month"
    r3 = run(cfg, llm, notify=False, log=lambda *a: None)
    assert len(r3.changes) == 1 and r3.changes[0]["competitor"] == "Rival Inc"
    json_prompt = next(p for kind, p in llm.calls if kind == "json")
    assert "-Pro $10/month" in json_prompt and "+Pro $12/month" in json_prompt
    assert r3.digest_path.exists()
    assert Store(tmp_path / "data").read_changes()[0]["summary"].startswith("Pro plan")


def test_one_broken_source_does_not_stop_the_run(tmp_path, monkeypatch):
    def broken(s, c, llm):
        raise RuntimeError("site down")

    monkeypatch.setitem(collectors.COLLECTORS, "pricing", broken)
    cfg = parse_config(BASE, base_dir=tmp_path)
    result = run(cfg, FakeLLM(), notify=False, log=lambda *a: None)
    assert result.errors == ["Rival Inc / pricing: site down"]
    assert "site down" in result.digest_path.read_text()


def test_no_llm_mode_skips_search_sources(tmp_path, monkeypatch):
    monkeypatch.setitem(collectors.COLLECTORS, "pricing", lambda s, c, llm: "Pro $10")
    cfg = parse_config(BASE, base_dir=tmp_path)
    result = run(cfg, None, notify=False, log=lambda *a: None)
    assert result.digest_path is None
    assert Store(tmp_path / "data").read_snapshot("rival-inc", "news") is None
    assert Store(tmp_path / "data").read_snapshot("rival-inc", "pricing") == "Pro $10\n"
