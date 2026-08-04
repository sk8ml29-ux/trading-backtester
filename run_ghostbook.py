#!/usr/bin/env python3
"""
Ghost Book — command line entry point.

    python run_ghostbook.py signal   --capital 50000     today's target book
    python run_ghostbook.py validate                     full validation battery
    python run_ghostbook.py study                        information-coefficient study
    python run_ghostbook.py sweep                        cadence / cost sweeps
    python run_ghostbook.py fetch    --what metrics      refresh the data cache
    python run_ghostbook.py spec                         print the frozen strategy

The strategy trades a market-neutral cross-section of USDT perpetuals on a
signal reconstructed from public open-interest flow. See GUIDE_GHOSTBOOK.md for
the idea, the evidence and the legal notes.
"""
from __future__ import annotations

import argparse
import json
import sys


def cmd_signal(args) -> int:
    from research.ghostbook.live import build_book, print_book, save_book
    from pathlib import Path
    book = build_book(capital_usd=args.capital, workers=args.workers,
                      max_candidates=args.candidates)
    print_book(book, args.top)
    if book.get("ok"):
        print(f"\nwrote {save_book(book, Path(args.out) if args.out else None)}")
        return 0
    return 1


def cmd_spec(args) -> int:
    from research.ghostbook.strategy import SPEC
    print(json.dumps(SPEC.describe(), indent=2, default=str))
    return 0


def _delegate(module: str, argv: list[str]) -> int:
    import importlib
    sys.argv = [module] + argv
    importlib.import_module(module).main()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Ghost Book", add_help=True)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("signal", help="generate today's target book")
    s.add_argument("--capital", type=float, default=100_000.0)
    s.add_argument("--workers", type=int, default=48)
    s.add_argument("--candidates", type=int, default=240)
    s.add_argument("--top", type=int, default=15)
    s.add_argument("--out", default="")
    s.set_defaults(fn=cmd_signal)

    sub.add_parser("spec", help="print the frozen strategy definition").set_defaults(fn=cmd_spec)

    for name, mod in [("validate", "research.ghostbook.validate"),
                      ("study", "research.ghostbook.study"),
                      ("sweep", "research.ghostbook.sweep"),
                      ("fetch", "research.ghostbook.vision_bulk")]:
        p = sub.add_parser(name, help=f"run {name} (extra flags are passed through)")
        p.set_defaults(fn=lambda a, m=mod: _delegate(m, a.rest), rest=[])

    args, rest = ap.parse_known_args()
    args.rest = rest
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
