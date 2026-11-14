"""uninstall-linux.sh: removes only what the overlay added (links into c101, stack hooks, PATH block, Desktop entries,
units, installed commands) and leaves user data alone; --dry-run changes nothing."""
from __future__ import annotations

import json
import os
import shlex
import sys
import unittest
from typing import Dict, Tuple

from helpers import ROOT, SECRET_SENTINEL, Sandbox, bash_lib, file_mode, make_stub_c101, needs_linux, rule, write_stack_env


@needs_linux
class UninstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.state = self.sb.base / "state.json"
        self.stub = make_stub_c101(self.sb.base / "c101", with_venv=False)
        self.src = str(self.stub.resolve())
        claude = self.sb.home / ".claude"
        (claude / "agents" / "stack" / "core").mkdir(parents=True)
        (claude / "skills").mkdir()
        (claude / "workflows").mkdir()
        (self.stub / ".claude" / "agents").mkdir(parents=True)
        (self.stub / ".claude" / "agents" / "a.md").write_text("agent")
        (self.stub / ".claude" / "skills" / "s").mkdir(parents=True)
        (self.stub / ".claude" / "workflows").mkdir()
        (self.stub / ".claude" / "workflows" / "w.js").write_text("w")
        os.symlink(self.src, claude / "stack")
        os.symlink(self.src + "/.claude/agents/a.md", claude / "agents" / "stack" / "core" / "a.md")
        os.symlink(self.src + "/.claude/skills/s", claude / "skills" / "s")
        os.symlink(self.src + "/.claude/workflows/w.js", claude / "workflows" / "w.js")
        # things that are the user's own and must survive
        (claude / "skills" / "mine").mkdir()
        os.symlink("/etc/hostname", claude / "skills" / "foreign-link")
        (claude / "stack.env").write_text("SECRET=1\n")
        (claude / "stack-data").mkdir()
        (claude / "settings.json").write_text(json.dumps({
            "agent": "delegator",
            "env": {"UV_PYTHON": "3.13"},
            "hooks": {"SessionStart": [
                {"hooks": [{"type": "command", "command": '"$HOME"/.claude/stack/bin/stack hook session-start'}]},
                {"hooks": [{"type": "command", "command": "echo mine"}]}]},
        }))
        (self.sb.home / ".profile").write_text("export KEEP=1\n")
        env = {"C101L_STATE_FILE": str(self.state)}
        res = bash_lib(self.sb, 'LOGIN_SHELL=bash\npath_apply 0\nstate_set c101_source "$(json_str "%s")"\n'
                       'state_set c101_flags \'"--user --main-agent"\'\nstate_set profile \'"standard"\'' % self.src, **env)
        self.assertEqual(res.returncode, 0, res.stderr)

    def uninstall(self, *args: str, **env: str):
        extra = ["--allow-root"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
        env.setdefault("C101L_STATE_FILE", str(self.state))
        return self.sb.run([str(ROOT / "uninstall-linux.sh"), *extra, *args], **env)

    def test_dry_run_changes_nothing(self) -> None:
        before = self.sb.snapshot()
        state_before = self.state.read_text()
        res = self.uninstall("--dry-run")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.sb.snapshot(), before)
        self.assertEqual(self.state.read_text(), state_before)
        self.assertIn("dry run complete", res.stdout)

    def test_removes_only_what_it_added(self) -> None:
        stale = self.state.parent / "pending"
        stale.mkdir()
        (stale / "delegate.conf").write_text("old")
        res = self.uninstall()
        self.assertFalse(stale.exists(), "leftover staged files from earlier deferred root commands are removed")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        claude = self.sb.home / ".claude"
        self.assertFalse((claude / "stack").exists() or (claude / "stack").is_symlink())
        self.assertFalse((claude / "agents" / "stack" / "core" / "a.md").is_symlink())
        self.assertFalse((claude / "skills" / "s").is_symlink())
        self.assertFalse((claude / "workflows" / "w.js").is_symlink())
        self.assertTrue((claude / "skills" / "mine").is_dir())
        self.assertTrue((claude / "skills" / "foreign-link").is_symlink(), "links that do not point into c101 stay")
        self.assertEqual((claude / "stack.env").read_text(), "SECRET=1\n")
        self.assertTrue((claude / "stack-data").is_dir())
        # hooks: the stack's go, the user's stay; a backup exists
        data = json.loads((claude / "settings.json").read_text())
        cmds = [h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertEqual(cmds, ["echo mine"])
        self.assertTrue(list(claude.glob("settings.json.bak-*")))
        # PATH block gone, the user's line kept
        self.assertEqual((self.sb.home / ".profile").read_text().strip(), "export KEEP=1")
        self.assertFalse(self.state.exists(), "the state file goes last")
        # c101's own reversal flags ran through install.sh
        calls = (self.stub / "calls.log").read_text()
        self.assertIn("--no-main-agent", calls)
        self.assertIn("--no-python-default", calls)

    def test_env_keys_mcp_servers_and_system_files(self) -> None:
        claude = self.sb.home / ".claude"
        settings = json.loads((claude / "settings.json").read_text())
        settings["env"] = {"UV_PYTHON": "3.13", "MAX_MCP_OUTPUT_TOKENS": "40000", "MINE": "1"}
        (claude / "settings.json").write_text(json.dumps(settings))
        log = self.sb.base / "claude.log"
        self.sb.fake_command("claude", 'echo "$@" >> "%s"' % log)
        dropin = self.sb.base / "delegate.conf"
        dropin.write_text("[Service]\nDelegate=cpu\n")
        managed = self.sb.base / "managed-settings.json"
        managed.write_text(json.dumps({"availableModels": ["admin"], "enforceAvailableModels": True, "other": 1}))
        setup = (
            'state_set settings_env_before \'{"MINE": "1"}\'\n'
            'state_append c101_mcp_names \'"tavily"\'\nstate_append c101_mcp_names \'"markitdown"\'\n'
            'state_append system_files "$(json_str "%s")"\n'
            'state_set managed_settings "$(json_str "%s")"\n'
            'state_set managed_settings_preexisting \'["availableModels"]\'\n' % (dropin, managed))
        res = bash_lib(self.sb, setup, C101L_STATE_FILE=str(self.state))
        self.assertEqual(res.returncode, 0, res.stderr)
        res = self.uninstall("--no-sudo")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        env = json.loads((claude / "settings.json").read_text())["env"]
        self.assertNotIn("MAX_MCP_OUTPUT_TOKENS", env, "a key c101 added and still holds is removed")
        self.assertEqual(env["MINE"], "1")
        self.assertEqual(env["UV_PYTHON"], "3.13", "the stub c101 flag did not remove it; not the overlay's job")
        calls = log.read_text()
        self.assertIn("mcp remove --scope user tavily", calls)
        self.assertIn("mcp remove --scope user markitdown", calls)
        script = (self.state.parent / "pending" / "pending-root-commands.sh").read_text()
        self.assertIn(str(dropin), script)
        self.assertIn("availableModelsMatch", script)
        self.assertNotIn(" availableModels ", script, "keys an administrator had before c101 stay")
        self.assertIn("enforceAvailableModels", script)

    def test_settings_env_values_never_reach_a_child_command_line(self) -> None:
        """R3: the uninstaller hands the helper only key NAMES (stdin, never argv). A state file an older version wrote, with the
        snapshot as an object holding values, still works: its keys count, and its values reach no command line."""
        token = "ghp_SENTINEL_settings_env_value_0123456789"
        claude = self.sb.home / ".claude"
        settings = json.loads((claude / "settings.json").read_text())
        settings["env"] = {"MAX_MCP_OUTPUT_TOKENS": "40000", "MY_TOKEN": token}
        (claude / "settings.json").write_text(json.dumps(settings))
        argv_log = self.sb.base / "python-argv.log"
        wrapper = self.sb.base / "py-wrapper"
        wrapper.write_text("#!/bin/sh\nprintf '%%s\\n' \"$@\" >> %s\nexec %s \"$@\"\n" % (shlex.quote(str(argv_log)), shlex.quote(sys.executable)))
        wrapper.chmod(0o755)
        res = bash_lib(self.sb, 'state_set settings_env_before \'{"MY_TOKEN": "%s"}\'' % token, C101L_STATE_FILE=str(self.state))
        self.assertEqual(res.returncode, 0, res.stderr)
        res = self.uninstall(C101L_PY=str(wrapper))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        env = json.loads((claude / "settings.json").read_text())["env"]
        self.assertEqual(env, {"MY_TOKEN": token}, "c101's key goes, the user's stays")
        self.assertIn("strip-env", argv_log.read_text(encoding="utf-8"), "the helper really ran through the wrapper")
        self.assertNotIn(token, argv_log.read_text(encoding="utf-8"), "no value on any command line")
        self.assertNotIn(token, res.stdout + res.stderr)

    def test_second_run_is_harmless(self) -> None:
        self.assertEqual(self.uninstall().returncode, 0)
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_keep_c101_leaves_links_and_hooks(self) -> None:
        res = self.uninstall("--keep-c101")
        self.assertEqual(res.returncode, 0, res.stderr)
        claude = self.sb.home / ".claude"
        self.assertTrue((claude / "stack").is_symlink())
        self.assertIn("stack hook", (claude / "settings.json").read_text())
        self.assertNotIn(">>> c101-linux", (self.sb.home / ".profile").read_text())

    def test_installed_commands_and_units_are_removed_only_when_marked(self) -> None:
        bin_dir = self.sb.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        ours = bin_dir / "claude-stack"
        ours.write_text("#!/bin/sh\n# c101-linux managed file\nexec x\n")
        theirs = bin_dir / "stack-sdk"
        theirs.write_text("#!/bin/sh\necho mine\n")
        unit_dir = self.sb.home / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        unit = unit_dir / "c101-podman-prune.service"
        unit.write_text("# c101-linux managed unit\n[Service]\nType=oneshot\n")
        bash_lib(self.sb, 'state_append files_installed "$(json_str "%s")"\nstate_append files_installed "$(json_str "%s")"\n'
                 'state_append systemd_units "$(json_str "%s")"' % (ours, theirs, unit), C101L_STATE_FILE=str(self.state))
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse(ours.exists())
        self.assertTrue(theirs.exists())
        self.assertFalse(unit.exists())

    def test_without_state_the_marker_blocks_are_still_found(self) -> None:
        self.state.unlink()
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn(">>> c101-linux", (self.sb.home / ".profile").read_text())
        self.assertFalse((self.sb.home / ".claude" / "stack").is_symlink())


@needs_linux
class UninstallParityTests(unittest.TestCase):
    """What the parity change adds to HOME (the learning backstop timer, the fal-runner agent and its whole-session config,
    the Desktop entry upgrade) goes away exactly as the state file recorded it; the keys file and its backups never move.
    The installed state is made by the overlay's own functions (learn_timer_install, fal_mcp_install) in the default state
    location, so the closing removal of the state directory is part of what is checked."""

    PRE = (
        '. "$C101L_ROOT/lib/learning.sh"\n'
        '. "$C101L_ROOT/lib/falmcp.sh"\n'
        "detect_init() { INIT_SYSTEM=systemd; SYSTEMD_USER_OK=1; }\n"
    )

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.systemctl = self.sb.argv_stub("systemctl")
        self.stub = make_stub_c101(self.sb.base / "c101", with_venv=False)
        self.src = str(self.stub.resolve())
        self.claude = self.sb.home / ".claude"
        self.claude.mkdir(parents=True)
        os.symlink(self.src, self.claude / "stack")
        self.stack_env = write_stack_env(self.sb.home, {"FAL_KEY": SECRET_SENTINEL, "EXA_API_KEY": "", "IMAGE_GEN_KEY": SECRET_SENTINEL})
        for stamp in ("20260102T030405Z", "20260102T030405Z-2"):  # backups the installer's merge left; never deleted by anyone
            backup = self.claude / ("stack.env.bak-" + stamp)
            backup.write_text("FAL_KEY=an-older-value\n", encoding="utf-8")
            backup.chmod(0o600)
        self.state_dir = self.sb.home / ".local" / "state" / "c101-linux"
        self.state_file = self.state_dir / "state.json"
        self.agent = self.claude / "agents" / "fal-runner.md"
        self.config = self.sb.home / ".config" / "c101" / "fal-mcp.json"
        self.script = self.sb.home / ".local" / "bin" / "c101-learn-backstop"
        self.units = [self.sb.home / ".config" / "systemd" / "user" / n for n in ("c101-learn-backstop.service", "c101-learn-backstop.timer")]
        self.stamps = self.state_dir / "learn-backstop"
        self.keys_before = self.keys_snapshot()

    # --- fixtures -----------------------------------------------------------------------------------------------

    def keys_snapshot(self) -> Dict[str, Tuple[bytes, int]]:
        files = [self.stack_env] + sorted(self.claude.glob("stack.env.bak-*"))
        return {p.name: (p.read_bytes(), file_mode(p)) for p in files}

    def install_features(self, flags: str = "--user") -> None:
        """An install as the overlay records it: c101 source and flags, the timer (script, two units, stamps), the fal agent."""
        snippet = (self.PRE + 'state_set c101_source "$(json_str "%s")"\nstate_set c101_flags \'"%s"\'\nstate_set profile \'"standard"\'\n'
                   "learn_timer_install 1\nfal_mcp_install 1\n" % (self.src, flags))
        res = bash_lib(self.sb, snippet)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.stamps.mkdir(parents=True)
        (self.stamps / "a1b2c3.stamp").write_text("1\n", encoding="utf-8")
        self.assertTrue(self.script.is_file() and self.agent.is_symlink() and self.config.is_file())
        self.assertTrue(all(u.is_file() for u in self.units))

    def write_state(self, snippet: str) -> None:
        res = bash_lib(self.sb, snippet)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def uninstall(self, *args: str, **env: str):
        extra = ["--allow-root"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
        return self.sb.run([str(ROOT / "uninstall-linux.sh"), *extra, "--no-sudo", *args], **env)

    def assert_keys_untouched(self) -> None:
        self.assertEqual(self.keys_snapshot(), self.keys_before, "stack.env and its .bak-* backups keep their bytes and their mode")

    # --- tests --------------------------------------------------------------------------------------------------

    def test_a_dry_run_removes_nothing_and_never_touches_the_key_file(self) -> None:
        self.install_features()
        before = self.sb.snapshot()
        res = self.uninstall("--dry-run")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.sb.snapshot(), before)
        self.assertIn("dry run complete", res.stdout)
        self.assertEqual([a for a in self.systemctl.argvs() if "disable" in a], [], "a dry run disables no timer")
        self.assertFalse((self.stub / "calls.log").exists())
        self.assert_keys_untouched()

    def test_the_timer_the_fal_agent_and_the_state_directory_are_removed_as_recorded(self) -> None:
        self.install_features()
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse(self.script.exists(), "the backstop script (files_installed, marked)")
        for unit in self.units:
            self.assertFalse(unit.exists(), unit.name)
        self.assertFalse(self.stamps.exists(), "the stamps live in the state directory and go before it does")
        self.assertFalse(self.agent.is_symlink() or self.agent.exists(), "the fal-runner agent link")
        self.assertFalse(self.config.exists(), "the whole-session config")
        self.assertFalse(self.config.parent.exists(), "~/.config/c101 is removed once empty")
        self.assertFalse(self.state_dir.exists(), "the state file goes last, with its directory")
        self.assertFalse((self.claude / "stack").is_symlink(), "c101's own links are still undone")
        if os.path.isdir("/run/systemd/system"):  # without a systemd there is no user manager to ask
            self.assertIn(["--user", "disable", "--now", "c101-learn-backstop.timer"], self.systemctl.argvs())
        self.assert_keys_untouched()
        self.sb.assert_no_secret_leak(res)

    def test_without_a_state_file_the_backstop_units_and_script_still_go(self) -> None:
        """R5: no state file (lost, or removed by hand) must not leave the learning backstop behind: its timer, service and
        script are looked for at their default paths and removed while they carry the overlay's marker, the way the fal-runner
        agent and the config already are."""
        self.install_features()
        self.state_file.unlink()
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse(self.script.exists(), "the backstop script, found at its default path")
        for unit in self.units:
            self.assertFalse(unit.exists(), unit.name)
        self.assertFalse(self.stamps.exists())
        self.assertFalse(self.agent.is_symlink() or self.agent.exists(), "the fal-runner agent (its own no-state fallback)")
        if os.path.isdir("/run/systemd/system"):
            self.assertIn(["--user", "disable", "--now", "c101-learn-backstop.timer"], self.systemctl.argvs())
        self.assert_keys_untouched()

    def test_without_a_state_file_a_script_or_unit_that_is_not_ours_stays(self) -> None:
        self.install_features()
        self.state_file.unlink()
        self.script.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        timer = self.units[1]
        timer.write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.script.read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n", "no marker, not ours")
        self.assertEqual(timer.read_text(encoding="utf-8"), "[Timer]\nOnCalendar=daily\n")
        self.assertFalse(self.units[0].exists(), "the service unit still carried the marker")
        self.assert_keys_untouched()

    def test_a_running_backstop_is_stopped_before_its_script_and_stamps_go(self) -> None:
        """RV1 R4: a backstop run in progress (c101-learn-backstop.service active) is stopped after its timer is disabled and
        while its script and stamp directory still exist, so it never loses its stamps mid-run and leaves no failed unit."""
        self.install_features()
        seen = self.sb.base / "systemctl-stop.log"  # what existed when the stop came; outside HOME
        self.sb.fake_command("systemctl", (
            'case " $* " in\n'
            '  *" stop c101-learn-backstop.service "*)\n'
            '    if [ -d %(stamps)s ] && [ -f %(script)s ]; then echo present; else echo gone; fi >> %(log)s ;;\n'
            'esac\n'
            'exit 0') % {"stamps": shlex.quote(str(self.stamps)), "script": shlex.quote(str(self.script)), "log": shlex.quote(str(seen))})
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(seen.read_text(encoding="utf-8").splitlines(), ["present"], "stopped once, before the purge")
        self.assertIn("stopped c101-learn-backstop.service", res.stdout)
        self.assertFalse(self.stamps.exists())
        self.assertFalse(self.script.exists())
        self.assert_keys_untouched()

    def test_an_idle_or_absent_backstop_service_is_left_alone_and_a_dry_run_only_plans_the_stop(self) -> None:
        self.install_features()
        self.systemctl.clear()
        res = self.uninstall("--dry-run")  # the default stub answers is-active with 0: the service reads as running
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("dry-run: systemctl --user stop c101-learn-backstop.service", res.stdout)
        self.assertEqual([a for a in self.systemctl.argvs() if "stop" in a], [], "a dry run stops nothing")
        idle = self.sb.argv_stub("systemctl", rules=[rule("is-active", code=3), rule("stop", stderr="Unit not loaded.\n", code=5)])
        idle.clear()
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual([a for a in idle.argvs() if "stop" in a], [], "an idle (or absent) service is not stopped")
        self.assertNotIn("stopped c101-learn-backstop.service", res.stdout)
        self.assertFalse(self.stamps.exists(), "the stamps still go")
        self.assert_keys_untouched()

    def test_a_second_run_is_harmless_and_still_leaves_the_keys(self) -> None:
        self.install_features()
        self.assertEqual(self.uninstall().returncode, 0)
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assert_keys_untouched()

    def test_an_edited_config_and_a_foreign_agent_link_are_left_in_place_with_a_warning(self) -> None:
        self.install_features()
        self.config.write_text('{"mcpServers": {"mine": {}}}\n', encoding="utf-8")
        self.agent.unlink()
        os.symlink("/etc/hostname", self.agent)
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.config.read_text(encoding="utf-8"), '{"mcpServers": {"mine": {}}}\n')
        self.assertEqual(os.readlink(self.agent), "/etc/hostname")
        self.assertIn("differs from the config this overlay installed", res.stderr)
        self.assertIn("is no longer the fal-runner link this overlay made", res.stderr)
        self.assertFalse(self.script.exists(), "what is still ours goes regardless")
        self.assert_keys_untouched()

    def test_a_keys_only_state_leaves_c101_alone(self) -> None:
        settings = self.claude / "settings.json"
        settings.write_text(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": '"$HOME"/.claude/stack/bin/stack hook session-start'}]}]}}), encoding="utf-8")
        settings_before = settings.read_bytes()
        self.write_state('state_set image_backend \'"fal"\'\nstate_set updated \'"20260102T030405Z"\'')
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("left as they are", res.stdout, "a state without c101_source records that this overlay never installed c101")
        self.assertTrue((self.claude / "stack").is_symlink(), "~/.claude/stack is not guessed at and removed")
        self.assertEqual(settings.read_bytes(), settings_before, "the stack hooks stay")
        self.assertFalse((self.stub / "calls.log").exists(), "c101's installer is never called")
        self.assertFalse(self.state_file.exists(), "the state file is still removed")
        self.assert_keys_untouched()

    def test_without_a_keys_file_c101_is_not_asked_to_reverse_anything(self) -> None:
        # c101's installer (any flag) creates ~/.claude/stack.env from its template when it is missing; the uninstaller
        # must not cause that, so the reversal is left to the user and said so
        self.write_state('state_set c101_source "$(json_str "%s")"\nstate_set c101_flags \'"--user --main-agent"\'\nstate_set profile \'"standard"\''
                         % self.src)
        for p in [self.stack_env, *self.claude.glob("stack.env.bak-*")]:
            p.unlink()
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse(self.stack_env.exists(), "stack.env must not come back")
        self.assertFalse((self.stub / "calls.log").exists(), "c101's installer was not run")
        self.assertIn("c101 reversal", res.stderr)
        self.assertIn("skipped", res.stderr)
        self.assertIn("Undo c101's settings by hand", res.stdout)

    def test_the_closing_hints_name_what_stays_behind(self) -> None:
        self.install_features()
        res = self.uninstall()
        self.assertIn("Next steps:", res.stdout)
        for text in ("~/.claude/stack.env", ".bak-", "ms-playwright", "uv cache clean", "~/.npm/_npx"):
            self.assertIn(text, res.stdout)
        res = self.uninstall(PLAYWRIGHT_BROWSERS_PATH=str(self.sb.base / "playwright-browsers"))
        self.assertIn(str(self.sb.base / "playwright-browsers"), res.stdout, "a custom browser directory is named instead")

    def test_a_state_file_written_before_the_parity_change_still_uninstalls(self) -> None:
        # an install by the first release recorded no timer, no fal agent and no preset: nothing here may trip over their absence
        self.write_state('state_set c101_source "$(json_str "%s")"\nstate_set c101_flags \'"--user"\'\nstate_set profile \'"standard"\'\n'
                         'state_set container_runtime \'"podman"\'' % self.src)
        res = self.uninstall()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse(self.state_dir.exists())
        self.assert_keys_untouched()


if __name__ == "__main__":
    unittest.main()
