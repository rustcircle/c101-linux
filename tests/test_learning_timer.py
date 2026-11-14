"""The learning backstop: its systemd units, its installer (lib/learning.sh) and bin/c101-learn-backstop.

c101 records a session's statistics and applies learned maxTurns/effort values only in `stack learn run`, which only the
SessionEnd hook starts. The backstop timer re-runs `learn run --transcript P` for sessions that never got that far.
Here the real `bin/stack` is a stub that logs its calls (helpers.build_stub_c101), systemctl is an argv stub, and every
transcript and obs file lives in a throw-away HOME. Nothing needs systemd, c101 or the network.
Run on Linux: tests/run-tests.sh -k learning_timer
"""
from __future__ import annotations

import configparser
import json
import os
import re
import stat
import subprocess
import time
import unittest
from pathlib import Path
from typing import Dict, List

from helpers import (ROOT, STACK_EXPECTED_KEYS, STACK_OPTIONAL_KEYS, Sandbox, bash_lib, build_stub_c101, configure_stack_stub,
                     make_obs, make_transcript, needs_linux, rule, stack_calls, stub_calls)

UNITS = ROOT / "systemd" / "user"
SERVICE = UNITS / "c101-learn-backstop.service"
TIMER = UNITS / "c101-learn-backstop.timer"
SCRIPT = ROOT / "bin" / "c101-learn-backstop"
UNIT_MARKER = "# c101-linux managed unit"
FILE_MARKER = "# c101-linux managed file"
KEY_NAMES = ("FAL_KEY",) + STACK_EXPECTED_KEYS + STACK_OPTIONAL_KEYS

ALLOWED = {  # keys the overlay's units may use (same set as the Podman units in test_podman.py)
    "Unit": {"Description", "Documentation", "Requires", "After", "Wants", "ConditionPathExists"},
    "Service": {"Type", "ExecStart", "KillMode", "Environment", "TimeoutStartSec", "Nice", "Delegate"},
    "Timer": {"OnCalendar", "RandomizedDelaySec", "Persistent", "Unit"},
    "Install": {"WantedBy"},
}


def set_age(path: Path, minutes: float) -> None:
    """Give a file the mtime it would have had `minutes` ago."""
    stamp = time.time() - minutes * 60.0
    os.utime(path, (stamp, stamp))


def parse(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str  # type: ignore[assignment]
    cp.read_string(path.read_text(encoding="utf-8"))
    return cp


class LearnUnitFileTests(unittest.TestCase):
    def test_both_units_start_with_the_managed_unit_marker(self) -> None:
        for unit in (SERVICE, TIMER):
            self.assertEqual(unit.read_text(encoding="utf-8").splitlines()[0], UNIT_MARKER, unit.name)

    def test_timer_is_persistent_and_fires_every_30_minutes(self) -> None:
        timer = parse(TIMER)["Timer"]
        self.assertEqual(timer["Persistent"], "true", "a missed run (laptop asleep or off) must be made up at the next boot")
        self.assertRegex(timer["OnCalendar"], r"^\*:(0|00)/30$", "every 30 minutes, on the half hour")
        self.assertIn("RandomizedDelaySec", timer)
        self.assertEqual(parse(TIMER)["Install"]["WantedBy"], "timers.target")

    def test_timer_starts_the_service_unit_that_exists(self) -> None:
        unit = parse(TIMER)["Timer"]["Unit"]
        self.assertEqual(unit, SERVICE.name)
        self.assertTrue((UNITS / unit).exists())

    def test_service_is_a_oneshot_that_runs_the_installed_script_only_when_c101_exists(self) -> None:
        cp = parse(SERVICE)
        self.assertEqual(cp["Service"]["Type"], "oneshot")
        self.assertEqual(cp["Service"]["ExecStart"], "%h/.local/bin/c101-learn-backstop")
        self.assertEqual(cp["Unit"]["ConditionPathExists"], "%h/.claude/stack/bin/stack")
        self.assertNotIn("Install", cp, "the timer starts it; the service is not enabled by itself")
        self.assertIn("%h/.local/bin", cp["Service"]["Environment"], "user units get a minimal PATH: uv lives in ~/.local/bin")

    def test_units_use_only_known_sections_keys_and_specifiers(self) -> None:
        for unit in (SERVICE, TIMER):
            text = unit.read_text(encoding="utf-8")
            cp = parse(unit)
            for section in cp.sections():
                self.assertIn(section, ALLOWED, unit.name)
                for key in cp[section]:
                    self.assertIn(key, ALLOWED[section], "%s [%s] %s" % (unit.name, section, key))
            for spec in re.findall(r"%.", text):
                self.assertIn(spec, ("%h", "%u"), "%s uses specifier %s" % (unit.name, spec))
            for line in text.splitlines():
                self.assertFalse(line.startswith(" "), "%s: indented line" % unit.name)

    def test_script_is_executable_marked_and_names_no_key(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!"), "needs a shebang")
        self.assertEqual(text.splitlines()[1], FILE_MARKER, "the installer refuses to replace an unmarked file by this name")
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR, "bin/c101-learn-backstop must be executable")
        for path in (SCRIPT, SERVICE, TIMER):
            body = path.read_text(encoding="utf-8")
            for name in KEY_NAMES:
                self.assertNotIn(name, body, "%s mentions key variable %s (keys live in stack.env only)" % (path.name, name))


@needs_linux
class LearnTimerInstallTests(unittest.TestCase):
    """learn_timer_install (lib/learning.sh) with a stub systemctl and a user manager that is "reachable" on demand."""

    PRE = (
        '. "$C101L_ROOT/lib/learning.sh"\n'
        "detect_init() { INIT_SYSTEM=systemd; SYSTEMD_USER_OK=${FAKE_USER_MANAGER:-1}; }\n"
    )
    SHOW_NEXT = 'for _n in ${NEXT_STEPS[@]+"${NEXT_STEPS[@]}"}; do printf "NEXT=%s\\n" "$_n"; done\n'

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.systemctl = self.sb.argv_stub("systemctl")
        self.state = self.sb.base / "state.json"
        self.dest = self.sb.home / ".local" / "bin" / "c101-learn-backstop"
        self.unit_dir = self.sb.home / ".config" / "systemd" / "user"

    def install(self, call: str = "learn_timer_install 1", **extra: str) -> subprocess.CompletedProcess:
        return bash_lib(self.sb, self.PRE + call + "\n" + self.SHOW_NEXT, C101L_STATE_FILE=str(self.state), **extra)

    def state_json(self) -> Dict[str, object]:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def test_enabling_installs_the_script_both_units_and_enables_the_timer(self) -> None:
        res = self.install()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertTrue(self.dest.is_file())
        self.assertTrue(self.dest.stat().st_mode & stat.S_IXUSR, "the installed script must be executable")
        self.assertEqual(self.dest.read_text(), SCRIPT.read_text(), "a copy, so it keeps working when the checkout moves")
        for name in ("c101-learn-backstop.service", "c101-learn-backstop.timer"):
            text = (self.unit_dir / name).read_text()
            self.assertTrue(text.startswith(UNIT_MARKER), name)
        self.assertIn("Persistent=true", (self.unit_dir / "c101-learn-backstop.timer").read_text())
        argvs = self.systemctl.argvs()
        self.assertIn(["--user", "daemon-reload"], argvs)
        self.assertIn(["--user", "enable", "--now", "c101-learn-backstop.timer"], argvs)
        self.assertLess(argvs.index(["--user", "daemon-reload"]), argvs.index(["--user", "enable", "--now", "c101-learn-backstop.timer"]))
        state = self.state_json()
        self.assertIs(state["learn_timer"], True)
        self.assertIn(str(self.dest), state["files_installed"])  # type: ignore[operator]
        for name in ("c101-learn-backstop.service", "c101-learn-backstop.timer"):
            self.assertIn(str(self.unit_dir / name), state["systemd_units"])  # type: ignore[operator]
        self.sb.assert_no_secret_leak(res, trees=[self.sb.base])

    def test_enabling_twice_changes_nothing_the_second_time(self) -> None:
        self.assertEqual(self.install().returncode, 0)
        before = self.sb.snapshot()
        res = self.install()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("already current", res.stdout)
        self.assertEqual(self.sb.snapshot(), before)
        self.assertEqual(list(self.unit_dir.glob("*.bak-*")), [])
        state = self.state_json()
        self.assertEqual(len(state["systemd_units"]), len(set(state["systemd_units"])))  # type: ignore[arg-type]

    def test_not_enabled_changes_nothing_on_the_system(self) -> None:
        before = self.sb.snapshot()
        res = self.install("learn_timer_install 0")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.sb.snapshot(), before)
        self.assertFalse(self.systemctl.called())
        self.assertIs(self.state_json()["learn_timer"], False)

    def test_without_a_user_manager_it_warns_and_suggests_cron(self) -> None:
        before = self.sb.snapshot()
        res = self.install(FAKE_USER_MANAGER="0")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertRegex(res.stderr, r"(?m)^WARN: .*systemd user manager")
        self.assertRegex(res.stdout, r"(?m)^NEXT=.*cron.*c101-learn-backstop")
        self.assertFalse(self.systemctl.called())
        self.assertEqual(self.sb.snapshot(), before)
        self.assertIs(self.state_json()["learn_timer"], False)

    def test_the_cron_hint_names_gnu_findutils(self) -> None:
        """RV1 R2: the cron fallback is what Alpine/OpenRC hosts get, where find is BusyBox's (no -printf): the hint says
        the backstop needs GNU findutils."""
        res = self.install(FAKE_USER_MANAGER="0")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertRegex(res.stdout, r"(?m)^NEXT=.*cron.*c101-learn-backstop.*GNU find.*apk add findutils")

    def test_a_foreign_script_by_that_name_is_never_replaced(self) -> None:
        self.dest.parent.mkdir(parents=True)
        self.dest.write_text("#!/bin/sh\necho mine\n")
        res = self.install()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertRegex(res.stderr, r"(?m)^WARN: .*not ours")
        self.assertEqual(self.dest.read_text(), "#!/bin/sh\necho mine\n")
        self.assertFalse(self.unit_dir.exists(), "no units without the script they run")
        self.assertFalse(self.systemctl.called())
        self.assertIs(self.state_json()["learn_timer"], False)

    def test_dry_run_plans_the_enable_but_writes_and_runs_nothing(self) -> None:
        before = self.sb.snapshot()
        res = self.install("DRY_RUN=1\nlearn_timer_install 1")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("dry-run: systemctl --user enable --now c101-learn-backstop.timer", res.stdout)
        self.assertEqual(self.sb.snapshot(), before)
        self.assertFalse(self.systemctl.called())
        self.assertFalse(self.state.exists())


@needs_linux
class BackstopTests(unittest.TestCase):
    """bin/c101-learn-backstop against a stub c101: which transcripts get `learn run`, in which order, and what changes."""

    @classmethod
    def setUpClass(cls) -> None:
        probe = subprocess.run(["find", "/", "-maxdepth", "0", "-printf", "x"], capture_output=True, text=True)
        if probe.returncode != 0 or probe.stdout != "x":
            raise unittest.SkipTest("the backstop needs GNU find (-printf), as every systemd Linux has")

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.c101 = build_stub_c101(self.sb.base / "c101")
        self.sb.link_c101(self.c101)
        self.stamps = self.sb.home / ".local" / "state" / "c101-linux" / "learn-backstop"
        self.data = self.sb.home / ".claude" / "stack-data"

    def transcript(self, stem: str, age_minutes: float, project: str = "proj") -> Path:
        return make_transcript(self.sb.home, stem, project=project, age_minutes=age_minutes)

    def run_backstop(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return self.sb.run(["bash", str(SCRIPT), *args], **extra)

    def learned(self) -> List[str]:
        """The transcripts `stack learn run --transcript P` was called with, in call order."""
        out = []
        for argv in stack_calls(self.c101, "learn"):
            self.assertEqual(argv[:3], ["learn", "run", "--transcript"], argv)
            out.append(argv[3])
        return out

    def test_learns_transcripts_older_than_30_minutes_oldest_first(self) -> None:
        old = self.transcript("session-old", 300)
        mid = self.transcript("session-mid", 90)
        young = self.transcript("session-young", 10)  # may still belong to a live session
        res = self.run_backstop()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.learned(), [str(old), str(mid)])
        self.assertNotIn(str(young), self.learned())
        for rec in stub_calls(self.c101):
            self.assertEqual(rec["stdin"], "devnull", "learn run must not inherit the timer's stdin")
        self.assertIn("learned: session-old", res.stdout)
        self.assertIn("learned: session-mid", res.stdout)
        self.assertTrue((self.stamps / "session-old.done").exists())
        self.assertFalse((self.stamps / "session-young.done").exists())
        log = (self.data / "learn.log").read_text()
        self.assertIn(str(old), log)
        self.assertIn(str(mid), log)

    def test_the_minimum_age_is_adjustable_for_tests(self) -> None:
        ten = self.transcript("ten-minutes", 10)
        self.transcript("two-minutes", 2)
        res = self.run_backstop(C101_LEARN_BACKSTOP_MIN_AGE_MIN="5")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.learned(), [str(ten)])

    def test_skips_transcripts_whose_obs_file_is_newer(self) -> None:
        self.transcript("already-ingested", 120)
        make_obs(self.sb.home, "already-ingested", age_minutes=0)  # newer than its transcript: a learn run saw it
        stale = self.transcript("stale-obs", 120)
        make_obs(self.sb.home, "stale-obs", age_minutes=300)  # older than its transcript: the session went on afterwards
        fresh = self.transcript("never-ingested", 90)
        res = self.run_backstop()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.learned(), [str(stale), str(fresh)])
        self.assertRegex(res.stdout, r"already learned 1\b")

    def test_a_transcript_is_not_run_again_until_it_changes(self) -> None:
        t = self.transcript("once", 120)
        self.assertEqual(self.run_backstop().returncode, 0)
        res = self.run_backstop()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.learned(), [str(t)], "a session with nothing to ingest never gets an obs file: the stamp stops the re-run")
        self.assertRegex(res.stdout, r"already backstopped 1\b")
        # the session was resumed and written to: newer than the stamp, so it is learned again
        set_age(t, 40)
        self.assertEqual(self.run_backstop().returncode, 0)
        self.assertEqual(self.learned(), [str(t), str(t)])

    def test_a_run_is_capped_at_20_by_default(self) -> None:
        paths = [self.transcript("s%02d" % i, 1300 - 10 * i) for i in range(22)]  # s00 is the oldest
        res = self.run_backstop()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.learned(), [str(p) for p in paths[:20]])
        self.assertIn("left for the next run 2 (cap 20)", res.stdout)

    def test_the_cap_is_adjustable_and_the_rest_follows_on_later_starts(self) -> None:
        paths = [self.transcript("t%d" % i, 500 - 100 * i) for i in range(5)]  # t0 is the oldest
        res = self.run_backstop(C101_LEARN_BACKSTOP_MAX="2")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.learned(), [str(paths[0]), str(paths[1])])
        self.assertIn("left for the next run 3 (cap 2)", res.stdout)
        self.assertEqual(self.run_backstop(C101_LEARN_BACKSTOP_MAX="2").returncode, 0)
        self.assertEqual(self.learned(), [str(p) for p in paths[:4]])
        self.assertEqual(self.run_backstop(C101_LEARN_BACKSTOP_MAX="2").returncode, 0)
        self.assertEqual(self.learned(), [str(p) for p in paths])
        self.assertEqual(self.run_backstop(C101_LEARN_BACKSTOP_MAX="2").returncode, 0)
        self.assertEqual(len(self.learned()), 5, "everything is done: nothing runs again")

    def test_dry_run_lists_what_would_run_and_changes_nothing(self) -> None:
        old = self.transcript("dry-old", 200)
        young = self.transcript("dry-young", 5)
        before = self.sb.snapshot()
        res = self.run_backstop("--dry-run")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertRegex(res.stdout, r"would run: .*learn run --transcript %s" % re.escape(str(old)))
        self.assertNotIn(str(young), res.stdout)
        self.assertIn("would run 1", res.stdout)
        self.assertEqual(stub_calls(self.c101), [], "a dry run must not start bin/stack")
        self.assertEqual(self.sb.snapshot(), before)
        self.assertFalse((self.sb.home / ".local" / "state").exists(), "no stamp directory")
        self.assertFalse(self.data.exists(), "no stack-data (learn.log, obs) either")

    def test_a_failed_learn_run_is_reported_and_retried_on_the_next_start(self) -> None:
        t = self.transcript("flaky", 120)
        configure_stack_stub(self.c101, exit_codes={"learn run": 1})
        res = self.run_backstop()
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertRegex(res.stderr, r"learn run failed \(exit 1\)")
        self.assertRegex(res.stdout, r"failed 1\b")
        self.assertFalse((self.stamps / "flaky.done").exists(), "a failed run must not be remembered as done")
        configure_stack_stub(self.c101, exit_codes={"learn run": 0})
        res = self.run_backstop()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.learned(), [str(t), str(t)])
        self.assertTrue((self.stamps / "flaky.done").exists())

    def test_the_main_thread_comes_from_the_earlier_obs_file_then_the_environment_then_main(self) -> None:
        with_obs = self.transcript("with-obs", 200)
        obs = make_obs(self.sb.home, "with-obs")
        # c101 writes obs files with json.dump(indent=2, sort_keys=True); the backstop reads the "main" record's agent_type
        obs.write_text(json.dumps({"main": {"agent_type": "delegator", "turns": 3}}, indent=2, sort_keys=True) + "\n")
        set_age(obs, 400)  # older than the transcript (200 min), so the session is re-learned and keeps its attribution
        without_obs = self.transcript("no-obs", 100)
        res = self.run_backstop(CLAUDE_STACK_MAIN_AGENT="orchestrator")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        plain = self.transcript("plain", 60)
        res = self.run_backstop()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        main = {rec["argv"][3]: rec["env_values"].get("CLAUDE_STACK_MAIN_AGENT") for rec in stub_calls(self.c101) if rec["argv"][:2] == ["learn", "run"]}
        self.assertEqual(main[str(with_obs)], "delegator", "attributed like the in-session run, not relabelled as main")
        self.assertEqual(main[str(without_obs)], "orchestrator")
        self.assertEqual(main[str(plain)], "main")

    def test_transcripts_older_than_the_look_back_window_are_left_alone(self) -> None:
        ancient = self.transcript("ancient", 5 * 24 * 60)
        res = self.run_backstop()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.learned(), [])
        res = self.run_backstop(C101_LEARN_BACKSTOP_MAX_AGE_DAYS="7")
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.learned(), [str(ancient)])

    def test_without_c101_or_transcripts_it_is_a_quiet_no_op(self) -> None:
        res = self.run_backstop()  # c101 is linked, but there is no projects directory yet
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("nothing to do", res.stdout)
        (self.sb.home / ".claude" / "stack").unlink()
        res = self.run_backstop()
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("c101 is not installed", res.stdout)
        self.assertEqual(stub_calls(self.c101), [])

    def test_help_exits_zero_and_an_unknown_argument_exits_two(self) -> None:
        res = self.run_backstop("--help")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("c101-learn-backstop", res.stdout)
        self.assertIn("--dry-run", res.stdout)
        res = self.run_backstop("--bogus")
        self.assertEqual(res.returncode, 2)
        self.assertIn("unknown argument", res.stderr)


@needs_linux
class BackstopFindProbeTests(unittest.TestCase):
    """RV1 R2: bin/c101-learn-backstop needs GNU find (-printf). A find without -printf (BusyBox, Alpine's default) must
    stop the run loudly, naming findutils, instead of reporting "0 transcript(s)" on every start; and find's own error
    messages reach the journal. `find` here is an argv stub, so these run on any Linux."""

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.c101 = build_stub_c101(self.sb.base / "c101")
        self.sb.link_c101(self.c101)
        make_transcript(self.sb.home, "busybox-host", age_minutes=120)

    def run_backstop(self, *args: str) -> subprocess.CompletedProcess:
        return self.sb.run(["bash", str(SCRIPT), *args])

    def test_a_find_without_printf_fails_and_names_gnu_findutils(self) -> None:
        find = self.sb.argv_stub("find", rules=[rule("-printf", stderr="find: unrecognized: -printf\n", code=1)])
        for args in ((), ("--dry-run",)):
            res = self.run_backstop(*args)
            self.assertEqual(res.returncode, 1, "%s:\n%s\n%s" % (args, res.stdout, res.stderr))
            self.assertIn("unrecognized: -printf", res.stderr, "find's own message is shown, not discarded")
            self.assertIn("GNU findutils", res.stderr)
            self.assertIn("apk add findutils", res.stderr)
            self.assertNotIn("transcript(s)", res.stdout, "no silent '0 transcript(s)' summary")
        self.assertTrue(find.called())
        self.assertEqual(stub_calls(self.c101), [], "bin/stack is never started")
        self.assertFalse((self.sb.home / ".local" / "state").exists(), "no stamp directory either")

    def test_finds_own_errors_reach_stderr(self) -> None:
        self.sb.argv_stub("find", rules=[
            rule("-maxdepth", "0", "-printf", stdout="ok"),  # the up-front probe: this find has -printf
            rule("-mindepth", stderr="find: '/home/x/.claude/projects/locked': Permission denied\n", code=1),  # the scan
        ])
        res = self.run_backstop("--dry-run")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("Permission denied", res.stderr, "the scan's stderr is no longer discarded")
        self.assertIn("0 transcript(s)", res.stdout)


if __name__ == "__main__":
    unittest.main()
