"""Distro-family detection from os-release fixtures, Desktop support rules and version comparison."""
from __future__ import annotations

import unittest
from pathlib import Path

from helpers import Sandbox, bash_lib, needs_linux

FIX = Path(__file__).resolve().parent / "fixtures"

CASES = [
    ("ubuntu-24.04", "apt", "none"),
    ("ubuntu-22.04", "apt", "none"),
    ("debian-12", "apt", "none"),
    ("mint-22", "apt", "none"),
    ("fedora-41", "dnf", "none"),
    ("silverblue-41", "dnf", "ostree"),
    ("rocky-9", "dnf", "none"),
    ("opensuse-leap", "zypper", "none"),
    ("arch", "pacman", "none"),
    ("steamos", "pacman", "steamos"),
    ("alpine-3.20", "apk", "none"),
    ("nixos", "nix", "nixos"),
    ("void", "unknown", "none"),
]


@needs_linux
class DetectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)

    def test_family_and_immutability(self) -> None:
        for name, family, immutable in CASES:
            res = bash_lib(self.sb, 'detect_os; printf "%s %s" "$OS_FAMILY" "$IMMUTABLE"',
                           C101_LINUX_OS_RELEASE=str(FIX / ("os-release-" + name)), C101_LINUX_OSTREE_BOOTED="0")
            self.assertEqual(res.stdout.strip(), "%s %s" % (family, immutable), name)

    def test_arch_mapping(self) -> None:
        for raw, want in (("x86_64", "amd64"), ("aarch64", "arm64"), ("armv7l", "armv7l")):
            res = bash_lib(self.sb, 'detect_os; printf %s "$ARCH"', C101_LINUX_UNAME_M=raw,
                           C101_LINUX_OS_RELEASE=str(FIX / "os-release-debian-12"))
            self.assertEqual(res.stdout, want)

    def test_desktop_official_support(self) -> None:
        def support(osid: str, ver: str, family: str = "apt", arch: str = "amd64", libc: str = "glibc", glibc: str = "2.39") -> str:
            snippet = ('OS_ID=%s OS_VERSION=%s OS_FAMILY=%s ARCH=%s LIBC=%s GLIBC_VERSION=%s; desktop_official_support'
                       % (osid, ver, family, arch, libc, glibc))
            return bash_lib(self.sb, snippet).stdout.strip()
        self.assertEqual(support("ubuntu", "24.04"), "yes")
        self.assertEqual(support("ubuntu", "22.04", glibc="2.35"), "yes")
        self.assertEqual(support("debian", "12", glibc="2.36"), "yes")
        self.assertIn("22.04", support("ubuntu", "20.04", glibc="2.35"))
        self.assertIn("2.34", support("linuxmint", "20", glibc="2.31"))
        self.assertIn("Debian 12", support("debian", "11", glibc="2.36"))
        self.assertIn("family", support("fedora", "41", family="dnf"))
        self.assertIn("glibc", support("ubuntu", "24.04", libc="musl", glibc=""))
        self.assertIn("amd64", support("ubuntu", "24.04", arch="riscv64"))

    def test_version_ge(self) -> None:
        cases = [("18.19.1", "18", 0), ("22", "18", 0), ("12.22.9", "18", 1), ("2.34", "2.34", 0),
                 ("2.9", "2.34", 1), ("24.04", "22.04", 0), ("1.10", "1.9", 0)]
        for a, b, rc in cases:
            res = bash_lib(self.sb, 'version_ge "%s" "%s"' % (a, b))
            self.assertEqual(res.returncode, rc, (a, b))

    def test_login_shell_override(self) -> None:
        res = bash_lib(self.sb, 'detect_login_shell; printf %s "$LOGIN_SHELL"', C101_LINUX_LOGIN_SHELL="/usr/bin/zsh")
        self.assertEqual(res.stdout, "zsh")
        res = bash_lib(self.sb, 'detect_login_shell; printf %s "$LOGIN_SHELL"', C101_LINUX_LOGIN_SHELL="/usr/bin/fish")
        self.assertEqual(res.stdout, "fish")
        res = bash_lib(self.sb, 'detect_login_shell; printf %s "$LOGIN_SHELL"', C101_LINUX_LOGIN_SHELL="/bin/dash")
        self.assertEqual(res.stdout, "other")


if __name__ == "__main__":
    unittest.main()
