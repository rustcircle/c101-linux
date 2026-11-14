"""Profile resolution and argument handling (install-linux.sh --print-config changes nothing)."""
from __future__ import annotations

import unittest
from typing import Dict

from helpers import Sandbox, installer_args, needs_linux


def resolve(sb: Sandbox, *args: str) -> Dict[str, str]:
    res = sb.run(installer_args("--print-config", *args))
    assert res.returncode == 0, res.stderr
    return dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)


@needs_linux
class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)

    def test_minimal(self) -> None:
        c = resolve(self.sb, "--profile", "minimal")
        self.assertEqual(c["C101_FLAGS"], "--user --packs core")
        self.assertEqual(c["PATH_INTEGRATION"], "0")
        self.assertEqual(c["SANDBOX"], "0")

    def test_standard_is_default(self) -> None:
        c = resolve(self.sb)
        self.assertEqual(c["PROFILE"], "standard")
        self.assertEqual(c["C101_FLAGS"], "--user --main-agent")
        self.assertEqual(c["PATH_INTEGRATION"], "1")
        self.assertEqual(c["CONTAINER_RUNTIME"], "podman")

    def test_full_enables_every_linux_feature(self) -> None:
        c = resolve(self.sb, "--profile", "full")
        self.assertEqual(c["C101_FLAGS"], "--user --main-agent --enforce-models")
        for key in ("SANDBOX", "PLAYWRIGHT_DEPS", "PODMAN_SERVICES", "LINK_BINS", "PATH_INTEGRATION"):
            self.assertEqual(c[key], "1", key)
        self.assertEqual(c["CONTAINER_RUNTIME"], "podman")
        self.assertEqual(c["MANAGED_SETTINGS"], "0", "managed settings stay behind their own flag")

    def test_headless_skips_desktop(self) -> None:
        c = resolve(self.sb, "--profile", "headless")
        self.assertEqual(c["DESKTOP_CHECKS"], "0")
        self.assertEqual(c["ENVIRONMENT_D"], "0")
        self.assertEqual(c["PATH_INTEGRATION"], "1")

    def test_ci_is_unattended_and_writes_no_profile_files(self) -> None:
        c = resolve(self.sb, "--profile", "ci")
        self.assertEqual((c["NO_SUDO"], c["ASSUME_YES"], c["PATH_INTEGRATION"], c["DESKTOP_CHECKS"]), ("1", "1", "0", "0"))

    def test_flag_overrides(self) -> None:
        c = resolve(self.sb, "--profile", "full", "--no-main-agent", "--packs", "rust,go", "--mcp", "tavily,markitdown")
        self.assertEqual(c["C101_FLAGS"], "--user --enforce-models --packs rust,go --mcp tavily,markitdown")
        c = resolve(self.sb, "--profile", "standard", "--enforce-models", "--sandbox", "--container-runtime", "docker")
        self.assertIn("--enforce-models", c["C101_FLAGS"])
        self.assertEqual((c["SANDBOX"], c["CONTAINER_RUNTIME"]), ("1", "docker"))
        c = resolve(self.sb, "--profile=minimal", "--managed-settings", "--no-sudo", "--yes")
        self.assertEqual((c["MANAGED_SETTINGS"], c["NO_SUDO"], c["ASSUME_YES"]), ("1", "1", "1"))

    def test_usage_errors_exit_2(self) -> None:
        for args in (["--profile", "nope"], ["--node", "bogus"], ["--container-runtime", "lxc"], ["--bogus"], ["--packs"]):
            res = self.sb.run(installer_args("--print-config", *args))
            self.assertEqual(res.returncode, 2, (args, res.stderr))

    def test_adobe_and_macos_only_servers_are_refused(self) -> None:
        for args in (["--adobe"], ["--adobe", "photoshop"], ["--no-adobe"], ["--mcp", "photoshop"], ["--mcp", "tavily,peekaboo"]):
            res = self.sb.run(installer_args("--print-config", *args))
            self.assertEqual(res.returncode, 2, (args, res.stdout, res.stderr))
            self.assertIn("Linux", res.stderr)
            self.assertIn("sandbox", res.stderr)


if __name__ == "__main__":
    unittest.main()
