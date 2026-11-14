"""MCP pre-warm and the user-level Playwright browser (lib/prewarm.sh; plan decisions D5 and R1).

What is pinned down here, all with argv-logging stubs of timeout, uvx, npx, npm and node (no network, no real uv or npm):
  * every child runs as `timeout 300 env [-u KEY]... CMD ARGS...` with stdin from /dev/null;
  * fetch and neural-memory (the version is read from c101's bin/neural-memory-mcp) are pre-warmed whenever PREWARM_MCP=1,
    even in minimal where PLAYWRIGHT_BROWSER=0; @playwright/mcp only when PLAYWRIGHT_BROWSER=1; markitdown only when listed;
  * a failure, a timeout or a missing tool only warns (exit 0); a missing uv warns and names uv;
  * a dry run starts no uvx, npx or npm;
  * key variables (FAL_*, IMAGE_*, *_API_KEY, ...) never reach a child (D6);
  * the Chromium install follows the R1 decision (npm view, then `install --no-shell chromium`, never --with-deps).
Run on Linux: tests/run-tests.sh -k prewarm
"""
from __future__ import annotations

import re
import shlex
import subprocess
import unittest
from typing import Any, Dict, List, Tuple

from helpers import ROOT, SECRET_SENTINEL, ArgvStub, Sandbox, build_stub_c101, needs_linux, rule

NM_PIN = "9.8.7"  # deliberately not helpers.NM_VERSION: proves the pin is read from c101's file, not known to the overlay
PW_VERSION = "1.99.0-alpha-2099-01-01"  # what the npm stub answers for `npm view @playwright/mcp@latest dependencies.playwright-core`

# Names the pre-warm must scrub (D6: the servers read their keys from ~/.claude/stack.env only). Values are the sentinel.
SECRET_NAMES: Tuple[str, ...] = (
    "FAL_KEY", "IMAGE_GEN_KEY", "IMAGE_GEN_URL", "IMAGE_GEN_MODEL", "EXA_API_KEY", "JINA_API_KEY", "SPIDER_API_KEY",
    "CONTEXT7_API_KEY", "FIRECRAWL_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY",
)
CONTROL = "C101L_PREWARM_CONTROL"  # a harmless variable that must still reach the child: proves the env check can see names
WATCHED: Tuple[str, ...] = SECRET_NAMES + (CONTROL,)

FETCH = ["uvx", "--python", "3.13", "mcp-server-fetch", "--help"]
NEURAL = ["uvx", "--python", "3.13", "--from", "neural-memory==%s" % NM_PIN, "nmem-mcp", "--help"]
MARKITDOWN = ["uvx", "--python", "3.13", "markitdown-mcp", "--help"]
PLAYWRIGHT = ["npx", "-y", "@playwright/mcp@latest", "--help"]
PW_VIEW = ["npm", "view", "@playwright/mcp@latest", "dependencies.playwright-core"]
PW_INSTALL = ["npx", "-y", "playwright-core@%s" % PW_VERSION, "install", "--no-shell", "chromium"]

HOST = "ARCH=amd64\nLIBC=glibc\nOS_ID=ubuntu\nOS_VERSION=24.04\nOS_FAMILY=apt\n"  # what detect_all would set on Ubuntu 24.04 amd64
LIBS = ("common", "detect", "profiles", "deps", "c101", "desktop", "pathprofile", "services", "prewarm")
REPORT = (
    "printf 'FAILED=%s\\n' \"${PREWARM_FAILED[*]-}\"\n"
    "for _n in ${NEXT_STEPS[@]+\"${NEXT_STEPS[@]}\"}; do printf 'NEXT=%s\\n' \"$_n\"; done\n"
)


def _hide(*names: str) -> str:
    """Shell text redefining `have` as on a host without these tools (the stubs stay on PATH; the overlay cannot see them)."""
    return 'have() { case $1 in %s) return 1 ;; esac; command -v "$1" >/dev/null 2>&1; }\n' % "|".join(names)


STUB_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "uvx": {},
    "npx": {"env_values": ("PLAYWRIGHT_BROWSERS_PATH",)},
    "npm": {"rules": [rule("view", stdout=PW_VERSION + "\n")]},
}


def secret_env() -> Dict[str, str]:
    return {name: SECRET_SENTINEL for name in SECRET_NAMES}


def wrapped(rec: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(variables removed by `env -u`, the command) of one call the timeout stub recorded as `300 env [-u NAME]... CMD ARGS...`."""
    argv = list(rec["argv"])
    if argv[:2] != ["300", "env"]:
        raise AssertionError("child not started as `timeout 300 env ...`: %r" % argv)
    i = 2
    unset: List[str] = []
    while i + 1 < len(argv) and argv[i] == "-u":
        unset.append(argv[i + 1])
        i += 2
    return unset, argv[i:]


def failed_labels(res: subprocess.CompletedProcess) -> List[str]:
    m = re.search(r"(?m)^FAILED=(.*)$", res.stdout)
    if not m:
        raise AssertionError("no FAILED= report line in:\n%s\n%s" % (res.stdout, res.stderr))
    return m.group(1).split()


def next_steps(res: subprocess.CompletedProcess) -> List[str]:
    return re.findall(r"(?m)^NEXT=(.*)$", res.stdout)


class _Fixture:
    """Stubs and a runner shared by the test classes. stdin of the shell is a pipe, so a child that records `devnull`
    got it from the overlay's own `</dev/null`, not from the test runner."""

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)  # type: ignore[attr-defined]
        self.c101 = build_stub_c101(self.sb.base / "c101-src", nm_version=NM_PIN)
        self.timeout = self.sb.argv_stub("timeout")  # runs the command it wraps, from the stub PATH only
        self.node = self.sb.argv_stub("node", rules=[rule("--version", stdout="v20.11.0\n")])
        self.uvx = self.stub("uvx")
        self.npx = self.stub("npx")
        self.npm = self.stub("npm")

    def stub(self, name: str, **kw: Any) -> ArgvStub:
        """(Re)create the stub of uvx, npx or npm; the watched variables stay on unless told otherwise."""
        opts: Dict[str, Any] = {"env_names": WATCHED}
        opts.update(STUB_DEFAULTS.get(name, {}))
        opts.update(kw)
        made = self.sb.argv_stub(name, **opts)
        setattr(self, name, made)
        return made

    def snippet(self, *steps: str, prewarm: int = 1, browser: int = 0, dry: int = 0, c101: bool = True, extra: str = "") -> str:
        lines = [
            HOST.rstrip("\n"), "DRY_RUN=%d" % dry, "P_PREWARM_MCP=%d" % prewarm, "P_PLAYWRIGHT_BROWSER=%d" % browser,
            "P_PLAYWRIGHT_DEPS=0", "OPT_MCP=''",
        ]
        if c101:
            lines.append("C101_SRC=%s" % shlex.quote(str(self.c101)))
        lines.append(extra)
        lines.extend(steps or ("prewarm_step",))
        lines.append(REPORT)
        return "\n".join(lines)

    def run_lib(self, snippet: str, **extra: str) -> subprocess.CompletedProcess:
        script = (
            "set -Eeuo pipefail\n"
            "C101L_ROOT=%s\n" % shlex.quote(str(ROOT))
            + 'C101L_TMP=$(mktemp -d "$TMPDIR/lib.XXXXXX")\n'
            + "".join('. "$C101L_ROOT/lib/%s.sh"\n' % lib for lib in LIBS)
            + snippet + "\n"
        )
        env = self.sb.env(**{CONTROL: "1"}, **extra)
        return subprocess.run(["bash", "-c", script], input="stdin is a pipe, not /dev/null\n", capture_output=True, text=True,
                              env=env, cwd=str(self.sb.base), timeout=120)

    def commands(self) -> List[List[str]]:
        """The commands the pre-warm started, in order, as the timeout stub saw them (after `timeout 300 env [-u ...]`)."""
        return [wrapped(rec)[1] for rec in self.timeout.calls(probes=False)]

    def assert_ok(self, res: subprocess.CompletedProcess) -> None:
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)  # type: ignore[attr-defined]


@needs_linux
class PrewarmTests(_Fixture, unittest.TestCase):
    def test_servers_are_prewarmed_under_timeout_300_with_stdin_from_devnull(self) -> None:
        res = self.run_lib(self.snippet(browser=1))
        self.assert_ok(res)
        self.assertEqual(self.commands(), [FETCH, NEURAL, PLAYWRIGHT])
        self.assertEqual(self.uvx.argvs(probes=False), [FETCH[1:], NEURAL[1:]])
        self.assertEqual(self.npx.argvs(probes=False), [PLAYWRIGHT[1:]])
        for stub in (self.timeout, self.uvx, self.npx):
            for rec in stub.calls(probes=False):
                self.assertEqual(rec["stdin"], "devnull", "%s kept the caller's stdin: %r" % (stub.name, rec["argv"]))
        self.assertEqual(failed_labels(res), [])
        self.assertNotIn("WARN", res.stderr)

    def test_fetch_and_neural_memory_are_prewarmed_in_minimal_without_the_browser(self) -> None:
        res = self.run_lib(self.snippet(prewarm=1, browser=0))
        self.assert_ok(res)
        self.assertEqual(self.commands(), [FETCH, NEURAL])
        self.assertFalse(self.npx.called(probes=False), "playwright is pre-warmed only when PLAYWRIGHT_BROWSER=1")
        self.assertFalse(self.npm.called(probes=False))
        self.assertNotIn("pre-warming playwright", res.stdout)
        self.assertEqual(failed_labels(res), [])

    def test_markitdown_is_prewarmed_only_when_it_is_in_the_mcp_list(self) -> None:
        res = self.run_lib(self.snippet(extra="OPT_MCP=exa,markitdown"))
        self.assert_ok(res)
        self.assertEqual(self.commands(), [FETCH, NEURAL, MARKITDOWN])
        self.uvx.clear()
        self.timeout.clear()
        self.assert_ok(self.run_lib(self.snippet(extra="OPT_MCP=exa")))
        self.assertEqual(self.commands(), [FETCH, NEURAL])

    def test_nothing_runs_when_the_prewarm_is_off(self) -> None:
        res = self.run_lib(self.snippet(prewarm=0, browser=1))
        self.assert_ok(res)
        for stub in (self.timeout, self.uvx, self.npx, self.npm):
            self.assertFalse(stub.called(), "%s ran although PREWARM_MCP=0" % stub.name)
        self.assertNotIn("pre-warm", res.stdout)

    def test_neural_memory_version_comes_from_c101_in_the_home_link(self) -> None:
        self.sb.link_c101(self.c101)  # ~/.claude/stack -> the c101 checkout, as c101's installer makes it
        res = self.run_lib(self.snippet(c101=False))
        self.assert_ok(res)
        self.assertEqual(self.commands(), [FETCH, NEURAL])

    def test_missing_neural_memory_pin_warns_and_fetch_is_still_prewarmed(self) -> None:
        res = self.run_lib(self.snippet(c101=False))  # no C101_SRC and no ~/.claude/stack
        self.assert_ok(res)
        self.assertEqual(self.commands(), [FETCH])
        self.assertRegex(res.stderr, r"(?m)^WARN: .*NM_VERSION")
        self.assertEqual(failed_labels(res), ["neural-memory"])

    def test_a_failing_prewarm_only_warns_and_the_next_server_is_still_tried(self) -> None:
        self.stub("uvx", rules=[rule("mcp-server-fetch", code=1, stderr="download failed\n")])
        res = self.run_lib(self.snippet(browser=1))
        self.assert_ok(res)
        self.assertEqual(self.commands(), [FETCH, NEURAL, PLAYWRIGHT])
        self.assertRegex(res.stderr, r"(?m)^WARN: .*fetch.*failed")
        self.assertIn("download failed", res.stderr, "the tail of the child's output is shown")
        self.assertEqual(failed_labels(res), ["fetch"])
        self.assertTrue(any("--prewarm-mcp" in s for s in next_steps(res)), next_steps(res))

    def test_the_rerun_hint_carries_the_profile_unless_it_is_standard(self) -> None:
        """R1: a hint without --profile would reinstall a minimal, headless or ci host as standard (the installer's default)."""
        self.stub("uvx", rules=[rule("mcp-server-fetch", code=1, stderr="download failed\n")])
        for profile, suffix in (("minimal", " --profile minimal"), ("ci", " --profile ci"), ("standard", "")):
            with self.subTest(profile=profile):
                res = self.run_lib(self.snippet(extra="PROFILE=%s" % profile))
                self.assert_ok(res)
                hints = [s for s in next_steps(res) if "--prewarm-mcp" in s]
                self.assertEqual(len(hints), 1, next_steps(res))
                self.assertTrue(hints[0].endswith("install-linux.sh --prewarm-mcp" + suffix), hints[0])

    def test_a_timed_out_prewarm_is_reported_as_a_timeout_and_only_warns(self) -> None:
        self.stub("uvx", rules=[rule("mcp-server-fetch", code=124)])
        res = self.run_lib(self.snippet())
        self.assert_ok(res)
        self.assertRegex(res.stderr, r"(?m)^WARN: .*timed out after 300 s")
        self.assertEqual(failed_labels(res), ["fetch"])

    def test_missing_uv_warns_naming_uv_and_skips_only_the_uvx_servers(self) -> None:
        res = self.run_lib(self.snippet(browser=1, extra=_hide("uv", "uvx")))
        self.assert_ok(res)
        self.assertRegex(res.stderr, r"(?m)^WARN: .*\buv\b", "the warning must name uv")
        self.assertRegex(res.stderr, r"(?m)^WARN: .*fetch.*neural-memory|^WARN: .*neural-memory.*fetch", "and the servers it affects")
        self.assertFalse(self.uvx.called(), "no uvx without uv")
        self.assertEqual(self.commands(), [PLAYWRIGHT], "npx servers do not need uv")
        self.assertEqual(failed_labels(res), ["fetch", "neural-memory"])

    def test_missing_node_skips_only_the_playwright_prewarm(self) -> None:
        res = self.run_lib(self.snippet(browser=1, extra=_hide("node", "npx", "npm")))
        self.assert_ok(res)
        self.assertEqual(self.commands(), [FETCH, NEURAL])
        self.assertFalse(self.npx.called(probes=False))
        self.assertRegex(res.stderr, r"(?m)^WARN: .*Node")
        self.assertEqual(failed_labels(res), ["playwright"])

    def test_missing_timeout_skips_the_whole_prewarm_with_a_warning(self) -> None:
        res = self.run_lib(self.snippet(browser=1, extra=_hide("timeout")))
        self.assert_ok(res)
        self.assertRegex(res.stderr, r"(?m)^WARN: .*timeout")
        for stub in (self.timeout, self.uvx, self.npx):
            self.assertFalse(stub.called(), "%s started although timeout is missing (an unbounded download)" % stub.name)
        self.assertEqual(failed_labels(res), ["all"])

    def test_dry_run_starts_no_uvx_npx_or_npm(self) -> None:
        before = self.sb.snapshot()
        res = self.run_lib(self.snippet("prewarm_step", "playwright_browser_step", browser=1, dry=1), **secret_env())
        self.assert_ok(res)
        for stub in (self.timeout, self.uvx, self.npx, self.npm, self.node):
            self.assertFalse(stub.called(), "%s was started in a dry run: %r" % (stub.name, stub.argvs()))
        lines = [ln for ln in res.stdout.splitlines() if ln.startswith("dry-run: timeout 300 env")]
        self.assertEqual(len(lines), 5, res.stdout)  # fetch, neural-memory, playwright --help, npm view, install
        for needle in ("mcp-server-fetch", "nmem-mcp", "neural-memory", "playwright", "--no-shell"):
            self.assertTrue(any(needle in ln for ln in lines), "%s missing from the plan:\n%s" % (needle, res.stdout))
        self.assertEqual(self.sb.snapshot(), before, "a dry run must not touch HOME")
        self.sb.assert_no_secret_leak(res)

    def test_key_variables_never_reach_a_child(self) -> None:
        res = self.run_lib(self.snippet("prewarm_step", "playwright_browser_step", browser=1), **secret_env())
        self.assert_ok(res)
        self.assertEqual([len(s.calls(probes=False)) for s in (self.uvx, self.npx, self.npm)], [2, 2, 1],
                         "every stub must have been hit, or the checks below are vacuous")
        for stub in (self.uvx, self.npx, self.npm):
            for rec in stub.calls(probes=False):
                self.assertEqual(rec["env_with_sentinel"], [], "%s %r got a key value in its environment" % (stub.name, rec["argv"]))
                leaked = [n for n in SECRET_NAMES if rec["env_present"][n]]
                self.assertEqual(leaked, [], "%s %r still has %s set" % (stub.name, rec["argv"], leaked))
                self.assertTrue(rec["env_present"][CONTROL], "non-key variables must still reach the child")
        for rec in self.timeout.calls(probes=False):
            unset, _cmd = wrapped(rec)
            self.assertEqual(sorted(set(SECRET_NAMES) - set(unset)), [], "env -u misses a key variable")
        self.sb.assert_no_secret_leak(res, argv_logs=[self.uvx, self.npx, self.npm], check_env=True)


@needs_linux
class PlaywrightBrowserTests(_Fixture, unittest.TestCase):
    def install_calls(self) -> List[Dict[str, Any]]:
        return [rec for rec in self.npx.calls(probes=False) if "install" in rec["argv"]]

    def test_chromium_is_installed_with_the_r1_commands_at_user_level(self) -> None:
        res = self.run_lib(self.snippet("playwright_browser_step", browser=1))
        self.assert_ok(res)
        self.assertEqual(self.commands(), [PW_VIEW, PW_INSTALL], "version lookup, then the install pinned to that version")
        for stub in (self.timeout, self.npm, self.npx):
            for rec in stub.calls(probes=False):
                self.assertEqual(rec["stdin"], "devnull", "%s %r" % (stub.name, rec["argv"]))
                self.assertNotIn("--with-deps", rec["argv"], "system libraries are the root-gated --playwright-deps step")
                self.assertNotIn("install-deps", rec["argv"])
        self.assertIn(PW_VERSION, res.stdout)
        self.assertNotIn("WARN", res.stderr)

    def test_the_browser_step_does_nothing_when_the_browser_is_off(self) -> None:
        res = self.run_lib(self.snippet("playwright_browser_step", browser=0))
        self.assert_ok(res)
        for stub in (self.timeout, self.npm, self.npx):
            self.assertFalse(stub.called(), stub.name)
        self.assertNotIn("Playwright browser", res.stdout)

    def test_the_browsers_path_is_kept_for_the_install_and_reported(self) -> None:
        root = self.sb.base / "pw-browsers"
        (root / "chromium-1234").mkdir(parents=True)
        res = self.run_lib(self.snippet("playwright_browser_step", browser=1), PLAYWRIGHT_BROWSERS_PATH=str(root))
        self.assert_ok(res)
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH is set", res.stdout)
        self.assertIn("chromium-1234", res.stdout)
        (rec,) = self.install_calls()
        self.assertEqual(rec["env_values"].get("PLAYWRIGHT_BROWSERS_PATH"), str(root), "the install must see the same path the MCP will use")

    def test_the_version_is_the_last_line_of_npm_view_without_quotes(self) -> None:
        self.stub("npm", rules=[rule("view", stdout="npm notice a new release is available\n'%s'\n" % PW_VERSION)])
        res = self.run_lib(self.snippet("playwright_browser_step", browser=1))
        self.assert_ok(res)
        self.assertEqual(self.commands(), [PW_VIEW, PW_INSTALL])

    def test_a_failing_version_lookup_skips_the_install_with_a_warning(self) -> None:
        self.stub("npm", rules=[rule("view", code=1, stderr="registry unreachable\n")])
        res = self.run_lib(self.snippet("playwright_browser_step", browser=1))
        self.assert_ok(res)
        self.assertEqual(self.install_calls(), [], "no install without a version to pin")
        self.assertRegex(res.stderr, r"(?m)^WARN: .*playwright-core version")
        self.assertTrue(any("--playwright-browser" in s for s in next_steps(res)), next_steps(res))

    def test_a_failing_install_only_warns_with_a_next_step(self) -> None:
        self.stub("npx", rules=[rule("install", code=1, stderr="download failed\n")])
        res = self.run_lib(self.snippet("playwright_browser_step", browser=1))
        self.assert_ok(res)
        self.assertEqual(len(self.install_calls()), 1)
        self.assertRegex(res.stderr, r"(?m)^WARN: .*Chromium install failed \(exit 1\)")
        self.assertIn("download failed", res.stderr)
        self.assertTrue(any("--playwright-browser" in s for s in next_steps(res)), next_steps(res))

    def test_musl_hosts_get_guidance_and_no_download(self) -> None:
        res = self.run_lib(self.snippet("playwright_browser_step", browser=1, extra="LIBC=musl\nOS_ID=alpine\nOS_FAMILY=apk"))
        self.assert_ok(res)
        for stub in (self.timeout, self.npm, self.npx):
            self.assertFalse(stub.called(), stub.name)
        self.assertRegex(res.stderr, r"(?m)^WARN: .*musl")
        self.assertIn("apk add chromium", res.stdout)
        self.assertIn("PLAYWRIGHT_MCP_EXECUTABLE_PATH", res.stdout)

    def test_arm64_on_a_distro_without_a_playwright_build_is_skipped(self) -> None:
        res = self.run_lib(self.snippet("playwright_browser_step", browser=1, extra="ARCH=arm64\nOS_ID=ubuntu\nOS_VERSION=20.04"))
        self.assert_ok(res)
        for stub in (self.timeout, self.npm, self.npx):
            self.assertFalse(stub.called(), stub.name)
        self.assertRegex(res.stderr, r"(?m)^WARN: .*arm64")


if __name__ == "__main__":
    unittest.main()
