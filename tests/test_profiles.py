"""Profile resolution and argument handling (install-linux.sh --print-config changes nothing)."""
from __future__ import annotations

import subprocess
import unittest
from typing import Dict, List, Tuple

from helpers import ROOT, Sandbox, bash_lib, installer_args, needs_linux


def resolve(sb: Sandbox, *args: str, **env: str) -> Dict[str, str]:
    res = sb.run(installer_args("--print-config", *args), **env)
    assert res.returncode == 0, res.stderr
    return dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)


def run_detached(sb: Sandbox, args: List[str], **extra: str) -> subprocess.CompletedProcess:
    """Sandbox.run with stdin from /dev/null: nothing can wait on the terminal that runs the tests (--pick-preset and the
    hidden key prompts both test stdin)."""
    return subprocess.run(args, capture_output=True, text=True, env=sb.env(**extra), cwd=str(sb.base), timeout=120,
                          stdin=subprocess.DEVNULL)


# plan.md "Profile table v7": the resolved plan of every shipped profile (print-config names; PACKS=all means every pack).
# PRESET=image (minimal, ci) is core + image + graphic-design; everything is every pack (standard, full, headless).
V7: Dict[str, Dict[str, str]] = {
    "minimal": {"PRESET": "image", "PACKS": "core,image,graphic-design", "KEYS": "auto", "IMAGE_BACKEND": "fal", "FAL_MCP": "1",
                "PREWARM_MCP": "1", "PLAYWRIGHT_BROWSER": "0", "LEARN_TIMER": "0"},
    "standard": {"PRESET": "everything", "PACKS": "all", "KEYS": "auto", "IMAGE_BACKEND": "fal", "FAL_MCP": "1",
                 "PREWARM_MCP": "1", "PLAYWRIGHT_BROWSER": "1", "LEARN_TIMER": "1"},
    "full": {"PRESET": "everything", "PACKS": "all", "KEYS": "auto", "IMAGE_BACKEND": "fal", "FAL_MCP": "1",
             "PREWARM_MCP": "1", "PLAYWRIGHT_BROWSER": "1", "LEARN_TIMER": "1"},
    "headless": {"PRESET": "everything", "PACKS": "all", "KEYS": "env", "IMAGE_BACKEND": "fal", "FAL_MCP": "1",
                 "PREWARM_MCP": "1", "PLAYWRIGHT_BROWSER": "1", "LEARN_TIMER": "1"},
    "ci": {"PRESET": "image", "PACKS": "core,image,graphic-design", "KEYS": "env", "IMAGE_BACKEND": "fal", "FAL_MCP": "1",
           "PREWARM_MCP": "0", "PLAYWRIGHT_BROWSER": "0", "LEARN_TIMER": "0"},
}


@needs_linux
class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)

    def test_minimal(self) -> None:
        c = resolve(self.sb, "--profile", "minimal")
        # fal image generation is on in every profile (D3), so the image pack (image + graphic-design) comes with core
        self.assertEqual(c["C101_FLAGS"], "--user --packs core,image,graphic-design")
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
        # a command-line pack list replaces the profile's (D9); the fal backend adds the image packs to it
        self.assertEqual(c["C101_FLAGS"], "--user --enforce-models --packs core,rust,go,image,graphic-design --mcp tavily,markitdown")
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

    # --- parity: profile table v7, the image packs, key flags and the opt-in steps -----------------------------------

    def test_every_profile_prints_the_v7_values(self) -> None:
        for profile, expected in V7.items():
            c = resolve(self.sb, "--profile", profile)
            for key, value in expected.items():
                self.assertEqual(c[key], value, "%s: %s" % (profile, key))
            self.assertEqual(c["KEYS_OVERWRITE"], "0", "%s: only --keys-overwrite sets it" % profile)

    def test_image_packs_come_with_every_profile(self) -> None:
        for profile in ("minimal", "ci"):
            c = resolve(self.sb, "--profile", profile)
            self.assertEqual(c["C101_FLAGS"], "--user --packs core,image,graphic-design", profile)
            self.assertEqual(c["PACKS"].split(","), ["core", "image", "graphic-design"], profile)
        for profile in ("standard", "full", "headless"):
            c = resolve(self.sb, "--profile", profile)
            self.assertEqual(c["PACKS"], "all", profile)
            self.assertNotIn("--packs", c["C101_FLAGS"], "%s installs every pack, image and graphic-design included" % profile)

    def test_image_backend_none_overrides_the_profile(self) -> None:
        for profile in V7:
            self.assertEqual(resolve(self.sb, "--profile", profile, "--image-backend", "none")["IMAGE_BACKEND"], "none", profile)
        # nothing adds the image packs to a command-line selection without fal ...
        c = resolve(self.sb, "--profile", "standard", "--packs", "rust", "--image-backend", "none")
        self.assertEqual(c["C101_FLAGS"], "--user --main-agent --packs core,rust")
        c = resolve(self.sb, "--profile", "standard", "--packs", "rust", "--image-backend=keep")
        self.assertEqual((c["IMAGE_BACKEND"], c["C101_FLAGS"]), ("keep", "--user --main-agent --packs core,rust"))
        c = resolve(self.sb, "--profile", "standard", "--packs", "rust", "--image-backend", "fal")
        self.assertEqual(c["C101_FLAGS"], "--user --main-agent --packs core,rust,image,graphic-design")
        # ... but a profile whose own preset is image keeps it
        c = resolve(self.sb, "--profile", "minimal", "--image-backend", "none")
        self.assertEqual((c["IMAGE_BACKEND"], c["PACKS"]), ("none", "core,image,graphic-design"))

    def test_keep_means_the_backend_an_earlier_run_recorded(self) -> None:
        state = self.sb.base / "state.json"
        state.write_text('{"version": 1, "image_backend": "fal"}\n', encoding="utf-8")
        args = ("--profile", "standard", "--packs", "rust", "--image-backend", "keep")
        c = resolve(self.sb, *args, C101L_STATE_FILE=str(state))
        self.assertEqual(c["C101_FLAGS"], "--user --main-agent --packs core,rust,image,graphic-design", "a recorded fal keeps its packs")
        state.write_text('{"version": 1, "image_backend": "none"}\n', encoding="utf-8")
        c = resolve(self.sb, *args, C101L_STATE_FILE=str(state))
        self.assertEqual(c["C101_FLAGS"], "--user --main-agent --packs core,rust")

    def test_fal_adds_the_image_packs_to_a_command_line_selection(self) -> None:
        c = resolve(self.sb, "--profile", "standard", "--packs", "rust,go")
        packs = c["PACKS"].split(",")
        self.assertEqual(packs[0], "core")
        self.assertEqual(set(packs), {"core", "rust", "go", "image", "graphic-design"})
        self.assertIn("--packs core,rust,go,image,graphic-design", c["C101_FLAGS"])
        c = resolve(self.sb, "--profile", "ci", "--preset", "science")
        self.assertEqual(set(c["PACKS"].split(",")), {"core", "mathematics", "physics", "quantum", "computer-science",
                                                     "computer-graphics", "julia", "image", "graphic-design"})
        self.assertEqual(c["PRESET"].split(","), ["science", "image"])

    def test_presets_compose_and_a_command_line_selection_replaces_the_profile(self) -> None:
        c = resolve(self.sb, "--profile", "full", "--preset", "research", "--preset", "writing")
        packs = c["PACKS"].split(",")
        self.assertEqual(packs[0], "core", "core always comes first")
        self.assertEqual(set(packs), {"core", "coding-core", "writing", "image", "graphic-design"}, "full no longer means every pack")
        self.assertEqual(c["PRESET"].split(","), ["research", "writing", "image"])
        self.assertEqual(resolve(self.sb, "--profile", "full", "--preset", "research,writing")["PACKS"], c["PACKS"])
        c = resolve(self.sb, "--profile", "minimal", "--preset", "everything")
        self.assertEqual((c["PRESET"], c["PACKS"]), ("everything", "all"), "any preset with an empty list selects every pack")

    def test_pick_preset_needs_a_terminal_and_never_waits_for_one(self) -> None:
        res = run_detached(self.sb, installer_args("--print-config", "--pick-preset"))
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("--pick-preset", res.stderr)

    def test_usage_errors_of_the_new_flags_exit_2(self) -> None:
        cases: List[Tuple[List[str], str]] = [
            (["--image-backend", "bogus"], "fal, none or keep"),
            (["--image-backend"], "needs a value"),
            (["--preset", "nope"], "unknown preset"),
            (["--preset"], "needs a value"),
            (["--keys-only", "--pick-preset"], "--keys-only"),
        ]
        for args, word in cases:
            res = run_detached(self.sb, installer_args("--print-config", *args))
            self.assertEqual(res.returncode, 2, (args, res.stderr))
            self.assertIn(word, res.stderr, args)

    def test_key_flags_set_the_key_source_and_only_one_flag_overwrites(self) -> None:
        self.assertEqual(resolve(self.sb, "--keys")["KEYS"], "prompt")
        self.assertEqual(resolve(self.sb, "--keys-from-env")["KEYS"], "env")
        self.assertEqual(resolve(self.sb, "--profile", "ci", "--keys")["KEYS"], "prompt", "a flag beats the profile's KEYS=env")
        self.assertEqual(resolve(self.sb, "--keys-overwrite")["KEYS_OVERWRITE"], "1")
        self.assertEqual(resolve(self.sb)["KEYS_OVERWRITE"], "0")
        for flag in ("--force", "--yes", "--keys", "--keys-from-env", "--managed-settings", "--desktop-chat-mcp"):
            self.assertEqual(resolve(self.sb, flag)["KEYS_OVERWRITE"], "0", "%s must not replace key values (D2)" % flag)

    def test_a_value_in_the_callers_environment_never_counts(self) -> None:
        c = resolve(self.sb, KEYS_OVERWRITE="1", KEYS="prompt", IMAGE_BACKEND="none", FAL_KEY="x", PREWARM_MCP="0")
        self.assertEqual((c["KEYS_OVERWRITE"], c["KEYS"], c["IMAGE_BACKEND"], c["PREWARM_MCP"]), ("0", "auto", "fal", "1"))

    def test_the_opt_in_steps_can_be_switched_per_run(self) -> None:
        cases: List[Tuple[Tuple[str, ...], str, str]] = [
            (("--profile", "standard", "--no-fal-mcp"), "FAL_MCP", "0"),
            (("--profile", "ci", "--fal-mcp"), "FAL_MCP", "1"),
            (("--profile", "minimal", "--no-prewarm-mcp"), "PREWARM_MCP", "0"),
            (("--profile", "ci", "--prewarm-mcp"), "PREWARM_MCP", "1"),
            (("--profile", "minimal", "--playwright-browser"), "PLAYWRIGHT_BROWSER", "1"),
            (("--profile", "standard", "--no-playwright-browser"), "PLAYWRIGHT_BROWSER", "0"),
            (("--profile", "ci", "--learn-timer"), "LEARN_TIMER", "1"),
            (("--profile", "full", "--no-learn-timer"), "LEARN_TIMER", "0"),
        ]
        for args, key, value in cases:
            self.assertEqual(resolve(self.sb, *args)[key], value, args)


class ProfileFileTests(unittest.TestCase):
    """The shipped profile files and PROFILE_KEYS themselves (plan.md table v7: 7 new keys in each of 5 profiles)."""

    KEYS = ("PRESET", "KEYS", "IMAGE_BACKEND", "FAL_MCP", "PREWARM_MCP", "PLAYWRIGHT_BROWSER", "LEARN_TIMER")

    def conf(self, name: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for line in (ROOT / "config" / "profiles" / (name + ".conf")).read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                out[key] = value
        return out

    def test_every_profile_file_holds_the_v7_values(self) -> None:
        checked = 0
        for profile, expected in V7.items():
            conf = self.conf(profile)
            for key in self.KEYS:
                self.assertEqual(conf.get(key), expected[key], "%s.conf: %s" % (profile, key))
                checked += 1
        self.assertEqual(checked, 35)

    def test_the_profile_table_is_complete(self) -> None:
        shipped = sorted(p.stem for p in (ROOT / "config" / "profiles").glob("*.conf"))
        self.assertEqual(shipped, sorted(V7))

    def test_key_replacement_is_never_a_profile_setting(self) -> None:
        for profile in V7:
            self.assertNotIn("KEYS_OVERWRITE", self.conf(profile), "only --keys-overwrite may replace stack.env values (D2)")
        text = (ROOT / "lib" / "profiles.sh").read_text(encoding="utf-8")
        keys_line = [ln for ln in text.splitlines() if ln.startswith("PROFILE_KEYS=")]
        self.assertEqual(len(keys_line), 1)
        self.assertNotIn("KEYS_OVERWRITE", keys_line[0])
        for key in self.KEYS:
            self.assertRegex(keys_line[0], r"\b%s\b" % key)


@needs_linux
class ProfileLibraryTests(unittest.TestCase):
    """lib/profiles.sh on a profile file of its own: old-style files keep working, typos and key replacement do not."""

    OLD_STYLE = (
        "DESCRIPTION=old style profile without the parity keys\nC101_FLAGS=--user\nC101_PACKS=core\nPATH_INTEGRATION=0\n"
        "ENVIRONMENT_D=0\nSANDBOX=0\nPLAYWRIGHT_DEPS=0\nDESKTOP_CHECKS=0\nNO_SUDO=1\nASSUME_YES=1\nLINK_BINS=0\n"
        "CONTAINER_RUNTIME=podman\nPODMAN_SERVICES=0\nDOCKER_HOST_PODMAN=auto\n"
    )

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.overlay = self.sb.base / "overlay"
        (self.overlay / "config" / "profiles").mkdir(parents=True)

    def lib(self, profile_text: str, snippet: str) -> subprocess.CompletedProcess:
        (self.overlay / "config" / "profiles" / "legacy.conf").write_text(profile_text, encoding="utf-8")
        return bash_lib(self.sb, "C101L_ROOT='%s'\n%s" % (self.overlay, snippet))

    def test_a_profile_without_the_parity_keys_gets_the_defaults(self) -> None:
        snippet = ("profile_load legacy\nPROFILE=legacy\nprofile_check_bool FAL_MCP PREWARM_MCP PLAYWRIGHT_BROWSER LEARN_TIMER\n"
                   'printf "KEYS=%s\\nIMAGE_BACKEND=%s\\nFAL_MCP=%s\\nPREWARM_MCP=%s\\nPLAYWRIGHT_BROWSER=%s\\nLEARN_TIMER=%s\\nPRESET=[%s]\\n"'
                   ' "$P_KEYS" "$P_IMAGE_BACKEND" "$P_FAL_MCP" "$P_PREWARM_MCP" "$P_PLAYWRIGHT_BROWSER" "$P_LEARN_TIMER" "$P_PRESET"')
        res = self.lib(self.OLD_STYLE, snippet)
        self.assertEqual(res.returncode, 0, res.stderr)
        got = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
        self.assertEqual(got, {"KEYS": "auto", "IMAGE_BACKEND": "fal", "FAL_MCP": "1", "PREWARM_MCP": "1", "PLAYWRIGHT_BROWSER": "0",
                               "LEARN_TIMER": "0", "PRESET": "[]"})

    def test_key_replacement_in_a_profile_file_is_an_unknown_key(self) -> None:
        res = self.lib(self.OLD_STYLE + "KEYS_OVERWRITE=1\n", "profile_load legacy")
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("unknown key 'KEYS_OVERWRITE'", res.stderr)

    def test_a_non_boolean_switch_is_refused(self) -> None:
        res = self.lib(self.OLD_STYLE + "FAL_MCP=2\n", "profile_load legacy\nPROFILE=legacy\nprofile_check_bool FAL_MCP")
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("FAL_MCP must be 0 or 1", res.stderr)


if __name__ == "__main__":
    unittest.main()
