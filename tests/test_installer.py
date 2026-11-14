"""install-linux.sh end to end WITHOUT changes: help, refusals, dry run with a stub c101 and a throw-away HOME."""
from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from pathlib import Path
from typing import Dict, List

from helpers import (FAL_OP_TABLE, FAL_URL, ROOT, SECRET_SENTINEL, Sandbox, backups_of, bash_lib, build_stub_c101, env_file_allowances,
                     fal_preset_values, file_mode, installer_args, make_stub_c101, needs_linux, read_stack_env, rule, sentinel_for,
                     write_stack_env)

PROFILES = ("minimal", "standard", "full", "headless", "ci")
KEY_NAMES = ("FAL_KEY", "EXA_API_KEY", "JINA_API_KEY", "SPIDER_API_KEY", "CONTEXT7_API_KEY")
KEY_ENV: Dict[str, str] = {name: sentinel_for(name) for name in KEY_NAMES}  # every key variable, each its own sentinel
IMAGE_PACKS = "core,image,graphic-design"  # what the fal backend makes of the minimal and ci profiles (D3)
# plan.md table v7: (PREWARM_MCP, PLAYWRIGHT_BROWSER, LEARN_TIMER) per profile
STEPS = {"minimal": ("1", "0", "0"), "standard": ("1", "1", "1"), "full": ("1", "1", "1"), "headless": ("1", "1", "1"),
         "ci": ("0", "0", "0")}


def run_detached(sb: Sandbox, args: List[str], **extra: str) -> subprocess.CompletedProcess:
    """Sandbox.run with stdin from /dev/null, so no key prompt or preset menu can ever wait on the terminal running the tests."""
    return subprocess.run(args, capture_output=True, text=True, env=sb.env(**extra), cwd=str(sb.base), timeout=120,
                          stdin=subprocess.DEVNULL)


def probing_stub(root: Path) -> Path:
    """make_stub_c101 whose installer entry points also report, by NAME, which environment variables of theirs hold the secret
    sentinel: stack.install appends a line to env-probe.txt (the dry-run path, run from the stub's .venv), install.sh writes
    how many to sentinel-vars.txt (the real path). A line or file with nothing in it proves no key reached c101's installer."""
    stub = make_stub_c101(root)
    (stub / "stack" / "install.py").write_text(
        "import os, sys\n"
        "print('STUB stack.install ' + ' '.join(sys.argv[1:]))\n"
        "names = sorted(k for k, v in os.environ.items() if %r in v)\n"
        "with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'env-probe.txt'), 'a') as fh:\n"
        "    fh.write(','.join(names) + '\\n')\n" % SECRET_SENTINEL, encoding="utf-8")
    install = stub / "install.sh"
    install.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$(dirname \"$0\")/calls.log\"\n"
        "env | grep -c SENTINEL > \"$(dirname \"$0\")/sentinel-vars.txt\"\n"
        "echo \"STUB install.sh $*\"\n", encoding="utf-8")
    install.chmod(0o755)
    return stub


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
                     "--docker-shim", "--enable-linger", "--link-bins",
                     # the parity flags (plan.md D2: 11 new names; --force is not one of them)
                     "--keys", "--keys-from-env", "--keys-only", "--keys-overwrite", "--image-backend", "--fal-mcp", "--preset",
                     "--pick-preset", "--prewarm-mcp", "--playwright-browser", "--learn-timer"):
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
        # a --packs list replaces the profile's (D9) and the fal backend adds the image packs to it
        self.assertIn("STUB stack.install --user --main-agent --packs core,rust,go,image,graphic-design --dry-run", res.stdout)
        self.assertFalse((self.stub / "calls.log").exists(), "install.sh itself must not run in a dry run")

    def test_dry_run_without_a_venv_only_prints_the_command(self) -> None:
        bare = make_stub_c101(self.sb.base / "bare", with_venv=False)
        res = self.sb.run(installer_args("--dry-run", "--profile", "minimal", "--source", str(bare)))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("would run: bash %s/install.sh --user --packs %s" % (bare.resolve(), IMAGE_PACKS), res.stdout)
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

    def test_mcp_fal_and_fal_oauth_are_refused_and_name_the_lazy_routes(self) -> None:
        """R2: c101 registers `fal` and `fal-oauth` at user scope, so fal's ~60 tools would load in every session; the hosted fal MCP
        is installed by default but loaded only through the fal-runner agent or `claude --mcp-config`. Other servers pass."""
        for names in ("fal", "fal-oauth", "tavily,fal", "fal-oauth,markitdown"):
            with self.subTest(mcp=names):
                res = self.sb.run(installer_args("--dry-run", "--mcp", names, "--source", str(self.stub)))
                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
                self.assertIn("fal-runner", res.stderr)
                self.assertIn("claude --mcp-config ~/.config/c101/fal-mcp.json", res.stderr)
                self.assertNotIn("STUB", res.stdout, "c101's installer must not start")
        self.assertIn("--mcp tavily,markitdown", self.dry("--mcp", "tavily,markitdown").stdout)

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

    # --- parity: keys, the image backend, the fal MCP agent, pre-warm ------------------------------------------------

    def dry(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        res = run_detached(self.sb, installer_args("--dry-run", "--source", str(self.stub), *args), **env)
        self.assertEqual(res.returncode, 0, "%s:\n%s\n%s" % (args, res.stdout, res.stderr))
        return res

    def fal_warnings(self, res: subprocess.CompletedProcess) -> List[str]:
        return [ln for ln in res.stderr.splitlines() if ln.startswith("WARN:") and "FAL_KEY" in ln]

    def c101_args(self, res: subprocess.CompletedProcess) -> List[str]:
        """The flags c101's installer was asked to run with (the stub prints them; --dry-run is the overlay's own addition)."""
        m = re.search(r"(?m)^STUB stack\.install (.*) --dry-run$", res.stdout)
        self.assertIsNotNone(m, res.stdout)
        return (m.group(1) if m else "").split()

    def test_help_documents_the_negations_and_where_keys_live(self) -> None:
        res = self.sb.run([str(ROOT / "install-linux.sh"), "--help"])
        self.assertEqual(res.returncode, 0, res.stderr)
        for text in ("--fal-mcp | --no-fal-mcp", "--prewarm-mcp | --no-prewarm-mcp",
                     "--playwright-browser | --no-playwright-browser", "--learn-timer | --no-learn-timer"):
            self.assertIn(text, res.stdout)
        for word in ("FAL_KEY", "stack.env", "exa, jina, spider, fetch and context7", "never touches stack.env"):
            self.assertIn(word, res.stdout)

    def test_a_dry_run_with_keys_from_env_writes_nothing_and_prints_names_only(self) -> None:
        before = self.sb.snapshot()
        res = self.dry("--keys-from-env", "--profile", "standard", **KEY_ENV)
        self.assertEqual(self.sb.snapshot(), before, "a dry run must not create stack.env, state or anything else")
        self.assertFalse(self.sb.stack_env.exists())
        self.assertEqual(list(self.sb.tmpdir.iterdir()), [], "temp files must be cleaned up")
        self.assertIn("from the environment:", res.stdout)
        for name in ("FAL_KEY", "EXA_API_KEY", "JINA_API_KEY", "SPIDER_API_KEY"):
            self.assertIn("would set %s" % name, res.stdout)
        self.assertEqual(self.fal_warnings(res), [], "FAL_KEY is in the environment: nothing to warn about")
        self.sb.assert_no_secret_leak(res)

    def test_keys_in_the_environment_reach_no_file_no_argv_and_no_child_in_a_dry_run(self) -> None:
        stub = probing_stub(self.sb.base / "probe")
        probe = stub / "env-probe.txt"
        for profile in PROFILES:
            before = self.sb.snapshot()
            res = run_detached(self.sb, installer_args("--dry-run", "--keys-from-env", "--profile", profile, "--source", str(stub)), **KEY_ENV)
            self.assertEqual(res.returncode, 0, "%s:\n%s\n%s" % (profile, res.stdout, res.stderr))
            self.assertEqual(self.sb.snapshot(), before, "dry run changed HOME for profile %s" % profile)
            self.assertEqual(list(self.sb.tmpdir.iterdir()), [], profile)
            self.assertIn("would set FAL_KEY", res.stdout, profile)
            # nothing the dry run printed or ran holds a value; with nothing written there is no environment.d file,
            # login block, unit, desktop config, settings.json or state that could hold one either
            self.sb.assert_no_secret_leak(res)
        for rel in (".config", ".claude/settings.json", ".local/state", ".profile", ".bash_profile"):
            self.assertFalse((self.sb.home / rel).exists(), rel)
        lines = probe.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), len(PROFILES), "c101's dry run ran once per profile: the probe saw every run")
        self.assertEqual(set(lines), {""}, "no key variable was in the environment of c101's installer")

    def test_an_unattended_run_without_a_fal_key_warns_once_and_names_the_fix(self) -> None:
        for args in (("--profile", "standard", "--yes"), ("--profile", "ci")):
            res = self.dry(*args)
            warns = self.fal_warnings(res)
            self.assertEqual(len(warns), 1, "%s: one warning about FAL_KEY, not one per step:\n%s" % (args, res.stderr))
            self.assertIn("FAL_KEY is not set", warns[0])
            self.assertIn("install-linux.sh --keys --keys-only", warns[0], "D14: the fix re-runs the key step only")
            self.assertNotIn("would ask", res.stdout, "an unattended run never prompts")
            self.assertRegex(res.stdout, r"(?m)^  - fal image generation .* needs FAL_KEY", "also in the closing next steps")
            self.sb.assert_no_secret_leak(res)
        res = self.dry("--profile", "standard", "--yes")
        self.assertIn("not interactive", res.stdout)

    def test_a_present_fal_key_silences_the_warning_and_none_never_warns(self) -> None:
        res = self.dry("--profile", "ci", FAL_KEY=sentinel_for("FAL_KEY"))
        self.assertEqual(self.fal_warnings(res), [])
        res = self.dry("--profile", "ci", "--image-backend", "none")
        self.assertEqual(self.fal_warnings(res), [], "--image-backend none opts out of fal: nothing is missing")
        self.assertIn("image-backend=none", res.stdout)

    def test_fal_key_hints_give_the_exact_keys_command_and_unattended_ones_add_the_env_form(self) -> None:
        """D13 (the user's literal D3 instruction, over T3 note 1) and D14 (QC-2): every FAL_KEY hint carries the exact
        re-run command `install-linux.sh --keys --keys-only` (the literal `install-linux.sh --keys`, hidden prompt, run
        from a terminal, limited to the key step so it never re-applies profile defaults; only --profile and
        --image-backend may follow, D12). A ci or --yes run cannot prompt, so the SAME single warning and the closing next
        step also give the unattended form: export FAL_KEY, then `install-linux.sh --keys-only --keys-from-env` (as the
        doctor's ci hint does). An interactive profile prints the terminal command alone."""
        bare_keys = r"--keys(?![-\w])"  # the interactive flag itself, not --keys-only or --keys-from-env
        # the exact command: `install-linux.sh --keys --keys-only`, then at most --profile and --image-backend, then no
        # other flag
        exact = r"install-linux\.sh --keys --keys-only(?: --profile \S+)?(?: --image-backend \S+)?(?! --)"
        for args in (("--profile", "ci"), ("--profile", "standard", "--yes")):
            res = self.dry(*args)
            warns = self.fal_warnings(res)
            self.assertEqual(len(warns), 1, "%s:\n%s" % (args, res.stderr))
            self.assertIn("export FAL_KEY", warns[0], args)
            self.assertIn("install-linux.sh --keys-only --keys-from-env", warns[0], args)
            self.assertRegex(warns[0], bare_keys, args)
            self.assertRegex(warns[0], exact, args)
            steps = [ln for ln in res.stdout.splitlines() if ln.startswith("  - fal image generation")]
            self.assertEqual(len(steps), 1, res.stdout)
            self.assertIn("--keys-from-env", steps[0], args)
            self.assertIn("export FAL_KEY", steps[0], args)
            self.assertIn("install-linux.sh --keys-only --keys-from-env", steps[0], args)
            self.assertRegex(steps[0], bare_keys, args)
            self.assertRegex(steps[0], exact, args)
            self.sb.assert_no_secret_leak(res)
        res = self.dry("--profile", "standard")  # stdin is not a terminal, but nothing says unattended: the D3 wording
        warns = self.fal_warnings(res)
        self.assertEqual(len(warns), 1, res.stderr)
        self.assertTrue(warns[0].endswith("install-linux.sh --keys --keys-only"), warns[0])
        self.assertNotIn("--keys-from-env", warns[0], "an interactive run gives the terminal command alone")
        self.assertRegex(res.stdout, r"(?m)^  - fal image generation .* needs FAL_KEY: .*install-linux\.sh --keys --keys-only asks for it")

    def test_c101_never_gets_fal_or_a_research_server_in_its_mcp_list(self) -> None:
        banned = {"fal", "exa", "jina", "spider", "fetch", "context7"}
        for profile in PROFILES:
            args = self.c101_args(self.dry("--profile", profile))
            self.assertNotIn("--mcp", args, "%s: c101 registers its research servers itself; the overlay adds no list" % profile)
            args = self.c101_args(self.dry("--profile", profile, "--mcp", "tavily,markitdown"))
            listed = args[args.index("--mcp") + 1].split(",")
            self.assertEqual(listed, ["tavily", "markitdown"], profile)
            self.assertFalse(banned & set(listed), profile)

    def test_c101_gets_the_image_packs_exactly_where_the_profile_table_says(self) -> None:
        for profile in PROFILES:
            args = self.c101_args(self.dry("--profile", profile))
            if profile in ("minimal", "ci"):
                self.assertEqual(args[args.index("--packs") + 1], IMAGE_PACKS, profile)
            else:
                self.assertNotIn("--packs", args, "%s installs every pack" % profile)

    def test_every_profile_plans_the_fal_runner_agent_unless_opted_out(self) -> None:
        agent = re.compile(r"dry-run: ln -sfn -- .*agents/fal-runner\.md .*\.claude/agents/fal-runner\.md")
        config = re.compile(r"dry-run: install -m 0644 -- .*config/fal-mcp\.json .*c101/fal-mcp\.json")
        for profile in PROFILES:
            res = self.dry("--profile", profile)
            self.assertRegex(res.stdout, agent, profile)
            self.assertRegex(res.stdout, config, profile)
            self.assertIn("fal-mcp=1", res.stdout, profile)
            res = self.dry("--profile", profile, "--no-fal-mcp")
            self.assertNotIn("fal-runner", res.stdout, "--no-fal-mcp plans no agent link (%s)" % profile)
            self.assertNotIn("fal-mcp.json", res.stdout, profile)
            self.assertIn("fal-mcp=0", res.stdout, profile)

    def test_the_profile_key_alone_decides_whether_the_fal_runner_gets_linked(self) -> None:
        link = self.sb.home / ".claude" / "agents" / "fal-runner.md"
        shipped = os.path.realpath(str(ROOT / "agents" / "fal-runner.md"))
        for profile in PROFILES:
            if link.is_symlink():
                link.unlink()
            snippet = '. "$C101L_ROOT/lib/falmcp.sh"\nprofile_load %s\nfal_mcp_install "$P_FAL_MCP"\n' % profile
            res = bash_lib(self.sb, snippet, C101L_STATE_FILE=str(self.sb.base / ("state-%s.json" % profile)))
            self.assertEqual(res.returncode, 0, "%s:\n%s\n%s" % (profile, res.stdout, res.stderr))
            self.assertTrue(link.is_symlink(), "%s: FAL_MCP=1 in every profile (D8)" % profile)
            self.assertEqual(os.readlink(link), shipped, profile)
            self.assertTrue((self.sb.home / ".config" / "c101" / "fal-mcp.json").is_file(), profile)
        link.unlink()
        res = bash_lib(self.sb, '. "$C101L_ROOT/lib/falmcp.sh"\nfal_mcp_install 0\n', C101L_STATE_FILE=str(self.sb.base / "state-off.json"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse(link.is_symlink(), "FAL_MCP=0 (--no-fal-mcp) links nothing")

    def test_prewarm_and_browser_steps_follow_their_profile_keys(self) -> None:
        fetch = re.compile(r"dry-run: timeout 300 env (?:-u \S+ )*uvx --python 3\.13 mcp-server-fetch --help")
        playwright = re.compile(r"dry-run: timeout 300 env (?:-u \S+ )*npx -y @playwright/mcp@latest --help")
        for profile, (prewarm, browser, timer) in STEPS.items():
            res = self.dry("--profile", profile)
            self.assertEqual(bool(fetch.search(res.stdout)), prewarm == "1", "%s: fetch pre-warm" % profile)
            self.assertEqual(bool(playwright.search(res.stdout)), browser == "1", "%s: @playwright/mcp pre-warm" % profile)
            m = re.search(r"keys\s+: keys=(\S+) image-backend=(\S+) fal-mcp=(\d) prewarm=(\d) browser=(\d) learn-timer=(\d)", res.stdout)
            self.assertIsNotNone(m, res.stdout)
            if m:
                self.assertEqual(m.group(2), "fal", profile)
                self.assertEqual((m.group(4), m.group(5), m.group(6)), (prewarm, browser, timer), profile)
        # every opt-in step has its own flag in both directions
        self.assertRegex(self.dry("--profile", "ci", "--prewarm-mcp").stdout, fetch)
        self.assertNotRegex(self.dry("--profile", "minimal", "--no-prewarm-mcp").stdout, fetch)
        self.assertRegex(self.dry("--profile", "minimal", "--playwright-browser").stdout, playwright)
        self.assertNotRegex(self.dry("--profile", "standard", "--no-playwright-browser").stdout, playwright)

    def test_desktop_chat_mcp_upgrades_the_old_playwright_entry_in_place(self) -> None:
        cfg = self.sb.home / ".config" / "Claude" / "claude_desktop_config.json"
        cfg.parent.mkdir(parents=True)
        old = {"command": "%s/.claude/stack/bin/mcp-run" % self.sb.home,
               "args": ["npx", "-y", "@playwright/mcp@latest", "--headless", "--isolated", "--caps=vision,pdf,devtools",
                        "--output-dir=%s/.claude/stack-data/playwright" % self.sb.home, "--viewport-size=1280x800"]}
        cfg.write_text(json.dumps({"mcpServers": {"c101-playwright": old}}), encoding="utf-8")
        before = self.sb.snapshot()
        res = self.dry("--profile", "minimal", "--desktop-chat-mcp")
        self.assertIn('updated: ["c101-playwright"]', res.stdout)
        self.assertEqual(self.sb.snapshot(), before, "a dry run leaves the Desktop config as it was")


@needs_linux
class KeysOnlyTests(unittest.TestCase):
    """--keys-only for real (not a dry run): only stack.env and the image_backend state entry change; c101's installer and
    every other step are skipped. Stdin is /dev/null throughout, so nothing can ask for a key."""

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.stub = probing_stub(self.sb.base / "c101")
        self.state_file = self.sb.home / ".local" / "state" / "c101-linux" / "state.json"

    def keys_only(self, *args: str, keys: bool = True, **env: str) -> subprocess.CompletedProcess:
        extra: Dict[str, str] = dict(KEY_ENV) if keys else {}
        extra.update(env)
        return run_detached(self.sb, installer_args("--keys-only", "--profile", "ci", "--source", str(self.stub), *args), **extra)

    def state(self) -> Dict[str, object]:
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def fal_warnings(self, res: subprocess.CompletedProcess) -> List[str]:
        return [ln for ln in res.stderr.splitlines() if ln.startswith("WARN:") and "FAL_KEY" in ln]

    def test_keys_only_seeds_stack_env_and_never_starts_c101s_installer(self) -> None:
        res = self.keys_only()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("--keys-only: only stack.env is touched", res.stdout)
        self.assertIn("created ~/.claude/stack.env (mode 600)", res.stdout)
        self.assertNotIn("STUB", res.stdout, "neither stack.install nor install.sh may run")
        self.assertFalse((self.stub / "calls.log").exists())
        self.assertFalse((self.stub / "env-probe.txt").exists())
        self.assertEqual(file_mode(self.sb.stack_env), 0o600)
        env = read_stack_env(self.sb.stack_env)
        for name, value in fal_preset_values(KEY_ENV["FAL_KEY"]).items():
            self.assertEqual(env.get(name), value, name)
        for name in KEY_NAMES[1:]:
            self.assertEqual(env[name], KEY_ENV[name], name)
        state = self.state()
        self.assertEqual(state["image_backend"], "fal")
        for key in ("c101_source", "c101_flags", "profile", "preset", "packs", "fal_mcp", "learn_timer", "files_installed"):
            self.assertNotIn(key, state, "--keys-only records the image backend and nothing else of an install")
        self.assertFalse((self.sb.home / ".claude" / "agents").exists())
        self.assertFalse((self.sb.home / ".config").exists())
        self.assertFalse((self.sb.home / ".local" / "bin").exists())
        self.assertEqual(list(self.sb.tmpdir.iterdir()), [])
        self.sb.assert_no_secret_leak(res)

    def test_without_a_key_the_preset_is_seeded_with_empty_keys_and_the_warning_comes_once(self) -> None:
        res = self.keys_only(keys=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        warns = self.fal_warnings(res)
        self.assertEqual(len(warns), 1, res.stderr)
        self.assertIn("install-linux.sh --keys --keys-only", warns[0])
        env = read_stack_env(self.sb.stack_env)
        for name, value in fal_preset_values("").items():
            self.assertEqual(env.get(name), value, name)
        self.assertEqual(env["IMAGE_GEN_URL"], FAL_URL)
        self.assertEqual(env["IMAGE_GEN_MODEL"], FAL_OP_TABLE[0][1])
        self.assertEqual(env["IMAGE_GEN_KEY"], "", "no image tool appears until a key is added")
        self.assertEqual(self.state()["image_backend"], "fal")
        self.assertEqual(file_mode(self.sb.stack_env), 0o600)

    def test_a_dry_run_of_keys_only_changes_nothing(self) -> None:
        before = self.sb.snapshot()
        res = self.keys_only("--dry-run")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.sb.snapshot(), before)
        self.assertIn("would set FAL_KEY", res.stdout)
        self.assertIn("dry run complete", res.stdout)
        self.assertFalse((self.stub / "calls.log").exists())
        self.sb.assert_no_secret_leak(res)

    def test_keys_without_a_terminal_asks_nobody(self) -> None:
        res = self.keys_only("--keys", keys=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("stdin is not a terminal", res.stderr)
        self.assertEqual(len(self.fal_warnings(res)), 1)

    def test_only_keys_overwrite_replaces_a_value_that_is_already_there(self) -> None:
        write_stack_env(self.sb.home, {"FAL_KEY": "old-fal-value", "EXA_API_KEY": "old-exa-value"})
        res = self.keys_only("--force")  # --force is about the Desktop config; it never touches stack.env (D2)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        env = read_stack_env(self.sb.stack_env)
        self.assertEqual(env["FAL_KEY"], "old-fal-value")
        self.assertEqual(env["EXA_API_KEY"], "old-exa-value")
        self.assertEqual(env["IMAGE_GEN_KEY"], "old-fal-value", "the file's own fal key fills the fal operations (D10)")
        self.assertIn("the existing value of", res.stdout)
        self.assertNotIn("old-fal-value", res.stdout + res.stderr, "values are never printed")
        res = self.keys_only("--keys-overwrite")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        env = read_stack_env(self.sb.stack_env)
        self.assertEqual(env["FAL_KEY"], KEY_ENV["FAL_KEY"])
        self.assertEqual(env["IMAGE_GEN_KEY"], KEY_ENV["FAL_KEY"])
        self.assertEqual(env["EXA_API_KEY"], KEY_ENV["EXA_API_KEY"])
        backups = backups_of(self.sb.stack_env)
        self.assertTrue(backups, "a change to an existing stack.env is backed up first")
        for backup in backups:
            self.assertEqual(file_mode(backup), 0o600, "backups hold keys: mode 600")
        self.assertTrue(any("old-fal-value" in b.read_text(encoding="utf-8") for b in backups))
        self.assertNotIn("old-fal-value", res.stdout + res.stderr)
        self.sb.assert_no_secret_leak(res)

    def test_the_none_backend_seeds_keys_but_no_image_operation(self) -> None:
        res = self.keys_only("--image-backend", "none")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.fal_warnings(res), [])
        env = read_stack_env(self.sb.stack_env)
        self.assertEqual(env["EXA_API_KEY"], KEY_ENV["EXA_API_KEY"])
        self.assertEqual(env.get("IMAGE_GEN_URL", ""), "", "the fal preset was not merged")
        self.assertEqual(self.state()["image_backend"], "none")

    def write_state(self, **values: object) -> None:
        doc: Dict[str, object] = {"version": 1}
        doc.update(values)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(doc) + "\n", encoding="utf-8")

    def config_of(self, *args: str) -> subprocess.CompletedProcess:
        return run_detached(self.sb, installer_args("--print-config", "--source", str(self.stub), *args))

    def test_a_run_without_profile_keeps_the_recorded_profile_and_an_explicit_one_wins(self) -> None:
        """R1: install-linux.sh defaults to standard, so a re-run (or a printed hint) without --profile used to turn a minimal,
        headless or ci host into a standard one. With a profile recorded in state and no --profile on the command line, that
        profile stays, with one info line; an explicit --profile wins."""
        self.write_state(profile="minimal")
        res = self.config_of()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        cfg = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
        self.assertEqual(cfg["PROFILE"], "minimal")
        self.assertEqual(res.stdout.count("as the earlier install recorded it"), 1, "one info line says so")
        res = self.config_of("--profile", "standard")
        cfg = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
        self.assertEqual(cfg["PROFILE"], "standard", "an explicit --profile wins")
        self.assertNotIn("as the earlier install recorded it", res.stdout)
        self.write_state(profile="no-such-profile")
        res = self.config_of()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("PROFILE=standard", res.stdout, "a recorded profile that has no config file is ignored")

    def test_every_hint_of_a_run_without_profile_names_the_recorded_profile(self) -> None:
        self.write_state(profile="minimal", image_backend="fal")
        res = run_detached(self.sb, installer_args("--keys-only", "--keys-from-env", "--source", str(self.stub)))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        hints = [ln for ln in res.stdout.splitlines() + res.stderr.splitlines() if "install-linux.sh --keys" in ln]
        self.assertTrue(hints, res.stdout + res.stderr)
        for line in hints:
            self.assertIn("--profile minimal", line)
        self.assertEqual(self.state().get("profile"), "minimal", "--keys-only leaves the recorded profile as it was")

    def test_keys_only_judges_fal_key_by_the_recorded_fal_mcp_not_the_profile_default(self) -> None:
        """R6: after an install with --no-fal-mcp (state fal_mcp=false) and backend none, no fal-runner agent exists, so a
        keys-only run does not list FAL_KEY as 'used by the fal-runner agent'; with the agent recorded (or --fal-mcp) it does."""
        wording = "FAL_KEY (used by the fal-runner agent only"
        self.write_state(image_backend="none", fal_mcp=False)
        res = self.keys_only("--keys-from-env", keys=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn(wording, res.stdout)
        self.assertEqual([ln for ln in res.stdout.splitlines() if "Keys still empty" in ln and "FAL_KEY" in ln], [])
        self.write_state(image_backend="none", fal_mcp=True)
        self.assertIn(wording, self.keys_only("--keys-from-env", keys=False).stdout)
        self.write_state(image_backend="none", fal_mcp=False)
        self.assertIn(wording, self.keys_only("--keys-from-env", "--fal-mcp", keys=False).stdout, "a flag on this command line wins")

    def test_a_recorded_none_backend_is_sticky_until_an_explicit_image_backend_fal(self) -> None:
        """D12 (RV1 R1): state image_backend=none and no --image-backend on the command line: the profile's fal does not come
        back, so following a printed `--keys-only` hint never re-enables fal; FAL_KEY is still seeded (the fal-runner agent
        uses it) and every hint carries --image-backend none. Only an explicit --image-backend fal turns fal on again."""
        self.write_state(image_backend="none")
        res = self.keys_only("--keys-from-env", keys=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.fal_warnings(res), [], "the image backend is off: no fal warning")
        self.assertIn("image-backend=none", res.stdout)
        self.assertIn("as an earlier --image-backend none recorded it", res.stdout)
        hints = [ln for ln in res.stdout.splitlines() + res.stderr.splitlines() if "install-linux.sh --keys" in ln]
        self.assertTrue(hints, res.stdout)
        for line in hints:
            self.assertIn("--image-backend none", line, "a hint carries the effective backend")
            # D13: profile ci cannot prompt, so a hint gives the exact terminal command and the unattended form, each
            # with the effective backend
            self.assertRegex(line, r"install-linux\.sh --keys --keys-only --profile ci --image-backend none(?! --)")
            self.assertIn("install-linux.sh --keys-only --keys-from-env --profile ci --image-backend none", line)
        self.assertIn("FAL_KEY (used by the fal-runner agent only", res.stdout)
        self.assertEqual(self.state()["image_backend"], "none")
        res = self.keys_only("--keys-from-env")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.fal_warnings(res), [])
        env = read_stack_env(self.sb.stack_env)
        self.assertEqual(env["FAL_KEY"], KEY_ENV["FAL_KEY"], "FAL_KEY is seeded for the fal-runner agent")
        self.assertEqual(env["EXA_API_KEY"], KEY_ENV["EXA_API_KEY"])
        self.assertNotIn(FAL_URL, env.values(), "no fal operation was added")
        for name in fal_preset_values(""):
            if name.startswith("IMAGE_") and name.endswith(("_URL", "_KEY")):
                self.assertEqual(env.get(name, ""), "", name)
        self.assertEqual(self.state()["image_backend"], "none", "state stays none")
        res = self.keys_only("--image-backend", "fal")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("as an earlier --image-backend none recorded it", res.stdout)
        env = read_stack_env(self.sb.stack_env)
        for name, value in fal_preset_values(KEY_ENV["FAL_KEY"]).items():
            self.assertEqual(env.get(name), value, name)
        self.assertEqual(self.state()["image_backend"], "fal", "an explicit --image-backend fal re-enables fal")
        self.sb.assert_no_secret_leak(res)

    def test_keys_only_finds_the_installed_c101_before_a_sibling_or_the_default_clone(self) -> None:
        """RV1 R3: without --source, --keys-only takes the c101 an earlier install used (state c101_source, else the target
        of ~/.claude/stack), so the fal preset is read from that checkout; --source and $C101_SOURCE still come first, and
        the preset-missing warning names --source."""
        def run(*args: str, **env: str) -> subprocess.CompletedProcess:
            extra = dict(KEY_ENV)
            extra.update(env)
            return run_detached(self.sb, installer_args("--keys-only", "--profile", "ci", *args), **extra)
        self.write_state(c101_source=str(self.stub))
        res = run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("c101 source: %s (installed)" % self.stub, res.stdout)
        self.assertEqual(read_stack_env(self.sb.stack_env)["IMAGE_GEN_URL"], FAL_URL, "the fal preset came from that checkout")
        self.assertFalse((self.stub / "calls.log").exists(), "c101's installer still never runs")
        self.state_file.unlink()  # nothing recorded: the target of ~/.claude/stack
        self.sb.link_c101(self.stub)
        res = run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("c101 source: %s (installed)" % self.stub, res.stdout)
        nofal = build_stub_c101(self.sb.base / "c101-nofal", with_fal_example=False)
        res = run("--source", str(nofal))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("c101 source: %s (flag)" % nofal, res.stdout)
        warned = [ln for ln in res.stderr.splitlines() if ln.startswith("WARN:") and "the fal preset" in ln]
        self.assertEqual(len(warned), 1, res.stderr)
        self.assertIn("--source PATH", warned[0])
        res = run(C101_SOURCE=str(nofal))
        self.assertIn("c101 source: %s (env)" % nofal, res.stdout)

    def test_a_symlinked_stack_env_stays_a_symlink(self) -> None:
        target_dir = self.sb.base / "dotfiles"
        loc = write_stack_env(self.sb.home, {"EXA_API_KEY": ""}, target_dir=target_dir)
        res = self.keys_only()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(loc.is_symlink(), "the merge writes through the link (stow, chezmoi, home-manager keep working)")
        target = target_dir / "stack.env"
        self.assertEqual(read_stack_env(target)["FAL_KEY"], KEY_ENV["FAL_KEY"])
        self.assertEqual(file_mode(target), 0o600)
        self.sb.assert_no_secret_leak(res, trees=[self.sb.home, self.sb.tmpdir, target_dir],
                                      allow=env_file_allowances(target) + (".claude/stack.env",))


@needs_linux
class RealInstallTests(unittest.TestCase):
    """One unattended install for real (profile ci, stubs for uv, node, npx and curl; no network, no sudo, no systemd) with
    every key in the environment: the fal features are wired, and no key lands anywhere but stack.env."""

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.stub = probing_stub(self.sb.base / "c101")
        self.sb.argv_stubs("uv", "npx", "curl")
        self.sb.argv_stub("node", rules=[rule("--version", stdout="v20.11.0\n")])
        self.state_file = self.sb.home / ".local" / "state" / "c101-linux" / "state.json"

    def install(self, *args: str, keys: bool = True, **env: str) -> subprocess.CompletedProcess:
        extra: Dict[str, str] = dict(KEY_ENV) if keys else {}
        extra["C101L_SKIP_NIXOS_CHECK"] = "1"  # a NixOS host would otherwise stop a real run at its own preflight (docs/PITFALLS.md)
        extra.update(env)
        res = run_detached(self.sb, installer_args("--profile", "ci", "--source", str(self.stub), *args), **extra)
        self.assertEqual(res.returncode, 0, "%s:\n%s\n%s" % (args, res.stdout, res.stderr))
        return res

    def state(self) -> Dict[str, object]:
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def test_the_fal_features_are_wired_and_no_key_lands_outside_stack_env(self) -> None:
        res = self.install("--path-integration", "--environment-d", "--desktop-chat-mcp")
        # c101's installer ran once, with the image packs and no server list, and saw no key variable
        self.assertEqual((self.stub / "calls.log").read_text(encoding="utf-8").splitlines(), ["--user --packs " + IMAGE_PACKS])
        self.assertEqual((self.stub / "sentinel-vars.txt").read_text(encoding="utf-8").strip(), "0")
        # stack.env: mode 600, one fal key in FAL_KEY and every fal operation, the research keys by name
        self.assertEqual(file_mode(self.sb.stack_env), 0o600)
        env = read_stack_env(self.sb.stack_env)
        for name, value in fal_preset_values(KEY_ENV["FAL_KEY"]).items():
            self.assertEqual(env.get(name), value, name)
        for name in KEY_NAMES[1:]:
            self.assertEqual(env[name], KEY_ENV[name], name)
        # the fal-runner agent is linked into ~/.claude/agents and the whole-session config copied
        link = self.sb.home / ".claude" / "agents" / "fal-runner.md"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), os.path.realpath(str(ROOT / "agents" / "fal-runner.md")))
        config = self.sb.home / ".config" / "c101" / "fal-mcp.json"
        self.assertEqual(config.read_bytes(), (ROOT / "config" / "fal-mcp.json").read_bytes())
        # state: names of choices, never keys or key names
        state = self.state()
        self.assertEqual(state["profile"], "ci")
        self.assertEqual(state["image_backend"], "fal")
        self.assertIs(state["fal_mcp"], True)
        self.assertIs(state["learn_timer"], False)
        self.assertEqual(state["preset"], "image")
        self.assertEqual(state["packs"], IMAGE_PACKS)
        self.assertNotIn("keys_names_set", state)
        for path in (link, config):
            self.assertIn(str(path), state["files_installed"])
        # the desktop config entry selects Playwright's own Chromium and the login profile does not export a browser name
        # (this profile does not install one)
        desktop = json.loads((self.sb.home / ".config" / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8"))
        args = desktop["mcpServers"]["c101-playwright"]["args"]
        self.assertEqual(args[args.index("--browser") + 1], "chromium")
        profile_text = (self.sb.home / ".profile").read_text(encoding="utf-8")
        envd_text = (self.sb.home / ".config" / "environment.d" / "60-c101-linux.conf").read_text(encoding="utf-8")
        self.assertIn("# >>> c101-linux >>>", profile_text)
        for text in (profile_text, envd_text):
            self.assertNotIn("PLAYWRIGHT_MCP_BROWSER", text)
            self.assertNotIn("_API_KEY", text)
            self.assertNotIn("FAL_KEY", text)
        # nothing outside stack.env holds a value: output, files, file names, the argv and environment of every stub and of c101
        self.assertEqual(list(self.sb.tmpdir.iterdir()), [], "temp files must be cleaned up")
        self.sb.assert_no_secret_leak(res, c101=self.stub, check_env=True)

    def test_the_image_backend_is_recorded_right_after_the_key_step(self) -> None:
        """R4: --image-backend none, then a failure later in the install (here: an unreadable Desktop config for
        --desktop-chat-mcp). The key step already changed stack.env, so state must say none, or the next run would merge fal."""
        cfg = self.sb.home / ".config" / "Claude" / "claude_desktop_config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{this is not json", encoding="utf-8")
        res = run_detached(self.sb, installer_args("--profile", "ci", "--source", str(self.stub), "--image-backend", "none",
                                                   "--desktop-chat-mcp"), C101L_SKIP_NIXOS_CHECK="1", **KEY_ENV)
        self.assertNotEqual(res.returncode, 0, "the Desktop merge failed, so the run did too:\n" + res.stdout + res.stderr)
        self.assertEqual(self.state()["image_backend"], "none")
        self.assertNotIn("profile", self.state(), "the final state block never ran")
        res = run_detached(self.sb, installer_args("--profile", "ci", "--source", str(self.stub), "--keys-only"), **KEY_ENV)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("as an earlier --image-backend none recorded it", res.stdout, "the sticky none survived the failed run")

    def test_settings_env_values_never_reach_state_json(self) -> None:
        """R3: a token in ~/.claude/settings.json env is not copied into state.json (names only). A snapshot an older release
        wrote as an object with values is cut down to its names by the next run."""
        token = "ghp_SENTINEL_settings_env_value_0123456789"
        settings = self.sb.home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"env": {"MY_TOKEN": token, "KEEP": "1"}}), encoding="utf-8")
        self.install()
        self.assertNotIn(token, self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(sorted(self.state()["settings_env_before"]), ["KEEP", "MY_TOKEN"])
        doc = self.state()
        doc["settings_env_before"] = {"MY_TOKEN": token, "KEEP": "1"}  # what an older release left behind
        self.state_file.write_text(json.dumps(doc) + "\n", encoding="utf-8")
        self.install()
        self.assertNotIn(token, self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(sorted(self.state()["settings_env_before"]), ["KEEP", "MY_TOKEN"])

    def test_no_fal_mcp_links_no_agent_and_copies_no_config(self) -> None:
        self.install("--no-fal-mcp")
        self.assertFalse((self.sb.home / ".claude" / "agents" / "fal-runner.md").is_symlink())
        self.assertFalse((self.sb.home / ".claude" / "agents" / "fal-runner.md").exists())
        self.assertFalse((self.sb.home / ".config" / "c101").exists())
        state = self.state()
        self.assertIs(state["fal_mcp"], False)
        self.assertEqual(state["image_backend"], "fal", "the opt-out concerns the hosted MCP server only, not image generation")
        self.assertEqual(read_stack_env(self.sb.stack_env)["IMAGE_GEN_URL"], FAL_URL)

    def test_the_none_backend_writes_no_stack_env_when_there_is_no_key(self) -> None:
        res = self.install("--image-backend", "none", keys=False)
        self.assertFalse(self.sb.stack_env.exists(), "no key and no preset: nothing to write")
        self.assertEqual(self.state()["image_backend"], "none")
        self.assertNotIn("FAL_KEY is not set", res.stderr)

    def test_an_unattended_install_without_keys_still_seeds_the_fal_preset_and_warns_once(self) -> None:
        res = self.install(keys=False)
        warns = [ln for ln in res.stderr.splitlines() if ln.startswith("WARN:") and "FAL_KEY" in ln]
        self.assertEqual(len(warns), 1, res.stderr)
        self.assertIn("install-linux.sh --keys --keys-only", warns[0])
        env = read_stack_env(self.sb.stack_env)
        for name, value in fal_preset_values("").items():
            self.assertEqual(env.get(name), value, name)
        self.assertEqual(self.state()["image_backend"], "fal")
        self.assertTrue((self.sb.home / ".claude" / "agents" / "fal-runner.md").is_symlink(), "installed by default, loaded only on demand")


class RerunHintTests(unittest.TestCase):
    """R1, static: every command a user is told to run goes through one builder, so none can drop --profile again."""

    FILES = ("install-linux.sh", "uninstall-linux.sh", "bin/c101-linux-doctor", "bin/c101-learn-backstop")

    def test_no_hint_names_install_linux_sh_outside_the_builders(self) -> None:
        builders = ("rerun_cmd", "rerun_profile_sfx", "$sfx")  # lib/common.sh, lib/keys.sh keys_rerun_cmd, doctor keys_fix_cmd
        allowed = ("needs bash", "remove what install-linux.sh added")  # the bash-version refusals and uninstall's usage line
        paths = [ROOT / name for name in self.FILES] + sorted((ROOT / "lib").glob("*.sh"))
        found = 0
        for path in paths:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#") or not re.search(r"(?<![\w-])install-linux\.sh\b", line):
                    continue
                found += 1
                self.assertTrue(any(b in line for b in builders) or any(a in line for a in allowed),
                                "%s:%d prints a re-run command without --profile:\n%s" % (path.relative_to(ROOT), number, line.strip()))
        self.assertGreaterEqual(found, 6, "the scan found the builders and the bash-version refusals")

    def test_the_doctor_and_the_libraries_use_the_builder_for_every_generic_fix(self) -> None:
        doctor = (ROOT / "bin" / "c101-linux-doctor").read_text(encoding="utf-8")
        self.assertGreaterEqual(doctor.count("$(rerun_cmd"), 20)
        self.assertNotRegex(doctor, r"(?i)\bre-?run install-linux\.sh")
        for name in ("prewarm.sh", "deps.sh", "desktop.sh", "services.sh"):
            text = (ROOT / "lib" / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"re-?run (with )?--", name)


if __name__ == "__main__":
    unittest.main()
