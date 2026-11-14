#!/usr/bin/env python3
"""Check install-pack names against c101's config/packs.json. Stdlib only, Python 3.9+. Reads, never writes.

  packs.py --packs-json FILE validate NAME...  NAMEs may be comma lists. Prints each unknown name on its own line
                                              (stdout); exit 0 when every name is known, 2 when any is unknown.
  packs.py --packs-json FILE list             print the known pack names, one per line, in file order
  packs.py --packs-json FILE resolve NAME...  print the selection plus its transitive 'requires' as a comma list,
                                              core first, the way c101's stack/packs.py resolve() does; no NAME or
                                              'all' means every pack. Unknown names: printed as by validate, exit 2.

'all' is accepted as a name because c101 accepts it (= every pack). Exit 1 when FILE is missing or is not a
packs.json, or on a usage error, so exit 2 always means "unknown pack name".
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Iterable, List

ALL = "all"
CORE = "core"


def load_packs(path: str) -> Dict[str, Any]:
    """The "packs" object of packs.json (name -> definition)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise SystemExit("packs.py: cannot read %s: %s" % (path, exc.strerror or exc))
    except ValueError as exc:
        raise SystemExit("packs.py: %s is not valid JSON: %s" % (path, exc))
    packs = data.get("packs") if isinstance(data, dict) else None
    if not isinstance(packs, dict) or not packs:
        raise SystemExit("packs.py: %s has no \"packs\" object" % path)
    return packs


def split_names(args: Iterable[str]) -> List[str]:
    """Flatten comma/space separated arguments into a de-duplicated list, keeping the first-seen order."""
    out: List[str] = []
    for arg in args:
        for part in arg.replace(" ", ",").split(","):
            name = part.strip()
            if name and name not in out:
                out.append(name)
    return out


def unknown_names(names: Iterable[str], packs: Dict[str, Any]) -> List[str]:
    return [n for n in names if n != ALL and n not in packs]


def requires_of(packs: Dict[str, Any], name: str) -> List[str]:
    spec = packs.get(name)
    req = spec.get("requires", []) if isinstance(spec, dict) else []
    return [r for r in req if isinstance(r, str)] if isinstance(req, list) else []


def resolve(names: List[str], packs: Dict[str, Any]) -> List[str]:
    """Selection plus transitive requirements, core first (mirrors c101 stack/packs.py resolve(), cycle-safe)."""
    out: List[str] = []
    visiting: List[str] = []

    def visit(p: str) -> None:
        if p in out or p in visiting:
            return
        visiting.append(p)
        for r in requires_of(packs, p):
            if r in packs:
                visit(r)
        visiting.remove(p)
        out.append(p)

    if CORE in packs:
        visit(CORE)
    selected = names or [ALL]
    for n in selected:
        if n == ALL:
            for q in packs:
                visit(q)
        else:
            visit(n)
    return out


def report_unknown(unknown: List[str]) -> int:
    for n in unknown:
        sys.stdout.write(n + "\n")
    return 2


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="packs.py", description="check c101 install-pack names")
    p.add_argument("--packs-json", required=True, help="c101's config/packs.json")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "resolve"):
        s = sub.add_parser(name)
        s.add_argument("names", nargs="*")
    sub.add_parser("list")
    try:
        a = p.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on a usage error; keep 2 for "unknown pack name"
        return 0 if exc.code in (0, None) else 1
    packs = load_packs(a.packs_json)
    if a.cmd == "list":
        for n in packs:
            sys.stdout.write(n + "\n")
        return 0
    names = split_names(a.names)
    unknown = unknown_names(names, packs)
    if unknown:
        return report_unknown(unknown)
    if a.cmd == "resolve":
        sys.stdout.write(",".join(resolve(names, packs)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
