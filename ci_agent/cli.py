"""Command line: `ci-agent init | run | ask | list`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, LLMConfig, load_config


def _llm(cfg_llm: LLMConfig):
    from .llm import LLM

    return LLM(cfg_llm)


def cmd_init(args) -> int:
    from .discover import build_config, discover, to_yaml

    out = Path(args.output)
    if out.exists() and not args.force:
        print(f"{out} already exists. Use --force to overwrite it.")
        return 1
    print(f"🔎 Researching {args.company} and its competitors (this takes a minute or two)…")
    found = discover(_llm(LLMConfig()), args.company, args.max_competitors, args.market)
    config = build_config(found)
    out.write_text(to_yaml(config, found))
    print(f"✅ Wrote {out} with {len(config['competitors'])} competitors:")
    for comp in found["competitors"]:
        print(f"   - {comp['name']}: {comp['why']}")
    print("Review the file, then run: ci-agent run")
    return 0


def cmd_run(args) -> int:
    from .pipeline import run

    cfg = load_config(args.config)
    llm = None if args.no_llm else _llm(cfg.llm)
    result = run(cfg, llm, only=args.competitor, notify=not args.no_notify, refresh_reports=args.refresh_reports)
    print(f"\nDone: {len(result.changes)} change(s), {len(result.reports)} report(s) updated, {len(result.errors)} error(s).")
    collected_anything = len(result.errors) < sum(len(c.sources) for c in cfg.competitors)
    return 0 if collected_anything else 1


def cmd_ask(args) -> int:
    from .ask import ask

    cfg = load_config(args.config)
    print(ask(cfg, _llm(cfg.llm), args.question))
    return 0


def cmd_list(args) -> int:
    cfg = load_config(args.config)
    for comp in cfg.competitors:
        print(f"{comp.name}")
        for s in comp.sources:
            print(f"  - {s.id:<16} {s.type:<10} {s.url or s.query or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # -c works both before and after the command: `ci-agent -c x.yaml run` or `ci-agent run -c x.yaml`
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=argparse.SUPPRESS, help="path to config file (default: config.yaml)")
    parser = argparse.ArgumentParser(
        prog="ci-agent", description="Company-agnostic competitive intelligence agent", parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_command(name, help_text):
        return sub.add_parser(name, help=help_text, parents=[common])

    p = add_command("init", "research a company's competitors and generate config.yaml")
    p.add_argument("--company", required=True, help='company name or website, e.g. "Notion" or notion.so')
    p.add_argument("--market", default="", help='optional focus, e.g. "Spain" or "SMB"')
    p.add_argument("--max-competitors", type=int, default=6)
    p.add_argument("-o", "--output", default="config.yaml")
    p.add_argument("--force", action="store_true", help="overwrite an existing config file")
    p.set_defaults(func=cmd_init)

    p = add_command("run", "collect sources, detect changes, update reports, send the digest")
    p.add_argument("--competitor", help="only run for this competitor")
    p.add_argument("--no-notify", action="store_true", help="save files but don't send issues/Slack/email")
    p.add_argument("--no-llm", action="store_true", help="only collect and diff, no Claude calls (free)")
    p.add_argument("--refresh-reports", action="store_true", help="regenerate reports even without changes")
    p.set_defaults(func=cmd_run)

    p = add_command("ask", "ask a question about your competitors")
    p.add_argument("question")
    p.set_defaults(func=cmd_ask)

    p = add_command("list", "show the configured competitors and sources")
    p.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    if not hasattr(args, "config"):
        args.config = "config.yaml"
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:  # LLMError, FetchError, OutputError
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
