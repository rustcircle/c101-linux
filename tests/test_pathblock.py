"""PATH integration: one managed block, idempotent, correct file per login shell, environment.d, nvm resolution."""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from helpers import Sandbox, bash_lib, needs_linux

BEGIN = "# >>> c101-linux >>>"
END = "# <<< c101-linux <<<"


@needs_linux
class PathBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.env = {"C101L_STATE_FILE": str(self.sb.base / "state.json")}

    def apply(self, shell: str = "bash", envd: str = "0", extra: str = "") -> str:
        res = bash_lib(self.sb, "LOGIN_SHELL=%s\n%s\npath_apply %s" % (shell, extra, envd), **self.env)
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        return res.stdout

    def test_twice_gives_one_block(self) -> None:
        self.apply()
        self.apply()
        text = (self.sb.home / ".profile").read_text()
        self.assertEqual(text.count(BEGIN), 1)
        self.assertEqual(text.count(END), 1)

    def test_second_run_reports_current_and_writes_no_backup(self) -> None:
        self.apply()
        out = self.apply()
        self.assertIn("already current", out)
        self.assertEqual(list(self.sb.home.glob(".profile.bak-*")), [])

    def test_existing_profile_is_backed_up_and_preserved(self) -> None:
        profile = self.sb.home / ".profile"
        profile.write_text("export KEEP=1\n")
        self.apply()
        backups = list(self.sb.home.glob(".profile.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "export KEEP=1\n")
        self.assertIn("export KEEP=1", profile.read_text())

    def test_bash_prefers_bash_profile_when_it_exists(self) -> None:
        (self.sb.home / ".bash_profile").write_text("# mine\n")
        self.apply()
        self.assertIn(BEGIN, (self.sb.home / ".bash_profile").read_text())
        self.assertFalse((self.sb.home / ".profile").exists())

    def test_zsh_and_fish_targets(self) -> None:
        self.apply("zsh")
        self.assertIn(BEGIN, (self.sb.home / ".zprofile").read_text())
        self.apply("fish")
        fish = (self.sb.home / ".config" / "fish" / "conf.d" / "c101-linux.fish").read_text()
        self.assertIn(BEGIN, fish)
        self.assertIn("fish_add_path --global --path", fish)

    def test_block_contains_the_required_directories(self) -> None:
        self.apply()
        text = (self.sb.home / ".profile").read_text()
        for needle in ('"$HOME/.local/bin"', '"$HOME/.cargo/bin"', '"/home/linuxbrew/.linuxbrew/bin"', '"/snap/bin"',
                       '"$HOME/.npm-global/bin"', '"$HOME/.bun/bin"', '"$HOME/.volta/bin"',
                       '"${ASDF_DATA_DIR:-$HOME/.asdf}/shims"', '"$HOME/.local/share/mise/shims"'):
            self.assertIn(needle, text)

    def test_nvm_default_directory_is_resolved(self) -> None:
        nvm = self.sb.home / ".nvm"
        for v in ("v18.20.0", "v20.1.0", "v22.3.0"):
            (nvm / "versions" / "node" / v / "bin").mkdir(parents=True)
        (nvm / "alias").mkdir()
        (nvm / "alias" / "default").write_text("20\n")
        self.apply()
        self.assertIn('"$HOME/.nvm/versions/node/v20.1.0/bin"', (self.sb.home / ".profile").read_text())

    def test_nvm_lts_alias_falls_back_to_newest(self) -> None:
        nvm = self.sb.home / ".nvm"
        for v in ("v18.20.0", "v22.3.0"):
            (nvm / "versions" / "node" / v / "bin").mkdir(parents=True)
        (nvm / "alias").mkdir()
        (nvm / "alias" / "default").write_text("lts/*\n")
        self.apply()
        self.assertIn("v22.3.0/bin", (self.sb.home / ".profile").read_text())

    def test_login_shell_gets_the_directories_without_duplicates(self) -> None:
        for d in (".local/bin", ".cargo/bin", ".bun/bin"):
            (self.sb.home / d).mkdir(parents=True)
        self.apply()
        res = self.sb.run(["env", "-i", "HOME=%s" % self.sb.home, "PATH=/usr/bin:/bin", "bash", "-lc", 'printf %s "$PATH"'])
        self.assertEqual(res.returncode, 0, res.stderr)
        parts = res.stdout.split(":")
        for d in (".local/bin", ".cargo/bin", ".bun/bin"):
            self.assertIn(str(self.sb.home / d), parts)
            self.assertEqual(parts.count(str(self.sb.home / d)), 1, "duplicate entry in %s" % res.stdout)
        # a second login-shell level must not add anything: source the profile again on top of its own result
        res2 = self.sb.run(["env", "-i", "HOME=%s" % self.sb.home, "PATH=%s" % res.stdout, "bash", "-lc", 'printf %s "$PATH"'])
        self.assertEqual(res2.stdout.split(":").count(str(self.sb.home / ".local/bin")), 1)

    def test_sandbox_runtime_and_docker_api_exports(self) -> None:
        self.apply(extra='ENV_SANDBOX_RUNTIME="$HOME/.local/bin/c101-podman"\nENV_DOCKER_API=1')
        text = (self.sb.home / ".profile").read_text()
        self.assertIn(': "${DESKTOP_SANDBOX_RUNTIME:=$HOME/.local/bin/c101-podman}"', text)
        self.assertIn("DOCKER_HOST:=unix://$XDG_RUNTIME_DIR/podman/podman.sock", text)
        self.assertIn("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", text)
        self.assertIn("TESTCONTAINERS_RYUK_DISABLED", text)
        res = self.sb.run(["env", "-i", "HOME=%s" % self.sb.home, "XDG_RUNTIME_DIR=/run/user/1000", "PATH=/usr/bin:/bin",
                           "bash", "-lc", 'printf "%s|%s" "$DESKTOP_SANDBOX_RUNTIME" "$DOCKER_HOST"'])
        self.assertEqual(res.stdout, "%s/.local/bin/c101-podman|unix:///run/user/1000/podman/podman.sock" % self.sb.home)
        # a user's own value wins
        res = self.sb.run(["env", "-i", "HOME=%s" % self.sb.home, "DESKTOP_SANDBOX_RUNTIME=docker", "PATH=/usr/bin:/bin",
                           "bash", "-lc", 'printf %s "$DESKTOP_SANDBOX_RUNTIME"'])
        self.assertEqual(res.stdout, "docker")

    def test_environment_d_file(self) -> None:
        self.apply(envd="1", extra='ENV_SANDBOX_RUNTIME="podman"\nENV_DOCKER_API=1')
        f = self.sb.home / ".config" / "environment.d" / "60-c101-linux.conf"
        text = f.read_text()
        self.assertTrue(text.startswith("# c101-linux managed file"))
        self.assertRegex(text, r"(?m)^PATH=\$HOME/\.local/bin:.*:\$PATH$")
        self.assertIn("DESKTOP_SANDBOX_RUNTIME=${DESKTOP_SANDBOX_RUNTIME:-podman}", text)
        self.assertRegex(text, r"(?m)^DOCKER_HOST=\$\{DOCKER_HOST:-unix:///run/user/\d+/podman/podman\.sock\}$")
        self.assertIn("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=${TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE:-", text)
        # idempotent
        before = f.stat().st_mtime_ns
        self.apply(envd="1", extra='ENV_SANDBOX_RUNTIME="podman"\nENV_DOCKER_API=1')
        self.assertEqual(f.stat().st_mtime_ns, before)
        self.assertEqual(list(f.parent.glob("*.bak-*")), [])

    def test_remove_restores_the_file_and_deletes_one_it_created(self) -> None:
        profile = self.sb.home / ".profile"
        profile.write_text("export KEEP=1\n")
        self.apply(envd="1")
        res = bash_lib(self.sb, "LOGIN_SHELL=bash\npath_remove", **self.env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(profile.read_text().strip(), "export KEEP=1")
        self.assertFalse((self.sb.home / ".config" / "environment.d" / "60-c101-linux.conf").exists())
        # a profile the overlay created is deleted again
        profile.unlink()
        self.apply()
        self.assertTrue(profile.exists())
        bash_lib(self.sb, "LOGIN_SHELL=bash\npath_remove", **self.env)
        self.assertFalse(profile.exists())

    def test_symlinked_dotfile_keeps_its_link(self) -> None:
        dot = self.sb.home / "dotfiles"
        dot.mkdir()
        (dot / "profile").write_text("export KEEP=1\n")
        (self.sb.home / ".profile").symlink_to(dot / "profile")
        self.apply()
        self.assertTrue((self.sb.home / ".profile").is_symlink(), "the link must survive")
        self.assertIn(BEGIN, (dot / "profile").read_text())
        self.assertEqual(len(list(dot.glob("profile.bak-*"))), 1, "the backup sits next to the real file")
        self.apply()
        self.assertEqual((dot / "profile").read_text().count(BEGIN), 1)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write read-only files")
    def test_read_only_link_target_is_refused_with_the_block_printed(self) -> None:
        ro = self.sb.base / "ro"
        ro.mkdir()
        (ro / "profile").write_text("managed elsewhere\n")
        (ro / "profile").chmod(0o444)
        (self.sb.home / ".profile").symlink_to(ro / "profile")
        res = bash_lib(self.sb, "LOGIN_SHELL=bash\npath_apply 0", **self.env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("may not edit", res.stderr)
        self.assertIn(BEGIN, res.stderr, "the block is printed for the user's own config")
        self.assertTrue((self.sb.home / ".profile").is_symlink())
        self.assertEqual((ro / "profile").read_text(), "managed elsewhere\n")

    def test_dry_run_without_python_shows_a_plan(self) -> None:
        before = self.sb.snapshot()
        res = bash_lib(self.sb, "_probe_helper_python() { return 1; }\nDRY_RUN=1\nLOGIN_SHELL=bash\npath_apply 1", **self.env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("plan only", res.stdout)
        self.assertEqual(self.sb.snapshot(), before)

    def test_empty_nvm_versions_directory_does_not_break_the_block(self) -> None:
        (self.sb.home / ".nvm" / "versions" / "node").mkdir(parents=True)
        self.apply()
        text = (self.sb.home / ".profile").read_text()
        self.assertIn('"$HOME/.cargo/bin"', text, "later entries must survive an nvm dir without versions")

    def test_dry_run_changes_nothing(self) -> None:
        before = self.sb.snapshot()
        res = bash_lib(self.sb, "DRY_RUN=1\nLOGIN_SHELL=bash\npath_apply 1", **self.env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.sb.snapshot(), before)
        self.assertFalse((self.sb.base / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
