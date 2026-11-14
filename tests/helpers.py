"""Shared helpers for the c101-linux tests. Run them on Linux: the installer refuses other platforms.

Every test works inside a throw-away HOME and TMPDIR; nothing touches the real home directory or the network.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
LINUX = sys.platform.startswith("linux")
needs_linux = unittest.skipUnless(LINUX, "the installer and its shell libraries target Linux (run these tests on Linux)")


class Sandbox:
    """A temporary HOME, TMPDIR and bin directory plus an environment that points the overlay at them."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="c101l-test-")
        self.base = Path(self._tmp.name).resolve()
        self.home = self.base / "home"
        self.tmpdir = self.base / "tmp"
        self.bindir = self.base / "bin"
        for d in (self.home, self.tmpdir, self.bindir):
            d.mkdir()
        # By default no Claude Desktop is "running", whatever the machine running the tests has open.
        self.fake_command("pgrep", "exit 1")

    def close(self) -> None:
        self._tmp.cleanup()

    def env(self, **extra: str) -> Dict[str, str]:
        env = {
            "HOME": str(self.home),
            "TMPDIR": str(self.tmpdir),
            "PATH": "%s:/usr/local/bin:/usr/bin:/bin" % self.bindir,
            "LANG": "C.UTF-8",
            "USER": "tester",
            "C101L_PY": sys.executable,
            "C101_LINUX_LOGIN_SHELL": "/bin/bash",
        }
        env.update(extra)
        return env

    def run(self, args: List[str], check: bool = False, **extra: str) -> subprocess.CompletedProcess:
        res = subprocess.run(args, capture_output=True, text=True, env=self.env(**extra), cwd=str(self.base), timeout=120)
        if check and res.returncode != 0:
            raise AssertionError("%s failed (%d):\n%s\n%s" % (args, res.returncode, res.stdout, res.stderr))
        return res

    def snapshot(self, path: Optional[Path] = None) -> Dict[str, str]:
        """Relative path -> 'dir' | 'link:<target>' | file content, for every entry under path (default HOME)."""
        base = path or self.home
        out: Dict[str, str] = {}
        for p in sorted(base.rglob("*")):
            rel = str(p.relative_to(base))
            if p.is_symlink():
                out[rel] = "link:" + os.readlink(p)
            elif p.is_dir():
                out[rel] = "dir"
            else:
                out[rel] = p.read_text(encoding="utf-8", errors="replace")
        return out

    def fake_podman(self) -> Path:
        """A podman that answers --version and records any other call in $HOME/podman-was-called (it must never run
        in a dry run or in the doctor of a user who never used podman: `podman info` initialises storage)."""
        return self.fake_command("podman", 'case "$1" in --version) echo "podman version 5.0.0" ;; *) touch "$HOME/podman-was-called" ;; esac')

    def fake_command(self, name: str, body: str) -> Path:
        path = self.bindir / name
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path


def make_stub_c101(root: Path, with_venv: bool = True) -> Path:
    """A minimal fake c101 checkout: enough for source validation and for recording the flags it was run with."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".python-version").write_text("3.13\n", encoding="utf-8")
    (root / "stack").mkdir(exist_ok=True)
    (root / "stack" / "__init__.py").write_text("", encoding="utf-8")
    (root / "stack" / "install.py").write_text(
        "import sys\nprint('STUB stack.install ' + ' '.join(sys.argv[1:]))\n", encoding="utf-8")
    install = root / "install.sh"
    install.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$(dirname \"$0\")/calls.log\"\necho \"STUB install.sh $*\"\n",
        encoding="utf-8")
    install.chmod(0o755)
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.json").write_text('{"env": {"MAX_MCP_OUTPUT_TOKENS": "40000"}}\n', encoding="utf-8")
    (root / "bin").mkdir(exist_ok=True)
    stack = root / "bin" / "stack"
    stack.write_text("#!/bin/sh\necho stub stack \"$@\"\n", encoding="utf-8")
    stack.chmod(0o755)
    if with_venv:
        (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
        link = root / ".venv" / "bin" / "python"
        if not link.exists():
            link.symlink_to(sys.executable)
    return root


def bash_lib(sb: Sandbox, snippet: str, **extra: str) -> subprocess.CompletedProcess:
    """Run a snippet with the overlay libraries sourced (same environment contract as the entry points)."""
    script = (
        "set -Eeuo pipefail\n"
        "C101L_ROOT=%s\n" % ROOT
        + "C101L_TMP=$(mktemp -d \"$TMPDIR/lib.XXXXXX\")\n"
        ". \"$C101L_ROOT/lib/common.sh\"\n"
        ". \"$C101L_ROOT/lib/detect.sh\"\n"
        ". \"$C101L_ROOT/lib/profiles.sh\"\n"
        ". \"$C101L_ROOT/lib/deps.sh\"\n"
        ". \"$C101L_ROOT/lib/c101.sh\"\n"
        ". \"$C101L_ROOT/lib/desktop.sh\"\n"
        ". \"$C101L_ROOT/lib/pathprofile.sh\"\n"
        ". \"$C101L_ROOT/lib/services.sh\"\n"
        + snippet + "\n"
    )
    return sb.run(["bash", "-c", script], **extra)


def installer_args(*args: str) -> List[str]:
    """install-linux.sh argv; adds --allow-root when the tests themselves run as root (CI containers)."""
    extra = ["--allow-root"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
    return [str(ROOT / "install-linux.sh"), *extra, *args]


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None
