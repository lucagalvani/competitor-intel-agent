# competitor-intel-agent

A **company-agnostic competitive intelligence agent**. Point it at any company and it will:

1. **Find the competitors** (`ci-agent init --company "Notion"`)
2. **Watch their public sources** every week: homepage, pricing, changelog, careers, blog/RSS, news and reviews
3. **Detect what changed** since last week and filter out the noise
4. **Explain what it means** for you: each change is classified (pricing, product, positioning, go-to-market, hiring, corporate…) with a significance level and an implication
5. **Keep battle cards, positioning teardowns and SWOTs up to date**
6. **Send a weekly digest** and instant alerts for important moves (GitHub Issue, Slack, email)
7. **Answer questions** from everything it has collected (`ci-agent ask "How does Legora price agents?"`)

It runs for free on **GitHub Actions**, uses the **repo as its database** (git history shows how every competitor page evolved), and uses **Claude** for the analysis.

```
Weekly cron (GitHub Action)
   │
   ├─ 1. COLLECT   fetch each source in config.yaml (web pages, RSS, Claude web search)
   ├─ 2. SNAPSHOT  clean text → data/snapshots/<competitor>/<source>.md
   ├─ 3. DIFF      compare with last week (page diff, or "new items" for feeds/searches)
   ├─ 4. CLASSIFY  Claude keeps meaningful changes: category, significance, implication
   ├─ 5. REPORTS   Claude updates data/reports/<competitor>/{battlecard,positioning,swot}.md
   ├─ 6. DIGEST    data/digests/<date>.md: summary, implications, recommended actions
   └─ 7. NOTIFY    GitHub Issue / Slack / email  (+ instant alerts for high-significance changes)
```

## Quick start

```bash
git clone https://github.com/lucagalvani/competitor-intel-agent
cd competitor-intel-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

export ANTHROPIC_API_KEY=sk-ant-...

# 1. Generate a config for any company (researches competitors and their URLs)
ci-agent init --company "Your Company" --market "Europe"

# 2. Review config.yaml, then run. The first run captures a baseline and writes the battle cards.
ci-agent run --no-notify

# 3. Ask questions
ci-agent ask "Which competitor changed pricing recently and how?"
```

Prefer to write the config yourself? Copy `config.example.yaml` to `config.yaml`. See `examples/` for two complete configs in very different markets (legal AI and project management).

## Run it every week on GitHub

1. Push your repo (with your `config.yaml`) to GitHub.
2. **Settings → Secrets and variables → Actions** → add `ANTHROPIC_API_KEY`.
   Optional: `SLACK_WEBHOOK_URL`, and `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` for email.
3. The workflow in `.github/workflows/competitor-intel.yml` runs every Monday at 07:00 UTC (or click **Actions → Competitor intelligence → Run workflow**).
   It commits the new snapshots and reports back to the repo and opens an issue with the digest.

## Commands

| Command | What it does |
|---|---|
| `ci-agent init --company X [--market Y]` | Research X's competitors and write `config.yaml` |
| `ci-agent run` | Full run: collect → diff → classify → reports → digest → notify |
| `ci-agent run --no-llm` | Only collect and diff (free; search sources are skipped). Good for testing URLs |
| `ci-agent run --competitor "Name"` | Run for one competitor |
| `ci-agent run --refresh-reports` | Rewrite all reports even if nothing changed |
| `ci-agent run --no-notify` | Save files but don't send issues/Slack/email |
| `ci-agent ask "question"` | Answer from reports, digests, change log and snapshots |
| `ci-agent list` | Show configured competitors and sources |

Use `-c path/to/config.yaml` to pick another config, e.g. `ci-agent -c examples/legal-ai.yaml run`.

## Sources

| `type` | Needs | How it's collected | How changes are detected |
|---|---|---|---|
| `homepage`, `pricing`, `changelog`, `careers`, `blog`, `docs` | `url` | Fetch page → main text (menus, footers, cookie banners removed) | Line diff |
| `page` | `url`, `name` | Same, for any other page | Line diff |
| `rss` | `url` | RSS/Atom feed items | New items only |
| `news` | – | Claude web search for funding, launches, M&A, partnerships | New items only |
| `reviews` | – | Claude web search on G2, Capterra, Reddit… | New items only |
| `search` | `query`, `name` | Claude web search with your own query | New items only |

Add `render: true` to a page source for JavaScript-heavy sites (`pip install -e '.[browser]' && playwright install chromium`).
The agent respects `robots.txt` and waits between requests to the same site.

## Customising the analysis

The analyses are plain markdown templates in `ci_agent/frameworks/`:

- `battlecard.md`: TL;DR, positioning, pricing, strengths, weaknesses, landmines, objection handling
- `positioning.md`: the four positioning questions (what is it, who is it for, what does it replace, why is it better), segment maturity, messaging hierarchy
- `swot.md`: SWOT plus "so what for us"

To add your own, create a folder (e.g. `my_frameworks/`), put a `.md` file with instructions in it (`{competitor}` and `{today}` are filled in), and add it to the config:

```yaml
frameworks_dir: my_frameworks
frameworks: [battlecard, positioning, pricing_watch]
```

Describing your own company under `company:` (and optionally a `positioning_file`) makes every analysis relative to you: "what this means for us", "how we beat them".

## Cost

Unchanged pages cost nothing: Claude is only called when something changed, plus one call per news/review search. The default model is Claude Sonnet 5.5 (half the price of Opus 5.5); switch to `claude-opus-5-5` under `llm:` if you want deeper analysis.

## Project layout

```
ci_agent/
├── cli.py          command line
├── config.py       load + validate config.yaml
├── collectors.py   one collector per source type (add yours here)
├── fetch.py        polite page fetching + text extraction
├── store.py        snapshots, diffs, reports, digests, change log
├── analyzer.py     Claude: classify changes, update reports, write digest
├── outputs.py      GitHub Issue, Slack, email (add yours here)
├── pipeline.py     the weekly run, end to end
├── discover.py     `init`: competitor discovery
├── ask.py          `ask`: Q&A over collected data
└── frameworks/     analysis templates
```

Run the tests (offline, no API key needed): `pip install -e '.[dev]' && pytest`

## License

MIT
