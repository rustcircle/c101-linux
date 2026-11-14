"""claude_desktop_config.json merge: idempotence, backup, invalid JSON, running-app refusal (mock pgrep), removal."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from helpers import LIB, ROOT, Sandbox

SERVERS = ROOT / "config" / "desktop-mcp.json"


class DesktopConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.cfg = self.sb.home / ".config" / "Claude" / "claude_desktop_config.json"

    def call(self, *args: str, **extra: str):
        return self.sb.run([sys.executable, "-I", str(LIB / "desktop_config.py"), *args], **extra)

    def merge(self, *args: str, **extra: str):
        return self.call("merge", "--config", str(self.cfg), "--servers", str(SERVERS), *args, **extra)

    def running(self) -> None:
        self.sb.fake_command("pgrep", "echo 424242\nexit 0")

    def test_creates_file_with_expanded_paths_and_no_backup(self) -> None:
        res = self.merge()
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertTrue(out["changed"])
        self.assertIsNone(out["backup"])
        data = json.loads(self.cfg.read_text())
        entry = data["mcpServers"]["c101-fetch"]
        self.assertEqual(entry["command"], "%s/.claude/stack/bin/mcp-run" % self.sb.home)
        pw = data["mcpServers"]["c101-playwright"]["args"]
        self.assertIn("--output-dir=%s/.claude/stack-data/playwright" % self.sb.home, pw)
        self.assertNotIn("~", json.dumps(data))
        self.assertNotIn("neural-memory", json.dumps(data), "the single-writer memory server must not be here")
        sandbox = data["mcpServers"]["c101-desktop-sandbox"]["args"]
        self.assertIn("CLAUDE_PROJECT_DIR=%s/.claude/stack-data/desktop-chat" % self.sb.home, sandbox,
                      "Desktop starts servers in an undefined directory; c101 would write .stack/sandbox there")

    def test_second_merge_is_a_no_op(self) -> None:
        self.merge()
        before = self.cfg.read_text()
        res = self.merge()
        out = json.loads(res.stdout)
        self.assertFalse(out["changed"])
        self.assertEqual(out["added"], [])
        self.assertEqual(self.cfg.read_text(), before)
        self.assertEqual(list(self.cfg.parent.glob("*.bak-*")), [])

    def test_existing_file_is_kept_and_backed_up_once(self) -> None:
        self.cfg.parent.mkdir(parents=True)
        original = {"theme": "dark", "mcpServers": {"mine": {"command": "x"}}}
        self.cfg.write_text(json.dumps(original))
        res = self.merge()
        out = json.loads(res.stdout)
        self.assertTrue(out["changed"])
        backups = list(self.cfg.parent.glob("claude_desktop_config.json.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), original)
        data = json.loads(self.cfg.read_text())
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["mcpServers"]["mine"], {"command": "x"})
        self.assertIn("c101-fetch", data["mcpServers"])
        self.merge()
        self.assertEqual(len(list(self.cfg.parent.glob("*.bak-*"))), 1, "an unchanged merge must not back up again")

    def test_hand_written_entry_with_the_same_name_is_never_overwritten(self) -> None:
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text(json.dumps({"mcpServers": {"c101-fetch": {"command": "mine"}}}))
        out = json.loads(self.merge().stdout)
        self.assertEqual(out["conflicts"], ["c101-fetch"])
        self.assertEqual(json.loads(self.cfg.read_text())["mcpServers"]["c101-fetch"], {"command": "mine"})

    def test_invalid_json_is_refused_and_untouched(self) -> None:
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text("{ not json")
        res = self.merge()
        self.assertEqual(res.returncode, 2)
        self.assertEqual(self.cfg.read_text(), "{ not json")
        self.assertEqual(list(self.cfg.parent.glob("*.bak-*")), [])

    def test_refuses_while_desktop_runs_unless_forced(self) -> None:
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text("{}")
        self.running()
        res = self.merge()
        self.assertEqual(res.returncode, 3, res.stdout + res.stderr)
        self.assertEqual(self.cfg.read_text(), "{}")
        forced = self.merge("--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertIn("c101-fetch", json.loads(self.cfg.read_text())["mcpServers"])

    def test_dry_run_never_writes_or_refuses(self) -> None:
        self.running()
        res = self.merge("--dry-run")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(json.loads(res.stdout)["changed"])
        self.assertFalse(self.cfg.exists())

    def test_remove_only_touches_entries_that_still_equal_ours(self) -> None:
        self.merge()
        data = json.loads(self.cfg.read_text())
        data["mcpServers"]["c101-image"]["args"].append("--edited-by-user")
        data["mcpServers"]["mine"] = {"command": "keep"}
        self.cfg.write_text(json.dumps(data))
        res = self.call("remove", "--config", str(self.cfg), "--servers", str(SERVERS))
        out = json.loads(res.stdout)
        self.assertEqual(out["modified"], ["c101-image"])
        self.assertEqual(sorted(out["removed"]), ["c101-desktop-sandbox", "c101-fetch", "c101-playwright"])
        left = json.loads(self.cfg.read_text())["mcpServers"]
        self.assertEqual(sorted(left), ["c101-image", "mine"])

    def test_remove_names_filter_and_empty_cleanup(self) -> None:
        self.merge()
        res = self.call("remove", "--config", str(self.cfg), "--servers", str(SERVERS), "--names", "c101-fetch,c101-image,c101-playwright,c101-desktop-sandbox")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("mcpServers", json.loads(self.cfg.read_text()))

    def test_validate(self) -> None:
        self.assertEqual(self.call("validate", "--config", str(self.cfg)).returncode, 0)
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text("[1]")
        self.assertEqual(self.call("validate", "--config", str(self.cfg)).returncode, 2)

    def test_shipped_server_file_is_valid_and_secret_free(self) -> None:
        data = json.loads(SERVERS.read_text())
        for name, entry in data["mcpServers"].items():
            self.assertTrue(name.startswith("c101-"))
            self.assertNotIn("env", entry, "keys come from ~/.claude/stack.env through bin/mcp-run, never from this file")
            self.assertTrue(entry["command"].startswith("~/.claude/stack/bin/mcp-run"))


if __name__ == "__main__":
    unittest.main()
