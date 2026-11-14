"""managed_block.py, state.py and jsontool.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from helpers import LIB, Sandbox

BEGIN = "# >>> c101-linux >>>"
END = "# <<< c101-linux <<<"


class ManagedBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.content = self.sb.base / "content"
        self.content.write_text("PATH_LINE\n")
        self.target = self.sb.home / "profile"

    def mb(self, *args: str):
        res = self.sb.run([sys.executable, "-I", str(LIB / "managed_block.py"), *args])
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_apply_is_idempotent(self) -> None:
        self.target.write_text("a\nb\n")
        first = self.mb("apply", "--file", str(self.target), "--content-file", str(self.content))
        self.assertTrue(first["changed"])
        text = self.target.read_text()
        second = self.mb("apply", "--file", str(self.target), "--content-file", str(self.content))
        self.assertFalse(second["changed"])
        self.assertEqual(self.target.read_text(), text)
        self.assertEqual(text.count(BEGIN), 1)

    def test_changed_content_replaces_in_place(self) -> None:
        self.mb("apply", "--file", str(self.target), "--content-file", str(self.content))
        self.content.write_text("NEW_LINE\n")
        out = self.mb("apply", "--file", str(self.target), "--content-file", str(self.content))
        self.assertTrue(out["changed"])
        text = self.target.read_text()
        self.assertIn("NEW_LINE", text)
        self.assertNotIn("PATH_LINE", text)
        self.assertEqual(text.count(BEGIN), 1)

    def test_duplicate_blocks_collapse_to_one(self) -> None:
        block = "%s\nOLD\n%s\n" % (BEGIN, END)
        self.target.write_text("x\n" + block + "y\n" + block)
        self.mb("apply", "--file", str(self.target), "--content-file", str(self.content))
        text = self.target.read_text()
        self.assertEqual(text.count(BEGIN), 1)
        self.assertIn("x\n", text)
        self.assertIn("y\n", text)

    def test_unterminated_block_is_repaired(self) -> None:
        self.target.write_text("keep\n%s\nhalf\n" % BEGIN)
        self.mb("apply", "--file", str(self.target), "--content-file", str(self.content))
        text = self.target.read_text()
        self.assertEqual(text.count(BEGIN), 1)
        self.assertEqual(text.count(END), 1)

    def test_remove_and_delete_if_empty(self) -> None:
        self.mb("apply", "--file", str(self.target), "--content-file", str(self.content))
        out = self.mb("remove", "--file", str(self.target), "--delete-if-empty")
        self.assertTrue(out["deleted"])
        self.assertFalse(self.target.exists())

    def test_check(self) -> None:
        self.assertNotEqual(self.sb.run([sys.executable, "-I", str(LIB / "managed_block.py"), "check", "--file", str(self.target)]).returncode, 0)
        self.mb("apply", "--file", str(self.target), "--content-file", str(self.content))
        self.assertEqual(self.sb.run([sys.executable, "-I", str(LIB / "managed_block.py"), "check", "--file", str(self.target)]).returncode, 0)

    def test_dry_run_writes_nothing(self) -> None:
        out = self.mb("apply", "--file", str(self.target), "--content-file", str(self.content), "--dry-run")
        self.assertTrue(out["changed"])
        self.assertFalse(self.target.exists())

    def test_write_file_marker_guard(self) -> None:
        marker = "# mine"
        out = self.mb("write-file", "--file", str(self.target), "--content-file", str(self.content), "--marker", marker)
        self.assertTrue(out["created"])
        self.assertTrue(self.target.read_text().startswith(marker))
        self.target.write_text("somebody else's file\n")
        out = self.mb("remove-file", "--file", str(self.target), "--marker", marker)
        self.assertFalse(out["changed"])
        self.assertTrue(self.target.exists())


class SymlinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.content = self.sb.base / "content"
        self.content.write_text("X\n")

    def mb(self, *args: str):
        return self.sb.run([sys.executable, "-I", str(LIB / "managed_block.py"), *args])

    def test_write_goes_through_the_link(self) -> None:
        real = self.sb.base / "dotfiles" / "profile"
        real.parent.mkdir()
        real.write_text("keep\n")
        link = self.sb.home / ".profile"
        link.symlink_to(real)
        res = self.mb("apply", "--file", str(link), "--content-file", str(self.content))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(link.is_symlink())
        self.assertIn(BEGIN, real.read_text())
        self.assertEqual(len(list(real.parent.glob("profile.bak-*"))), 1)
        res = self.mb("remove", "--file", str(link))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(link.is_symlink())
        self.assertNotIn(BEGIN, real.read_text())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write read-only files")
    def test_read_only_target_is_refused_with_exit_4(self) -> None:
        real = self.sb.base / "ro" / "profile"
        real.parent.mkdir()
        real.write_text("fixed\n")
        real.chmod(0o444)
        link = self.sb.home / ".profile"
        link.symlink_to(real)
        res = self.mb("apply", "--file", str(link), "--content-file", str(self.content))
        self.assertEqual(res.returncode, 4)
        self.assertTrue(json.loads(res.stdout)["refused"])
        self.assertEqual(real.read_text(), "fixed\n")


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.f = self.sb.base / "state" / "state.json"

    def st(self, *args: str):
        return self.sb.run([sys.executable, "-I", str(LIB / "state.py"), "--file", str(self.f), *args])

    def test_set_get_append_del(self) -> None:
        self.assertEqual(self.st("set", "a.b", '"x"').returncode, 0)
        self.assertEqual(self.st("get", "a.b").stdout.strip(), "x")
        self.st("append", "files", '"one"')
        self.st("append", "files", '"one"')
        self.st("append", "files", '"two"')
        self.assertEqual(json.loads(self.st("get", "files").stdout), ["one", "two"])
        self.st("del", "a.b")
        self.assertEqual(self.st("get", "a.b").returncode, 1)
        self.assertEqual(oct(self.f.stat().st_mode & 0o777), "0o600")

    def test_corrupt_state_is_not_overwritten(self) -> None:
        self.f.parent.mkdir(parents=True)
        self.f.write_text("{oops")
        self.assertNotEqual(self.st("set", "k", "1").returncode, 0)
        self.assertEqual(self.f.read_text(), "{oops")


class JsonToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.f = self.sb.home / "settings.json"
        self.mark = "/.claude/stack/bin/stack hook"
        self.f.write_text(json.dumps({
            "agent": "delegator",
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": '"$HOME"/.claude/stack/bin/stack hook session-start'}]}],
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": '"$HOME"/.claude/stack/bin/stack hook pre-tool'}]},
                    {"hooks": [{"type": "command", "command": "my-own-hook"}]},
                ],
            },
        }))

    def jt(self, *args: str):
        return self.sb.run([sys.executable, "-I", str(LIB / "jsontool.py"), *args])

    def test_hook_count_and_strip(self) -> None:
        self.assertEqual(self.jt("hook-count", str(self.f), self.mark).stdout.strip(), "2")
        res = self.jt("strip-hooks", str(self.f), self.mark)
        self.assertEqual(json.loads(res.stdout)["removed"], 2)
        data = json.loads(self.f.read_text())
        self.assertEqual(data["hooks"], {"PreToolUse": [{"hooks": [{"type": "command", "command": "my-own-hook"}]}]})
        self.assertEqual(data["agent"], "delegator")
        self.assertEqual(len(list(self.sb.home.glob("settings.json.bak-*"))), 1)
        self.assertEqual(json.loads(self.jt("strip-hooks", str(self.f), self.mark).stdout)["removed"], 0)

    def test_strip_hooks_dry_run(self) -> None:
        before = self.f.read_text()
        self.jt("strip-hooks", str(self.f), self.mark, "--dry-run")
        self.assertEqual(self.f.read_text(), before)

    def test_strip_keys_deletes_file_when_empty(self) -> None:
        m = self.sb.home / "managed.json"
        m.write_text(json.dumps({"availableModels": ["a"], "enforceAvailableModels": True, "other": 1}))
        self.jt("strip-keys", str(m), "availableModels", "enforceAvailableModels")
        self.assertEqual(json.loads(m.read_text()), {"other": 1})
        self.jt("strip-keys", str(m), "other")
        self.assertFalse(m.exists())

    def test_has_keys_and_env_snapshot(self) -> None:
        self.f.write_text(json.dumps({"agent": "x", "env": {"A": "1"}}))
        self.assertEqual(json.loads(self.jt("has-keys", str(self.f), "agent", "nope", "env").stdout), ["agent", "env"])
        self.assertEqual(json.loads(self.jt("env-snapshot", str(self.f)).stdout), ["A"], "names only (R3), never the value")
        self.f.write_text("{}")
        self.assertEqual(json.loads(self.jt("env-snapshot", str(self.f)).stdout), [])

    def test_env_snapshot_never_prints_a_value(self) -> None:
        """R3: a token in the user's settings.json env must not travel into state.json, so the snapshot holds names only."""
        token = "ghp_SENTINEL_settings_env_value_0123456789"
        self.f.write_text(json.dumps({"env": {"MY_TOKEN": token, "B": "2"}}))
        res = self.jt("env-snapshot", str(self.f))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn(token, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout), ["B", "MY_TOKEN"])
        self.f.write_text(json.dumps({"env": "not-an-object"}))
        self.assertEqual(json.loads(self.jt("env-snapshot", str(self.f)).stdout), [])

    def jt_stdin(self, text: str, *args: str):
        return subprocess.run([sys.executable, "-I", str(LIB / "jsontool.py"), *args], input=text, capture_output=True, text=True,
                              env=self.sb.env(), cwd=str(self.sb.base), timeout=120)

    def test_env_names_cuts_an_older_states_object_down_to_its_keys(self) -> None:
        token = "ghp_SENTINEL_old_state_value"
        res = self.jt_stdin(json.dumps({"B": token, "A": "1"}), "env-names", "-")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), ["A", "B"])
        self.assertNotIn(token, res.stdout)
        self.assertEqual(json.loads(self.jt_stdin(json.dumps(["Z", "Y"]), "env-names", "-").stdout), ["Y", "Z"])
        self.assertEqual(self.jt_stdin("{not json", "env-names", "-").returncode, 2)

    def test_strip_env_removes_only_what_c101_added_and_still_holds(self) -> None:
        src = self.sb.home / "c101-settings.json"
        src.write_text(json.dumps({"env": {"MAX": "40000", "DEPTH": "8", "USERSET": "x"}}))
        self.f.write_text(json.dumps({"env": {"MAX": "40000", "DEPTH": "9", "USERSET": "x", "MINE": "1"}}))
        before = json.dumps({"USERSET": "x"})  # the user already had USERSET before c101 ran
        res = self.jt("strip-env", str(self.f), str(src), before)
        self.assertEqual(json.loads(res.stdout)["removed"], ["MAX"])
        self.assertEqual(json.loads(self.f.read_text())["env"], {"DEPTH": "9", "USERSET": "x", "MINE": "1"})
        self.assertEqual(len(list(self.sb.home.glob("settings.json.bak-*"))), 1)

    def test_strip_env_takes_names_an_older_states_object_and_stdin(self) -> None:
        """R3: BEFORE is a list of names; an older state file's object still works (its keys count); `-` reads it from stdin, so
        no value ever sits on a command line."""
        src = self.sb.home / "c101-settings.json"
        src.write_text(json.dumps({"env": {"MAX": "40000", "DEPTH": "8", "USERSET": "x"}}))
        original = json.dumps({"env": {"MAX": "40000", "DEPTH": "8", "USERSET": "x", "MINE": "1"}})
        for label, before, via_stdin in (("names", json.dumps(["USERSET"]), False), ("older object", json.dumps({"USERSET": "x"}), False),
                                         ("stdin names", json.dumps(["USERSET"]), True), ("stdin object", json.dumps({"USERSET": "x"}), True)):
            with self.subTest(label):
                self.f.write_text(original)
                if via_stdin:
                    res = self.jt_stdin(before, "strip-env", str(self.f), str(src), "-")
                else:
                    res = self.jt("strip-env", str(self.f), str(src), before)
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertEqual(sorted(json.loads(res.stdout)["removed"]), ["DEPTH", "MAX"])
                self.assertEqual(json.loads(self.f.read_text())["env"], {"USERSET": "x", "MINE": "1"})

    def test_writes_go_through_symlinks(self) -> None:
        real = self.sb.base / "real.json"
        real.write_text(self.f.read_text())
        self.f.unlink()
        self.f.symlink_to(real)
        self.jt("strip-hooks", str(self.f), self.mark)
        self.assertTrue(self.f.is_symlink())
        self.assertNotIn("stack hook", real.read_text())

    def test_invalid_and_missing(self) -> None:
        self.f.write_text("nope")
        self.assertEqual(self.jt("valid", str(self.f)).returncode, 2)
        self.assertEqual(self.jt("valid", str(self.sb.home / "absent.json")).returncode, 4)


if __name__ == "__main__":
    unittest.main()
