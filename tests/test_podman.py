"""The c101-podman wrapper, the systemd unit files and the container helpers (no podman or systemd needed)."""
from __future__ import annotations

import configparser
import os
import re
import shutil
import unittest
from pathlib import Path

from helpers import ROOT, Sandbox, bash_lib, needs_linux

UNITS = ROOT / "systemd" / "user"
ALLOWED = {
    "Unit": {"Description", "Documentation", "Requires", "After", "Wants", "ConditionPathExists"},
    "Service": {"Type", "ExecStart", "KillMode", "Environment", "TimeoutStartSec", "Nice", "Delegate"},
    "Socket": {"ListenStream", "SocketMode", "DirectoryMode"},
    "Timer": {"OnCalendar", "RandomizedDelaySec", "Persistent", "Unit"},
    "Install": {"WantedBy"},
}


def parse(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str  # type: ignore[assignment]
    cp.read_string(path.read_text(encoding="utf-8"))
    return cp


class UnitFileTests(unittest.TestCase):
    def test_all_units_use_valid_sections_keys_and_specifiers(self) -> None:
        files = sorted(UNITS.iterdir())
        self.assertEqual({f.name for f in files}, {
            "c101-podman-api.socket", "c101-podman-api.service", "c101-sandbox-image.service",
            "c101-sandbox-image.timer", "c101-podman-prune.service", "c101-podman-prune.timer"})
        for f in files:
            text = f.read_text(encoding="utf-8")
            self.assertEqual(text.splitlines()[0], "# c101-linux managed unit", f.name)
            cp = parse(f)
            for section in cp.sections():
                self.assertIn(section, ALLOWED, f.name)
                for key in cp[section]:
                    self.assertIn(key, ALLOWED[section], "%s [%s] %s" % (f.name, section, key))
            for spec in re.findall(r"%.", text):
                self.assertIn(spec, ("%h", "%t", "%u"), "%s uses specifier %s" % (f.name, spec))
            for line in text.splitlines():
                self.assertFalse(line.startswith(" "), "%s: indented line" % f.name)

    def test_install_targets(self) -> None:
        self.assertEqual(parse(UNITS / "c101-podman-api.socket")["Install"]["WantedBy"], "sockets.target")
        for t in ("c101-sandbox-image.timer", "c101-podman-prune.timer"):
            self.assertEqual(parse(UNITS / t)["Install"]["WantedBy"], "timers.target")
        for s in ("c101-podman-api.service", "c101-sandbox-image.service", "c101-podman-prune.service"):
            self.assertNotIn("Install", parse(UNITS / s), "%s is started by its socket/timer, not enabled" % s)

    def test_socket_and_service_pair_up(self) -> None:
        sock = parse(UNITS / "c101-podman-api.socket")
        self.assertEqual(sock["Socket"]["ListenStream"], "%t/podman/podman.sock")
        svc = parse(UNITS / "c101-podman-api.service")
        self.assertEqual(svc["Unit"]["Requires"], "c101-podman-api.socket")
        self.assertIn("podman system service --time=0", svc["Service"]["ExecStart"])
        self.assertEqual(svc["Service"]["Type"], "exec")
        self.assertEqual(svc["Service"]["Delegate"], "true", "containers started through the API need their own cgroup tree")

    def test_timers_point_at_existing_services_and_are_weekly(self) -> None:
        for t in UNITS.glob("*.timer"):
            cp = parse(t)
            self.assertTrue((UNITS / cp["Timer"]["Unit"]).exists(), t.name)
            self.assertEqual(cp["Timer"]["OnCalendar"], "weekly")

    def test_sandbox_unit_path_covers_nixos_and_linuxbrew(self) -> None:
        text = (UNITS / "c101-sandbox-image.service").read_text()
        for needle in ("/run/current-system/sw/bin", "/etc/profiles/per-user/%u/bin", "%h/.nix-profile/bin", "/home/linuxbrew/.linuxbrew/bin"):
            self.assertIn(needle, text)

    def test_oneshots_and_the_sandbox_build(self) -> None:
        build = parse(UNITS / "c101-sandbox-image.service")
        self.assertEqual(build["Service"]["Type"], "oneshot")
        self.assertIn("%h/.claude/stack/bin/desktop-sandbox --build", build["Service"]["ExecStart"])
        self.assertIn("Environment=DESKTOP_SANDBOX_RUNTIME=", (UNITS / "c101-sandbox-image.service").read_text())
        prune_text = (UNITS / "c101-podman-prune.service").read_text()
        self.assertEqual(parse(UNITS / "c101-podman-prune.service")["Service"]["Type"], "oneshot")
        cmds = [ln for ln in prune_text.splitlines() if ln.startswith("ExecStart=")]
        self.assertEqual(len(cmds), 2)
        self.assertIn("podman container prune -f --filter label=dev.c101.desktop-sandbox=1 --filter until=168h", cmds[0])
        self.assertIn("podman image prune -f --filter label=dev.c101.desktop-sandbox.packages", cmds[1])
        for cmd in cmds:
            self.assertNotIn("system prune", cmd, "a bare system prune would delete the user's other stopped containers")
            self.assertNotIn("--volumes", cmd, "the prune unit must never delete volumes")
            self.assertNotIn(" -a", cmd)
            self.assertIn("--filter label=", cmd, "prune only c101-labelled resources")


@needs_linux
class WrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.sb.fake_command("podman", 'printf "%s\\n" "$@"')
        self.wrapper = str(ROOT / "bin" / "c101-podman")

    def args(self, *a: str, **extra: str) -> list:
        res = self.sb.run(["bash", self.wrapper, *a], **extra)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout.split()

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "keep-id is only added for non-root users")
    def test_run_gets_keep_id_for_rootless(self) -> None:
        out = self.args("run", "--detach", "img")
        self.assertEqual(out[0], "run")
        self.assertIn("--userns=keep-id", out)
        self.assertEqual(out[-2:], ["--detach", "img"])

    def test_other_subcommands_pass_through_unchanged(self) -> None:
        self.assertEqual(self.args("image", "inspect", "x"), ["image", "inspect", "x"])
        self.assertEqual(self.args("ps", "--all"), ["ps", "--all"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "non-root only")
    def test_selinux_enforcing_disables_labels_unless_kept(self) -> None:
        self.sb.fake_command("getenforce", "echo Enforcing")
        self.assertIn("label=disable", self.args("run", "img"))
        self.assertNotIn("label=disable", self.args("run", "img", C101_PODMAN_SELINUX="keep"))
        self.sb.fake_command("getenforce", "echo Permissive")
        self.assertNotIn("label=disable", self.args("run", "img"))

    def test_missing_podman_is_a_clear_error(self) -> None:
        if shutil.which("podman", path="/usr/local/bin:/usr/bin:/bin"):
            self.skipTest("a system podman exists; the wrapper would find it")
        (self.sb.bindir / "podman").unlink()
        res = self.sb.run([shutil.which("bash") or "/bin/bash", self.wrapper, "ps"], PATH=str(self.sb.bindir))
        self.assertEqual(res.returncode, 127)
        self.assertIn("podman not found", res.stderr)

    def test_wrapper_never_calls_itself(self) -> None:
        # installed under the name podman in front of the real one: it must skip itself on PATH
        (self.sb.bindir / "podman").unlink()
        real = self.sb.base / "real"
        real.mkdir()
        (real / "podman").write_text('#!/bin/sh\necho REAL "$@"\n')
        (real / "podman").chmod(0o755)
        (self.sb.bindir / "podman").symlink_to(self.wrapper)
        res = self.sb.run(["podman", "ps"], PATH="%s:%s:/usr/bin:/bin" % (self.sb.bindir, real))
        self.assertEqual(res.stdout.strip(), "REAL ps", res.stderr)


@needs_linux
class ContainerHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)

    def test_next_free_subid_starts_after_existing_ranges(self) -> None:
        f = self.sb.base / "subuid"
        f.write_text("alice:100000:65536\nbob:165536:65536\n")
        res = bash_lib(self.sb, 'next_free_subid "%s"' % f)
        self.assertEqual(res.stdout.strip(), "231072")
        res = bash_lib(self.sb, 'next_free_subid "%s"' % (self.sb.base / "none"))
        self.assertEqual(res.stdout.strip(), "100000")

    def test_subid_has_matches_name_or_numeric_uid(self) -> None:
        f = self.sb.base / "subuid"
        f.write_text("alice:100000:65536\n%d:200000:65536\n" % os.getuid())
        self.assertEqual(bash_lib(self.sb, 'subid_has alice "%s"' % f).returncode, 0)
        self.assertEqual(bash_lib(self.sb, 'subid_has carol "%s"' % f).returncode, 0, "numeric uid line should match")
        f.write_text("alice:100000:65536\n")
        self.assertNotEqual(bash_lib(self.sb, 'subid_has carol "%s"' % f).returncode, 0)

    def test_sandbox_runtime_value(self) -> None:
        res = bash_lib(self.sb, 'P_CONTAINER_RUNTIME=podman; OPT_PODMAN_WRAPPER=1; sandbox_runtime_value')
        self.assertEqual(res.stdout.strip(), "%s/.local/bin/c101-podman" % self.sb.home)
        res = bash_lib(self.sb, 'P_CONTAINER_RUNTIME=podman; OPT_PODMAN_WRAPPER=0; sandbox_runtime_value')
        self.assertEqual(res.stdout.strip(), "podman")
        res = bash_lib(self.sb, 'P_CONTAINER_RUNTIME=docker; sandbox_runtime_value')
        self.assertEqual(res.stdout.strip(), "docker")

    def test_install_unit_rewrites_the_runtime_line(self) -> None:
        res = bash_lib(self.sb, 'install_unit c101-sandbox-image.service docker', C101L_STATE_FILE=str(self.sb.base / "s.json"))
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        unit = self.sb.home / ".config" / "systemd" / "user" / "c101-sandbox-image.service"
        self.assertIn("Environment=DESKTOP_SANDBOX_RUNTIME=docker", unit.read_text())
        self.assertTrue(unit.read_text().startswith("# c101-linux managed unit"))
        res = bash_lib(self.sb, 'install_unit c101-sandbox-image.service docker', C101L_STATE_FILE=str(self.sb.base / "s.json"))
        self.assertIn("already current", res.stdout)

    def test_deferred_root_commands_survive_the_run(self) -> None:
        if os.getuid() == 0:
            self.skipTest("root runs the command instead of deferring it")
        snippet = ('NO_SUDO=1\nsrc=$(tmp_file key)\necho payload >"$src"\n'
                   'install_if_different "$src" /nonexistent-dir/dest 0644\nprint_manual_and_next')
        res = bash_lib(self.sb, snippet, C101L_STATE_FILE=str(self.sb.base / "s.json"))
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        script = self.sb.base / "pending" / "pending-root-commands.sh"
        self.assertTrue(script.exists(), res.stdout)
        text = script.read_text()
        staged = self.sb.base / "pending" / "dest"
        self.assertIn(str(staged), text, "the deferred command must point at a persistent copy")
        self.assertIn("/nonexistent-dir/dest", text)
        self.assertEqual(staged.read_text().strip(), "payload")
        self.assertNotIn(str(self.sb.tmpdir), text, "no reference to the run's temp dir")

    def test_cgroup_missing_controllers(self) -> None:
        f = self.sb.base / "cgroup.controllers"
        f.write_text("memory pids\n")
        res = bash_lib(self.sb, "cgroup_missing_controllers", C101_LINUX_CGROUP_FILE=str(f))
        self.assertEqual(res.stdout.strip(), "cpu")
        f.write_text("cpuset cpu io memory pids\n")
        self.assertEqual(bash_lib(self.sb, "cgroup_missing_controllers", C101_LINUX_CGROUP_FILE=str(f)).stdout.strip(), "")
        self.assertEqual(bash_lib(self.sb, "cgroup_missing_controllers", C101_LINUX_CGROUP_FILE=str(self.sb.base / "none")).stdout.strip(), "")

    def test_cgroup_delegation_dropin_is_planned_and_staged(self) -> None:
        f = self.sb.base / "cgroup.controllers"
        f.write_text("memory pids\n")
        res = bash_lib(self.sb, "DRY_RUN=1\nensure_cgroup_delegation", C101_LINUX_CGROUP_FILE=str(f))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("user@.service.d/delegate.conf", res.stdout)
        self.assertIn("systemctl daemon-reload", res.stdout)
        if os.getuid() != 0:
            res = bash_lib(self.sb, "NO_SUDO=1\nensure_cgroup_delegation\nprint_manual_and_next",
                           C101_LINUX_CGROUP_FILE=str(f), C101L_STATE_FILE=str(self.sb.base / "s.json"))
            staged = self.sb.base / "pending" / "delegate.conf"
            self.assertEqual(staged.read_text(), "[Service]\nDelegate=cpu cpuset io memory pids\n")

    def test_docker_host_decision(self) -> None:
        pre = ("docker() { case $1 in --version) echo 'Docker version 27.0.0' ;; context) echo \"${FAKE_CTX:-default}\" ;; esac; }\n"
               "systemctl() { return 3; }\nSYSTEMD_USER_OK=1\nENV_DOCKER_API=0\n")

        def decide(mode: str, extra: str = "", **env: str) -> str:
            res = bash_lib(self.sb, pre + extra + "\ndocker_host_decision %s\nprintf %%s \"$ENV_DOCKER_API\"" % mode,
                           C101_LINUX_DOCKER_SOCKETS="/nonexistent", **env)
            self.assertEqual(res.returncode, 0, res.stderr)
            return res.stdout.strip().splitlines()[-1]
        self.assertEqual(decide("auto"), "1", "no Docker daemon, default context: safe to point at podman")
        self.assertEqual(decide("auto", FAKE_CTX="desktop-linux"), "0", "a non-default docker context is in use")
        self.assertEqual(decide("auto", "DOCKER_HOST=unix:///var/run/docker.sock"), "0", "the user's own DOCKER_HOST")
        self.assertEqual(decide("auto", "DOCKER_HOST=unix:///run/user/1000/podman/podman.sock"), "1")
        self.assertEqual(decide("auto", "SYSTEMD_USER_OK=0"), "0", "no API unit without systemd")
        self.assertEqual(decide("1", FAKE_CTX="desktop-linux"), "1", "explicit choice wins")
        self.assertEqual(decide("0"), "0")
        shim = pre.replace("Docker version 27.0.0", "podman version 5.0.0")
        res = bash_lib(self.sb, shim + 'FAKE_CTX=other\ndocker_host_decision auto\nprintf %s "$ENV_DOCKER_API"',
                       C101_LINUX_DOCKER_SOCKETS="/nonexistent")
        self.assertEqual(res.stdout.strip().splitlines()[-1], "1", "podman's own docker shim is not a Docker daemon")

    def test_nixos_preflight(self) -> None:
        no_uv = "have() { return 1; }\nIMMUTABLE=nixos\n"
        res = bash_lib(self.sb, no_uv + "DRY_RUN=0\nnixos_check")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("programs.nix-ld.enable = true", res.stderr)
        self.assertIn("virtualisation.podman", res.stderr)
        res = bash_lib(self.sb, no_uv + "DRY_RUN=1\nnixos_check")
        self.assertEqual(res.returncode, 0)
        self.assertIn("a real run would stop here", res.stdout)
        res = bash_lib(self.sb, no_uv + "DRY_RUN=0\nnixos_check", C101L_SKIP_NIXOS_CHECK="1")
        self.assertEqual(res.returncode, 0)
        res = bash_lib(self.sb, no_uv + "DRY_RUN=0\nensure_uv")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("nixpkgs", res.stderr, "astral's binary must not be fetched on NixOS")

    def test_wrapper_selinux_opt_out_file(self) -> None:
        if os.getuid() == 0:
            self.skipTest("keep-id is only added for non-root users")
        sb = Sandbox()
        self.addCleanup(sb.close)
        sb.fake_command("podman", 'printf "%s\\n" "$@"')
        sb.fake_command("getenforce", "echo Enforcing")
        conf = sb.home / ".config" / "c101-linux"
        conf.mkdir(parents=True)
        (conf / "podman-selinux").write_text("keep\n")
        res = sb.run(["bash", str(ROOT / "bin" / "c101-podman"), "run", "img"])
        self.assertNotIn("label=disable", res.stdout)
        (conf / "podman-selinux").unlink()
        res = sb.run(["bash", str(ROOT / "bin" / "c101-podman"), "run", "img"])
        self.assertIn("label=disable", res.stdout)

    def test_manual_api_command_for_non_systemd(self) -> None:
        res = bash_lib(self.sb, "manual_api_command")
        self.assertIn("podman system service --time=0", res.stdout)
        self.assertIn("podman.sock", res.stdout)


if __name__ == "__main__":
    unittest.main()
