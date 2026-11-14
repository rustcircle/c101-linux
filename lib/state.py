#!/usr/bin/env python3
"""State file for c101-linux: records what the overlay added, so uninstall removes only that.

Stdlib only, Python 3.9+. The file is JSON, written atomically with mode 0600.

  state.py --file F set KEY JSON      set a dotted key to a JSON value
  state.py --file F append KEY JSON   append to a list at KEY (no duplicates)
  state.py --file F get KEY           print the value (strings raw, others as JSON); exit 1 if missing
  state.py --file F del KEY           delete a key
  state.py --file F dump              print the whole file
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional

VERSION = 1


def load(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"version": VERSION}
    except ValueError as exc:
        raise SystemExit("state file %s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise SystemExit("state file %s: top level must be an object" % path)
    return data


def save(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _walk(data: Dict[str, Any], key: str, create: bool) -> Optional[Dict[str, Any]]:
    node: Any = data
    for part in key.split(".")[:-1]:
        if not isinstance(node, dict):
            return None
        if part not in node:
            if not create:
                return None
            node[part] = {}
        node = node[part]
    return node if isinstance(node, dict) else None


def get(data: Dict[str, Any], key: str) -> Any:
    parent = _walk(data, key, create=False)
    leaf = key.split(".")[-1]
    if parent is None or leaf not in parent:
        raise KeyError(key)
    return parent[leaf]


def set_value(data: Dict[str, Any], key: str, value: Any) -> None:
    parent = _walk(data, key, create=True)
    if parent is None:
        raise SystemExit("cannot set %s: a parent is not an object" % key)
    parent[key.split(".")[-1]] = value


def append(data: Dict[str, Any], key: str, value: Any) -> None:
    parent = _walk(data, key, create=True)
    if parent is None:
        raise SystemExit("cannot append to %s: a parent is not an object" % key)
    leaf = key.split(".")[-1]
    cur: List[Any] = parent.setdefault(leaf, [])
    if not isinstance(cur, list):
        raise SystemExit("cannot append to %s: not a list" % key)
    if value not in cur:
        cur.append(value)


def delete(data: Dict[str, Any], key: str) -> None:
    parent = _walk(data, key, create=False)
    if parent is not None:
        parent.pop(key.split(".")[-1], None)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="state.py")
    p.add_argument("--file", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("set", "append"):
        s = sub.add_parser(name)
        s.add_argument("key")
        s.add_argument("value", help="JSON")
    for name in ("get", "del"):
        s = sub.add_parser(name)
        s.add_argument("key")
    sub.add_parser("dump")
    a = p.parse_args(argv)
    data = load(a.file)
    if a.cmd == "dump":
        json.dump(data, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    if a.cmd == "get":
        try:
            val = get(data, a.key)
        except KeyError:
            return 1
        sys.stdout.write(val if isinstance(val, str) else json.dumps(val))
        sys.stdout.write("\n")
        return 0
    if a.cmd in ("set", "append"):
        try:
            val = json.loads(a.value)
        except ValueError as exc:
            raise SystemExit("value is not JSON: %s" % exc)
        (set_value if a.cmd == "set" else append)(data, a.key, val)
    else:
        delete(data, a.key)
    data.setdefault("version", VERSION)
    save(a.file, data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
