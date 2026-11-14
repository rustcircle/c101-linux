#!/usr/bin/env python3
"""Small JSON probes and one edit for ~/.claude/settings.json. Stdlib only, Python 3.9+.

  jsontool.py valid FILE                       exit 0 when FILE is a JSON object
  jsontool.py hook-count FILE MARK             number of hook commands containing MARK (prints it)
  jsontool.py strip-hooks FILE MARK [--dry-run]  remove hook commands containing MARK (backup first)
  jsontool.py get FILE KEY                     print a dotted key (exit 1 when missing)
  jsontool.py strip-keys FILE KEY... [--dry-run]  delete top-level keys; removes FILE when it ends up empty (backup first)
  jsontool.py has-keys FILE KEY...             print the JSON list of those top-level keys that exist
  jsontool.py env-snapshot FILE                print the sorted NAMES of the "env" object as a JSON list (or []; never values)
  jsontool.py env-names -                      stdin: a JSON list or object (an older state's snapshot); print its sorted names
  jsontool.py strip-env FILE SOURCE BEFORE [--dry-run]  drop env keys c101 added (see strip_env); BEFORE is a JSON list
                                               of names (an older state's object works too, its keys count), or - to read it
                                               from stdin so no value ever sits on a command line
Writes go through symlinks and refuse /nix/store targets.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List


def load(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("top level is not an object")
    return data


def env_names(snapshot: Any) -> List[str]:
    """The key names of an env snapshot: a list of names (current state files) or an object (older ones: only its keys count)."""
    if isinstance(snapshot, dict):
        return sorted(str(k) for k in snapshot)
    if isinstance(snapshot, list):
        return sorted(str(k) for k in snapshot)
    return []


def count_hooks(data: Dict[str, Any], mark: str) -> int:
    n = 0
    for groups in (data.get("hooks") or {}).values():
        for g in groups if isinstance(groups, list) else []:
            for h in g.get("hooks", []) if isinstance(g, dict) else []:
                if isinstance(h, dict) and mark in str(h.get("command", "")):
                    n += 1
    return n


def strip_hooks(data: Dict[str, Any], mark: str) -> int:
    removed = 0
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        keep: List[Any] = []
        for g in groups:
            if not isinstance(g, dict):
                keep.append(g)
                continue
            inner = g.get("hooks", [])
            kept = [h for h in inner if not (isinstance(h, dict) and mark in str(h.get("command", "")))]
            removed += len(inner) - len(kept)
            if kept:
                g = dict(g)
                g["hooks"] = kept
                keep.append(g)
        if keep:
            hooks[event] = keep
        else:
            del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    return removed


def real_target(path: str) -> str:
    real = os.path.realpath(path)
    if real.startswith("/nix/store/"):
        raise SystemExit("%s points into /nix/store; edit the Nix configuration instead" % path)
    return real


def write_json_atomic(path: str, data: Dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".c101l-", dir=os.path.dirname(os.path.abspath(path)))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, os.stat(path).st_mode & 0o7777)
    os.replace(tmp, path)


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd, path = argv[0], argv[1]
    if cmd == "env-names":
        try:
            print(json.dumps(env_names(json.loads(sys.stdin.read() or "[]"))))
        except ValueError as exc:
            print("invalid JSON on stdin: %s" % exc, file=sys.stderr)
            return 2
        return 0
    try:
        data = load(path)
    except FileNotFoundError:
        print("missing: %s" % path, file=sys.stderr)
        return 4
    except ValueError as exc:
        print("invalid JSON in %s: %s" % (path, exc), file=sys.stderr)
        return 2
    if cmd == "valid":
        return 0
    if cmd == "hook-count":
        print(count_hooks(data, argv[2]))
        return 0
    if cmd == "get":
        node: Any = data
        for part in argv[2].split("."):
            if not isinstance(node, dict) or part not in node:
                return 1
            node = node[part]
        print(node if isinstance(node, str) else json.dumps(node))
        return 0
    if cmd == "has-keys":
        print(json.dumps([k for k in argv[2:] if k in data]))
        return 0
    if cmd == "env-snapshot":
        # Names only (R3): a token in the user's env must never be copied into state.json or onto a command line.
        print(json.dumps(env_names(data.get("env"))))
        return 0
    dry = "--dry-run" in argv
    if cmd == "strip-hooks":
        n = strip_hooks(data, argv[2])
        if n and not dry:
            commit(path, data, delete_if_empty=False)
        print(json.dumps({"removed": n}))
        return 0
    if cmd == "strip-keys":
        keys = [k for k in argv[2:] if k != "--dry-run"]
        gone = [k for k in keys if k in data]
        for k in gone:
            data.pop(k)
        if gone and not dry:
            commit(path, data, delete_if_empty=True)
        print(json.dumps({"removed": gone}))
        return 0
    if cmd == "strip-env":
        # strip-env SETTINGS SOURCE_SETTINGS BEFORE_JSON [--dry-run]: remove the env keys c101 merged in (those of
        # SOURCE_SETTINGS' env) that were not there before c101 ran (BEFORE_JSON) and still hold c101's value.
        before_raw = sys.stdin.read() if argv[3] == "-" else argv[3]
        removed = strip_env(data, load(argv[2]).get("env") or {}, env_names(json.loads(before_raw)))
        if removed and not dry:
            commit(path, data, delete_if_empty=False)
        print(json.dumps({"removed": removed}))
        return 0
    raise SystemExit("unknown command %s" % cmd)


def strip_env(data: Dict[str, Any], source_env: Dict[str, Any], before: Any) -> List[str]:
    """before: the names (a list, or an older state's object whose keys count) the user's env held before c101 ran."""
    env = data.get("env")
    if not isinstance(env, dict):
        return []
    before_names = set(env_names(before))
    removed = [k for k, v in source_env.items() if k not in before_names and k in env and env[k] == v]
    for k in removed:
        del env[k]
    if not env:
        data.pop("env", None)
    return removed


def commit(path: str, data: Dict[str, Any], delete_if_empty: bool) -> None:
    """Back up, then write through symlinks (or delete when the object is empty and asked to)."""
    real = real_target(path)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    shutil.copy2(real, "%s.bak-%s" % (real, stamp))
    if delete_if_empty and not data:
        os.unlink(real)
    else:
        write_json_atomic(real, data)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
