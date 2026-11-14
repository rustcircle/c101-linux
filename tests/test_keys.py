"""Key provisioning for stack.env: lib/stackenv.py (names, ops, check, merge) and the key step of lib/keys.sh.

Nothing here touches the real home directory or the network. Every test gets a throw-away HOME (mode 700), a stub c101
checkout (config/stack.env.example and config/stack.env.fal.example, see helpers.build_stub_c101) and fake keys:
SECRET_SENTINEL and sentinel_for(NAME) from helpers, which may show up in stack.env (and its mode-600 backups) and
nowhere else: not in stdout, stderr, a child's argv or environment, HOME or TMPDIR.

Three layers, all run on Linux (tests/run-tests.sh, e.g. `-k test_keys`):
  StackenvCliTests ... `python -I lib/stackenv.py ...` with the values on stdin, the way keys.sh calls it.
  KeysStepTests ...... the keys.sh functions through bash exactly as install-linux.sh calls them: the key variables are
                       un-exported first (key_env_unexport is taken from the installer's own text), and a recording
                       Python stands in for the helper interpreter ($C101L_PY), so the argv, the stdin kind and the
                       environment of every child are visible to the assertions.
  KeysPromptTests .... the hidden prompts on a pseudo terminal (the only way to reach `read -s`).
The mandatory behaviours of node T1 are the test methods named after them (keep_existing_without_keys_overwrite,
keys_overwrite_overwrites, force_alone_keeps_existing, backup_mode_600, symlinked_stack_env, ...).
"""
from __future__ import annotations

import json
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import pty
    import termios
except ImportError:  # no POSIX terminals: the terminal tests below skip themselves
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]

from helpers import (
    FAL_OP_TABLE,
    FAL_OPS,
    FAL_PLACEHOLDER,
    FAL_URL,
    LIB,
    ROOT,
    SECRET_SENTINEL,
    STACK_ENV_ALLOW,
    Sandbox,
    backups_of,
    build_stub_c101,
    env_file_allowances,
    fal_preset_values,
    file_mode,
    find_secret_leaks,
    installer_args,
    make_stub_c101,
    needs_linux,
    read_jsonl,
    read_stack_env,
    sentinel_for,
    write_stack_env,
)

BASH = shutil.which("bash") or "bash"
PYTHON = sys.executable

RESEARCH_KEYS = ("EXA_API_KEY", "JINA_API_KEY", "SPIDER_API_KEY", "CONTEXT7_API_KEY")
EXTRA_KEYS = ("FIRECRAWL_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY", "REPLICATE_API_TOKEN", "RUNWARE_API_KEY")
KEY_ORDER = ("FAL_KEY",) + RESEARCH_KEYS  # prompt order and message order (D3, D5)
OLD_FAL = "old-fal-fixture"
OLD_EXA = "old-exa-fixture"
FAL_MODELS = {op: model for op, model, _params in FAL_OP_TABLE}

STEP_FAL = "KEYS=env\nIMAGE_BACKEND=fal\nkeys_step\nreport\n"
STEP_NONE = "KEYS=env\nIMAGE_BACKEND=none\nkeys_step\nreport\n"


# ---- small helpers ----------------------------------------------------------------------------------------------


def decoded(proc: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    """The same CompletedProcess with str stdout and stderr (bytes in, so no newline translation corrupts a test)."""
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    proc.stderr = proc.stderr.decode("utf-8", "replace")
    return proc


def run_stackenv(sb: Sandbox, args: List[str], stdin: Any = b"") -> subprocess.CompletedProcess:
    """`python -I lib/stackenv.py ARGS` with `stdin` (str or bytes) on a pipe, in the sandbox environment."""
    data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    proc = subprocess.run([PYTHON, "-I", str(LIB / "stackenv.py"), *args], input=data, capture_output=True,
                          env=sb.env(), cwd=str(sb.base), timeout=60)
    return decoded(proc)


def all_key_env() -> Dict[str, str]:
    """Every key name the key step imports, each holding a value that contains SECRET_SENTINEL."""
    env = {"FAL_KEY": SECRET_SENTINEL}
    for name in RESEARCH_KEYS + EXTRA_KEYS:
        env[name] = sentinel_for(name)
    return env


def parse_report(stdout: str) -> Dict[str, str]:
    """The `@@NAME=value` lines the bash runner prints after the step (names and fixed words only)."""
    out = {}  # type: Dict[str, str]
    for line in stdout.splitlines():
        if line.startswith("@@") and "=" in line:
            name, value = line[2:].split("=", 1)
            out[name] = value
    return out


# One `install-linux.sh` command in printed text: the script, then its flags, each with at most one value (a value never
# starts with "-"; the first word after the last flag, e.g. "from" or "asks", is taken as a value and ignored).
INSTALL_CMD = re.compile(r"install-linux\.sh((?: --[a-z][a-z-]*(?: (?!-)[^\s,;()]+)?)*)")


def install_commands(text: str) -> List[List[str]]:
    """The flags of every `install-linux.sh` command in `text`, in order, values left out."""
    return [re.findall(r"--[a-z][a-z-]*", m.group(1)) for m in INSTALL_CMD.finditer(text)]


def installer_function(name: str) -> str:
    """The text of one function of install-linux.sh, so the runner un-exports the key variables exactly as the installer
    does before its first child process (lib/keys.sh documents that contract)."""
    text = (ROOT / "install-linux.sh").read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{\n.*?^\}\n" % re.escape(name), text, re.MULTILINE | re.DOTALL)
    if m is None:
        raise AssertionError("install-linux.sh no longer defines %s(): update tests/test_keys.py" % name)
    return m.group(0)


KEYS_PRELUDE = r'''set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true
C101L_ROOT=@ROOT@
. "$C101L_ROOT/lib/common.sh"
. "$C101L_ROOT/lib/keys.sh"
@UNEXPORT@
key_env_unexport
report() {
  printf '@@RESULT=%s\n' "$KEYS_RESULT"
  printf '@@CHANGED=%s\n' "$KEYS_CHANGED"
  printf '@@KEPT=%s\n' "$KEYS_KEPT"
  printf '@@KEPT_OPS=%s\n' "$KEYS_KEPT_OPS"
  printf '@@MISSING=%s\n' "$KEYS_MISSING"
  printf '@@FAL_MISSING=%s\n' "$KEYS_FAL_MISSING"
  printf '@@BACKEND=%s\n' "$KEYS_BACKEND_EFFECTIVE"
  printf '@@STACK_ENV=%s\n' "$KEYS_STACK_ENV"
}
'''


def keys_script(body: str, c101: Optional[Path] = None) -> str:
    """A bash program: common.sh and keys.sh sourced, the installer's key_env_unexport run, then `body`."""
    head = KEYS_PRELUDE.replace("@ROOT@", shlex.quote(str(ROOT))).replace("@UNEXPORT@", installer_function("key_env_unexport"))
    if c101 is not None:
        head += "C101_SRC=%s\n" % shlex.quote(str(c101))
    return head + body


SPY_SOURCE = r'''import json
import os
import stat
import sys

LOG = __LOG__
NEEDLE = __NEEDLE_A__ + __NEEDLE_B__
REAL = __REAL__


def stdin_kind():
    try:
        if os.isatty(0):
            return "tty"
        mode = os.fstat(0).st_mode
    except OSError:
        return "closed"
    if stat.S_ISFIFO(mode):
        return "pipe"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISCHR(mode):
        return "devnull"
    return "other"


rec = {
    "stub": "python",
    "argv": sys.argv[1:],
    "stdin": stdin_kind(),
    "env_with_sentinel": sorted(k for k, v in os.environ.items() if NEEDLE in v),
}
try:
    fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(rec, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
except OSError:
    pass
os.execv(REAL, [REAL] + sys.argv[1:])
'''


def install_python_spy(sb: Sandbox) -> Tuple[Path, Path]:
    """A recording stand-in for the helper interpreter: set C101L_PY to the returned launcher and every `helper` call of
    the shell libraries first appends one JSON line (argv, stdin kind, NAMES of variables holding the sentinel) to the
    returned log, then runs the real interpreter with the same arguments and descriptors. The sentinel is not written
    out in full anywhere in these files. They live under base/spy, outside the HOME and TMPDIR the leak scans cover."""
    spydir = sb.base / "spy"
    spydir.mkdir()
    log = sb.logdir / "python.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    source = (SPY_SOURCE.replace("__LOG__", repr(str(log))).replace("__NEEDLE_A__", repr(SECRET_SENTINEL[:7]))
              .replace("__NEEDLE_B__", repr(SECRET_SENTINEL[7:])).replace("__REAL__", repr(PYTHON)))
    (spydir / "spy.py").write_text(source, encoding="utf-8")
    launcher = spydir / "python-spy"
    launcher.write_text('#!/bin/sh\nexec %s -I %s "$@"\n' % (shlex.quote(PYTHON), shlex.quote(str(spydir / "spy.py"))),
                        encoding="utf-8")
    launcher.chmod(0o755)
    return launcher, log


class KeysRun:
    """The outcome of one bash run of the key step."""

    def __init__(self, proc: subprocess.CompletedProcess) -> None:
        self.proc = proc
        self.rc = proc.returncode
        self.out = proc.stdout
        self.err = proc.stderr
        self.rep = parse_report(proc.stdout)

    def warnings(self) -> List[str]:
        return [line for line in self.err.splitlines() if line.startswith("WARN:")]


# ---- fixtures ---------------------------------------------------------------------------------------------------


class EnvCase(unittest.TestCase):
    """A sandbox HOME, a stub c101 checkout and helpers to drive lib/stackenv.py. Holds no tests itself."""

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.sb.home.chmod(0o700)  # the directory checks must not depend on the umask of whoever runs the tests
        self.c101 = make_stub_c101(self.sb.base / "c101", with_venv=False)
        self.template = self.c101 / "config" / "stack.env.example"
        self.preset = self.c101 / "config" / "stack.env.fal.example"
        self.path = self.sb.stack_env

    def put(self, content: Any, *, mode: int = 0o600, target_dir: Optional[Path] = None, relative: bool = False,
            path: Optional[Path] = None) -> Path:
        """write_stack_env, then make every directory it created or used private (mode 700)."""
        loc = write_stack_env(self.sb.home, content, mode=mode, target_dir=target_dir, relative=relative, path=path)
        loc.parent.chmod(0o700)
        if target_dir is not None:
            Path(target_dir).chmod(0o700)
        return loc

    def stackenv(self, *args: str, stdin: Any = b"") -> subprocess.CompletedProcess:
        return run_stackenv(self.sb, list(args), stdin)

    def merge(self, stdin: Any = "", *flags: str, path: Optional[Path] = None, template: bool = True,
              preset: bool = True) -> subprocess.CompletedProcess:
        args = ["merge", "--file", str(path or self.path)]
        if template:
            args += ["--template", str(self.template)]
        if preset:
            args += ["--preset", str(self.preset)]
        return run_stackenv(self.sb, args + list(flags), stdin)

    def merged(self, res: subprocess.CompletedProcess) -> Dict[str, Any]:
        """The one-line JSON of a successful merge."""
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return json.loads(res.stdout)

    def values(self, path: Optional[Path] = None) -> Dict[str, str]:
        return read_stack_env(path or self.path)

    def text(self, path: Optional[Path] = None) -> str:
        return Path(path or self.path).read_text(encoding="utf-8")

    def ops(self, path: Optional[Path] = None) -> Dict[str, Any]:
        res = self.stackenv("ops", "--file", str(path or self.path))
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)


# ---- lib/stackenv.py through its command line -----------------------------------------------------------------


class StackenvCliTests(EnvCase):
    # the fal preset and the one fal key ------------------------------------------------------------------------

    def test_one_fal_key_fills_all_fal_ops_and_FAL_KEY(self) -> None:
        """one-fal-key-fills-all-fal-ops-and-FAL_KEY (D7): the key is given once and lands in FAL_KEY and in the KEY of
        every operation whose URL is the fal queue."""
        key = sentinel_for("FAL_KEY")
        out = self.merged(self.merge("FAL_KEY=%s\n" % key))
        self.assertTrue(out["created"] and out["wrote"])
        vals = self.values()
        self.assertEqual(vals["FAL_KEY"], key)
        for op in FAL_OPS:
            self.assertEqual(vals["IMAGE_%s_KEY" % op], key, op)
        for name, want in fal_preset_values(key).items():
            self.assertEqual(vals.get(name), want, name)
        self.assertEqual(out["missing_keys"], [])
        self.assertEqual(out["kept_ops"], [])
        self.assertEqual(out["overwritten_ops"], [])
        self.assertEqual(self.text().count("# c101-linux keys"), 1)
        self.assertEqual(file_mode(self.path), 0o600)

    def test_fal_preset_fills_9_ops(self) -> None:
        """fal-preset-fills-9-ops: URL, KEY and MODEL (and fixed PARAMS) of the nine fal operations, read back by the
        same judgement c101's image router applies (stackenv.py ops)."""
        key = sentinel_for("FAL_KEY")
        self.merged(self.merge("FAL_KEY=%s\n" % key))
        vals = self.values()
        for op, model, params in FAL_OP_TABLE:
            self.assertEqual(vals["IMAGE_%s_URL" % op], FAL_URL, op)
            self.assertEqual(vals["IMAGE_%s_KEY" % op], key, op)
            self.assertEqual(vals["IMAGE_%s_MODEL" % op], model, op)
            self.assertEqual(vals.get("IMAGE_%s_PARAMS" % op), params, op)
        report = self.ops()
        self.assertEqual(sorted(op for op, r in report.items() if r["provider"] == "fal"), sorted(FAL_OPS))
        self.assertEqual(len(FAL_OPS), 9)
        for op in FAL_OPS:
            self.assertEqual(report[op]["status"], "configured", op)
            self.assertEqual(report[op]["missing"], [], op)
            self.assertEqual(report[op]["issues"], [], op)
        self.assertNotEqual(report["VECTOR_MANIPULATION"]["provider"], "fal")  # fal hosts no chat model: agents edit SVG

    def test_fal_preset_without_key_writes_url_model_empty_keys(self) -> None:
        """fal-preset-without-key-writes-url-model-empty-keys (D3): no key anywhere, still every URL, MODEL and PARAMS
        line, every KEY empty, and the empty names listed so the caller can warn once."""
        out = self.merged(self.merge(""))
        vals = self.values()
        for name, want in fal_preset_values("").items():
            self.assertEqual(vals.get(name), want, name)
        self.assertNotIn(FAL_PLACEHOLDER, vals.values())  # the preset's placeholder never stays as a key
        self.assertEqual(set(out["missing_keys"]), {"FAL_KEY"} | {"IMAGE_%s_KEY" % op for op in FAL_OPS})
        report = self.ops()
        for op in FAL_OPS:
            self.assertEqual(report[op]["status"], "key-empty", op)
            self.assertEqual(report[op]["missing"], ["IMAGE_%s_KEY" % op], op)
            self.assertEqual(report[op]["provider"], "fal", op)

    def test_a_key_added_later_fills_the_empty_fal_keys(self) -> None:
        """The key-less merge followed by `--keys` with a key (the re-run command the warning prints)."""
        self.merged(self.merge(""))
        key = sentinel_for("FAL_KEY")
        out = self.merged(self.merge("FAL_KEY=%s\n" % key))
        self.assertEqual(out["missing_keys"], [])
        vals = self.values()
        self.assertEqual([vals["IMAGE_%s_KEY" % op] for op in FAL_OPS], [key] * len(FAL_OPS))
        self.assertEqual(vals["FAL_KEY"], key)

    def test_second_identical_merge_changes_nothing(self) -> None:
        key = sentinel_for("FAL_KEY")
        self.merged(self.merge("FAL_KEY=%s\n" % key))
        before = self.path.read_bytes()
        out = self.merged(self.merge("FAL_KEY=%s\n" % key))
        self.assertFalse(out["wrote"] or out["would_write"])
        self.assertEqual(out["changed"], [])
        self.assertEqual(out["kept"], [])
        self.assertIsNone(out["backup"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(backups_of(self.path), [])

    # keep, overwrite, never blank -----------------------------------------------------------------------------

    def test_merge_keeps_a_different_existing_value_without_overwrite(self) -> None:
        self.put({"EXA_API_KEY": OLD_EXA, "JINA_API_KEY": ""})
        out = self.merged(self.merge("EXA_API_KEY=%s\nJINA_API_KEY=%s\n" % (sentinel_for("EXA_API_KEY"),
                                                                           sentinel_for("JINA_API_KEY")),
                                     template=False, preset=False))
        self.assertEqual(out["kept"], ["EXA_API_KEY"])
        self.assertEqual(out["changed"], ["JINA_API_KEY"])
        vals = self.values()
        self.assertEqual(vals["EXA_API_KEY"], OLD_EXA)
        self.assertEqual(vals["JINA_API_KEY"], sentinel_for("JINA_API_KEY"))
        self.assertNotIn(sentinel_for("EXA_API_KEY"), self.text())

    def test_merge_overwrite_replaces_an_existing_value(self) -> None:
        self.put({"EXA_API_KEY": OLD_EXA, "JINA_API_KEY": ""})
        out = self.merged(self.merge("EXA_API_KEY=%s\nJINA_API_KEY=%s\n" % (sentinel_for("EXA_API_KEY"),
                                                                           sentinel_for("JINA_API_KEY")),
                                     "--overwrite", template=False, preset=False))
        self.assertEqual(out["kept"], [])
        self.assertEqual(sorted(out["changed"]), ["EXA_API_KEY", "JINA_API_KEY"])
        vals = self.values()
        self.assertEqual(vals["EXA_API_KEY"], sentinel_for("EXA_API_KEY"))
        self.assertNotIn(OLD_EXA, self.text())
        baks = backups_of(self.path)
        self.assertEqual(len(baks), 1)
        self.assertIn(OLD_EXA, baks[0].read_text(encoding="utf-8"))

    def test_overwrite_never_blanks_an_existing_value(self) -> None:
        """D10: --overwrite replaces a value with another value, never with an empty one."""
        self.put({"EXA_API_KEY": OLD_EXA})
        before = self.path.read_bytes()
        out = self.merged(self.merge("EXA_API_KEY=\n", "--overwrite", template=False, preset=False))
        self.assertEqual(out["ignored"], ["EXA_API_KEY"])
        self.assertEqual(out["changed"], [])
        self.assertFalse(out["wrote"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_and_placeholder_values_are_ignored_and_listed(self) -> None:
        out = self.merged(self.merge("EXA_API_KEY=\nJINA_API_KEY=%s\nSPIDER_API_KEY=%s\n" % (
            FAL_PLACEHOLDER, sentinel_for("SPIDER_API_KEY")), template=False, preset=False))
        self.assertEqual(out["ignored"], ["EXA_API_KEY", "JINA_API_KEY"])
        self.assertEqual(out["changed"], ["SPIDER_API_KEY"])

    def test_force_is_not_a_stackenv_option(self) -> None:
        """D2: overwriting has exactly one switch (--overwrite, reached only through --keys-overwrite); a --force given
        to the helper is an unknown option and changes nothing."""
        self.put({"EXA_API_KEY": OLD_EXA})
        before = self.path.read_bytes()
        res = self.merge("EXA_API_KEY=%s\n" % sentinel_for("EXA_API_KEY"), "--force", template=False, preset=False)
        self.assertEqual(res.returncode, 2, res.stdout)
        self.assertIn("--force", res.stderr)
        self.assertEqual(self.path.read_bytes(), before)

    # backups -----------------------------------------------------------------------------------------------------

    def test_backup_mode_600(self) -> None:
        """backup-mode-600: the old bytes are saved first, next to the target, with mode 600, and a 644 file ends at 600."""
        original = "EXA_API_KEY=%s\n" % OLD_EXA
        self.put(original, mode=0o644)
        out = self.merged(self.merge("EXA_API_KEY=%s\n" % sentinel_for("EXA_API_KEY"), "--overwrite", template=False,
                                     preset=False))
        baks = backups_of(self.path)
        self.assertEqual(len(baks), 1)
        self.assertEqual(file_mode(baks[0]), 0o600)
        self.assertEqual(baks[0].read_text(encoding="utf-8"), original)
        self.assertEqual(Path(out["backup"]).resolve(), baks[0].resolve())
        self.assertEqual(out["backups"], 1)
        self.assertEqual(file_mode(self.path), 0o600)
        self.assertTrue(out["mode_fixed"])
        self.assertNotIn(OLD_EXA, self.text())

    def test_backups_are_never_deleted_and_counted(self) -> None:
        """SEC-2: nothing is pruned; the count tells the caller how many plain-text copies of the keys sit there."""
        self.put({"EXA_API_KEY": OLD_EXA})
        first = self.merged(self.merge("EXA_API_KEY=new-exa-one-fixture\n", "--overwrite", template=False, preset=False))
        second = self.merged(self.merge("EXA_API_KEY=new-exa-two-fixture\n", "--overwrite", template=False, preset=False))
        self.assertEqual((first["backups"], second["backups"]), (1, 2))
        self.assertNotEqual(first["backup"], second["backup"])
        self.assertEqual(len(backups_of(self.path)), 2)

    def test_symlinked_stack_env(self) -> None:
        """symlinked-stack-env: a stow or chezmoi style link survives; the target is rewritten and backed up beside itself."""
        for relative in (False, True):
            with self.subTest(relative=relative):
                dot = self.sb.base / ("dotfiles-relative" if relative else "dotfiles")
                dot.mkdir()
                link = self.put({"EXA_API_KEY": OLD_EXA}, target_dir=dot, relative=relative)
                target = dot / "stack.env"
                link_text = os.readlink(link)
                out = self.merged(self.merge("EXA_API_KEY=%s\nFAL_KEY=%s\n" % (sentinel_for("EXA_API_KEY"),
                                                                              sentinel_for("FAL_KEY")),
                                             "--overwrite", path=link))
                self.assertTrue(link.is_symlink())
                self.assertEqual(os.readlink(link), link_text)
                self.assertEqual(Path(out["target"]).resolve(), target.resolve())
                self.assertEqual(Path(out["file"]).absolute(), link.absolute())
                vals = read_stack_env(target)
                self.assertEqual(vals["EXA_API_KEY"], sentinel_for("EXA_API_KEY"))
                self.assertEqual(vals["IMAGE_GEN_KEY"], sentinel_for("FAL_KEY"))
                self.assertEqual(file_mode(target), 0o600)
                baks = sorted(dot.glob("stack.env.bak-*"))
                self.assertEqual(len(baks), 1)
                self.assertEqual(file_mode(baks[0]), 0o600)
                self.assertEqual(list(link.parent.glob("stack.env.bak-*")), [])
                self.assertEqual([p.name for p in dot.iterdir() if p.name.startswith(".stack.env-")], [])  # no temp file left

    # dry run -------------------------------------------------------------------------------------------------------

    def test_dry_run_writes_nothing(self) -> None:
        """dry-run-writes-nothing: the plan is reported, no file, directory, backup or mode change appears."""
        key = sentinel_for("FAL_KEY")
        out = self.merged(self.merge("FAL_KEY=%s\n" % key, "--dry-run"))
        self.assertTrue(out["dry_run"] and out["would_write"])
        self.assertFalse(out["wrote"] or out["created"])
        self.assertIn("FAL_KEY", out["changed"])
        self.assertEqual(self.sb.snapshot(), {})  # not even ~/.claude
        self.put({"EXA_API_KEY": OLD_EXA}, mode=0o644)
        before = self.path.read_bytes()
        out = self.merged(self.merge("EXA_API_KEY=%s\nFAL_KEY=%s\n" % (sentinel_for("EXA_API_KEY"), key), "--dry-run",
                                     "--overwrite"))
        self.assertTrue(out["would_write"])
        self.assertFalse(out["wrote"] or out["mode_fixed"])
        self.assertIsNone(out["backup"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(file_mode(self.path), 0o644)  # tightening is never done in a dry run
        self.assertEqual(backups_of(self.path), [])

    # other providers ------------------------------------------------------------------------------------------------

    OTHER_GEN = {
        "IMAGE_GEN_URL": "https://openrouter.ai/api/v1/images",
        "IMAGE_GEN_KEY": "other-op-key-fixture",
        "IMAGE_GEN_MODEL": "other/model-fixture",
    }

    def test_existing_non_fal_op_kept_and_reported(self) -> None:
        """existing-non-fal-op-kept-and-reported (D3): an operation already on another provider is kept whole (never a
        fal URL with another provider's key) and listed by operation name in kept_ops."""
        self.put(self.OTHER_GEN)
        key = sentinel_for("FAL_KEY")
        out = self.merged(self.merge("FAL_KEY=%s\n" % key, template=False))
        self.assertEqual(out["kept_ops"], ["GEN"])
        self.assertEqual(out["overwritten_ops"], [])
        self.assertEqual(out["missing_keys"], [])
        vals = self.values()
        for name, want in self.OTHER_GEN.items():
            self.assertEqual(vals[name], want, name)
        self.assertNotIn("IMAGE_GEN_PARAMS", vals)
        self.assertEqual([n for n in out["changed"] if n.startswith("IMAGE_GEN_")], [])
        for op in FAL_OPS:
            if op != "GEN":
                self.assertEqual((vals["IMAGE_%s_URL" % op], vals["IMAGE_%s_KEY" % op]), (FAL_URL, key), op)
        report = self.ops()
        self.assertEqual((report["GEN"]["provider"], report["GEN"]["status"]), ("other", "configured"))

    def test_keys_overwrite_replaces_non_fal_op(self) -> None:
        """keys-overwrite-replaces-non-fal-op: with --overwrite (the flag behind --keys-overwrite) the whole operation
        switches to fal, API/PARAMS cleared first so no provider is mixed, and the old values are in the backup."""
        self.put(dict(self.OTHER_GEN, IMAGE_GEN_API="openrouter"))
        key = sentinel_for("FAL_KEY")
        out = self.merged(self.merge("FAL_KEY=%s\n" % key, "--overwrite", template=False))
        self.assertEqual(out["overwritten_ops"], ["GEN"])
        self.assertEqual(out["kept_ops"], [])
        vals = self.values()
        self.assertEqual(vals["IMAGE_GEN_URL"], FAL_URL)
        self.assertEqual(vals["IMAGE_GEN_KEY"], key)
        self.assertEqual(vals["IMAGE_GEN_MODEL"], FAL_MODELS["GEN"])
        self.assertEqual(vals.get("IMAGE_GEN_API", ""), "")
        self.assertEqual(vals["IMAGE_GEN_PARAMS"], '{"quality":"high"}')
        self.assertNotIn("other-op-key-fixture", self.text())
        baks = backups_of(self.path)
        self.assertEqual(len(baks), 1)
        self.assertIn("other-op-key-fixture", baks[0].read_text(encoding="utf-8"))
        self.assertEqual(self.ops()["GEN"]["provider"], "fal")

    def test_stdin_supplied_image_op_is_never_replaced_by_the_preset(self) -> None:
        """An IMAGE_<OP>_* given on stdin (it came from the environment) is the user's explicit choice: the preset keeps
        away from that operation even with --overwrite, and its other fields are not touched."""
        own = "IMAGE_GEN_URL=https://example.invalid/v1\nIMAGE_GEN_KEY=%s\nIMAGE_GEN_MODEL=env/model-fixture\n" % sentinel_for(
            "IMAGE_GEN_KEY")
        for i, flags in enumerate(((), ("--overwrite",))):
            with self.subTest(flags=flags):
                path = self.sb.base / ("own-%d" % i) / "stack.env"
                path.parent.mkdir()
                path.parent.chmod(0o700)
                key = sentinel_for("FAL_KEY")
                out = self.merged(self.merge(own + "FAL_KEY=%s\n" % key, *flags, path=path))
                self.assertEqual(out["kept_ops"], ["GEN"])
                self.assertEqual(out["overwritten_ops"], [])
                vals = read_stack_env(path)
                self.assertEqual(vals["IMAGE_GEN_URL"], "https://example.invalid/v1")
                self.assertEqual(vals["IMAGE_GEN_KEY"], sentinel_for("IMAGE_GEN_KEY"))
                self.assertEqual(vals["IMAGE_GEN_MODEL"], "env/model-fixture")
                self.assertNotIn("IMAGE_GEN_PARAMS", vals)
                self.assertEqual(vals["IMAGE_REF_KEY"], key)

    # SEC-6: where the fal key may go ------------------------------------------------------------------------------

    def test_fal_key_goes_only_to_https_queue_fal_run(self) -> None:
        """SEC-6: http, a look-alike host, userinfo, another port and a backslash are not the fal queue: the operation is
        kept whole and its key stays empty, so the fal key never reaches another host."""
        hostile = (
            "http://queue.fal.run",
            "https://queue.fal.run.evil.example",
            "https://queue.fal.run@evil.example/x",
            "https://queue.fal.run:8443",
            "https://queue.fal.run\\@evil.example",
            "https://user:pw-fixture@queue.fal.run",
        )
        key = sentinel_for("FAL_KEY")
        for i, url in enumerate(hostile):
            with self.subTest(url=url):
                path = self.sb.base / ("hostile-%d" % i) / "stack.env"
                path.parent.mkdir()
                path.parent.chmod(0o700)
                self.put("IMAGE_GEN_URL=%s\nIMAGE_GEN_KEY=\nIMAGE_GEN_MODEL=m/other-fixture\n" % url, path=path)
                out = self.merged(self.merge("FAL_KEY=%s\n" % key, path=path, template=False))
                self.assertEqual(out["kept_ops"], ["GEN"])
                vals = read_stack_env(path)
                self.assertEqual(vals["IMAGE_GEN_URL"], url)
                self.assertEqual(vals.get("IMAGE_GEN_KEY", ""), "")
                self.assertEqual(vals["IMAGE_REF_KEY"], key)

    def test_fal_key_fills_an_existing_https_queue_fal_run_op(self) -> None:
        """The positive control of SEC-6: an operation already on https://queue.fal.run just gets the key."""
        self.put("IMAGE_GEN_URL=%s\nIMAGE_GEN_KEY=\nIMAGE_GEN_MODEL=%s\n" % (FAL_URL, FAL_MODELS["GEN"]))
        key = sentinel_for("FAL_KEY")
        out = self.merged(self.merge("FAL_KEY=%s\n" % key, template=False))
        self.assertEqual(out["kept_ops"], [])
        self.assertEqual(self.values()["IMAGE_GEN_KEY"], key)

    def test_placeholder_never_becomes_the_key_of_a_non_fal_op(self) -> None:
        """SEC-6: a placeholder line of the preset is replaced by the fal key only inside an operation whose preset URL is
        the fal queue; anywhere else it becomes empty."""
        preset = self.sb.base / "preset.example"
        preset.write_text(
            "IMAGE_GEN_URL=%s\nIMAGE_GEN_KEY=%s\nIMAGE_GEN_MODEL=m/gen-fixture\n"
            "IMAGE_UPSCALE_URL=https://example.invalid/upscale\nIMAGE_UPSCALE_KEY=%s\nIMAGE_UPSCALE_MODEL=m/up-fixture\n"
            % (FAL_URL, FAL_PLACEHOLDER, FAL_PLACEHOLDER), encoding="utf-8")
        key = sentinel_for("FAL_KEY")
        res = run_stackenv(self.sb, ["merge", "--file", str(self.path), "--preset", str(preset)], "FAL_KEY=%s\n" % key)
        out = self.merged(res)
        vals = self.values()
        self.assertEqual(vals["IMAGE_GEN_KEY"], key)
        self.assertEqual(vals["IMAGE_UPSCALE_KEY"], "")
        self.assertEqual(vals["IMAGE_UPSCALE_URL"], "https://example.invalid/upscale")
        self.assertNotIn("IMAGE_UPSCALE_KEY", out["missing_keys"])  # not a fal operation: no fal key is expected there

    # SEC-3: mode ---------------------------------------------------------------------------------------------------

    def test_loose_mode_is_tightened_to_600_even_when_nothing_else_changes(self) -> None:
        """SEC-3: a stack.env of yours that group or others can read is tightened to 600, without a rewrite or a backup,
        but never in a dry run."""
        self.put({"EXA_API_KEY": OLD_EXA}, mode=0o644)
        dry = self.merged(self.merge("", "--dry-run", template=False, preset=False))
        self.assertFalse(dry["mode_fixed"])
        self.assertEqual(file_mode(self.path), 0o644)
        out = self.merged(self.merge("", template=False, preset=False))
        self.assertTrue(out["mode_fixed"])
        self.assertFalse(out["wrote"] or out["would_write"])
        self.assertEqual(file_mode(self.path), 0o600)
        self.assertEqual(self.values()["EXA_API_KEY"], OLD_EXA)
        self.assertEqual(backups_of(self.path), [])

    # SEC-5: line breaks ---------------------------------------------------------------------------------------------

    def test_a_carriage_return_in_a_value_is_refused(self) -> None:
        """SEC-5: bin/mcp-run keeps a CR that c101's Python readers drop; the value is refused (usage error), and the
        refusal text carries no value."""
        res = self.merge("EXA_API_KEY=%s\rtail\n" % SECRET_SENTINEL, template=False, preset=False)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(json.loads(res.stdout), {"error": "stdin-value"})
        self.assertNotIn(SECRET_SENTINEL, res.stdout + res.stderr)
        self.assertFalse(self.path.exists())

    def test_crlf_stack_env_is_rewritten_with_lf_and_the_backup_keeps_the_original(self) -> None:
        """SEC-5: bash readers split on LF only, so a CRLF file is rewritten with LF (reported as crlf_normalized)."""
        original = b"# note\r\nEXA_API_KEY=\r\nJINA_API_KEY=keep-jina-fixture\r\n"
        self.put("")
        self.path.write_bytes(original)
        self.path.chmod(0o600)
        out = self.merged(self.merge("EXA_API_KEY=%s\n" % sentinel_for("EXA_API_KEY"), template=False, preset=False))
        self.assertTrue(out["crlf_normalized"] and out["wrote"])
        self.assertNotIn(b"\r", self.path.read_bytes())
        vals = self.values()
        self.assertEqual(vals["EXA_API_KEY"], sentinel_for("EXA_API_KEY"))
        self.assertEqual(vals["JINA_API_KEY"], "keep-jina-fixture")
        baks = backups_of(self.path)
        self.assertEqual([b.read_bytes() for b in baks], [original])

    def test_other_line_breaks_in_the_file_refuse_the_merge(self) -> None:
        """SEC-5: a bare CR, VT, FF, FS, NEL, LS, PS or NUL would be split differently by the Python and bash readers:
        exit 3, code line-breaks, the file untouched."""
        for label, brk in (("CR", b"\r"), ("VT", b"\x0b"), ("FF", b"\x0c"), ("FS", b"\x1c"), ("NEL", "\u0085".encode()),
                           ("LS", " ".encode()), ("PS", " ".encode()), ("NUL", b"\x00")):
            with self.subTest(label):
                path = self.sb.base / ("breaks-%s" % label) / "stack.env"
                path.parent.mkdir()
                path.parent.chmod(0o700)
                data = b"EXA_API_KEY=a" + brk + b"b\n"
                path.write_bytes(data)
                path.chmod(0o600)
                res = self.merge("JINA_API_KEY=%s\n" % sentinel_for("JINA_API_KEY"), path=path, template=False, preset=False)
                self.assertEqual(res.returncode, 3, res.stdout + res.stderr)
                self.assertEqual(json.loads(res.stdout), {"error": "line-breaks"})
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(list(path.parent.glob("stack.env.bak-*")), [])

    # SEC-11 and stdin rules -------------------------------------------------------------------------------------

    def test_control_characters_in_a_value_are_refused(self) -> None:
        """SEC-11: C0, DEL, C1 (U+0085 among them), U+2028 and U+2029 are refused with exit 2 and nothing written."""
        for label, ch in (("SOH", "\x01"), ("ESC", "\x1b"), ("TAB", "\t"), ("DEL", "\x7f"), ("NEL", "\x85"),
                          ("APC", "\x9f"), ("LS", " "), ("PS", " ")):
            with self.subTest(label):
                res = self.merge("EXA_API_KEY=%s%sx\n" % (SECRET_SENTINEL, ch), template=False, preset=False)
                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
                self.assertEqual(json.loads(res.stdout), {"error": "stdin-value"})
                self.assertNotIn(SECRET_SENTINEL, res.stdout + res.stderr)
                self.assertFalse(self.path.exists())

    def test_bad_stdin_lines_are_usage_errors(self) -> None:
        cases = (
            ("exa_api_key=x\n", "stdin-name"),
            ("NOEQUALS\n", "stdin-format"),
            (b"EXA_API_KEY=\xff\xfe\n", "stdin-not-utf8"),
            ("EXA_API_KEY=" + "x" * 16385 + "\n", "stdin-value"),
        )
        for stdin, code in cases:
            with self.subTest(code=code):
                res = self.merge(stdin, template=False, preset=False)
                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
                self.assertEqual(json.loads(res.stdout), {"error": code})
                self.assertFalse(self.path.exists())

    def test_values_with_spaces_hash_and_quotes_read_back_unchanged(self) -> None:
        """A value is quoted only when a parser would otherwise change it, and every parser reads it back identically."""
        odd = {
            "EXA_API_KEY": "two words",
            "JINA_API_KEY": "has#hash",
            "SPIDER_API_KEY": "'single-quote-start",
            "CONTEXT7_API_KEY": '"double-quote-start',
            "FIRECRAWL_API_KEY": 'it\'s a "mixed" one',
        }
        self.merged(self.merge("".join("%s=%s\n" % kv for kv in odd.items()), template=False, preset=False))
        self.assertEqual({k: v for k, v in self.values().items() if k in odd}, odd)

    def test_comments_order_and_inline_notes_survive_a_merge(self) -> None:
        original = ("# my notes\nEXA_API_KEY=   # exa key goes here\nOTHER_VAR=keepme\n"
                    "export JINA_API_KEY=old-jina-fixture  # keep this note\n")
        self.put(original)
        self.merged(self.merge("EXA_API_KEY=%s\nJINA_API_KEY=%s\nSPIDER_API_KEY=%s\n" % (
            sentinel_for("EXA_API_KEY"), sentinel_for("JINA_API_KEY"), sentinel_for("SPIDER_API_KEY")), "--overwrite",
            template=False, preset=False))
        text = self.text()
        self.assertTrue(text.startswith("# my notes\n"))
        self.assertIn("# exa key goes here", text)
        self.assertIn("# keep this note", text)
        self.assertIn("export JINA_API_KEY=", text)
        self.assertEqual(text.count("# c101-linux keys"), 1)
        self.assertLess(text.index("OTHER_VAR=keepme"), text.index("# c101-linux keys"))
        self.assertLess(text.index("# c101-linux keys"), text.index("SPIDER_API_KEY="))
        vals = self.values()
        self.assertEqual((vals["OTHER_VAR"], vals["EXA_API_KEY"], vals["JINA_API_KEY"], vals["SPIDER_API_KEY"]),
                         ("keepme", sentinel_for("EXA_API_KEY"), sentinel_for("JINA_API_KEY"), sentinel_for("SPIDER_API_KEY")))

    def test_duplicate_assignments_are_brought_in_line_by_overwrite(self) -> None:
        """bin/mcp-run takes the FIRST non-empty assignment of a name, every other reader the LAST: both are replaced."""
        self.put("EXA_API_KEY=old-first-fixture\nEXA_API_KEY=old-last-fixture\n")
        self.merged(self.merge("EXA_API_KEY=new-exa-fixture\n", "--overwrite", template=False, preset=False))
        text = self.text()
        self.assertEqual(text.count("EXA_API_KEY=new-exa-fixture"), 2)
        self.assertNotIn("old-first-fixture", text)
        self.assertNotIn("old-last-fixture", text)

    # names, check, read-only ----------------------------------------------------------------------------------

    def test_names_reports_set_empty_and_absent_without_values(self) -> None:
        self.put("EXA_API_KEY=%s\nJINA_API_KEY=\nSPIDER_API_KEY=%s\n" % (SECRET_SENTINEL, FAL_PLACEHOLDER))
        res = self.stackenv("names", "--file", str(self.path), "--keys", ",".join(RESEARCH_KEYS))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), {"EXA_API_KEY": "set", "JINA_API_KEY": "empty", "SPIDER_API_KEY": "empty",
                                                  "CONTEXT7_API_KEY": "absent"})
        self.assertNotIn(SECRET_SENTINEL, res.stdout + res.stderr)
        every = json.loads(self.stackenv("names", "--file", str(self.path)).stdout)
        self.assertEqual(sorted(every), ["EXA_API_KEY", "JINA_API_KEY", "SPIDER_API_KEY"])
        missing = json.loads(self.stackenv("names", "--file", str(self.sb.base / "nope.env"), "--keys", "EXA_API_KEY").stdout)
        self.assertEqual(missing, {"EXA_API_KEY": "absent"})

    def test_stackenv_output_holds_names_only(self) -> None:
        """Whatever the command, stdout and stderr never carry a value (the sentinel stands for every key)."""
        results = [self.merge("FAL_KEY=%s\nEXA_API_KEY=%s\n" % (SECRET_SENTINEL, sentinel_for("EXA_API_KEY")))]
        results.append(self.merge("FAL_KEY=%s\n" % SECRET_SENTINEL, "--dry-run"))
        results.append(self.merge("FAL_KEY=%s\n" % SECRET_SENTINEL, "--overwrite"))
        results.append(self.stackenv("names", "--file", str(self.path)))
        results.append(self.stackenv("ops", "--file", str(self.path)))
        results.append(self.stackenv("check", "--file", str(self.path)))
        self.assertEqual(self.sb.secret_leaks(*results, argv_logs=[]), [])
        self.assertEqual([r.returncode for r in results], [0] * len(results))

    def test_check_describes_the_link_its_target_and_the_directories(self) -> None:
        dot = self.sb.base / "dotfiles"
        dot.mkdir()
        link = self.put({"EXA_API_KEY": OLD_EXA}, mode=0o644, target_dir=dot)
        res = self.stackenv("check", "--file", str(link))
        self.assertEqual(res.returncode, 0, res.stderr)
        info = json.loads(res.stdout)
        self.assertTrue(info["exists"] and info["is_symlink"])
        self.assertFalse(info["dangling"])
        self.assertEqual(Path(info["target"]).resolve(), (dot / "stack.env").resolve())
        self.assertEqual((info["mode"], info["mode_ok"], info["owner_ok"]), ("644", False, True))
        self.assertEqual((info["target_writable"], info["readonly_fs"], info["reason"]), (True, False, None))
        self.assertEqual(Path(info["dir"]).resolve(), dot.resolve())
        self.assertEqual(Path(info["link_dir"]), link.parent)
        self.assertEqual((info["dir_issues"], info["link_dir_issues"], info["dir_ok"]), ([], [], True))

    def test_world_writable_directory_is_reported_by_check(self) -> None:
        """SEC-10: another account could swap the file; reported (never a refusal) for the target's directory and the link's."""
        dot = self.sb.base / "dotfiles"
        dot.mkdir()
        link = self.put({"EXA_API_KEY": OLD_EXA}, target_dir=dot)
        dot.chmod(0o777)
        info = json.loads(self.stackenv("check", "--file", str(link)).stdout)
        self.assertIn("world-writable", info["dir_issues"])
        self.assertEqual(info["link_dir_issues"], [])
        self.assertFalse(info["dir_ok"])
        dot.chmod(0o700)
        link.parent.chmod(0o777)
        res = self.stackenv("check", "--file", str(link))
        self.assertEqual(res.returncode, 0)
        info = json.loads(res.stdout)
        self.assertIn("world-writable", info["link_dir_issues"])
        self.assertEqual(info["dir_issues"], [])
        self.assertFalse(info["dir_ok"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write read-only files")
    def test_read_only_target_is_refused_with_exit_4(self) -> None:
        self.put({"EXA_API_KEY": OLD_EXA}, mode=0o444)
        before = self.path.read_bytes()
        res = self.merge("EXA_API_KEY=%s\n" % sentinel_for("EXA_API_KEY"), template=False, preset=False)
        self.assertEqual(res.returncode, 4)
        self.assertEqual(json.loads(res.stdout), {"refused": True, "reason": "not-writable"})
        self.assertEqual(self.path.read_bytes(), before)
        chk = self.stackenv("check", "--file", str(self.path))
        self.assertEqual(chk.returncode, 4)
        self.assertEqual(json.loads(chk.stdout)["reason"], "not-writable")

    def test_nix_store_link_is_refused_with_exit_4(self) -> None:
        """A home-manager style link into /nix/store: refused on the path alone (the file need not exist), never written."""
        missing = "/nix/store/c101l-test-missing/stack.env"
        self.path.parent.mkdir(parents=True)
        self.path.parent.chmod(0o700)
        self.path.symlink_to(missing)
        res = self.merge("EXA_API_KEY=%s\n" % sentinel_for("EXA_API_KEY"), template=False, preset=False)
        self.assertEqual(res.returncode, 4, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout), {"refused": True, "reason": "nix-store"})
        chk = self.stackenv("check", "--file", str(self.path))
        self.assertEqual(chk.returncode, 4)
        info = json.loads(chk.stdout)
        self.assertEqual((info["reason"], info["readonly_fs"], info["is_symlink"], info["dangling"]),
                         ("nix-store", True, True, True))
        self.assertFalse(Path(missing).exists())


@unittest.skipIf(pty is None, "needs the pty module")
class StackenvTerminalTests(EnvCase):
    def test_stdin_terminal_is_refused(self) -> None:
        """A terminal on stdin would echo the key: merge refuses it (exit 2) before reading or writing anything."""
        master, slave = pty.openpty()
        try:
            proc = subprocess.run([PYTHON, "-I", str(LIB / "stackenv.py"), "merge", "--file", str(self.path)], stdin=slave,
                                  capture_output=True, env=self.sb.env(), cwd=str(self.sb.base), timeout=60)
        finally:
            os.close(master)
            os.close(slave)
        proc = decoded(proc)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"error": "stdin-tty"})
        self.assertFalse(self.path.exists())


# ---- keys.sh through bash -----------------------------------------------------------------------------------------


class KeysCase(EnvCase):
    """EnvCase plus a bash runner for lib/keys.sh with the recording Python in front of lib/stackenv.py."""

    def setUp(self) -> None:
        super().setUp()
        self.spy, self.spy_log = install_python_spy(self.sb)

    def run_keys(self, body: str, *, env: Optional[Dict[str, str]] = None, stdin: Any = None, c101: Any = True) -> KeysRun:
        """Run `body` after keys.sh was sourced. stdin None = /dev/null, else the given text on a pipe. c101: True = the
        stub checkout of this test (C101_SRC), a Path = that checkout, None/False = no C101_SRC at all."""
        src = self.c101 if c101 is True else (c101 or None)
        extra = {"C101L_PY": str(self.spy)}
        extra.update(env or {})
        kwargs = {"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin.encode("utf-8")}
        proc = subprocess.run([BASH, "-c", keys_script(body, src)], capture_output=True, env=self.sb.env(**extra),
                              cwd=str(self.sb.base), timeout=120, **kwargs)
        return KeysRun(decoded(proc))

    def spy_records(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.spy_log)

    def stackenv_calls(self, sub: str) -> List[Dict[str, Any]]:
        """The recorded helper calls `stackenv.py SUB ...`."""
        return [r for r in self.spy_records()
                if len(r["argv"]) >= 3 and r["argv"][1].endswith("/lib/stackenv.py") and r["argv"][2] == sub]

    def assert_clean(self, *runs: KeysRun, trees: Optional[List[Path]] = None, allow: Any = None) -> None:
        """The sentinel is nowhere but in stack.env: not in stdout, stderr, the bash argv, any helper's argv or environment,
        HOME or TMPDIR (and `trees`, with `allow` for the files that may hold a key)."""
        kw = {}  # type: Dict[str, Any]
        if trees is not None:
            kw["trees"] = trees
        if allow is not None:
            kw["allow"] = allow
        self.assertEqual(self.sb.secret_leaks(*[r.proc for r in runs], argv_logs=[self.spy_log], check_env=True, **kw), [])


@needs_linux
class KeysStepTests(KeysCase):
    # secrets ---------------------------------------------------------------------------------------------------------

    def test_sentinel_never_in_output(self) -> None:
        """sentinel-never-in-output: every key goes through the step; stdout, stderr and the file tree hold no value (only
        stack.env does), and names are all the step prints."""
        run = self.run_keys(STEP_FAL, env=all_key_env())
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        vals = self.values()  # the scan below is not vacuous: the keys did land in stack.env
        self.assertEqual(vals["FAL_KEY"], SECRET_SENTINEL)
        for name in RESEARCH_KEYS + EXTRA_KEYS:
            self.assertEqual(vals[name], sentinel_for(name), name)
        self.assertEqual(vals["IMAGE_UPSCALE_KEY"], SECRET_SENTINEL)
        self.assertNotIn(SECRET_SENTINEL, run.out)
        self.assertNotIn(SECRET_SENTINEL, run.err)
        self.assertEqual(run.rep["MISSING"], "")
        self.assertIn("FAL_KEY", run.rep["CHANGED"].split())
        self.assert_clean(run)

    def test_sentinel_never_in_child_argv(self) -> None:
        """sentinel-never-in-child-argv: the values travel on the merge's stdin pipe; no helper child has one in its argv
        or environment (the installer un-exported the variables before the first child)."""
        run = self.run_keys(STEP_FAL, env=all_key_env())
        self.assertEqual(run.rc, 0, run.err)
        records = self.spy_records()
        self.assertTrue(records, "the recording interpreter was never used")
        for rec in records:
            self.assertFalse([a for a in rec["argv"] if SECRET_SENTINEL in a], rec["argv"][:4])
            self.assertEqual(rec["env_with_sentinel"], [], "a helper child inherited a key variable")
        merges = self.stackenv_calls("merge")
        self.assertEqual(len(merges), 1)
        self.assertEqual(merges[0]["stdin"], "pipe")
        self.assertNotIn("--overwrite", merges[0]["argv"])
        self.assertNotIn(SECRET_SENTINEL, " ".join(run.proc.args))
        self.assertEqual(find_secret_leaks(argv_logs=[self.spy_log], check_env=True), [])

    def test_children_never_inherit_key_variables(self) -> None:
        """P7: after the step every key variable is gone (not exported, not even a shell variable), so later children
        (pre-warm, the browser download, podman) cannot inherit one."""
        body = r'''KEYS=env
IMAGE_BACKEND=fal
keys_step
printf '@@AFTER_FAL_KEY=[%s]\n' "${FAL_KEY-unset}"
printf '@@AFTER_GEN_URL=[%s]\n' "${IMAGE_GEN_URL-unset}"
exported=$(compgen -e)
printf '@@EXPORTED=%s\n' "${exported//$'\n'/ }"
'''
        env = dict(all_key_env(), IMAGE_GEN_URL="https://example.invalid/v1", IMAGE_GEN_KEY=sentinel_for("IMAGE_GEN_KEY"),
                   IMAGE_GEN_MODEL="env/model-fixture")
        run = self.run_keys(body, env=env)
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["AFTER_FAL_KEY"], "[unset]")
        self.assertEqual(run.rep["AFTER_GEN_URL"], "[unset]")
        exported = run.rep["EXPORTED"].split()
        self.assertEqual([n for n in exported if n in KEY_ORDER + EXTRA_KEYS or n.startswith("IMAGE_")], [])
        self.assert_clean(run)

    def test_xtrace_is_off_while_a_key_is_held(self) -> None:
        run = self.run_keys("KEYS=env\nIMAGE_BACKEND=fal\nset -x\nkeys_step\nset +x\nreport\n", env=all_key_env())
        self.assertEqual(run.rc, 0, run.err)
        self.assertIn("+ keys_step", run.err)  # tracing was on around the call
        self.assertNotIn(SECRET_SENTINEL, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")

    # keep, overwrite, force -----------------------------------------------------------------------------------

    def test_keep_existing_without_keys_overwrite(self) -> None:
        """keep-existing-without-keys-overwrite (D10): a different FAL_KEY from the environment is reported as kept; the
        file's key stays and fills the empty fal operation keys; no --overwrite reaches stackenv.py."""
        self.put({"FAL_KEY": OLD_FAL})
        run = self.run_keys(STEP_FAL, env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        self.assertEqual(run.rep["KEPT"], "FAL_KEY")
        self.assertIn("kept the existing value of: FAL_KEY", run.out)
        self.assertIn("--keys-overwrite", run.out)
        vals = self.values()
        self.assertEqual(vals["FAL_KEY"], OLD_FAL)
        self.assertEqual([vals["IMAGE_%s_KEY" % op] for op in FAL_OPS], [OLD_FAL] * len(FAL_OPS))
        self.assertNotIn(SECRET_SENTINEL, self.text())
        self.assertNotIn("--overwrite", self.stackenv_calls("merge")[0]["argv"])
        self.assertEqual(len(backups_of(self.path)), 1)  # the file changed (operations were added), so it was backed up
        self.assert_clean(run)

    def test_keys_overwrite_overwrites(self) -> None:
        """keys-overwrite-overwrites (D2): KEYS_OVERWRITE=1, set after sourcing as --keys-overwrite does, passes --overwrite:
        the new key replaces FAL_KEY and every fal operation key, with a mode-600 backup of the old file first."""
        self.put({"FAL_KEY": OLD_FAL, "IMAGE_GEN_URL": FAL_URL, "IMAGE_GEN_KEY": OLD_FAL, "IMAGE_GEN_MODEL": FAL_MODELS["GEN"]})
        run = self.run_keys("KEYS=env\nIMAGE_BACKEND=fal\nKEYS_OVERWRITE=1\nkeys_step\nreport\n",
                            env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        self.assertEqual(run.rep["KEPT"], "")
        self.assertIn("overwrite=1", run.out)
        self.assertIn("--overwrite", self.stackenv_calls("merge")[0]["argv"])
        vals = self.values()
        self.assertEqual(vals["FAL_KEY"], SECRET_SENTINEL)
        self.assertEqual([vals["IMAGE_%s_KEY" % op] for op in FAL_OPS], [SECRET_SENTINEL] * len(FAL_OPS))
        self.assertNotIn(OLD_FAL, self.text())
        baks = backups_of(self.path)
        self.assertEqual(len(baks), 1)
        self.assertEqual(file_mode(baks[0]), 0o600)
        self.assertIn(OLD_FAL, baks[0].read_text(encoding="utf-8"))
        self.assertIn("previous version:", run.out)
        self.assertIn("bak-* file(s) now sit in", run.out)
        self.assert_clean(run)

    def test_force_alone_keeps_existing(self) -> None:
        """force-alone-keeps-existing (D2): --force belongs to the Desktop config merge and means nothing here, and a
        KEYS_OVERWRITE inherited from the caller's environment is reset when keys.sh is sourced: the existing key stays."""
        self.put({"FAL_KEY": OLD_FAL})
        body = "OPT_FORCE=1\nFORCE=1\nKEYS=env\nIMAGE_BACKEND=fal\nkeys_step\nreport\n"
        run = self.run_keys(body, env={"FAL_KEY": SECRET_SENTINEL, "KEYS_OVERWRITE": "1"})
        self.assertEqual(run.rc, 0, run.err)
        self.assertIn("overwrite=0", run.out)
        self.assertEqual(run.rep["KEPT"], "FAL_KEY")
        self.assertEqual(self.values()["FAL_KEY"], OLD_FAL)
        self.assertNotIn(SECRET_SENTINEL, self.text())
        for call in self.stackenv_calls("merge"):
            self.assertNotIn("--overwrite", call["argv"])
        self.assert_clean(run)

    def test_keys_overwrite_switches_a_non_fal_op_and_says_so(self) -> None:
        self.put(dict(StackenvCliTests.OTHER_GEN))
        run = self.run_keys("KEYS=env\nIMAGE_BACKEND=fal\nKEYS_OVERWRITE=1\nkeys_step\nreport\n", env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["KEPT_OPS"], "")
        self.assertIn("switched to the fal preset (--keys-overwrite): GEN", run.out)
        vals = self.values()
        self.assertEqual((vals["IMAGE_GEN_URL"], vals["IMAGE_GEN_KEY"]), (FAL_URL, SECRET_SENTINEL))
        self.assert_clean(run)

    def test_kept_ops_are_reported_by_operation_name(self) -> None:
        """D3: an operation on another provider stays as it is; the step names it, never a value."""
        self.put(dict(StackenvCliTests.OTHER_GEN))
        run = self.run_keys(STEP_FAL, env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["KEPT_OPS"], "GEN")
        self.assertIn("(kept_ops): GEN", run.out)
        vals = self.values()
        self.assertEqual(vals["IMAGE_GEN_KEY"], "other-op-key-fixture")
        self.assertEqual(vals["IMAGE_REF_KEY"], SECRET_SENTINEL)
        self.assert_clean(run)

    def test_environment_image_op_is_kept_whole_and_never_replaced_by_the_preset(self) -> None:
        """IMAGE_<OP>_* variables of another provider reach stackenv.py on stdin; that operation is the user's choice."""
        for i, extra in enumerate(("", "KEYS_OVERWRITE=1\n")):
            with self.subTest(overwrite=bool(extra)):
                if i:
                    self.path.unlink()
                env = dict(all_key_env(), IMAGE_GEN_URL="https://example.invalid/v1", IMAGE_GEN_KEY=sentinel_for("IMAGE_GEN_KEY"),
                           IMAGE_GEN_MODEL="env/model-fixture")
                run = self.run_keys("KEYS=env\nIMAGE_BACKEND=fal\n%skeys_step\nreport\n" % extra, env=env)
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual(run.rep["KEPT_OPS"], "GEN")
                self.assertIn("IMAGE_GEN_*", run.out)
                vals = self.values()
                self.assertEqual(vals["IMAGE_GEN_URL"], "https://example.invalid/v1")
                self.assertEqual(vals["IMAGE_GEN_KEY"], sentinel_for("IMAGE_GEN_KEY"))
                self.assertNotIn("IMAGE_GEN_PARAMS", vals)
                self.assertEqual(vals["IMAGE_TRACE_KEY"], SECRET_SENTINEL)

    # no terminal, key step off ------------------------------------------------------------------------------------

    def test_non_tty_skips(self) -> None:
        """non-tty-skips: --keys without a terminal warns once and asks nothing; what is waiting on stdin is not read, and
        the keys from the environment are still imported."""
        run = self.run_keys("KEYS=prompt\nIMAGE_BACKEND=none\nkeys_step\nreport\n", stdin="typed-exa-fixture\n",
                            env={"JINA_API_KEY": sentinel_for("JINA_API_KEY")})
        self.assertEqual(run.rc, 0, run.err)
        self.assertIn("stdin is not a terminal", run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        vals = self.values()
        self.assertEqual(vals["JINA_API_KEY"], sentinel_for("JINA_API_KEY"))
        self.assertEqual(vals["EXA_API_KEY"], "")
        self.assertNotIn("typed-exa-fixture", self.text())
        self.assertNotIn("Enter keeps it", run.err)
        self.assert_clean(run)

    def test_auto_without_a_terminal_skips_the_prompt(self) -> None:
        run = self.run_keys("KEYS=auto\nIMAGE_BACKEND=none\nkeys_step\nreport\n", stdin="typed-exa-fixture\n",
                            env={"JINA_API_KEY": sentinel_for("JINA_API_KEY")})
        self.assertEqual(run.rc, 0, run.err)
        self.assertIn("not interactive: no key prompt", run.out)
        self.assertNotIn("typed-exa-fixture", self.text())
        self.assertEqual(self.values()["JINA_API_KEY"], sentinel_for("JINA_API_KEY"))

    def test_keys_env_never_prompts_and_never_reads_stdin(self) -> None:
        """KEYS=env (the ci and headless profiles): only the environment is read."""
        run = self.run_keys(STEP_NONE, stdin="typed-exa-fixture\n", env={"JINA_API_KEY": sentinel_for("JINA_API_KEY")})
        self.assertEqual(run.rc, 0, run.err)
        self.assertNotIn("typed-exa-fixture", self.text())
        self.assertNotIn("stdin is not a terminal", run.err)  # no prompt branch is even reached
        self.assertEqual(self.values()["JINA_API_KEY"], sentinel_for("JINA_API_KEY"))

    def test_keys_0_ignores_the_environment_but_still_merges_the_fal_preset(self) -> None:
        """KEYS=0: nothing is imported or asked; IMAGE_BACKEND=fal still writes the preset (key-less) and warns."""
        run = self.run_keys("KEYS=0\nIMAGE_BACKEND=fal\nkeys_step\nreport\n", env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        vals = self.values()
        self.assertEqual(vals["IMAGE_GEN_URL"], FAL_URL)
        self.assertEqual(vals["IMAGE_GEN_KEY"], "")
        self.assertEqual(vals["FAL_KEY"], "")
        self.assertEqual(run.rep["FAL_MISSING"], "1")
        self.assertTrue([w for w in run.warnings() if "FAL_KEY" in w])
        self.assertNotIn(SECRET_SENTINEL, self.text())

    def test_key_step_off_only_warns_about_a_loose_stack_env(self) -> None:
        """SEC-3: with the step off nothing is written, but a stack.env that others can read is named."""
        self.put({"EXA_API_KEY": OLD_EXA}, mode=0o644)
        run = self.run_keys("KEYS=0\nIMAGE_BACKEND=none\nkeys_step\nreport\n")
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "skipped")
        self.assertIn("has mode 644", run.err)
        self.assertIn("stack.env", run.err)
        self.assertEqual(file_mode(self.path), 0o644)
        self.assertEqual(self.stackenv_calls("merge"), [])

    # the fal warning -----------------------------------------------------------------------------------------------

    def test_fal_preset_without_key_warns_once_naming_FAL_KEY_and_the_rerun_command(self) -> None:
        """D3: no fal key anywhere: the preset is still merged (URL and MODEL lines, empty keys) and ONE warning names
        FAL_KEY and prints `install-linux.sh --keys --keys-only` (D14: the literal D3 command, limited to the key step)."""
        run = self.run_keys(STEP_FAL)
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        self.assertEqual(run.rep["FAL_MISSING"], "1")
        self.assertEqual(run.rep["MISSING"], "FAL_KEY EXA_API_KEY JINA_API_KEY SPIDER_API_KEY CONTEXT7_API_KEY")
        warned = [w for w in run.warnings() if "FAL_KEY" in w]
        self.assertEqual(len(warned), 1, run.err)
        self.assertIn("install-linux.sh --keys --keys-only", warned[0])
        vals = self.values()
        for name, want in fal_preset_values("").items():
            self.assertEqual(vals.get(name), want, name)
        self.assertEqual(self.ops()["GEN"]["status"], "key-empty")

    def test_an_unattended_fal_warning_keeps_the_exact_keys_command_and_adds_the_env_form(self) -> None:
        """D13, D14: under profile ci or --yes (ASSUME_YES=1) there is still ONE warning, by name only; it prints the exact
        `install-linux.sh --keys --keys-only` (a re-run of the key step from a terminal, hidden prompt; only --profile may
        follow here) and the unattended form: export FAL_KEY, then `install-linux.sh --keys-only --keys-from-env`."""
        for prefix, profile in (("PROFILE=ci\n", " --profile ci"), ("ASSUME_YES=1\n", "")):
            with self.subTest(prefix=prefix.strip()):
                if self.path.exists():
                    self.path.unlink()
                run = self.run_keys(prefix + STEP_FAL)
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual(run.rep["FAL_MISSING"], "1")
                warned = [w for w in run.warnings() if "FAL_KEY" in w]
                self.assertEqual(len(warned), 1, run.err)
                self.assertRegex(warned[0], r"install-linux\.sh --keys --keys-only%s(?![-\w])(?! --)" % re.escape(profile))
                self.assertIn("export FAL_KEY", warned[0])
                self.assertIn("install-linux.sh --keys-only --keys-from-env%s" % profile, warned[0])
                self.assert_clean(run)

    def assert_keys_only_command(self, flags: List[str], profile: str, backend: str, context: str) -> None:
        """D14: a key re-run command is `install-linux.sh --keys --keys-only` or `install-linux.sh --keys-only
        --keys-from-env`, followed only by --profile (when the profile is not standard), --image-backend (when the
        effective backend is not fal, D12) and a --source the caller adds."""
        self.assertIn(flags[:2], (["--keys", "--keys-only"], ["--keys-only", "--keys-from-env"]), context)
        rest = flags[2:]
        self.assertEqual([f for f in rest if f not in ("--profile", "--image-backend", "--source")], [], context)
        self.assertEqual("--profile" in rest, profile not in ("", "standard"), context)
        self.assertEqual("--image-backend" in rest, backend not in ("", "fal"), context)

    def test_no_key_hint_ever_lacks_keys_only(self) -> None:
        """D14 (QC-2): a key hint never re-runs the whole installer. A plain `install-linux.sh --keys` would re-apply the
        profile's defaults (FAL_MCP=1, PRESET=everything, the timer and pre-warm opt-ins) over the choices of an earlier
        install such as `--preset research --no-fal-mcp`. So every command keys_rerun_cmd and keys_rerun_hint print, in
        every mode, profile, --yes setting and effective backend, and every command a real key step prints, carries
        --keys-only: the user's literal `install-linux.sh --keys` followed by `--keys-only` from a terminal, or the
        unattended `install-linux.sh --keys-only --keys-from-env` (ci and --yes runs print both)."""
        body = r"""for PROFILE in standard minimal ci; do
  for ASSUME_YES in 0 1; do
    for KEYS_BACKEND_EFFECTIVE in fal none keep; do
      ctx="$PROFILE|$ASSUME_YES|$KEYS_BACKEND_EFFECTIVE"
      printf '@@CMD=%s|%s\n' "$ctx" "$(keys_rerun_cmd)" "$ctx" "$(keys_rerun_cmd only)" "$ctx" "$(keys_rerun_cmd env)"
      printf '@@HINT=%s|%s\n' "$ctx" "$(keys_rerun_hint)" "$ctx" "$(keys_rerun_hint only)" \
        "$ctx" "$(keys_rerun_hint "" FAL_KEY)" "$ctx" "$(keys_rerun_hint "" "" "--source PATH")"
    done
  done
done
"""
        run = self.run_keys(body)
        self.assertEqual(run.rc, 0, run.err)
        lines = [ln for ln in run.out.splitlines() if ln.startswith(("@@CMD=", "@@HINT="))]
        self.assertEqual(len(lines), 3 * 2 * 3 * 7, run.out)
        for line in lines:
            kind, rest = line[2:].split("=", 1)
            profile, yes, backend, text = rest.split("|", 3)
            cmds = install_commands(text)
            unattended = profile == "ci" or yes == "1"
            self.assertEqual(len(cmds), 2 if kind == "HINT" and unattended else 1, line)
            for flags in cmds:
                self.assert_keys_only_command(flags, profile, backend, line)
            if kind == "HINT" and unattended:
                self.assertEqual([c[:2] for c in cmds], [["--keys", "--keys-only"], ["--keys-only", "--keys-from-env"]], line)
            else:  # `keys_rerun_cmd env` is the unattended form; every other single command is the terminal one
                want = ["--keys-only", "--keys-from-env"] if "--keys-from-env" in text else ["--keys", "--keys-only"]
                self.assertEqual(cmds[0][:2], want, line)
        self.assertEqual(len([ln for ln in lines if ln.startswith("@@CMD=") and "--keys-from-env" in ln]), 3 * 2 * 3,
                         "only `keys_rerun_cmd env` prints the unattended form on its own")
        # the warning and hints of a real key step without a fal key, interactive and unattended
        for prefix, profile in (("", ""), ("PROFILE=ci\n", "ci"), ("ASSUME_YES=1\n", ""), ("PROFILE=minimal\nASSUME_YES=1\n", "minimal")):
            with self.subTest(prefix=prefix.strip()):
                if self.path.exists():
                    self.path.unlink()
                run = self.run_keys(prefix + STEP_FAL)
                self.assertEqual(run.rc, 0, run.err)
                printed = [ln for ln in run.out.splitlines() + run.err.splitlines() if "install-linux.sh" in ln]
                self.assertTrue([ln for ln in printed if "FAL_KEY" in ln], run.err)
                for ln in printed:
                    cmds = install_commands(ln)
                    self.assertTrue(cmds, ln)
                    for flags in cmds:
                        self.assert_keys_only_command(flags, profile, "fal", ln)
                self.assert_clean(run)

    def test_missing_fal_preset_warns_and_the_keys_still_merge(self) -> None:
        nofal = build_stub_c101(self.sb.base / "c101-nofal", with_venv=False, with_fal_example=False)
        run = self.run_keys(STEP_FAL, env={"FAL_KEY": SECRET_SENTINEL}, c101=nofal)
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        self.assertTrue([w for w in run.warnings() if "the fal preset" in w and "not on disk" in w], run.err)
        vals = self.values()
        self.assertEqual(vals["FAL_KEY"], SECRET_SENTINEL)
        self.assertEqual(vals.get("IMAGE_GEN_URL", ""), "")
        self.assert_clean(run)

    def test_backend_none_and_keep_change_no_image_setting(self) -> None:
        for backend in ("none", "keep"):
            with self.subTest(backend=backend):
                if self.path.exists():
                    self.path.unlink()
                run = self.run_keys("KEYS=env\nIMAGE_BACKEND=%s\nkeys_step\nreport\n" % backend, env={"FAL_KEY": SECRET_SENTINEL})
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual(run.rep["KEPT_OPS"], "")
                vals = self.values()
                self.assertEqual(vals["FAL_KEY"], SECRET_SENTINEL)
                self.assertEqual([vals.get("IMAGE_%s_URL" % op, "") for op in FAL_OPS], [""] * len(FAL_OPS))
                self.assertNotIn("IMAGE_GEN_PARAMS", vals)

    def test_keep_backend_reports_the_recorded_backend(self) -> None:
        """P4: keep never erases a recorded fal (or none): the effective backend is what the state file holds."""
        state = self.sb.home / ".local" / "state" / "c101-linux" / "state.json"
        state.parent.mkdir(parents=True)
        for recorded, want in (("fal", "fal"), ("none", "none"), (None, "keep")):
            with self.subTest(recorded=recorded):
                doc = {"version": 1}  # type: Dict[str, Any]
                if recorded:
                    doc["image_backend"] = recorded
                state.write_text(json.dumps(doc) + "\n", encoding="utf-8")
                run = self.run_keys("KEYS=0\nIMAGE_BACKEND=keep\nkeys_step\nreport\n")
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual(run.rep["BACKEND"], want)
                self.assertEqual(run.rep["RESULT"], "skipped")

    # dry run -------------------------------------------------------------------------------------------------------

    def test_dry_run_writes_nothing(self) -> None:
        """dry-run-writes-nothing: `would set NAME` lines, no file, no directory, no temp file, no value printed."""
        run = self.run_keys("DRY_RUN=1\n" + STEP_FAL, env=all_key_env())
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "dry-run")
        self.assertIn("would set FAL_KEY", run.out)
        self.assertIn("would set IMAGE_GEN_URL", run.out)
        self.assertIn("would set EXA_API_KEY", run.out)
        self.assertIn("would create ~/.claude/stack.env (mode 600)", run.out)
        self.assertEqual(self.sb.snapshot(), {})
        self.assertEqual(list(self.sb.tmpdir.iterdir()), [])
        merges = self.stackenv_calls("merge")
        self.assertEqual(len(merges), 1)
        self.assertIn("--dry-run", merges[0]["argv"])
        self.assert_clean(run)

    def test_dry_run_leaves_an_existing_loose_file_alone(self) -> None:
        self.put({"FAL_KEY": OLD_FAL}, mode=0o644)
        before = self.path.read_bytes()
        run = self.run_keys("DRY_RUN=1\n" + STEP_FAL, env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertIn("would tighten", run.out)
        self.assertIn("would keep the existing value of: FAL_KEY", run.out)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(file_mode(self.path), 0o644)
        self.assertEqual(backups_of(self.path), [])
        self.assert_clean(run)

    # the file's place: links, mode, directories, read-only ---------------------------------------------------------

    def test_key_step_writes_through_a_symlinked_stack_env(self) -> None:
        dot = self.sb.base / "dotfiles"
        dot.mkdir()
        link = self.put(None, target_dir=dot)  # c101's own template, as c101's installer leaves it
        link_text = os.readlink(link)
        run = self.run_keys(STEP_FAL, env=all_key_env())
        self.assertEqual(run.rc, 0, run.err)
        self.assertIn("updated ~/.claude/stack.env (mode 600)", run.out)
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), link_text)
        vals = read_stack_env(dot / "stack.env")
        self.assertEqual(vals["FAL_KEY"], SECRET_SENTINEL)
        self.assertEqual(vals["IMAGE_GEN_KEY"], SECRET_SENTINEL)
        self.assertEqual(file_mode(dot / "stack.env"), 0o600)
        allow = list(env_file_allowances(link)) + list(env_file_allowances(dot / "stack.env")) + list(STACK_ENV_ALLOW)
        self.assert_clean(run, trees=[self.sb.home, self.sb.tmpdir, dot], allow=allow)

    def test_loose_stack_env_is_tightened_by_the_key_step(self) -> None:
        """SEC-3: with nothing else to do the merge still tightens a 644 file (no rewrite, no backup) and the step says so."""
        self.put({"EXA_API_KEY": OLD_EXA}, mode=0o644)
        before = self.path.read_bytes()
        run = self.run_keys(STEP_NONE)
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        self.assertIn("tightened", run.out)
        self.assertIn("644", run.out)
        self.assertEqual(file_mode(self.path), 0o600)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(backups_of(self.path), [])

    def test_group_or_world_writable_directory_is_warned_about_by_path(self) -> None:
        """SEC-10: warn (never refuse) when another account could swap stack.env; the keys are still written."""
        self.put({"EXA_API_KEY": OLD_EXA})
        self.path.parent.chmod(0o777)
        run = self.run_keys(STEP_FAL, env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        warned = [w for w in run.warnings() if "world-writable" in w]
        self.assertEqual(len(warned), 1, run.err)
        self.assertIn("another local account could swap the file", warned[0])
        self.assertEqual(self.values()["FAL_KEY"], SECRET_SENTINEL)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write read-only files")
    def test_read_only_target_is_a_warning_not_a_failure(self) -> None:
        """Exit 4 of stackenv.py check: one warning, no prompt, nothing written, and the step returns 0 (the install goes on)."""
        self.put({"EXA_API_KEY": OLD_EXA}, mode=0o444)
        before = self.path.read_bytes()
        run = self.run_keys(STEP_FAL, env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "readonly")
        self.assertTrue([w for w in run.warnings() if "cannot be rewritten (not-writable)" in w], run.err)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(file_mode(self.path), 0o444)
        self.assertEqual(self.stackenv_calls("merge"), [])
        self.assert_clean(run)

    def test_nix_store_link_gets_the_home_manager_hint(self) -> None:
        missing = "/nix/store/c101l-test-missing/stack.env"
        self.path.parent.mkdir(parents=True)
        self.path.parent.chmod(0o700)
        self.path.symlink_to(missing)
        run = self.run_keys(STEP_FAL, env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "readonly")
        self.assertTrue([w for w in run.warnings() if "cannot be rewritten (nix-store)" in w], run.err)
        self.assertIn("home-manager", run.out)
        self.assertEqual(self.stackenv_calls("merge"), [])
        self.assertFalse(Path(missing).exists())
        self.assert_clean(run)

    # values the helper would refuse ----------------------------------------------------------------------------

    def test_values_with_control_characters_are_dropped_by_name(self) -> None:
        """SEC-5, SEC-11: one value stackenv.py would refuse (CR, U+2028, U+0085, invalid UTF-8) is dropped here by name
        with a warning, so it cannot block the other keys, and the warning carries no part of the value."""
        body = r'''KEYS=env
IMAGE_BACKEND=none
EXA_API_KEY=$'crval-LEAKCHECK\rtail'
JINA_API_KEY=$'ls-LEAKCHECK\xe2\x80\xa8tail'
SPIDER_API_KEY=$'nel-LEAKCHECK\xc2\x85tail'
FIRECRAWL_API_KEY=$'bad-LEAKCHECK\xff'
keys_step
report
'''
        run = self.run_keys(body, env={"CONTEXT7_API_KEY": sentinel_for("CONTEXT7_API_KEY")})
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "ok")
        for name in ("EXA_API_KEY", "JINA_API_KEY", "SPIDER_API_KEY", "FIRECRAWL_API_KEY"):
            self.assertTrue([w for w in run.warnings() if name in w and "it was ignored" in w], (name, run.err))
        self.assertNotIn("LEAKCHECK", run.out + run.err)
        vals = self.values()
        self.assertEqual(vals["CONTEXT7_API_KEY"], sentinel_for("CONTEXT7_API_KEY"))
        for name in ("EXA_API_KEY", "JINA_API_KEY", "SPIDER_API_KEY", "FIRECRAWL_API_KEY"):
            self.assertEqual(vals[name], "", name)

    # inputs, order, entry points ---------------------------------------------------------------------------------

    def test_missing_keys_are_reported_in_prompt_order(self) -> None:
        """The order of the names in every message is the order of the prompts: FAL_KEY, EXA, JINA, SPIDER, CONTEXT7."""
        run = self.run_keys(STEP_NONE)
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["RESULT"], "nochange")
        self.assertEqual(run.rep["MISSING"], " ".join(KEY_ORDER))
        self.assertIn("missing: FAL_KEY EXA_API_KEY JINA_API_KEY SPIDER_API_KEY CONTEXT7_API_KEY(optional)", run.out)
        self.assertEqual(run.rep["FAL_MISSING"], "0")  # backend none: no fal warning
        self.assertEqual(run.warnings(), [])
        self.assertFalse(self.path.exists())

    def test_keys_validate_rejects_bad_values_with_exit_2(self) -> None:
        for body, message in (("KEYS=bogus\nkeys_validate\n", "KEYS must be 0, env, prompt or auto"),
                              ("IMAGE_BACKEND=bogus\nkeys_validate\n", "--image-backend must be fal, none or keep"),
                              ("KEYS_OVERWRITE=2\nkeys_validate\n", "KEYS_OVERWRITE must be 0 or 1"),
                              ("KEYS=bogus\nkeys_step\n", "KEYS must be 0, env, prompt or auto")):
            with self.subTest(message=message):
                run = self.run_keys(body)
                self.assertEqual(run.rc, 2, run.err)
                self.assertIn(message, run.err)
        self.assertFalse(self.path.exists())
        ok = self.run_keys("KEYS=auto\nIMAGE_BACKEND=keep\nkeys_validate\nreport\n")
        self.assertEqual(ok.rc, 0, ok.err)

    def test_keys_only_touches_only_stack_env(self) -> None:
        run = self.run_keys("KEYS=env\nIMAGE_BACKEND=fal\nkeys_only\nreport\n", env={"FAL_KEY": SECRET_SENTINEL})
        self.assertEqual(run.rc, 0, run.err)
        self.assertIn("--keys-only: only stack.env is touched", run.out)
        self.assertEqual(run.rep["RESULT"], "ok")
        self.assertEqual(sorted(self.sb.snapshot()), [".claude", ".claude/stack.env"])
        self.assert_clean(run)


@needs_linux
class InstallerKeyFlagTests(unittest.TestCase):
    """install-linux.sh --print-config: how the key flags and profiles reach the key step's inputs (no change is made)."""

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)

    def config(self, *args: str, **env: str) -> Dict[str, str]:
        res = subprocess.run(installer_args("--print-config", *args), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             env=self.sb.env(**env), cwd=str(self.sb.base), timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)

    def test_install_linux_force_does_not_enable_keys_overwrite(self) -> None:
        """D2: --keys-overwrite is the only switch; --force (Desktop config merge) and an inherited KEYS_OVERWRITE are not."""
        self.assertEqual(self.config()["KEYS_OVERWRITE"], "0")
        self.assertEqual(self.config("--force")["KEYS_OVERWRITE"], "0")
        self.assertEqual(self.config(KEYS_OVERWRITE="1")["KEYS_OVERWRITE"], "0")
        self.assertEqual(self.config("--keys-overwrite")["KEYS_OVERWRITE"], "1")
        self.assertEqual(self.config("--keys-overwrite", "--force")["KEYS_OVERWRITE"], "1")

    def test_install_linux_key_flags_and_profile_defaults(self) -> None:
        defaults = {"minimal": "auto", "standard": "auto", "full": "auto", "headless": "env", "ci": "env"}
        for profile, keys in defaults.items():
            with self.subTest(profile=profile):
                cfg = self.config("--profile", profile)
                self.assertEqual((cfg["KEYS"], cfg["IMAGE_BACKEND"]), (keys, "fal"))
        self.assertEqual(self.config("--keys")["KEYS"], "prompt")
        self.assertEqual(self.config("--keys-from-env")["KEYS"], "env")
        self.assertEqual(self.config("--profile", "headless", "--keys")["KEYS"], "prompt")
        for backend in ("fal", "none", "keep"):
            self.assertEqual(self.config("--image-backend", backend)["IMAGE_BACKEND"], backend)

    def test_install_linux_keeps_a_recorded_none_backend_unless_image_backend_is_given(self) -> None:
        """D12 (RV1 R1): a recorded image_backend=none is sticky in the plan: no profile turns fal back on by itself; an
        explicit --image-backend (fal, keep or none) is taken as given."""
        state = self.sb.home / ".local" / "state" / "c101-linux" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"version": 1, "image_backend": "none"}) + "\n", encoding="utf-8")
        for profile in ("minimal", "standard", "full", "headless", "ci"):
            with self.subTest(profile=profile):
                self.assertEqual(self.config("--profile", profile)["IMAGE_BACKEND"], "none")
        for backend in ("fal", "none", "keep"):
            self.assertEqual(self.config("--image-backend", backend)["IMAGE_BACKEND"], backend)
        state.write_text(json.dumps({"version": 1, "image_backend": "fal"}) + "\n", encoding="utf-8")
        self.assertEqual(self.config()["IMAGE_BACKEND"], "fal", "only none is sticky")

    def test_install_linux_keys_only_seeds_stack_env_and_leaves_no_key_anywhere_else(self) -> None:
        """--keys-only --keys-from-env end to end: stack.env (mode 600) gets the keys, the state file records the backend and
        no key, and the sentinel is in no output and no file but stack.env; c101's own installer is never started."""
        stub = make_stub_c101(self.sb.base / "c101")
        res = subprocess.run(installer_args("--keys-only", "--keys-from-env", "--image-backend", "fal", "--no-sudo", "--yes",
                                            "--source", str(stub)),
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, env=self.sb.env(**all_key_env()),
                             cwd=str(self.sb.base), timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        vals = read_stack_env(self.sb.stack_env)
        self.assertEqual(vals["FAL_KEY"], SECRET_SENTINEL)
        self.assertEqual(vals["IMAGE_GEN_KEY"], SECRET_SENTINEL)
        self.assertEqual(vals["EXA_API_KEY"], sentinel_for("EXA_API_KEY"))
        self.assertEqual(file_mode(self.sb.stack_env), 0o600)
        state = json.loads((self.sb.home / ".local" / "state" / "c101-linux" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["image_backend"], "fal")
        self.assertEqual(find_secret_leaks(results=[res], trees=[self.sb.home, self.sb.tmpdir], allow=STACK_ENV_ALLOW), [])
        self.assertFalse((stub / "calls.log").exists())  # c101's install.sh did not run
        self.assertEqual(read_jsonl(stub / "stack-calls.jsonl"), [])


# ---- the hidden prompts on a terminal ---------------------------------------------------------------------------


class Terminal:
    """`argv` with stdin on a pseudo terminal and stdout and stderr on pipes, driven like a person at the keyboard.

    The prompts of `read -s -p` go to stderr, so they are read from the stderr pipe; what the terminal itself shows (the
    echo of typed characters) is read from the master side. A reply to a hidden prompt is typed only once the terminal's
    ECHO flag is off, so a key that would be echoed fails the wait instead of showing up on the screen. Every wait is a
    condition with a deadline: no sleeps."""

    def __init__(self, argv: List[str], env: Dict[str, str], cwd: str, timeout: float = 40.0) -> None:
        self.argv = list(argv)
        self.deadline = time.monotonic() + timeout
        self.master, self.slave = pty.openpty()
        self.proc = subprocess.Popen(self.argv, stdin=self.slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                     cwd=cwd, start_new_session=True)
        self.fds = {"out": self.proc.stdout.fileno(), "err": self.proc.stderr.fileno(), "tty": self.master}
        self.buf = {"out": b"", "err": b"", "tty": b""}
        self.live = {"out": True, "err": True, "tty": True}
        self.mark = 0
        for fd in self.fds.values():
            os.set_blocking(fd, False)

    def text(self, key: str) -> str:
        return self.buf[key].decode("utf-8", "replace")

    @property
    def stdout(self) -> str:
        return self.text("out")

    @property
    def stderr(self) -> str:
        return self.text("err")

    @property
    def screen(self) -> str:
        """Everything the terminal displayed (the echo of typed input)."""
        return self.text("tty")

    def as_result(self) -> Any:
        """Shaped like a CompletedProcess for the leak scans of helpers."""
        return types.SimpleNamespace(stdout=self.stdout, stderr=self.stderr, args=self.argv, returncode=self.proc.returncode)

    def pump(self, wait: float = 0.05) -> None:
        live = [self.fds[k] for k in self.fds if self.live[k]]
        if not live:
            time.sleep(wait)
            return
        ready, _, _ = select.select(live, [], [], wait)
        for key, fd in self.fds.items():
            if not self.live[key] or fd not in ready:
                continue
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                chunk = b""
            if chunk:
                self.buf[key] += chunk
            else:
                self.live[key] = False

    def drain(self) -> None:
        while (self.live["out"] or self.live["err"]) and time.monotonic() < self.deadline:
            self.pump()
        self.pump(0)

    def wait_for(self, condition: Callable[[], bool], what: str) -> None:
        while not condition():
            if time.monotonic() > self.deadline:
                raise AssertionError("timed out waiting for %s; stderr so far: %r" % (what, self.stderr[-600:]))
            if self.proc.poll() is not None:
                self.drain()
                if condition():
                    return
                raise AssertionError("the script ended (exit %s) before %s; stderr: %r" % (self.proc.returncode, what, self.stderr[-600:]))
            self.pump()

    def expect(self, text: str) -> None:
        """Wait for `text` on stderr after the previous match (the prompts are printed there)."""
        needle = text.encode("utf-8")
        self.wait_for(lambda: needle in self.buf["err"][self.mark:], "the prompt %r" % text)
        self.mark = self.buf["err"].index(needle, self.mark) + len(needle)

    def echo_on(self) -> bool:
        return bool(termios.tcgetattr(self.slave)[3] & termios.ECHO)

    def answer_hidden(self, reply: str) -> None:
        """Type a reply to a `read -s` prompt: only after the terminal stopped echoing."""
        self.wait_for(lambda: not self.echo_on(), "the terminal to stop echoing (the prompt must be hidden)")
        os.write(self.master, reply.encode("utf-8") + b"\n")

    def answer(self, reply: str) -> None:
        os.write(self.master, reply.encode("utf-8") + b"\n")

    def finish(self) -> int:
        self.wait_for(lambda: self.proc.poll() is not None, "the script to finish")
        self.drain()
        return int(self.proc.returncode)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                self.proc.kill()
            self.proc.wait()
        for pipe in (self.proc.stdout, self.proc.stderr):
            pipe.close()
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@needs_linux
@unittest.skipIf(pty is None, "needs the pty module")
class KeysPromptTests(KeysCase):
    def terminal(self, body: str, **env: str) -> Terminal:
        term = Terminal([BASH, "-c", keys_script(body, self.c101)], self.sb.env(C101L_PY=str(self.spy), **env), str(self.sb.base))
        self.addCleanup(term.close)
        return term

    def prompt_names(self, term: Terminal) -> List[str]:
        return re.findall(r"^ {4}([A-Z][A-Z0-9_]*) - ", term.stderr, re.MULTILINE)

    def test_prompt_order_is_fal_exa_jina_spider_context7_and_the_input_is_hidden(self) -> None:
        """D3, D5: FAL_KEY first, then the research keys; the extras sit behind one y/N question. Typed keys are never
        echoed (each reply waits for ECHO to go off) and the terminal is restored afterwards."""
        term = self.terminal("KEYS=auto\nIMAGE_BACKEND=fal\nkeys_step\nreport\n")
        replies = (("FAL_KEY", SECRET_SENTINEL), ("EXA_API_KEY", sentinel_for("EXA_API_KEY")), ("JINA_API_KEY", ""),
                   ("SPIDER_API_KEY", sentinel_for("SPIDER_API_KEY")), ("CONTEXT7_API_KEY", ""))
        for name, reply in replies:
            term.expect("    %s - " % name)
            term.answer_hidden(reply)
        term.expect("enter any now?")
        term.answer("n")
        self.assertEqual(term.finish(), 0, term.stderr)
        self.assertEqual(self.prompt_names(term), list(KEY_ORDER))
        self.assertNotIn("FIRECRAWL_API_KEY - ", term.stderr)  # the extras were declined by the y/N question
        self.assertEqual(parse_report(term.stdout)["RESULT"], "ok")
        vals = self.values()
        self.assertEqual(vals["FAL_KEY"], SECRET_SENTINEL)
        self.assertEqual([vals["IMAGE_%s_KEY" % op] for op in FAL_OPS], [SECRET_SENTINEL] * len(FAL_OPS))
        self.assertEqual((vals["EXA_API_KEY"], vals["JINA_API_KEY"], vals["SPIDER_API_KEY"], vals["CONTEXT7_API_KEY"]),
                         (sentinel_for("EXA_API_KEY"), "", sentinel_for("SPIDER_API_KEY"), ""))
        self.assertTrue(term.echo_on(), "the terminal was left with echo off")
        self.assertEqual(find_secret_leaks(results=[term.as_result()], texts={"terminal": term.screen}, trees=[self.sb.home, self.sb.tmpdir],
                                           argv_logs=[self.spy_log], allow=STACK_ENV_ALLOW, check_env=True), [])

    def test_keys_prompt_asks_even_under_yes_and_skips_keys_already_set(self) -> None:
        """--keys asks on a terminal even with --yes; a key already in the file is not asked again."""
        self.put({"FAL_KEY": OLD_FAL, "EXA_API_KEY": OLD_EXA})
        term = self.terminal("ASSUME_YES=1\nKEYS=prompt\nIMAGE_BACKEND=fal\nkeys_step\nreport\n")
        for name, reply in (("JINA_API_KEY", sentinel_for("JINA_API_KEY")), ("SPIDER_API_KEY", sentinel_for("SPIDER_API_KEY")),
                            ("CONTEXT7_API_KEY", "")):
            term.expect("    %s - " % name)
            term.answer_hidden(reply)
        term.expect("enter any now?")
        term.answer("n")
        self.assertEqual(term.finish(), 0, term.stderr)
        self.assertEqual(self.prompt_names(term), ["JINA_API_KEY", "SPIDER_API_KEY", "CONTEXT7_API_KEY"])
        vals = self.values()
        self.assertEqual((vals["FAL_KEY"], vals["EXA_API_KEY"]), (OLD_FAL, OLD_EXA))
        self.assertEqual((vals["JINA_API_KEY"], vals["SPIDER_API_KEY"]), (sentinel_for("JINA_API_KEY"), sentinel_for("SPIDER_API_KEY")))
        self.assertEqual(vals["IMAGE_GEN_KEY"], OLD_FAL)  # the one fal key fills the operations
        self.assertNotIn(sentinel_for("JINA_API_KEY"), term.screen + term.stdout + term.stderr)

    def test_keys_overwrite_asks_for_keys_that_are_already_set(self) -> None:
        """--keys-overwrite asks for every key and shows only whether it is set; Enter keeps the current value."""
        self.put({"FAL_KEY": OLD_FAL, "EXA_API_KEY": OLD_EXA})
        term = self.terminal("KEYS=prompt\nKEYS_OVERWRITE=1\nIMAGE_BACKEND=fal\nkeys_step\nreport\n")
        for name in KEY_ORDER:
            term.expect("    %s - " % name)
            term.answer_hidden("")
        term.expect("enter any now?")
        term.answer("")
        self.assertEqual(term.finish(), 0, term.stderr)
        self.assertEqual(self.prompt_names(term), list(KEY_ORDER))
        self.assertRegex(term.stderr, r"(?m)^ {4}FAL_KEY - .*\(set; Enter keeps it\)")
        self.assertRegex(term.stderr, r"(?m)^ {4}JINA_API_KEY - .*\(empty; Enter keeps it\)")
        vals = self.values()
        self.assertEqual((vals["FAL_KEY"], vals["EXA_API_KEY"]), (OLD_FAL, OLD_EXA))
        self.assertIn("--overwrite", self.stackenv_calls("merge")[0]["argv"])

    def test_extras_are_asked_directly_when_their_server_is_in_mcp_and_after_a_yes_otherwise(self) -> None:
        """The optional servers' keys come last: asked straight away when --mcp names their server, otherwise only after
        one y/N question that defaults to no."""
        term = self.terminal("OPT_MCP=tavily\nKEYS=prompt\nIMAGE_BACKEND=none\nkeys_step\nreport\n")
        for name in KEY_ORDER:
            term.expect("    %s - " % name)
            term.answer_hidden("")
        term.expect("    TAVILY_API_KEY - ")
        term.answer_hidden(sentinel_for("TAVILY_API_KEY"))
        term.expect("enter any now?")
        self.assertIn("(FIRECRAWL_API_KEY BRAVE_API_KEY REPLICATE_API_TOKEN RUNWARE_API_KEY)", term.stderr)
        term.answer("y")
        for name, reply in (("FIRECRAWL_API_KEY", ""), ("BRAVE_API_KEY", sentinel_for("BRAVE_API_KEY")),
                            ("REPLICATE_API_TOKEN", ""), ("RUNWARE_API_KEY", "")):
            term.expect("    %s - " % name)
            term.answer_hidden(reply)
        self.assertEqual(term.finish(), 0, term.stderr)
        self.assertEqual(self.prompt_names(term), list(KEY_ORDER) + ["TAVILY_API_KEY", "FIRECRAWL_API_KEY", "BRAVE_API_KEY",
                                                                        "REPLICATE_API_TOKEN", "RUNWARE_API_KEY"])
        vals = self.values()
        self.assertEqual((vals["TAVILY_API_KEY"], vals["BRAVE_API_KEY"]), (sentinel_for("TAVILY_API_KEY"), sentinel_for("BRAVE_API_KEY")))
        self.assertEqual((vals["FIRECRAWL_API_KEY"], vals.get("REPLICATE_API_TOKEN", "")), ("", ""))
        self.assertNotIn(sentinel_for("TAVILY_API_KEY"), term.screen + term.stdout + term.stderr)

    def test_interrupt_restores_the_terminal_and_drops_the_values(self) -> None:
        """SEC-8: a signal during a hidden prompt puts the terminal back (echo on), forgets what was held and re-sends
        the signal, so the installer dies as it would without the key step. TERM is used: the code treats it like Ctrl-C
        and it is not ignored by a background job."""
        term = self.terminal("KEYS=prompt\nIMAGE_BACKEND=fal\nkeys_step\nreport\n")
        term.expect("    FAL_KEY - ")
        term.wait_for(lambda: not term.echo_on(), "the hidden prompt to switch echo off")
        term.proc.send_signal(signal.SIGTERM)
        rc = term.finish()
        self.assertIn(rc, (-signal.SIGTERM, 128 + signal.SIGTERM), term.stderr)
        self.assertTrue(term.echo_on(), "echo was left off after the interrupt")
        self.assertNotIn("@@RESULT", term.stdout)
        self.assertFalse(self.path.exists())

    def test_keys_env_never_prompts_even_on_a_terminal(self) -> None:
        """ci and headless (KEYS=env): a terminal on stdin changes nothing, no prompt is reachable."""
        term = self.terminal("KEYS=env\nIMAGE_BACKEND=fal\nkeys_step\nreport\n", FAL_KEY=SECRET_SENTINEL)
        self.assertEqual(term.finish(), 0, term.stderr)
        self.assertNotIn("Enter keeps it", term.stderr)
        self.assertEqual(parse_report(term.stdout)["RESULT"], "ok")
        self.assertEqual(self.values()["FAL_KEY"], SECRET_SENTINEL)

    def test_auto_with_yes_never_prompts_even_on_a_terminal(self) -> None:
        term = self.terminal("ASSUME_YES=1\nKEYS=auto\nIMAGE_BACKEND=fal\nkeys_step\nreport\n", FAL_KEY=SECRET_SENTINEL)
        self.assertEqual(term.finish(), 0, term.stderr)
        self.assertNotIn("Enter keeps it", term.stderr)
        self.assertIn("not interactive: no key prompt", term.stdout)
        self.assertEqual(self.values()["FAL_KEY"], SECRET_SENTINEL)

    def test_dry_run_on_a_terminal_lists_the_questions_in_order_and_asks_nothing(self) -> None:
        term = self.terminal("DRY_RUN=1\nKEYS=prompt\nIMAGE_BACKEND=fal\nkeys_step\nreport\n")
        self.assertEqual(term.finish(), 0, term.stderr)
        self.assertIn("would ask (hidden input) for: FAL_KEY EXA_API_KEY JINA_API_KEY SPIDER_API_KEY CONTEXT7_API_KEY", term.stdout)
        self.assertIn("would offer the optional keys: FIRECRAWL_API_KEY TAVILY_API_KEY BRAVE_API_KEY REPLICATE_API_TOKEN "
                      "RUNWARE_API_KEY", term.stdout)
        self.assertNotIn("Enter keeps it", term.stderr)
        self.assertEqual(parse_report(term.stdout)["RESULT"], "dry-run")
        self.assertEqual(self.sb.snapshot(), {})


if __name__ == "__main__":
    unittest.main()
