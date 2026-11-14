"""install-linux.sh end to end WITHOUT changes: help, refusals, dry run with a stub c101 and a throw-away HOME."""
from __future__ import annotations

import os
import unittest

from helpers import ROOT, Sandbox, installer_args, make_stub_c101, needs_linux


@needs_linux
class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.stub = make_stub_c101(self.sb.base / "c101")

    def test_help_exits_zero_and_documents_every_flag(self) -> None:
        res = self.sb.run([str(ROOT / "install-linux.sh"), "--help"])
        self.assertEqual(res.returncode, 0, res.stderr)
        for flag in ("--profile", "--source", "--repo", "--ref", "--packs", "--mcp", "--install-desktop", "--install-cli",
                     "--node", "--desktop-chat-mcp", "--sandbox", "--playwright-deps", "--no-sudo", "--yes", "--dry-run",
                     "--allow-root", "--managed-settings", "--container-runtime", "--podman-services", "--podman-timers",
                     "--docker-shim", "--enable-linger", "--link-bins"):
            self.assertIn(flag, res.stdout)
        self.assertNotIn("force-platform", res.stdout)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "needs a non-root user to prove the refusal")
    def test_root_is_refused_unless_allowed(self) -> None:
        res = self.sb.run([str(ROOT / "install-linux.sh"), "--dry-run", "--source", str(self.stub)], C101_LINUX_FAKE_EUID="0")
        self.assertEqual(res.returncode, 3)
        self.assertIn("refusing to run as root", res.stderr)
        self.assertEqual(self.sb.snapshot(), {})
        ok = self.sb.run([str(ROOT / "install-linux.sh"), "--dry-run", "--allow-root", "--profile", "ci", "--source", str(self.stub)],
                         C101_LINUX_FAKE_EUID="0")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("Desktop will not start as root", ok.stderr)

    def test_dry_run_changes_nothing_for_every_profile(self) -> None:
        for profile in ("minimal", "standard", "full", "headless", "ci"):
            before = self.sb.snapshot()
            res = self.sb.run(installer_args("--dry-run", "--profile", profile, "--source", str(self.stub)))
            self.assertEqual(res.returncode, 0, "%s: %s\n%s" % (profile, res.stdout, res.stderr))
            self.assertEqual(self.sb.snapshot(), before, "dry run changed HOME for profile %s" % profile)
            self.assertIn("dry run complete", res.stdout)
        self.assertFalse((self.sb.home / ".local" / "state").exists())
        self.assertEqual(list(self.sb.tmpdir.iterdir()), [], "temp files must be cleaned up")

    def test_dry_run_never_touches_the_container_engine(self) -> None:
        self.sb.fake_podman()
        before = self.sb.snapshot()
        res = self.sb.run(installer_args("--dry-run", "--profile", "full", "--source", str(self.stub)))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse((self.sb.home / "podman-was-called").exists(), "a dry run called podman beyond --version")
        self.assertEqual(self.sb.snapshot(), before)

    def test_doctor_does_not_initialise_podman_storage(self) -> None:
        self.sb.fake_podman()
        self.sb.run([str(ROOT / "bin" / "c101-linux-doctor"), "--skip-c101", "--profile", "full"])
        self.assertFalse((self.sb.home / "podman-was-called").exists(), "the doctor ran podman info for a user who never used podman")
        self.assertFalse((self.sb.home / ".local" / "share" / "containers").exists())

    def test_dry_run_passes_dry_run_and_flags_to_c101(self) -> None:
        res = self.sb.run(installer_args("--dry-run", "--profile", "standard", "--packs", "rust,go", "--source", str(self.stub)))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("STUB stack.install --user --main-agent --packs rust,go --dry-run", res.stdout)
        self.assertFalse((self.stub / "calls.log").exists(), "install.sh itself must not run in a dry run")

    def test_dry_run_without_a_venv_only_prints_the_command(self) -> None:
        bare = make_stub_c101(self.sb.base / "bare", with_venv=False)
        res = self.sb.run(installer_args("--dry-run", "--profile", "minimal", "--source", str(bare)))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("would run: bash %s/install.sh --user --packs core" % bare.resolve(), res.stdout)
        self.assertFalse((bare / ".venv").exists())

    def test_full_profile_plans_the_podman_layer(self) -> None:
        res = self.sb.run(installer_args("--dry-run", "--profile", "full", "--source", str(self.stub), "--mcp", "tavily"))
        self.assertEqual(res.returncode, 0, res.stderr)
        out = res.stdout
        self.assertIn("--enforce-models", out)
        self.assertIn("--mcp tavily", out)
        self.assertIn("Rootless Podman", out)
        self.assertIn("desktop-sandbox image", out)
        self.assertIn("c101-podman", out)
        self.assertIn("Playwright system libraries", out)

    def test_unsupported_distro_gets_community_desktop_guidance_without_install_desktop(self) -> None:
        fixtures = ROOT / "tests" / "fixtures"
        res = self.sb.run(installer_args("--dry-run", "--install-cli", "--source", str(self.stub)),
                          C101_LINUX_OS_RELEASE=str(fixtures / "os-release-fedora-41"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("Nothing is downloaded for these routes", res.stdout)
        self.assertIn("claude-desktop-extra", res.stdout)

    def test_podman_is_the_sandbox_default_wherever_the_profile_block_is_written(self) -> None:
        for profile, expected in (("standard", True), ("headless", True), ("full", True), ("minimal", False), ("ci", False)):
            res = self.sb.run(installer_args("--dry-run", "--profile", profile, "--source", str(self.stub)))
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(".local/bin/c101-podman" in res.stdout, expected, profile)

    def test_not_a_c101_checkout_is_rejected(self) -> None:
        empty = self.sb.base / "empty"
        empty.mkdir()
        res = self.sb.run(installer_args("--dry-run", "--source", str(empty)))
        self.assertEqual(res.returncode, 1)
        self.assertIn("not a c101 checkout", res.stderr)

    def test_adobe_is_refused_with_the_alternative(self) -> None:
        res = self.sb.run(installer_args("--adobe", "--dry-run", "--source", str(self.stub)))
        self.assertEqual(res.returncode, 2)
        self.assertIn("not available on Linux", res.stderr)
        self.assertIn("desktop-sandbox", res.stderr)

    def test_unwritable_source_is_explained(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        self.sb.fake_command("uv", 'echo "uv 0.0.0"')  # keep the test off the network
        ro = make_stub_c101(self.sb.base / "ro")
        ro.chmod(0o555)
        self.addCleanup(lambda: ro.chmod(0o755))
        res = self.sb.run(installer_args("--profile", "ci", "--source", str(ro)))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("not writable", res.stderr)


if __name__ == "__main__":
    unittest.main()
