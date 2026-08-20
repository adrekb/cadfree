"""Headless tool runner so a DeepSeek Harness plugin can shell into Cadfree."""

from __future__ import annotations

import argparse
import json
import sys

from cadfree.agent.tools import make_handlers
from cadfree.store.db import init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cadfree")
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tool")
    t.add_argument("project_id")
    t.add_argument("name")
    t.add_argument("args_json", nargs="?", default="{}")
    args = parser.parse_args(argv)
    init_db()
    if args.cmd == "tool":
        handlers = make_handlers(args.project_id)
        if args.name not in handlers:
            print(json.dumps({"error": f"unknown tool {args.name}"}))
            return 2
        payload = json.loads(args.args_json)
        result = handlers[args.name](**payload) if payload else handlers[args.name]()
        print(json.dumps(result, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
