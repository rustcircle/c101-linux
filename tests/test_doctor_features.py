"""bin/c101-linux-doctor: the checks the parity change added (image generation through fal, research servers, MCP runtime,
hooks, workflows, agents, settings, learning).

Every test runs the real doctor in a throw-away HOME against the stub c101 of helpers.py (its bin/stack and
bin/image-router log every call and answer in the real formats). Facts pinned down here, all from plan.md D3, D5, D6, D8:
  * the sections come in a fixed order and the 22 per-feature ids each report in their own section;
  * the profile decides how bad a gap is: FAIL in minimal, standard, full and headless, WARN in ci (profile from --profile,
    else from state, else standard); an image backend that is not recorded as fal only ever warns;
  * keys are reported by NAME and status, never by value; key variables of the doctor's own environment never reach
    c101's bin/stack or bin/image-router; the fal MCP reachability probe sends no credentials;
  * the doctor changes nothing.
The stub c101 has no PIL in its .venv and no workflows or agents, so image-deps, workflows-linked and similar ids report
warnings or INFO here; these tests look at single ids, never at the overall exit code, except where it follows from the
summary line.
Run on Linux: tests/run-tests.sh -k doctor_features
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from helpers import (NM_VERSION, ROOT, SECRET_SENTINEL, Sandbox, build_stub_c101, env_file_allowances, fal_preset_values, file_mode,
                     make_argv_stub, needs_linux, rule, sentinel_for, stack_calls, stub_calls)

DOCTOR = ROOT / "bin" / "c101-linux-doctor"
SHIPPED_AGENT = os.path.realpath(str(ROOT / "agents" / "fal-runner.md"))
SHIPPED_CONFIG = ROOT / "config" / "fal-mcp.json"

IMAGE = "Image generation (fal)"
RESEARCH = "Research servers"
SECTIONS = (IMAGE, RESEARCH, "MCP runtime", "Hooks", "Workflows", "Agents and skills", "Settings", "Learning", "Platform")

# The per-feature ids (DR1): 8 + 2 + 2 + 2 + 1 + 1 + 1 + 5 = 22. The Platform section keeps its older ids.
FEATURE_IDS: Dict[str, Tuple[str, ...]] = {
    IMAGE: ("stack-env", "stack-env-mode", "keys-image", "image-router", "image-deps", "fal-runner", "fal-mcp-config",
            "fal-mcp-reach"),
    RESEARCH: ("keys-research", "mcp-fetch-cache"),
    "MCP runtime": ("playwright-browser", "neural-memory-cache"),
    "Hooks": ("hooks-events", "hooks-runtime"),
    "Workflows": ("workflows-linked",),
    "Agents and skills": ("agents-linked",),
    "Settings": ("settings-env",),
    "Learning": ("learning-config", "learning-writable", "learning-timer", "learning-log", "depth-stats"),
}

# Every key set, each to its own sentinel variant (a scan for SECRET_SENTINEL finds them all).
ALL_SET: Dict[str, str] = {
    "fal": sentinel_for("FAL_KEY"),
    "exa": sentinel_for("EXA_API_KEY"),
    "jina": sentinel_for("JINA_API_KEY"),
    "spider": sentinel_for("SPIDER_API_KEY"),
    "context7": sentinel_for("CONTEXT7_API_KEY"),
}
KEY_ENV = {
    "FAL_KEY": ALL_SET["fal"],
    "EXA_API_KEY": ALL_SET["exa"],
    "JINA_API_KEY": ALL_SET["jina"],
    "SPIDER_API_KEY": ALL_SET["spider"],
    "CONTEXT7_API_KEY": ALL_SET["context7"],
    "IMAGE_GEN_KEY": sentinel_for("IMAGE_GEN_KEY"),
}


# One `install-linux.sh` command in a message: the script, then its flags, each with at most one value (a value never
# starts with "-"; the first word after the last flag, e.g. "from", is taken as a value and ignored).
INSTALL_CMD = re.compile(r"install-linux\.sh((?: --[a-z][a-z-]*(?: (?!-)[^\s,;()]+)?)*)")


def install_commands(text: str) -> List[List[str]]:
    """The flags of every `install-linux.sh` command in `text`, in order, values left out."""
    return [re.findall(r"--[a-z][a-z-]*", m.group(1)) for m in INSTALL_CMD.finditer(text)]


class Row:
    """One verdict line: PASS | WARN | FAIL | INFO, the check id, the message, and its indented detail lines."""

    def __init__(self, sev: str, ident: str, msg: str) -> None:
        self.sev = sev
        self.id = ident
        self.msg = msg
        self.detail: List[str] = []

    def __repr__(self) -> str:
        return "%s %s %s" % (self.sev, self.id, self.msg)


class Report:
    """The doctor's stdout, split into sections of rows. Line formats (bin/c101-linux-doctor): `== Name ==` section header;
    `PASS|WARN|FAIL|INFO  <id padded to 22> <message>`; six-space detail lines; `N passed, N warnings, N failed`."""

    ROW = re.compile(r"^(PASS|WARN|FAIL|INFO)  (\S+)\s+(.*)$")
    HEAD = re.compile(r"^== (.+) ==$")
    SUMMARY = re.compile(r"^(\d+) passed, (\d+) warnings, (\d+) failed$")

    def __init__(self, text: str) -> None:
        self.text = text
        self.header = ""
        self.sections: List[str] = []
        self.rows: Dict[str, List[Row]] = {}
        self.summary: Optional[Tuple[int, int, int]] = None
        current: Optional[str] = None
        last: Optional[Row] = None
        for line in text.splitlines():
            if line.startswith("c101-linux-doctor:"):
                self.header = line
                continue
            m = self.HEAD.match(line)
            if m:
                current = m.group(1)
                self.sections.append(current)
                self.rows[current] = []
                last = None
                continue
            m = self.ROW.match(line)
            if m and current is not None:
                last = Row(m.group(1), m.group(2), m.group(3))
                self.rows[current].append(last)
                continue
            if line.startswith("      ") and line.strip() and last is not None:
                last.detail.append(line.strip())
                continue
            m = self.SUMMARY.match(line)
            if m:
                self.summary = (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def all_rows(self) -> List[Row]:
        return [row for name in self.sections for row in self.rows[name]]

    def find(self, ident: str, contains: str = "") -> List[Row]:
        return [r for r in self.all_rows() if r.id == ident and contains in r.msg]

    def one(self, ident: str, contains: str = "") -> Row:
        found = self.find(ident, contains)
        if len(found) != 1:
            raise AssertionError("expected exactly one %r row containing %r, found %d:\n%s" % (ident, contains, len(found), self.text))
        return found[0]


@needs_linux
class DoctorCase(unittest.TestCase):
    """A sandbox with a stub c101 linked as ~/.claude/stack, a state file path and helpers to run the doctor."""

    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.c101 = build_stub_c101(self.sb.base / "c101")
        headers = self.c101 / "bin" / "mcp-headers"  # the headersHelper the HTTP servers and fal-runner call; only its presence matters
        headers.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        headers.chmod(0o755)
        self.sb.link_c101(self.c101)
        self.state_path = self.sb.base / "state.json"
        # No real uv or uvx may run against the throw-away HOME (uv writes a cache there). After the login PATH, ~/.local/bin is
        # the first place the doctor looks for uvx (c101's bin/mcp-run puts it first too); the stub answers every --offline call
        # with success, i.e. "cache warm".
        self.uvx = make_argv_stub(self.sb.home / ".local" / "bin", "uvx", log=self.sb.logdir / "uvx.jsonl", rules=[rule("--offline")])
        self.sb.argv_stub("uv")

    # --- fixtures -----------------------------------------------------------------------------------------------

    def write_state(self, **values: Any) -> None:
        data: Dict[str, Any] = {"version": 1}
        data.update(values)
        self.state_path.write_text(json.dumps(data), encoding="utf-8")
        self.state_path.chmod(0o600)

    def stack_env(self, *, fal: str = "", ops: bool = True, exa: str = "", jina: str = "", spider: str = "",
                  context7: str = "", **kw: Any) -> Path:
        """stack.env for the doctor: with ops, the fal preset (every image op on fal, its key = `fal`); FAL_KEY and the four
        research keys with the given values ("" = the name is there but empty)."""
        values: Dict[str, str] = dict(fal_preset_values(fal)) if ops else {"FAL_KEY": fal}
        values.update({"EXA_API_KEY": exa, "JINA_API_KEY": jina, "SPIDER_API_KEY": spider, "CONTEXT7_API_KEY": context7})
        return self.sb.write_stack_env(values, **kw)

    def link_agent(self) -> Path:
        link = self.sb.home / ".claude" / "agents" / "fal-runner.md"
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(SHIPPED_AGENT, link)
        return link

    def install_config(self, text: Optional[str] = None) -> Path:
        dest = self.sb.home / ".config" / "c101" / "fal-mcp.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            shutil.copyfile(SHIPPED_CONFIG, dest)
        else:
            dest.write_text(text, encoding="utf-8")
        return dest

    def doctor(self, *args: str, **env: str) -> Tuple[subprocess.CompletedProcess, Report]:
        res = self.sb.run([str(DOCTOR), *args], C101L_STATE_FILE=str(self.state_path), **env)
        if res.returncode not in (0, 1):
            raise AssertionError("doctor exited %d:\n%s\n%s" % (res.returncode, res.stdout, res.stderr))
        return res, Report(res.stdout)

    def standard(self, *extra: str, **env: str) -> Tuple[subprocess.CompletedProcess, Report]:
        return self.doctor("--profile", "standard", "--skip-c101", "--offline", *extra, **env)

    def ci(self, *extra: str, **env: str) -> Tuple[subprocess.CompletedProcess, Report]:
        return self.doctor("--profile", "ci", "--skip-c101", "--offline", *extra, **env)


class SectionTests(DoctorCase):
    def test_sections_come_in_the_documented_order(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(**ALL_SET)
        res, rep = self.standard()
        self.assertEqual(rep.sections, list(SECTIONS), res.stdout)
        self.assertEqual(rep.sections[0], IMAGE, "image generation (fal) runs first")
        self.assertEqual(rep.sections[1], RESEARCH, "research servers run right after it")

    def test_all_22_feature_ids_report_in_their_own_section(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(**ALL_SET)
        res, rep = self.standard()
        self.assertEqual(sum(len(ids) for ids in FEATURE_IDS.values()), 22)
        for section, ids in FEATURE_IDS.items():
            seen = {row.id for row in rep.rows[section]}
            for ident in ids:
                self.assertIn(ident, seen, "%s is missing from %s:\n%s" % (ident, section, res.stdout))
        every = [i for ids in FEATURE_IDS.values() for i in ids]
        for section, rows in rep.rows.items():
            for row in rows:
                if row.id in every:
                    self.assertIn(row.id, FEATURE_IDS.get(section, ()), "%s printed under %s" % (row.id, section))

    def test_the_summary_line_matches_the_rows_and_the_exit_code(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(fal="")
        res, rep = self.standard()
        self.assertIsNotNone(rep.summary, res.stdout)
        passed, warned, failed = rep.summary or (0, 0, 0)
        rows = rep.all_rows()
        self.assertEqual(passed, len([r for r in rows if r.sev == "PASS"]))
        self.assertEqual(warned, len([r for r in rows if r.sev == "WARN"]))
        self.assertEqual(failed, len([r for r in rows if r.sev == "FAIL"]), "INFO lines and detail lines are not counted")
        self.assertEqual(res.returncode, 1 if failed else 0, "exit 1 on any FAIL, else 0")
        self.assertGreater(failed, 0, "the empty fal key alone must fail in standard")

    def test_the_doctor_changes_nothing(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(**ALL_SET)
        before = self.sb.snapshot()
        res, _ = self.standard()
        self.assertEqual(self.sb.snapshot(), before, res.stdout)
        self.assertEqual(list(self.sb.tmpdir.iterdir()), [], "no temp files left behind")

    def test_quiet_prints_only_warnings_and_failures(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(fal="")
        res, _ = self.standard("--quiet")
        lines = [ln for ln in res.stdout.splitlines() if re.match(r"^(PASS|WARN|FAIL|INFO)  ", ln)]
        self.assertTrue(lines, res.stdout)
        for ln in lines:
            self.assertTrue(ln.startswith(("WARN", "FAIL")), ln)
        self.assertIsNone(re.search(r"(?m)^== ", res.stdout), "--quiet drops the section headers")
        self.assertRegex(res.stdout, r"(?m)^\d+ passed, \d+ warnings, \d+ failed$")

    def test_help_and_usage_errors(self) -> None:
        res = self.sb.run([str(DOCTOR), "--help"])
        self.assertEqual(res.returncode, 0, res.stderr)
        for word in ("--profile", "--skip-c101", "--offline", "--quiet", IMAGE, RESEARCH):
            self.assertIn(word, res.stdout)
        self.assertEqual(self.sb.run([str(DOCTOR), "--bogus"]).returncode, 2)
        self.assertEqual(self.sb.run([str(DOCTOR), "--profile"]).returncode, 2, "a value is required")
        res = self.sb.run([str(DOCTOR), "--profile", "nope", "--skip-c101"])
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("unknown profile", res.stderr)

    def test_the_profile_comes_from_state_unless_given(self) -> None:
        self.write_state(profile="ci", image_backend="fal")
        self.stack_env(fal="")
        res, rep = self.doctor("--skip-c101", "--offline")
        self.assertIn("profile=ci", rep.header, res.stdout)
        self.assertEqual(rep.one("keys-image").sev, "WARN", "ci, read from state: a gap only warns")
        res, rep = self.doctor("--profile", "standard", "--skip-c101", "--offline")
        self.assertIn("profile=standard", rep.header)
        self.assertEqual(rep.one("keys-image").sev, "FAIL", "--profile beats the recorded profile")
        self.state_path.unlink()
        res, rep = self.doctor("--skip-c101", "--offline")
        self.assertIn("profile=standard", rep.header, "no state, no flag: standard")


class ImageSectionTests(DoctorCase):
    def test_empty_fal_key_fails_in_standard_and_names_the_fix(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(fal="", **{k: v for k, v in ALL_SET.items() if k != "fal"})
        res, rep = self.standard()
        keys = rep.one("keys-image")
        self.assertEqual(keys.sev, "FAIL", res.stdout)
        self.assertIn("FAL_KEY is empty", keys.msg)
        self.assertIn("empty key", keys.msg)
        self.assertIn("install-linux.sh --keys --keys-only", keys.msg, "the fix command is printed")
        self.assertTrue(keys.msg.endswith("install-linux.sh --keys --keys-only"),
                        "D13, D14: the exact command, nothing after it: " + keys.msg)
        router = rep.one("image-router")
        self.assertEqual(router.sev, "FAIL", "c101's own view agrees: no GEN, so no text-to-image tool")
        self.assertIn("GEN is not configured", router.msg)
        self.assertEqual(res.returncode, 1)
        # every operation is listed by name with its status; a value never shows
        for op in ("GEN", "REF", "EDIT", "TRANSPARENT", "SEGMENT", "BG_REMOVAL", "UPSCALE", "VECTOR", "TRACE"):
            self.assertTrue(any(re.match(r"^%s\s+key empty \(fal\)$" % op, d) for d in keys.detail), (op, keys.detail))

    def test_the_same_gap_only_warns_in_ci(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(fal="")
        res, rep = self.ci()
        keys = rep.one("keys-image")
        self.assertEqual(keys.sev, "WARN", res.stdout)
        self.assertIn("FAL_KEY", keys.msg)
        self.assertIn("install-linux.sh --keys --keys-only", keys.msg)
        self.assertIn("--keys-from-env", keys.msg, "ci cannot prompt: the fix says to export the key and use --keys-from-env")
        # D13, D14: the exact terminal command (only --profile may follow it here) AND the unattended form, for FAL_KEY by
        # name
        exact = r"install-linux\.sh --keys --keys-only --profile ci(?![-\w])(?! --)"
        for row in (keys, rep.one("image-router")):
            self.assertRegex(row.msg, r"--keys(?![-\w])", row.id)
            self.assertRegex(row.msg, exact, row.id)
            self.assertIn("export FAL_KEY", row.msg, row.id)
            self.assertIn("install-linux.sh --keys-only --keys-from-env --profile ci", row.msg, row.id)
        self.assertEqual(rep.one("image-router").sev, "WARN")
        self.assertEqual([r for r in rep.rows[IMAGE] if r.sev == "FAIL"], [], "nothing in the image section fails in ci")

    def test_a_set_fal_key_passes_and_every_operation_is_configured(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(**ALL_SET)
        res, rep = self.standard()
        keys = rep.one("keys-image")
        self.assertEqual(keys.sev, "PASS", res.stdout)
        self.assertIn("FAL_KEY set", keys.msg)
        for op in ("GEN", "REF", "EDIT", "TRANSPARENT", "SEGMENT", "BG_REMOVAL", "UPSCALE", "VECTOR", "TRACE"):
            self.assertTrue(any(re.match(r"^%s\s+configured \(fal\)$" % op, d) for d in keys.detail), (op, keys.detail))
        self.assertEqual(rep.one("image-router").sev, "PASS", "the stub image-router lists GEN as configured")
        self.sb.assert_no_secret_leak(res, c101=self.c101)

    def test_operations_without_a_preset_are_reported_as_not_configured(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(fal=ALL_SET["fal"], ops=False)
        res, rep = self.standard()
        keys = rep.one("keys-image")
        self.assertEqual(keys.sev, "FAIL", res.stdout)
        self.assertIn("GEN (text-to-image) is not configured", keys.msg)
        self.assertTrue(any(re.match(r"^GEN\s+not configured$", d) for d in keys.detail), keys.detail)

    def test_a_backend_that_is_not_recorded_as_fal_only_warns(self) -> None:
        self.stack_env(fal="")  # no state file at all
        res, rep = self.standard()
        keys = rep.one("keys-image")
        self.assertEqual(keys.sev, "WARN", res.stdout)
        self.assertIn("not recorded", keys.msg)
        self.assertIn("empty key", keys.msg)
        self.write_state(profile="standard", image_backend="keep")
        res, rep = self.standard()
        self.assertEqual(rep.one("keys-image").sev, "WARN", "keep never turns a gap into a failure")

    def test_the_none_backend_is_informational(self) -> None:
        self.write_state(profile="standard", image_backend="none")
        self.stack_env(fal="", ops=False)
        res, rep = self.standard()
        keys = rep.one("keys-image")
        self.assertEqual(keys.sev, "INFO", res.stdout)
        self.assertIn("backend none", keys.msg)
        self.assertEqual([r for r in rep.rows[IMAGE] if r.id in ("keys-image", "image-router") and r.sev == "FAIL"], [])

    def test_fix_commands_carry_a_recorded_none_backend(self) -> None:
        """D12 (RV1 R1): with image_backend=none recorded, no printed fix may turn fal back on: the fix for the fal-runner's
        empty FAL_KEY (and for every other key) carries --image-backend none."""
        self.write_state(profile="standard", image_backend="none", fal_mcp=True)
        self.stack_env(ops=False, **{**ALL_SET, "fal": "", "spider": ""})
        self.link_agent()
        res, rep = self.standard()
        agent = rep.one("fal-runner")
        self.assertEqual(agent.sev, "WARN", res.stdout)
        self.assertIn("install-linux.sh --keys --keys-only --image-backend none", agent.msg)
        spider = rep.one("keys-research", "spider")
        self.assertIn("install-linux.sh --keys --keys-only --image-backend none", spider.msg)
        self.assertEqual(rep.one("keys-image").sev, "INFO", res.stdout)
        res, rep = self.ci()
        self.assertIn("install-linux.sh --keys --keys-only --profile ci --image-backend none", rep.one("fal-runner").msg)
        self.assertIn("--keys-from-env", rep.one("fal-runner").msg)
        # D13: the unattended form carries the recorded backend too
        self.assertIn("install-linux.sh --keys-only --keys-from-env --profile ci --image-backend none", rep.one("fal-runner").msg)

    def test_no_key_fix_reruns_the_full_install_over_recorded_choices(self) -> None:
        """D14 (QC-2): an install like `--preset research --no-fal-mcp` records preset, packs and fal_mcp=false. A fix that
        re-ran the whole installer (`install-linux.sh --keys` alone) would bring the profile's defaults back (FAL_MCP=1
        links fal-runner again, PRESET=everything links every pack). So every key fix the doctor prints, in every profile,
        is the user's literal `install-linux.sh --keys` followed by `--keys-only` (then only --profile and --image-backend,
        D12), and in ci also the unattended `install-linux.sh --keys-only --keys-from-env`: no printed command with a key
        flag lacks --keys-only. The INFO line that suggests adding fal follows the same rule."""
        self.write_state(profile="standard", image_backend="fal", preset="research",
                         packs="core,coding-core,image,graphic-design", fal_mcp=False)
        self.stack_env(fal="", spider="")
        for name, run in (("standard", self.standard), ("ci", self.ci)):
            res, rep = run()
            for ident in ("keys-image", "image-router"):
                self.assertIn("install-linux.sh --keys --keys-only", rep.one(ident).msg, (name, ident, res.stdout))
            self.assertIn("install-linux.sh --keys --keys-only", rep.one("keys-research", "spider").msg, name)
            key_cmds = [(row, flags) for row in rep.all_rows() for flags in install_commands(row.msg)
                        if any(f.startswith("--keys") for f in flags)]
            self.assertGreaterEqual(len(key_cmds), 3, res.stdout)
            for row, flags in key_cmds:
                self.assertIn(flags[:2], (["--keys", "--keys-only"], ["--keys-only", "--keys-from-env"]), (name, row))
                self.assertEqual([f for f in flags[2:] if f not in ("--profile", "--image-backend")], [], (name, row))
                self.assertEqual("--profile" in flags, name == "ci", (name, row))
                for opt in ("--preset", "--packs", "--fal-mcp", "--no-fal-mcp"):
                    self.assertNotIn(opt, flags, (name, row))
            unattended = [flags for _row, flags in key_cmds if "--keys-from-env" in flags]
            self.assertEqual(bool(unattended), name == "ci", "only ci adds the unattended form")
        # backend keep with nothing configured: the INFO line that suggests adding fal is a key re-run too
        self.write_state(profile="standard", image_backend="keep", preset="research", fal_mcp=False)
        self.stack_env(fal="", ops=False)
        res, rep = self.standard()
        row = rep.one("keys-image")
        self.assertEqual(row.sev, "INFO", res.stdout)
        self.assertIn("install-linux.sh --keys --keys-only --image-backend fal", row.msg)
        self.assertEqual(install_commands(row.msg), [["--keys", "--keys-only", "--image-backend"]], row.msg)

    def test_a_missing_stack_env_follows_the_profile_rule(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        res, rep = self.standard()
        row = rep.one("stack-env", "is missing")
        self.assertEqual(row.sev, "FAIL", res.stdout)
        self.assertIn("install-linux.sh --keys --keys-only", row.msg)
        res, rep = self.ci()
        self.assertEqual(rep.one("stack-env", "is missing").sev, "WARN", res.stdout)

    def test_a_group_readable_stack_env_fails_in_every_profile(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(**ALL_SET, mode=0o644)
        for run in (self.standard, self.ci):
            res, rep = run()
            row = rep.one("stack-env-mode")
            self.assertEqual(row.sev, "FAIL", res.stdout)
            self.assertIn("mode 644", row.msg)
            self.assertIn("chmod 600", row.msg)
        self.stack_env(**ALL_SET)
        res, rep = self.standard()
        self.assertEqual(rep.one("stack-env-mode").sev, "PASS")
        self.assertIn("mode 600", rep.one("stack-env-mode").msg)

    def test_a_dotfiles_symlink_is_followed_and_named(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        target_dir = self.sb.base / "dotfiles"
        loc = self.stack_env(**ALL_SET, target_dir=target_dir)
        self.assertTrue(loc.is_symlink())
        res, rep = self.standard()
        row = rep.one("stack-env")
        self.assertEqual(row.sev, "PASS", res.stdout)
        self.assertIn("symlink followed", row.msg)
        self.assertIn("dotfiles", row.msg, "the target is named")
        self.assertEqual(rep.one("keys-image").sev, "PASS", "the keys are read through the link")
        self.sb.assert_no_secret_leak(res, c101=self.c101, trees=[self.sb.home, self.sb.tmpdir, target_dir],
                                      allow=env_file_allowances(target_dir / "stack.env") + (".claude/stack.env",))

    def test_claude_stack_env_is_honoured_and_passed_to_c101s_router(self) -> None:
        self.write_state(profile="ci", image_backend="fal")
        elsewhere = self.sb.base / "keys" / "my.env"
        self.stack_env(**ALL_SET, path=elsewhere)
        res, rep = self.ci(CLAUDE_STACK_ENV=str(elsewhere))
        row = rep.one("stack-env", "from CLAUDE_STACK_ENV")
        self.assertEqual(row.sev, "PASS", res.stdout)
        self.assertEqual(rep.one("keys-image").sev, "PASS", "~/.claude/stack.env does not exist; the named file was read")
        checks = [r for r in stub_calls(self.c101, "image-router") if "--check" in r["argv"]]
        self.assertTrue(checks, "the doctor asks c101's router for its own view")
        self.assertEqual(checks[-1]["env_values"].get("CLAUDE_STACK_ENV"), str(elsewhere))


class ResearchSectionTests(DoctorCase):
    def research_rows(self, rep: Report) -> Dict[str, Row]:
        return {r.msg.split(":", 1)[0]: r for r in rep.rows[RESEARCH] if r.id == "keys-research" and ":" in r.msg}

    def test_spider_without_a_key_fails_in_standard_and_warns_in_ci(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(fal=ALL_SET["fal"], exa=ALL_SET["exa"], jina=ALL_SET["jina"], spider="")
        res, rep = self.standard()
        spider = self.research_rows(rep)["spider"]
        self.assertEqual(spider.sev, "FAIL", res.stdout)
        self.assertIn("SPIDER_API_KEY is empty", spider.msg)
        self.assertIn("requires a key", spider.msg)
        self.assertIn("install-linux.sh --keys --keys-only", spider.msg)
        res, rep = self.ci()
        self.assertEqual(self.research_rows(rep)["spider"].sev, "WARN", res.stdout)
        for profile in ("minimal", "full", "headless"):
            res, rep = self.doctor("--profile", profile, "--skip-c101", "--offline")
            self.assertEqual(self.research_rows(rep)["spider"].sev, "FAIL", profile)

    def test_exa_and_jina_without_a_key_warn_and_say_they_work_anonymously(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(fal=ALL_SET["fal"], spider=ALL_SET["spider"])
        for run in (self.standard, self.ci):
            res, rep = run()
            rows = self.research_rows(rep)
            for server, name in (("exa", "EXA_API_KEY"), ("jina", "JINA_API_KEY")):
                self.assertEqual(rows[server].sev, "WARN", (server, res.stdout))
                self.assertIn("%s is empty" % name, rows[server].msg)
                self.assertIn("anonymously", rows[server].msg)
                self.assertIn("rate limits", rows[server].msg)

    def test_context7_without_a_key_is_informational(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(**{**ALL_SET, "context7": ""})
        res, rep = self.standard()
        row = self.research_rows(rep)["context7"]
        self.assertEqual(row.sev, "INFO", res.stdout)
        self.assertIn("optional", row.msg)

    def test_set_research_keys_pass_by_name_only(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(**ALL_SET)
        res, rep = self.standard()
        rows = self.research_rows(rep)
        self.assertEqual({k: v.sev for k, v in rows.items()}, {"exa": "PASS", "jina": "PASS", "spider": "PASS", "context7": "PASS"})
        self.assertIn("SPIDER_API_KEY set", rows["spider"].msg)
        self.sb.assert_no_secret_leak(res, c101=self.c101)

    def test_a_missing_headers_helper_is_reported(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(**ALL_SET)
        res, rep = self.standard()
        self.assertEqual(rep.find("keys-research", "mcp-headers missing"), [])
        (self.c101 / "bin" / "mcp-headers").unlink()
        res, rep = self.standard()
        row = rep.one("keys-research", "mcp-headers missing")
        self.assertEqual(row.sev, "WARN")

    def test_fetch_and_neural_memory_use_uvx_and_the_pin_from_c101(self) -> None:
        uvx = self.uvx
        self.write_state(profile="standard", image_backend="fal")
        self.stack_env(**ALL_SET)
        before = self.sb.snapshot()
        res, rep = self.standard()
        if not uvx.called():
            self.skipTest("a real uvx on the login PATH answered instead of the stub")
        argvs = uvx.argvs()
        self.assertIn(["--offline", "--python", "3.13", "mcp-server-fetch", "--help"], argvs)
        self.assertIn(["--offline", "--python", "3.13", "--from", "neural-memory==%s" % NM_VERSION, "nmem-mcp", "--help"], argvs,
                      "the neural-memory version is read from NM_VERSION in c101's bin/neural-memory-mcp")
        fetch = rep.one("mcp-fetch-cache", "cache warm")
        self.assertEqual(fetch.sev, "PASS", res.stdout)
        self.assertEqual(rep.one("neural-memory-cache").sev, "PASS")
        self.assertIn(NM_VERSION, rep.one("neural-memory-cache").msg)
        self.assertEqual(self.sb.snapshot(), before)


class FalMcpTests(DoctorCase):
    """D8: the fal MCP server rides on the lazy fal-runner agent; the doctor checks the link, the key and the config."""

    def test_an_installed_agent_with_a_key_passes(self) -> None:
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**ALL_SET)
        self.link_agent()
        config = self.install_config()
        res, rep = self.standard()
        agent = rep.one("fal-runner")
        self.assertEqual(agent.sev, "PASS", res.stdout)
        self.assertIn("FAL_KEY set", agent.msg)
        self.assertEqual(rep.one("fal-mcp-config").sev, "PASS")
        self.assertIn("claude --mcp-config", rep.one("fal-mcp-config").msg)
        self.assertIn(os.path.basename(str(config)), rep.one("fal-mcp-config").msg)
        self.assertEqual(rep.one("fal-mcp-reach").sev, "INFO")
        self.assertIn("--offline", rep.one("fal-mcp-reach").msg)

    def test_a_recorded_agent_that_is_missing_fails(self) -> None:
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**ALL_SET)
        res, rep = self.standard()
        agent = rep.one("fal-runner")
        self.assertEqual(agent.sev, "FAIL", res.stdout)
        self.assertIn("missing or not ours", agent.msg)
        self.assertIn("--no-fal-mcp", agent.msg, "the opt-out is named")
        config = rep.one("fal-mcp-config")
        self.assertEqual(config.sev, "WARN", "the whole-session config is optional: a WARN, not a FAIL")
        self.assertIn("missing", config.msg)

    def test_a_foreign_link_at_that_path_is_not_ours(self) -> None:
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**ALL_SET)
        link = self.sb.home / ".claude" / "agents" / "fal-runner.md"
        link.parent.mkdir(parents=True)
        os.symlink("/etc/hostname", link)
        res, rep = self.standard()
        self.assertEqual(rep.one("fal-runner").sev, "FAIL", res.stdout)

    def test_a_linked_agent_without_a_fal_key_warns_by_name(self) -> None:
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**{**ALL_SET, "fal": ""})
        self.link_agent()
        res, rep = self.standard()
        agent = rep.one("fal-runner")
        self.assertEqual(agent.sev, "WARN", res.stdout)
        self.assertIn("FAL_KEY is empty", agent.msg)
        self.assertIn("install-linux.sh --keys --keys-only", agent.msg)

    def test_the_agents_header_helper_must_exist(self) -> None:
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**ALL_SET)
        self.link_agent()
        (self.c101 / "bin" / "mcp-headers").unlink()
        res, rep = self.standard()
        agent = rep.one("fal-runner")
        self.assertEqual(agent.sev, "FAIL", res.stdout)
        self.assertIn("mcp-headers", agent.msg)

    def test_an_edited_whole_session_config_warns(self) -> None:
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**ALL_SET)
        self.link_agent()
        self.install_config('{"mcpServers": {}}\n')
        res, rep = self.standard()
        config = rep.one("fal-mcp-config")
        self.assertEqual(config.sev, "WARN", res.stdout)
        self.assertIn("differs", config.msg)

    def test_not_installed_is_informational_whatever_the_profile(self) -> None:
        self.write_state(profile="standard", image_backend="fal", fal_mcp=False)
        self.stack_env(**ALL_SET)
        for run in (self.standard, self.ci):
            res, rep = run()
            self.assertEqual(rep.one("fal-runner").sev, "INFO", res.stdout)
            self.assertIn("--no-fal-mcp", rep.one("fal-runner").msg)
            self.assertEqual(rep.one("fal-mcp-config").sev, "INFO")
            self.assertEqual(rep.one("fal-mcp-reach").sev, "INFO")
            self.assertIn("not probed", rep.one("fal-mcp-reach").msg)

    def test_the_reachability_probe_sends_no_credentials(self) -> None:
        curl = self.sb.argv_stub("curl", stdout="401")  # what the server answers a request without a key
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**ALL_SET)
        self.link_agent()
        self.install_config()
        res, rep = self.doctor("--profile", "standard", "--skip-c101")  # no --offline
        calls = curl.argvs()
        self.assertEqual(len(calls), 1, calls)
        argv = calls[0]
        self.assertEqual(argv[0], "-q", "-q first: no ~/.curlrc can add a header or a netrc")
        self.assertEqual(argv[-1], "https://mcp.fal.ai/mcp")
        self.assertEqual(argv[argv.index("--proto") + 1], "=https")
        joined = " ".join(argv).lower()
        for word in ("authorization", "bearer", "x-api-key", "fal_key"):
            self.assertNotIn(word, joined)
        for flag in ("-H", "--header", "-u", "--user", "-n", "--netrc", "--netrc-file", "--netrc-optional", "-d", "--data",
                     "-b", "--cookie", "-L", "--location", "-K", "--config"):
            self.assertNotIn(flag, argv)
        probe = rep.one("fal-mcp-reach")
        self.assertEqual(probe.sev, "PASS", res.stdout)
        self.assertIn("HTTP 401", probe.msg, "any HTTP status, a 401 included, means reachable")
        self.sb.assert_no_secret_leak(res, c101=self.c101, check_env=True)

    def test_an_unreachable_server_is_informational_and_offline_skips_the_probe(self) -> None:
        curl = self.sb.argv_stub("curl", stdout="000", code=7)
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**ALL_SET)
        self.link_agent()
        res, rep = self.doctor("--profile", "standard", "--skip-c101")
        self.assertEqual(rep.one("fal-mcp-reach").sev, "INFO", res.stdout)
        self.assertIn("not reachable", rep.one("fal-mcp-reach").msg)
        calls_before = len(curl.calls())
        res, rep = self.doctor("--profile", "standard", "--skip-c101", "--offline")
        self.assertEqual(len(curl.calls()), calls_before, "--offline must not start curl")
        self.assertIn("--offline", rep.one("fal-mcp-reach").msg)


class RuntimeAndLearningTests(DoctorCase):
    def test_the_learning_timer_is_checked_only_when_it_was_installed(self) -> None:
        self.stack_env(**ALL_SET)
        self.write_state(profile="standard", image_backend="fal", learn_timer=False)
        res, rep = self.standard()
        row = rep.one("learning-timer")
        self.assertEqual(row.sev, "INFO", res.stdout)
        self.assertIn("--learn-timer", row.msg)
        self.write_state(profile="standard", image_backend="fal", learn_timer=True)
        res, rep = self.standard()
        row = rep.one("learning-timer")
        self.assertEqual(row.sev, "WARN", "recorded as installed, but no unit or script is on disk: " + res.stdout)
        self.assertIn("c101-learn-backstop.timer missing", row.msg)
        self.assertIn("install-linux.sh --learn-timer", row.msg)

    def test_every_fix_line_names_the_profile_of_the_host(self) -> None:
        """R1: the doctor judges a host by its recorded profile, and every re-run command it prints carries that profile, so
        following one never reinstalls a minimal, headless or ci host as standard (the installer's default)."""
        self.stack_env(**ALL_SET)
        self.write_state(profile="minimal", image_backend="fal", learn_timer=True)
        res, rep = self.doctor("--skip-c101", "--offline")
        self.assertIn("profile=minimal", rep.header, res.stdout)
        self.assertIn("install-linux.sh --learn-timer --profile minimal", rep.one("learning-timer").msg)
        mentions = [r for r in rep.all_rows() if "install-linux.sh" in r.msg]
        self.assertTrue(mentions, res.stdout)
        for row in mentions:
            self.assertIn("--profile minimal", row.msg, (row.id, row.msg))
        self.write_state(profile="standard", image_backend="fal", learn_timer=True)
        res, rep = self.doctor("--skip-c101", "--offline")
        self.assertIn("install-linux.sh --learn-timer", rep.one("learning-timer").msg)
        for row in rep.all_rows():
            self.assertNotIn("--profile standard", row.msg, "standard is the installer's default and stays implicit")

    def test_playwright_browser_is_expected_only_where_the_profile_installs_it(self) -> None:
        self.stack_env(**ALL_SET)
        self.write_state(profile="standard", image_backend="fal")
        res, rep = self.standard()
        row = rep.one("playwright-browser")
        if "musl" in row.msg:
            self.skipTest("Playwright ships no Chromium for musl")
        self.assertEqual(row.sev, "WARN", res.stdout)
        self.assertIn("install-linux.sh --playwright-browser", row.msg)
        res, rep = self.doctor("--profile", "minimal", "--skip-c101", "--offline")
        row = rep.one("playwright-browser")
        self.assertEqual(row.sev, "INFO", "minimal does not install the browser: " + res.stdout)
        self.assertIn("not set up", row.msg)

    def test_c101s_own_statistics_commands_are_asked_for_their_view(self) -> None:
        self.stack_env(**ALL_SET)
        self.write_state(profile="standard", image_backend="fal")
        res, rep = self.standard()
        self.assertEqual(stack_calls(self.c101, "depth"), [["depth"]])
        self.assertIn(["learn", "report"], stack_calls(self.c101, "learn"))
        rows = rep.find("depth-stats")
        self.assertTrue(rows, res.stdout)
        self.assertTrue(any("bin/stack depth ok" in r.msg for r in rows), rows)


class SecretHygieneTests(DoctorCase):
    def test_no_key_value_reaches_the_output_or_a_c101_child(self) -> None:
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**ALL_SET)
        self.link_agent()
        self.install_config()
        res, rep = self.standard(**KEY_ENV)  # the keys are in the doctor's own environment too
        self.assertNotIn(SECRET_SENTINEL, res.stdout)
        self.assertNotIn(SECRET_SENTINEL, res.stderr)
        self.assertTrue([c for c in stub_calls(self.c101, "image-router") if "--check" in c["argv"]], "image-router --check ran")
        self.assertTrue(stack_calls(self.c101, "depth"), "bin/stack depth ran")
        # argv and environment of c101's two stubs; the doctor's own other children (uv, curl) inherit its environment by design
        self.sb.assert_no_secret_leak(res, c101=self.c101, check_env=True, argv_logs=[])
        self.assertIn("FAL_KEY set", rep.one("keys-image").msg)

    def test_keys_in_every_state_stay_out_of_the_output(self) -> None:
        for fal in ("", ALL_SET["fal"]):
            self.write_state(profile="standard", image_backend="fal")
            self.stack_env(fal=fal, exa=ALL_SET["exa"], jina="", spider=ALL_SET["spider"], context7=ALL_SET["context7"])
            for profile in ("standard", "ci"):
                res, _ = self.doctor("--profile", profile, "--skip-c101", "--offline", **KEY_ENV)
                self.sb.assert_no_secret_leak(res, c101=self.c101, check_env=True, argv_logs=[])
        # the summary says WHICH keys, never what they are
        res, rep = self.standard()
        for row in rep.all_rows():
            self.assertNotIn(SECRET_SENTINEL, row.msg)

    def test_keys_read_through_a_symlinked_stack_env_still_stay_hidden(self) -> None:
        self.write_state(profile="standard", image_backend="fal")
        target_dir = self.sb.base / "dotfiles"
        self.stack_env(**ALL_SET, target_dir=target_dir, relative=True)
        res, _ = self.standard(**KEY_ENV)
        self.assertEqual(file_mode(self.sb.home / ".claude" / "stack.env"), 0o600)
        self.sb.assert_no_secret_leak(res, c101=self.c101, check_env=True, argv_logs=[], trees=[self.sb.home, self.sb.tmpdir],
                                      allow=(".claude/stack.env",))


class RecordedFlagsTests(DoctorCase):
    """RV1 R5: the hook and model expectations follow the flags c101's installer was actually run with (state c101_flags),
    not the profile's default flags: an install --profile full --no-enforce-models expects no PreModelSwitch hook."""

    HOOK = '"$HOME"/.claude/stack/bin/stack hook %s'

    def hooks(self) -> None:
        """c101's settings.json with a SessionStart and a PreModelSwitch hook; the user's with the SessionStart one only."""
        def entry(name: str) -> List[Dict[str, Any]]:
            return [{"hooks": [{"type": "command", "command": self.HOOK % name}]}]
        proj = {"hooks": {"SessionStart": entry("session-start"), "PreModelSwitch": entry("model-switch")}}
        (self.c101 / ".claude" / "settings.json").write_text(json.dumps(proj), encoding="utf-8")
        (self.sb.home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": entry("session-start")}}),
                                                                encoding="utf-8")

    def full(self) -> Tuple[subprocess.CompletedProcess, Report]:
        return self.doctor("--profile", "full", "--skip-c101", "--offline")

    def test_recorded_flags_without_enforce_models_expect_no_model_switch_hook(self) -> None:
        self.hooks()
        self.stack_env(**ALL_SET)
        self.write_state(profile="full", image_backend="fal", c101_flags="--user --main-agent")
        res, rep = self.full()
        row = rep.one("hooks-events")
        self.assertEqual(row.sev, "PASS", res.stdout)
        self.assertIn("expected only with --enforce-models", row.msg)
        self.assertEqual(rep.find("enforce-models"), [], "no availableModels expectation either")

    def test_recorded_flags_decide_both_ways_and_the_profile_stands_in_when_nothing_was_recorded(self) -> None:
        self.hooks()
        self.stack_env(**ALL_SET)
        self.write_state(profile="standard", image_backend="fal", c101_flags="--user --main-agent --enforce-models")
        res, rep = self.standard()
        row = rep.one("hooks-events")
        self.assertEqual(row.sev, "WARN", res.stdout)
        self.assertIn("PreModelSwitch", row.msg)
        self.assertEqual(rep.one("enforce-models").sev, "WARN", res.stdout)
        self.write_state(profile="full", image_backend="fal")  # no c101_flags: the full profile's flags apply
        res, rep = self.full()
        self.assertEqual(rep.one("hooks-events").sev, "WARN", res.stdout)
        self.assertEqual(rep.one("enforce-models").sev, "WARN", res.stdout)


class DoctorChildEnvTests(DoctorCase):
    """T3 note 3: the doctor's own uv, uvx and curl children run without key variables (env -u, lib/prewarm.sh's name set),
    like c101's bin/stack and bin/image-router already did."""

    def test_uv_uvx_and_curl_inherit_no_key_variable(self) -> None:
        uv = self.sb.stubs["uv"]
        curl = self.sb.argv_stub("curl", stdout="401")
        self.write_state(profile="standard", image_backend="fal", fal_mcp=True)
        self.stack_env(**ALL_SET)
        self.link_agent()
        self.install_config()
        res, rep = self.doctor("--profile", "standard", "--skip-c101", **KEY_ENV)  # no --offline: the reach probe runs curl
        self.assertTrue(curl.called(), res.stdout)
        self.assertTrue(uv.called(), res.stdout)  # uvx is the stub too unless a real one sits on the login PATH
        self.assertEqual(rep.one("fal-mcp-reach").sev, "PASS", res.stdout)
        self.sb.assert_no_secret_leak(res, check_env=True, argv_logs=[uv, self.uvx, curl])


if __name__ == "__main__":
    unittest.main()
