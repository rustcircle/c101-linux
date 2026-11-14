#!/usr/bin/env python3
"""Merge or remove MCP server entries in Claude Desktop's claude_desktop_config.json.

Stdlib only, Python 3.9+. Rules:
  * Desktop rewrites this file while it runs, so every write is refused while the app is running (pgrep)
    unless --force. --dry-run never writes and never refuses.
  * Invalid JSON is never overwritten (exit 2).
  * An existing file is copied to <file>.bak-<UTC timestamp> before it changes; an unchanged file is not touched.
  * A server name that already exists with different content is left alone (reported as "conflicts"), so a
    hand-written entry is never clobbered; entries this tool added earlier (same name, same content) are no-ops.
  * An existing entry equal to one an earlier release shipped (S's "_legacy" map: name -> list of entries) is
    ours too: merge replaces it with the current entry (reported as "updated") and remove deletes it.
  * "~" at the start of a command or argument is expanded to $HOME.

  desktop_config.py merge  --config F --servers S.json [--force] [--dry-run]
  desktop_config.py remove --config F --servers S.json [--names a,b] [--force] [--dry-run]
                                                                 removes only entries equal to S's (or S's legacy)
  desktop_config.py validate --config F
  desktop_config.py running                                                    exit 0 when Desktop is running

Prints one line of JSON describing the result. Exit codes: 0 ok, 2 invalid input, 3 Desktop running.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Set

DEFAULT_PATTERN = r"(^|/)claude-desktop($| )"


def ancestors() -> Set[int]:
    """PIDs of this process and its parents (never count ourselves as 'Desktop running')."""
    pids = {os.getpid()}
    pid = os.getppid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
            pid = int(out) if out else 0
        except (OSError, ValueError, subprocess.SubprocessError):
            break
    return pids


def desktop_running() -> bool:
    """True when pgrep finds a Claude Desktop process. Pattern is overridable for packaging variants."""
    pattern = os.environ.get("C101_LINUX_DESKTOP_PATTERN", DEFAULT_PATTERN)
    if shutil.which("pgrep") is None:
        return False
    skip = ancestors()
    cmds = [["pgrep", "-x", "claude-desktop"], ["pgrep", "-f", pattern]]
    for cmd in cmds:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0:
            pids = {int(x) for x in res.stdout.split() if x.isdigit()}
            if pids - skip:
                return True
    return False


def expand(value: Any, home: str) -> Any:
    if isinstance(value, str):
        if value == "~":
            return home
        if value.startswith("~/"):
            return home + value[1:]
        return re.sub(r"\$\{HOME\}|\$HOME", home, value)
    if isinstance(value, list):
        return [expand(v, home) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, home) for k, v in value.items()}
    return value


def load_servers(path: str, home: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    servers = data.get("mcpServers", data) if isinstance(data, dict) else None
    if not isinstance(servers, dict) or not servers:
        raise SystemExit("servers file %s: expected a non-empty object of servers" % path)
    return {k: expand(v, home) for k, v in servers.items() if not k.startswith("_")}


def load_legacy(path: str, home: str) -> Dict[str, List[Any]]:
    """S's "_legacy" map (name -> entries earlier releases shipped), expanded; "_" keys are annotations."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    legacy = data.get("_legacy") if isinstance(data, dict) else None
    out: Dict[str, List[Any]] = {}
    if not isinstance(legacy, dict):
        return out
    for name, entries in legacy.items():
        if name.startswith("_") or not isinstance(entries, list):
            continue
        out[name] = [expand({k: v for k, v in e.items() if not k.startswith("_")}, home)
                     for e in entries if isinstance(e, dict)]
    return out


def load_config(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return None
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("%s is not valid JSON (%s); fix or remove it, nothing was changed" % (path, exc))
    if not isinstance(data, dict):
        raise ValueError("%s: top level must be a JSON object; nothing was changed" % path)
    return data


def real_target(path: str) -> str:
    """Edit through symlinks; refuse the Nix store (home-manager owns that file)."""
    real = os.path.realpath(path)
    if real.startswith("/nix/store/"):
        raise PermissionError("%s points into /nix/store; add the servers to the Nix configuration instead" % path)
    return real


def backup(path: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = "%s.bak-%s" % (path, stamp)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = "%s.bak-%s-%d" % (path, stamp, n)
    shutil.copy2(path, dest)
    return dest


def write_json(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".c101l-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, (os.stat(path).st_mode & 0o7777) if os.path.exists(path) else 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def merge(config: Dict[str, Any], servers: Dict[str, Any],
          legacy: Optional[Dict[str, List[Any]]] = None) -> Dict[str, List[str]]:
    cur = config.get("mcpServers", {})
    if not isinstance(cur, dict):
        raise ValueError("mcpServers is not an object; nothing was changed")
    old = legacy or {}
    res: Dict[str, List[str]] = {"added": [], "updated": [], "unchanged": [], "conflicts": []}
    for name, entry in servers.items():
        if name not in cur:
            cur[name] = entry
            res["added"].append(name)
        elif cur[name] == entry:
            res["unchanged"].append(name)
        elif cur[name] in old.get(name, []):
            cur[name] = entry  # an entry an earlier release shipped: upgrade it in place
            res["updated"].append(name)
        else:
            res["conflicts"].append(name)
    if res["added"] or res["updated"]:
        config["mcpServers"] = cur
    return res


def remove(config: Dict[str, Any], servers: Dict[str, Any],
           legacy: Optional[Dict[str, List[Any]]] = None) -> Dict[str, List[str]]:
    cur = config.get("mcpServers")
    old = legacy or {}
    res: Dict[str, List[str]] = {"removed": [], "absent": [], "modified": []}
    if not isinstance(cur, dict):
        res["absent"] = list(servers)
        return res
    for name, entry in servers.items():
        if name not in cur:
            res["absent"].append(name)
        elif cur[name] == entry or cur[name] in old.get(name, []):
            del cur[name]
            res["removed"].append(name)
        else:
            res["modified"].append(name)
    if not cur:
        config.pop("mcpServers", None)
    return res


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="desktop_config.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("merge", "remove", "validate"):
        s = sub.add_parser(name)
        s.add_argument("--config", required=True)
        s.add_argument("--servers")
        s.add_argument("--force", action="store_true")
        s.add_argument("--dry-run", action="store_true")
        s.add_argument("--names", help="comma list: only these server names (remove: the ones this tool added)")
    sub.add_parser("running")
    a = p.parse_args(argv)
    home = os.path.expanduser("~")

    if a.cmd == "running":
        return 0 if desktop_running() else 1
    try:
        config = load_config(a.config)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        print(str(exc), file=sys.stderr)
        return 2
    if a.cmd == "validate":
        print(json.dumps({"valid": True, "exists": config is not None}))
        return 0
    if not a.servers:
        raise SystemExit("--servers is required")
    servers = load_servers(a.servers, home)
    legacy = load_legacy(a.servers, home)
    if a.names:
        wanted = {n.strip() for n in a.names.split(",") if n.strip()}
        servers = {k: v for k, v in servers.items() if k in wanted}
    work: Dict[str, Any] = json.loads(json.dumps(config)) if config is not None else {}
    try:
        res = merge(work, servers, legacy) if a.cmd == "merge" else remove(work, servers, legacy)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        print(str(exc), file=sys.stderr)
        return 2
    changed = work != (config if config is not None else {})
    out: Dict[str, Any] = dict(res)
    out.update({"changed": bool(changed), "config": a.config, "backup": None, "dry_run": a.dry_run})
    if changed and not a.dry_run:
        if not a.force and desktop_running():
            msg = ("Claude Desktop is running and rewrites %s on exit; quit it first (or pass --force)." % a.config)
            print(json.dumps({"error": msg, "running": True}))
            print(msg, file=sys.stderr)
            return 3
        try:
            target = real_target(a.config)
        except PermissionError as exc:
            print(json.dumps({"error": str(exc)}))
            print(str(exc), file=sys.stderr)
            return 4
        if config is not None and os.path.exists(target):
            out["backup"] = backup(target)
        write_json(target, work)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
