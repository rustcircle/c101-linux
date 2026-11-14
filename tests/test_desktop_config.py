"""claude_desktop_config.json merge: idempotence, backup, invalid JSON, running-app refusal (mock pgrep), removal."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict

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

    # --- parity: Playwright gets its own Chromium; what the first release shipped is upgraded in place ----------------

    def first_release_playwright(self) -> Dict[str, Any]:
        """The c101-playwright entry the first release (96050d1) shipped, before --browser chromium, as merge writes it."""
        home = str(self.sb.home)
        return {"command": home + "/.claude/stack/bin/mcp-run",
                "args": ["npx", "-y", "@playwright/mcp@latest", "--headless", "--isolated", "--caps=vision,pdf,devtools",
                         "--output-dir=%s/.claude/stack-data/playwright" % home, "--viewport-size=1280x800"]}

    def seed(self, servers: Dict[str, Any]) -> Dict[str, Any]:
        config = {"theme": "dark", "mcpServers": servers}
        self.cfg.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.write_text(json.dumps(config), encoding="utf-8")
        return config

    def test_playwright_selects_chromium_before_its_other_options(self) -> None:
        shipped = json.loads(SERVERS.read_text(encoding="utf-8"))["mcpServers"]["c101-playwright"]["args"]
        self.assertEqual(shipped[shipped.index("--browser") + 1], "chromium", "Chrome's channel rarely exists on Linux (never on arm64)")
        self.assertLess(shipped.index("@playwright/mcp@latest"), shipped.index("--browser"))
        self.assertLess(shipped.index("--browser"), shipped.index("--headless"))
        res = self.merge()
        out = json.loads(res.stdout)
        self.assertEqual(out["updated"], [], "a fresh config has nothing to upgrade")
        args = json.loads(self.cfg.read_text(encoding="utf-8"))["mcpServers"]["c101-playwright"]["args"]
        self.assertEqual(args[args.index("--browser") + 1], "chromium")
        self.assertIn("--output-dir=%s/.claude/stack-data/playwright" % self.sb.home, args)

    def test_the_legacy_block_is_bookkeeping_and_is_never_installed(self) -> None:
        self.merge()
        data = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(sorted(data), ["mcpServers"])
        self.assertFalse([k for k in data["mcpServers"] if k.startswith("_")])
        self.assertNotIn("_since", json.dumps(data))

    def test_the_recorded_first_release_entry_is_what_that_release_shipped(self) -> None:
        shipped = json.loads(SERVERS.read_text(encoding="utf-8"))
        legacy = {k: v for k, v in shipped["_legacy"].items() if not k.startswith("_")}
        self.assertEqual(sorted(legacy), ["c101-playwright"])
        clean = [{k: v for k, v in e.items() if not k.startswith("_")} for e in legacy["c101-playwright"]]
        self.assertIn({"command": "~/.claude/stack/bin/mcp-run",
                       "args": ["npx", "-y", "@playwright/mcp@latest", "--headless", "--isolated", "--caps=vision,pdf,devtools",
                                "--output-dir=${HOME}/.claude/stack-data/playwright", "--viewport-size=1280x800"]}, clean)
        for name, entries in legacy.items():
            self.assertIn(name, shipped["mcpServers"])
            for entry in entries:
                self.assertNotEqual({k: v for k, v in entry.items() if not k.startswith("_")}, shipped["mcpServers"][name],
                                    "an old entry equal to the current one would hide a real change")

    def test_an_entry_the_first_release_shipped_is_upgraded_in_place(self) -> None:
        original = self.seed({"c101-playwright": self.first_release_playwright(), "mine": {"command": "keep"}})
        res = self.merge()
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual(out["updated"], ["c101-playwright"])
        self.assertEqual(sorted(out["added"]), ["c101-desktop-sandbox", "c101-fetch", "c101-image"])
        self.assertEqual(out["conflicts"], [])
        self.assertTrue(out["changed"])
        self.assertIsNotNone(out["backup"])
        self.assertEqual(json.loads(Path(out["backup"]).read_text(encoding="utf-8")), original, "the backup holds the old file")
        data = json.loads(self.cfg.read_text(encoding="utf-8"))
        args = data["mcpServers"]["c101-playwright"]["args"]
        self.assertEqual(args[args.index("--browser") + 1], "chromium")
        self.assertEqual(data["mcpServers"]["mine"], {"command": "keep"})
        self.assertEqual(data["theme"], "dark")
        # idempotent afterwards: nothing to update, no second backup
        again = json.loads(self.merge().stdout)
        self.assertEqual((again["updated"], again["added"], again["changed"]), ([], [], False))
        self.assertEqual(len(list(self.cfg.parent.glob("*.bak-*"))), 1)

    def test_an_edited_old_entry_is_a_conflict_and_never_upgraded(self) -> None:
        edited = self.first_release_playwright()
        edited["args"].append("--my-own-flag")
        self.seed({"c101-playwright": edited})
        out = json.loads(self.merge().stdout)
        self.assertEqual(out["conflicts"], ["c101-playwright"])
        self.assertEqual(out["updated"], [])
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8"))["mcpServers"]["c101-playwright"], edited)

    def test_the_upgrade_honours_the_running_guard_and_the_dry_run(self) -> None:
        self.seed({"c101-playwright": self.first_release_playwright()})
        before = self.cfg.read_text(encoding="utf-8")
        dry = json.loads(self.merge("--dry-run").stdout)
        self.assertEqual(dry["updated"], ["c101-playwright"])
        self.assertTrue(dry["changed"])
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), before, "--dry-run writes nothing")
        self.assertEqual(list(self.cfg.parent.glob("*.bak-*")), [])
        self.running()
        res = self.merge()
        self.assertEqual(res.returncode, 3, res.stdout + res.stderr)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), before, "Desktop rewrites the file on exit: refused while it runs")
        forced = json.loads(self.merge("--force").stdout)
        self.assertEqual(forced["updated"], ["c101-playwright"])

    def test_names_limit_the_upgrade_too(self) -> None:
        self.seed({"c101-playwright": self.first_release_playwright()})
        before = self.cfg.read_text(encoding="utf-8")
        out = json.loads(self.merge("--names", "c101-fetch").stdout)
        self.assertEqual((out["updated"], out["added"]), ([], ["c101-fetch"]))
        data = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["mcpServers"]["c101-playwright"], json.loads(before)["mcpServers"]["c101-playwright"])

    def test_remove_deletes_an_entry_the_first_release_shipped_but_not_an_edited_one(self) -> None:
        self.seed({"c101-playwright": self.first_release_playwright(), "mine": {"command": "keep"}})
        out = json.loads(self.call("remove", "--config", str(self.cfg), "--servers", str(SERVERS)).stdout)
        self.assertEqual(out["removed"], ["c101-playwright"], "the uninstaller records upgraded entries, so it must remove them")
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8"))["mcpServers"], {"mine": {"command": "keep"}})
        edited = self.first_release_playwright()
        edited["args"].append("--my-own-flag")
        self.seed({"c101-playwright": edited})
        out = json.loads(self.call("remove", "--config", str(self.cfg), "--servers", str(SERVERS)).stdout)
        self.assertEqual((out["removed"], out["modified"]), ([], ["c101-playwright"]))
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8"))["mcpServers"]["c101-playwright"], edited)

    def test_no_key_value_can_enter_the_config_through_the_shipped_servers(self) -> None:
        text = SERVERS.read_text(encoding="utf-8")
        for needle in ("FAL_KEY", "_API_KEY", "IMAGE_", "Authorization", "Bearer"):
            self.assertNotIn(needle, text, "keys come from ~/.claude/stack.env through bin/mcp-run only")


if __name__ == "__main__":
    unittest.main()
