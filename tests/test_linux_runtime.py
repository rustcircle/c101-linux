"""Linux-only runtime checks of the three doubts that could not be verified on the machine this overlay was written on.

These are NOT unit tests. They look at a REAL install on a REAL Linux user session, so they are opt-in:

    C101_LINUX_RUNTIME_TESTS=1 tests/run-tests.sh -k linux_runtime
    C101_LINUX_RUNTIME_TESTS=1 python3 -m unittest discover -s tests -p 'test_linux_runtime.py' -v

Without that variable, or on anything but Linux, every test is SKIPPED (never failed), so the normal suite stays hermetic.
Run them after `./install-linux.sh --profile standard` (or full, headless), and after logging out and back in once: the
systemd user manager reads environment.d only when it starts.

Doubts covered (plan decision D4; the write-ups belong in docs/VERIFICATION.md and docs/PITFALLS.md):
  1. environment.d reaching the stdio MCP servers: PLAYWRIGHT_MCP_BROWSER (and PATH) must be in
     `systemctl --user show-environment`, in a child the user manager starts (`systemd-run --user --pipe printenv`) and,
     when one is running, in the environment of a live @playwright/mcp process.
  2. The Chromium install command for @playwright/mcp (R1 decision): a chromium build under ~/.cache/ms-playwright (or
     $PLAYWRIGHT_BROWSERS_PATH), at the revision `playwright-core@<the version @playwright/mcp@latest pins>` wants, that starts.
  3. The learning backstop: the timer is enabled and persistent, the service unit loads, and a session whose SessionEnd never
     ran gets its `learn run` (a history row) from the installed script, with the real c101 and the real learn engine.

Environment knobs: C101_LINUX_EXPECT_PLAYWRIGHT_BROWSER (default chromium: the value the install should have put in the
user manager's environment), C101_LINUX_RUNTIME_QUIT_SESSION (the manual Desktop-quit check below).

MANUAL STEP, SessionEnd on a Desktop quit (cannot be automated: it needs the real Claude Desktop):
  1. Start Claude Desktop, open the Code tab, send one message in a new session, and note the session id (the file name of
     ~/.claude/projects/<project>/<id>.jsonl).
  2. Quit Desktop completely (the Quit menu entry, not just the window), without opening another session.
  3. Check that ~/.claude/stack-data/history.jsonl has NO line with that session id. If it has one, SessionEnd did run on a
     Desktop quit: record that in docs/VERIFICATION.md; the backstop then simply finds nothing to do.
  4. Wait 30 minutes, or age the transcript:  touch -d '40 minutes ago' ~/.claude/projects/<project>/<id>.jsonl
     then run:  systemctl --user start c101-learn-backstop.service
  5. Run:  C101_LINUX_RUNTIME_QUIT_SESSION=<id> C101_LINUX_RUNTIME_TESTS=1 tests/run-tests.sh -k desktop_quit
     It passes when the history line exists AND the backstop's stamp for the session exists (the backstop, not SessionEnd,
     recorded it); it reports a skip with the reason when the stamp is absent because SessionEnd ran after all.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from helpers import LINUX

OPT_IN = os.environ.get("C101_LINUX_RUNTIME_TESTS") == "1"
needs_runtime = unittest.skipUnless(
    LINUX and OPT_IN,
    "Linux-only runtime checks of a real install: set C101_LINUX_RUNTIME_TESTS=1 on Linux (see this file's docstring)")

UNIT_MARKER = "# c101-linux managed unit"
TIMER = "c101-learn-backstop.timer"
SERVICE = "c101-learn-backstop.service"
SYNTHETIC_MAIN_AGENT = "c101-runtime-test-main"  # no agent file by this name exists, so `learn run` can never rewrite one


def home() -> Path:
    return Path(os.path.expanduser("~"))


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or home() / ".config")


def run(argv: List[str], timeout: float = 60, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)


def systemctl_user(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", *args], timeout=timeout)


def parse_environment(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        name, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            out[name] = value
    return out


def expected_browser() -> str:
    return os.environ.get("C101_LINUX_EXPECT_PLAYWRIGHT_BROWSER", "chromium")


def browsers_root() -> Optional[Path]:
    """Where Playwright keeps its browsers on Linux: $PLAYWRIGHT_BROWSERS_PATH, else $XDG_CACHE_HOME or ~/.cache + ms-playwright.
    None when PLAYWRIGHT_BROWSERS_PATH=0 (browsers inside node_modules: not what this overlay installs)."""
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if custom == "0":
        return None
    if custom:
        return Path(custom).expanduser()
    return Path(os.environ.get("XDG_CACHE_HOME") or home() / ".cache") / "ms-playwright"


def chromium_builds(root: Path) -> List[Path]:
    """chromium-<revision> directories (not chromium_headless_shell-<revision>: the install uses --no-shell)."""
    return sorted(p for p in root.glob("chromium-[0-9]*") if p.is_dir())


def chromium_executables(build: Path) -> List[Path]:
    """chrome-linux/chrome (older builds) or chrome-linux64/chrome (Chrome-for-Testing builds)."""
    return sorted(p for p in build.glob("chrome-linux*/chrome") if p.is_file())


class _UserManager:
    """Mixin: skip the whole class when there is no systemd user manager to ask."""

    manager_env: Dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("systemctl") is None:
            raise unittest.SkipTest("systemctl is not installed")
        res = systemctl_user("show-environment")
        if res.returncode != 0:
            raise unittest.SkipTest("no reachable systemd user manager (ssh without a login session, su, a container): %s" % res.stderr.strip())
        cls.manager_env = parse_environment(res.stdout)


@needs_runtime
class UserManagerEnvironmentTests(_UserManager, unittest.TestCase):
    """Doubt 1: does what the overlay writes to environment.d reach the processes Claude Desktop and its MCP servers run in?"""

    RELOGIN = ("environment.d is read when the user manager starts, so after installing log out and back in (or reboot) "
               "and run this again. If that was done, the doubt is real: record it in docs/VERIFICATION.md.")

    def setUp(self) -> None:
        self.envd = config_home() / "environment.d" / "60-c101-linux.conf"
        if not self.envd.is_file():
            self.skipTest("this install wrote no %s (environment.d was not wanted or is unsupported here)" % self.envd)

    def test_user_manager_environment_has_the_playwright_browser(self) -> None:
        self.assertEqual(self.manager_env.get("PLAYWRIGHT_MCP_BROWSER"), expected_browser(),
                         "`systemctl --user show-environment` lacks the value. " + self.RELOGIN)

    def test_user_manager_path_has_the_local_bin_directory_of_the_environment_d_file(self) -> None:
        # the file says PATH=$HOME/.local/bin:...:$PATH, so this also proves $HOME and $PATH expand in environment.d
        entries = self.manager_env.get("PATH", "").split(":")
        self.assertIn(str(home() / ".local" / "bin"), entries, "PATH of the user manager is %r. %s" % (entries, self.RELOGIN))

    def test_a_child_of_the_user_manager_sees_the_playwright_browser(self) -> None:
        systemd_run, printenv = shutil.which("systemd-run"), shutil.which("printenv")
        if not systemd_run or not printenv:
            self.skipTest("systemd-run or printenv not found")
        res = run([systemd_run, "--user", "--wait", "--pipe", "--collect", printenv, "PLAYWRIGHT_MCP_BROWSER"], timeout=90)
        self.assertIn(expected_browser(), res.stdout.splitlines(),
                      "a service started by the user manager does not have PLAYWRIGHT_MCP_BROWSER (exit %d, stderr %r). %s"
                      % (res.returncode, res.stderr.strip()[-300:], self.RELOGIN))

    def test_a_running_playwright_mcp_server_has_the_browser_setting_in_its_environment(self) -> None:
        if shutil.which("pgrep") is None:
            self.skipTest("pgrep not found")
        pids = [int(p) for p in run(["pgrep", "-u", str(os.getuid()), "-f", "@playwright/mcp"]).stdout.split() if int(p) != os.getpid()]
        if not pids:
            self.skipTest("no @playwright/mcp process is running: start Claude Desktop and connect the Playwright server (/mcp), then re-run")
        seen = {}
        for pid in pids:
            try:
                raw = Path("/proc/%d/environ" % pid).read_bytes()
            except OSError:
                continue
            env = parse_environment(raw.decode("utf-8", "replace").replace("\0", "\n"))
            seen[pid] = env.get("PLAYWRIGHT_MCP_BROWSER")
        if not seen:
            self.skipTest("could not read the environment of pids %s" % pids)
        self.assertEqual(set(seen.values()), {expected_browser()},
                         "the Playwright MCP process does not run with the browser setting (pid -> value: %r). Desktop imports its "
                         "environment once, at start: quit it completely and start it again after a re-login." % seen)


@needs_runtime
class PlaywrightChromiumTests(unittest.TestCase):
    """Doubt 2: the Chromium install command of the R1 decision (npx -y playwright-core@<pin> install --no-shell chromium)."""

    FIX = "run ./install-linux.sh --playwright-browser (user level, no root)"

    def setUp(self) -> None:
        self.root = browsers_root()
        if self.root is None:
            self.skipTest("PLAYWRIGHT_BROWSERS_PATH=0: browsers live in node_modules, not where the overlay installs them")

    def test_a_chromium_build_exists_under_the_playwright_browsers_directory(self) -> None:
        builds = chromium_builds(self.root)
        self.assertTrue(builds, "no chromium-<revision> directory under %s: %s" % (self.root, self.FIX))

    def test_the_chromium_build_has_an_executable_chrome(self) -> None:
        builds = chromium_builds(self.root)
        if not builds:
            self.skipTest("no chromium build under %s (see the previous test)" % self.root)
        exes = [exe for b in builds for exe in chromium_executables(b)]
        self.assertTrue(exes, "no chrome-linux*/chrome inside %s: the download is incomplete, %s" % (builds, self.FIX))
        self.assertTrue(any(os.access(exe, os.X_OK) for exe in exes), "chrome is not executable: %s" % exes)

    def test_the_build_is_the_revision_the_pinned_playwright_core_wants(self) -> None:
        npm, npx = shutil.which("npm"), shutil.which("npx")
        if not npm or not npx:
            self.skipTest("npm and npx are needed to ask which revision the pinned playwright-core wants")
        pin = run([npm, "view", "@playwright/mcp@latest", "dependencies.playwright-core"], timeout=120)
        version = pin.stdout.strip().splitlines()[-1].strip().strip("'\"") if pin.stdout.strip() else ""
        if pin.returncode != 0 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]*", version):
            self.skipTest("npm view could not read the playwright-core pin (offline?): rc=%d %r" % (pin.returncode, pin.stderr.strip()[-200:]))
        # --dry-run prints where the install would put each browser and installs nothing
        dry = run([npx, "-y", "playwright-core@%s" % version, "install", "--dry-run", "--no-shell", "chromium"], timeout=300)
        if dry.returncode != 0:
            self.skipTest("playwright-core@%s install --dry-run failed (offline?): %r" % (version, dry.stderr.strip()[-200:]))
        locations = re.findall(r"(?ms)^browser:\s+chromium\s+version\b[^\n]*\n(?:[^\n]*\n)*?\s*Install location:\s*(\S+)", dry.stdout)
        if not locations:
            self.skipTest("could not read `Install location:` for chromium from the dry-run output (format changed?):\n%s" % dry.stdout[-600:])
        wanted = Path(locations[0])
        self.assertTrue(wanted.is_dir(),
                        "@playwright/mcp@latest pins playwright-core@%s, which wants %s, but that directory is missing (installed: %s). "
                        "The MCP would fail with \"Executable doesn't exist\": %s" % (version, wanted, chromium_builds(self.root), self.FIX))

    def test_the_chrome_binary_starts(self) -> None:
        exes = [exe for b in chromium_builds(self.root) for exe in chromium_executables(b)]
        if not exes:
            self.skipTest("no chromium build under %s" % self.root)
        res = run([str(exes[-1]), "--version"], timeout=60)
        if res.returncode != 0 and "error while loading shared libraries" in res.stderr:
            self.skipTest("Chromium's system libraries are missing: that is the root-gated step, ./install-linux.sh --playwright-deps (%s)"
                          % res.stderr.strip()[-160:])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Chrom", res.stdout)


@needs_runtime
class LearningBackstopRuntimeTests(_UserManager, unittest.TestCase):
    """Doubt 3, the part a machine can check: the timer is live and the installed backstop teaches the real learn engine."""

    def setUp(self) -> None:
        self.unit_dir = config_home() / "systemd" / "user"
        if not (self.unit_dir / TIMER).is_file():
            self.skipTest("the learning backstop timer is not installed (LEARN_TIMER=0 in this profile): re-install with --learn-timer")

    def test_both_units_are_ours_and_systemd_loads_them(self) -> None:
        for name in (SERVICE, TIMER):
            first = (self.unit_dir / name).read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first, UNIT_MARKER, name)
            res = systemctl_user("show", "--property=LoadState", name)
            self.assertEqual(res.stdout.strip(), "LoadState=loaded", "%s: %s" % (name, res.stderr.strip()))

    def test_the_timer_is_enabled_active_and_persistent(self) -> None:
        enabled = systemctl_user("is-enabled", TIMER)
        self.assertEqual((enabled.returncode, enabled.stdout.strip()), (0, "enabled"), enabled.stderr)
        active = systemctl_user("is-active", TIMER)
        self.assertEqual((active.returncode, active.stdout.strip()), (0, "active"), "the timer is enabled but not running: " + active.stderr)
        persistent = systemctl_user("show", "--property=Persistent", TIMER)
        self.assertEqual(persistent.stdout.strip(), "Persistent=yes")

    def test_the_installed_script_runs_in_dry_run_mode(self) -> None:
        script = home() / ".local" / "bin" / "c101-learn-backstop"
        self.assertTrue(os.access(script, os.X_OK), "%s is missing or not executable" % script)
        res = run([str(script), "--dry-run"], timeout=120)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("c101-learn-backstop:", res.stdout)

    def test_a_session_whose_sessionend_never_ran_gets_a_history_row(self) -> None:
        script = home() / ".local" / "bin" / "c101-learn-backstop"
        stack = home() / ".claude" / "stack" / "bin" / "stack"
        if not os.access(script, os.X_OK) or not os.access(stack, os.X_OK):
            self.skipTest("the installed backstop or c101's bin/stack is missing")
        session = str(uuid.uuid4())
        with tempfile.TemporaryDirectory(prefix="c101l-runtime-") as tmp:
            base = Path(tmp)
            projects, data, state = base / "projects", base / "stack-data", base / "state"
            transcript = projects / "c101-runtime-test" / (session + ".jsonl")
            transcript.parent.mkdir(parents=True)
            rows = [
                {"type": "user", "sessionId": session, "timestamp": "2026-01-01T10:00:00.000Z",
                 "message": {"role": "user", "content": "hello"}},
                {"type": "assistant", "sessionId": session, "timestamp": "2026-01-01T10:00:05.000Z", "requestId": "req_runtime_test",
                 "message": {"id": "msg_runtime_test", "model": "c101-runtime-test", "stop_reason": "end_turn",
                             "usage": {"input_tokens": 12, "output_tokens": 7}, "content": [{"type": "text", "text": "hi"}]}},
            ]
            transcript.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            old = time.time() - 2 * 3600  # a session that ended two hours ago and never ran SessionEnd
            os.utime(transcript, (old, old))
            env = dict(os.environ)
            env.update(CLAUDE_PROJECTS_ROOT=str(projects), CLAUDE_STACK_DATA=str(data), XDG_STATE_HOME=str(state),
                       CLAUDE_STACK_MAIN_AGENT=SYNTHETIC_MAIN_AGENT)
            env.pop("CLAUDE_PROJECT_DIR", None)  # keep c101 from looking at any project's agents
            res = run([str(script)], timeout=600, env=env)
            log = (data / "learn.log").read_text(errors="replace")[-1500:] if (data / "learn.log").exists() else "(no learn.log)"
            self.assertEqual(res.returncode, 0, "backstop failed: %s%s\nlearn.log tail: %s" % (res.stdout, res.stderr, log))
            self.assertIn("learned: %s" % session, res.stdout, log)
            self.assertTrue((data / "obs" / (session + ".json")).is_file(), "learn run wrote no obs file; learn.log: " + log)
            history = data / "history.jsonl"
            self.assertTrue(history.is_file(), "no history.jsonl; learn.log: " + log)
            rows_for = [r for r in (json.loads(ln) for ln in history.read_text(encoding="utf-8").splitlines() if ln.strip())
                        if r.get("session_id") == session]
            self.assertEqual(len(rows_for), 1, "exactly one history row per session")
            self.assertEqual(rows_for[0].get("project"), "c101-runtime-test")
            self.assertIn(SYNTHETIC_MAIN_AGENT, rows_for[0].get("agents", {}), "the main thread is attributed like an in-session run")
            self.assertTrue((state / "c101-linux" / "learn-backstop" / (session + ".done")).is_file(), "the backstop remembers it ran")
            # a second start finds nothing to do and does not add a second row
            again = run([str(script)], timeout=600, env=env)
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertNotIn("learned: %s" % session, again.stdout)
            lines = [ln for ln in history.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1)


@needs_runtime
class DesktopQuitManualCheck(unittest.TestCase):
    """The semi-automatic half of the Desktop-quit doubt: see MANUAL STEP in the module docstring. Skipped unless
    C101_LINUX_RUNTIME_QUIT_SESSION names the session id that was left behind by quitting Desktop."""

    def test_desktop_quit_leaves_a_session_the_backstop_records(self) -> None:
        session = os.environ.get("C101_LINUX_RUNTIME_QUIT_SESSION", "").strip()
        if not session:
            self.skipTest("manual step: quit Claude Desktop in a session, wait 30 minutes, then set C101_LINUX_RUNTIME_QUIT_SESSION=<id> "
                          "(see the module docstring)")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", session):
            self.fail("C101_LINUX_RUNTIME_QUIT_SESSION must be a session id (the transcript's file name without .jsonl)")
        root = Path(os.environ.get("CLAUDE_PROJECTS_ROOT") or home() / ".claude" / "projects")
        transcripts = list(root.glob("*/%s.jsonl" % session))
        self.assertTrue(transcripts, "no transcript %s.jsonl under %s" % (session, root))
        data = Path(os.environ.get("CLAUDE_STACK_DATA") or home() / ".claude" / "stack-data")
        history = data / "history.jsonl"
        rows = []
        if history.is_file():
            rows = [json.loads(ln) for ln in history.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertTrue([r for r in rows if r.get("session_id") == session],
                        "no history row for %s: neither SessionEnd nor the backstop recorded it. Check "
                        "`journalctl --user -u c101-learn-backstop` and %s/learn.log" % (session, data))
        stamp = Path(os.environ.get("XDG_STATE_HOME") or home() / ".local" / "state") / "c101-linux" / "learn-backstop" / (session + ".done")
        if not stamp.is_file():
            self.skipTest("the history row exists but the backstop never ran on this session: SessionEnd DID run when Desktop quit "
                          "(or another session's SessionEnd caught up). Record that outcome in docs/VERIFICATION.md.")


if __name__ == "__main__":
    unittest.main()
