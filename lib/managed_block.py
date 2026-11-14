#!/usr/bin/env python3
"""Idempotent managed blocks and managed files for shell profiles and environment.d.

Stdlib only, Python 3.9+. Every write to an existing file first copies it to <file>.bak-<UTC timestamp>.

  managed_block.py apply  --file F --content-file C [--begin B --end E] [--dry-run]
  managed_block.py remove --file F [--begin B --end E] [--delete-if-empty] [--dry-run]
  managed_block.py check  --file F [--begin B --end E]            exit 0 when exactly one block exists
  managed_block.py write-file --file F --content-file C --marker M [--dry-run]
  managed_block.py remove-file --file F --marker M [--dry-run]    removes F only when it carries marker M

Each command prints one line of JSON ({"changed": bool, ...}) on stdout. A symlinked file is edited through the link
(the link survives); a target in /nix/store or a read-only one is refused: JSON {"refused": true, "reason": ...}, exit 4.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

BEGIN = "# >>> c101-linux >>>"
END = "# <<< c101-linux <<<"


class ReadOnlyTarget(Exception):
    """The file to edit is (a link to) something this tool must not rewrite: a /nix/store path, a read-only file."""


def resolve_for_write(path: str) -> str:
    """Follow symlinks (stow, chezmoi, home-manager) so the link survives and the real file is edited; refuse targets
    that are read-only or live in the Nix store (the user's own config has to carry the change)."""
    real = os.path.realpath(path)
    if real.startswith("/nix/store/"):
        raise ReadOnlyTarget("%s points into /nix/store (managed by Nix/home-manager)" % path)
    if os.path.exists(real):
        if not os.access(real, os.W_OK):
            raise ReadOnlyTarget("%s is not writable" % real)
    elif os.path.isdir(os.path.dirname(real)) and not os.access(os.path.dirname(real), os.W_OK):
        raise ReadOnlyTarget("%s is not writable" % os.path.dirname(real))
    return real


def read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def backup(path: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = "%s.bak-%s" % (path, stamp)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = "%s.bak-%s-%d" % (path, stamp, n)
    shutil.copy2(path, dest)
    return dest


def write_atomic(path: str, text: str, mode_from: Optional[str] = None) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".c101l-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if mode_from and os.path.exists(mode_from):
            os.chmod(tmp, os.stat(mode_from).st_mode & 0o7777)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def split_blocks(text: str, begin: str, end: str) -> Tuple[List[str], int]:
    """Return (lines outside every block, number of blocks). An unterminated begin marker counts as a block
    that runs to the end of the file; the caller then rewrites it, so a half-edited block is repaired."""
    out: List[str] = []
    count = 0
    inside = False
    for line in text.splitlines():
        s = line.strip()
        if not inside and s == begin:
            inside = True
            count += 1
            continue
        if inside:
            if s == end:
                inside = False
            continue
        out.append(line)
    return out, count


def render(outside: List[str], block: Optional[str]) -> str:
    lines = list(outside)
    while lines and lines[-1].strip() == "":
        lines.pop()
    text = "\n".join(lines)
    if text:
        text += "\n"
    if block is not None:
        if text:
            text += "\n"
        text += block.rstrip("\n") + "\n"
    return text


def apply_block(path: str, content: str, begin: str, end: str, dry: bool) -> Dict[str, Any]:
    path = resolve_for_write(path)
    block = "%s\n%s\n%s" % (begin, content.strip("\n"), end)
    cur = read(path)
    if cur is None:
        new = render([], block)
        if not dry:
            write_atomic(path, new)
        return {"changed": True, "created": True, "backup": None}
    outside, _count = split_blocks(cur, begin, end)
    new = render(outside, block)
    if new == cur or (new.rstrip("\n") == cur.rstrip("\n") and _count == 1):
        return {"changed": False, "created": False, "backup": None}
    bak = None
    if not dry:
        bak = backup(path)
        write_atomic(path, new, mode_from=path)
    return {"changed": True, "created": False, "backup": bak}


def remove_block(path: str, begin: str, end: str, delete_if_empty: bool, dry: bool) -> Dict[str, Any]:
    path = resolve_for_write(path)
    cur = read(path)
    if cur is None:
        return {"changed": False, "deleted": False, "backup": None}
    outside, count = split_blocks(cur, begin, end)
    if count == 0:
        return {"changed": False, "deleted": False, "backup": None}
    new = render(outside, None)
    bak = None
    if not dry:
        bak = backup(path)
        if not new.strip() and delete_if_empty:
            os.unlink(path)
            return {"changed": True, "deleted": True, "backup": bak}
        write_atomic(path, new, mode_from=path)
    return {"changed": True, "deleted": bool(not new.strip() and delete_if_empty), "backup": bak}


def write_file(path: str, content: str, marker: str, dry: bool) -> Dict[str, Any]:
    path = resolve_for_write(path)
    body = content if content.startswith(marker) else marker + "\n" + content
    body = body.rstrip("\n") + "\n"
    cur = read(path)
    if cur == body:
        return {"changed": False, "created": False, "backup": None}
    bak = None
    if cur is not None and not dry:
        bak = backup(path)
    if not dry:
        write_atomic(path, body)
    return {"changed": True, "created": cur is None, "backup": bak}


def remove_file(path: str, marker: str, dry: bool) -> Dict[str, Any]:
    cur = read(path)
    if cur is None or not cur.startswith(marker):
        return {"changed": False, "backup": None, "reason": "absent" if cur is None else "not ours"}
    bak = None
    if not dry:
        bak = backup(path)
        os.unlink(path)
    return {"changed": True, "backup": bak}


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="managed_block.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("apply", "remove", "check", "write-file", "remove-file"):
        s = sub.add_parser(name)
        s.add_argument("--file", required=True)
        s.add_argument("--dry-run", action="store_true")
        s.add_argument("--begin", default=BEGIN)
        s.add_argument("--end", default=END)
        s.add_argument("--content-file")
        s.add_argument("--marker")
        s.add_argument("--delete-if-empty", action="store_true")
    a = p.parse_args(argv)
    if a.cmd in ("apply", "write-file"):
        if not a.content_file:
            raise SystemExit("--content-file is required")
        with open(a.content_file, encoding="utf-8") as fh:
            content = fh.read()
    try:
        return run_command(a, content if a.cmd in ("apply", "write-file") else "")
    except ReadOnlyTarget as exc:
        print(json.dumps({"refused": True, "reason": str(exc)}))
        print("refused: %s" % exc, file=sys.stderr)
        return 4


def run_command(a: argparse.Namespace, content: str) -> int:
    if a.cmd == "apply":
        res = apply_block(a.file, content, a.begin, a.end, a.dry_run)
    elif a.cmd == "remove":
        res = remove_block(a.file, a.begin, a.end, a.delete_if_empty, a.dry_run)
    elif a.cmd == "check":
        cur = read(a.file)
        _out, count = split_blocks(cur or "", a.begin, a.end)
        print(json.dumps({"blocks": count}))
        return 0 if count == 1 else 1
    elif a.cmd == "write-file":
        if not a.marker:
            raise SystemExit("--marker is required")
        res = write_file(a.file, content, a.marker, a.dry_run)
    else:
        if not a.marker:
            raise SystemExit("--marker is required")
        res = remove_file(a.file, a.marker, a.dry_run)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
