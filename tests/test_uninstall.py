"""uninstall-linux.sh: removes only what the overlay added (links into c101, stack hooks, PATH block, Desktop entries,
units, installed commands) and leaves user data alone; --dry-run changes nothing."""
from __future__ import annotations

import json
import os
import unittest

from helpers import ROOT, Sandbox, bash_lib, make_stub_c101, needs_linux


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

    def uninstall(self, *args: str):
        extra = ["--allow-root"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
        return self.sb.run([str(ROOT / "uninstall-linux.sh"), *extra, *args], C101L_STATE_FILE=str(self.state))

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


if __name__ == "__main__":
    unittest.main()
