"""Presets and packs: config/presets.conf, lib/presets.sh (selection rules D1 and D9) and lib/packs.py.

Four layers, all hermetic (a throw-away HOME, the stub c101 checkout of helpers with its 21-pack config/packs.json, no network):
  PresetsConfTests ..... config/presets.conf against the decided mapping (D1)
  PacksPyTests ......... `python -I lib/packs.py --packs-json FILE validate|list|resolve`
  PresetsLibTests ...... lib/presets.sh functions through bash, called as install-linux.sh calls them
  InstallerPresetTests . install-linux.sh --print-config (nothing is installed): profiles, --preset, --packs, --image-backend
The picker (--pick-preset) is driven on a pseudo terminal by writing the answers before the script starts reading.
Run them on Linux with tests/run-tests.sh (-k test_presets).
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pty
except ImportError:  # no POSIX terminals: the picker tests skip themselves
    pty = None  # type: ignore[assignment]

from helpers import LIB, PACK_NAMES, PACK_REQUIRES, ROOT, Sandbox, installer_args, make_stub_c101, needs_linux

BASH = shutil.which("bash") or "bash"
PYTHON = sys.executable

# D1 (plan.md): presets -> packs, in the order of config/presets.conf (the picker numbers them 1..7). `core` is always
# installed and not listed; an empty list (everything) selects every pack.
D1_PRESETS = {
    "research": ["core", "coding-core"],
    "coding": ["coding-core", "python", "typescript", "rust", "cpp", "go", "java", "kotlin", "haskell", "julia", "devops",
               "containers"],
    "image": ["image", "graphic-design"],
    "science": ["mathematics", "physics", "quantum", "computer-science", "computer-graphics", "julia"],
    "writing": ["writing"],
    "devops": ["devops", "containers", "python"],
    "everything": [],
}  # type: Dict[str, List[str]]
PRESET_LINE = "research, coding, image, science, writing, devops, everything"


def with_core(*packs: str) -> List[str]:
    """The pack list presets_resolve produces: core first, then the packs in first-seen order, no duplicates."""
    out = ["core"]
    for pack in packs:
        if pack not in out:
            out.append(pack)
    return out


def decoded(proc: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    if isinstance(proc.stdout, bytes):
        proc.stdout = proc.stdout.decode("utf-8", "replace")
    if isinstance(proc.stderr, bytes):
        proc.stderr = proc.stderr.decode("utf-8", "replace")
    return proc


def parse_report(stdout: str) -> Dict[str, str]:
    out = {}  # type: Dict[str, str]
    for line in stdout.splitlines():
        if line.startswith("@@") and "=" in line:
            name, value = line[2:].split("=", 1)
            out[name] = value
    return out


PRESETS_PRELUDE = r'''set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true
C101L_ROOT=@ROOT@
. "$C101L_ROOT/lib/common.sh"
. "$C101L_ROOT/lib/presets.sh"
show() {
  printf '@@PACKS=%s\n' "$PRESET_PACKS"
  printf '@@ALL=%s\n' "$PRESET_ALL"
  printf '@@RESOLVED=%s\n' "$PRESET_RESOLVED"
  printf '@@SOURCE=%s\n' "$PRESET_SOURCE"
  printf '@@PICKED=%s\n' "$PRESETS_PICKED"
  printf '@@SUMMARY=%s\n' "$(presets_summary)"
}
'''


def presets_script(body: str) -> str:
    return PRESETS_PRELUDE.replace("@ROOT@", shlex.quote(str(ROOT))) + body


class Run:
    """The outcome of one bash run of lib/presets.sh."""

    def __init__(self, proc: subprocess.CompletedProcess) -> None:
        self.proc = proc
        self.rc = proc.returncode
        self.out = proc.stdout
        self.err = proc.stderr
        self.rep = parse_report(proc.stdout)

    @property
    def packs(self) -> List[str]:
        value = self.rep.get("PACKS", "")
        return value.split(",") if value else []


# ---- config/presets.conf ---------------------------------------------------------------------------------------


class PresetsConfTests(unittest.TestCase):
    def parse(self) -> Dict[str, Dict[str, Any]]:
        out = {}  # type: Dict[str, Dict[str, Any]]
        for line in (ROOT / "config" / "presets.conf").read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            name, packs, desc = line.split("|", 2)
            out[name] = {"packs": packs.split(",") if packs else [], "desc": desc}
        return out

    def test_presets_conf_matches_the_d1_mapping(self) -> None:
        parsed = self.parse()
        self.assertEqual(list(parsed), list(D1_PRESETS))  # same names, same order (the picker numbers them)
        for name, packs in D1_PRESETS.items():
            self.assertEqual(parsed[name]["packs"], packs, name)
            self.assertTrue(parsed[name]["desc"].strip(), "preset %s has no description" % name)

    def test_every_preset_pack_is_a_c101_pack(self) -> None:
        for name, entry in self.parse().items():
            for pack in entry["packs"]:
                self.assertIn(pack, PACK_NAMES, "preset %s names an unknown pack" % name)

    def test_everything_is_the_only_preset_with_an_empty_pack_list(self) -> None:
        empty = [name for name, entry in self.parse().items() if not entry["packs"]]
        self.assertEqual(empty, ["everything"])


# ---- lib/packs.py -----------------------------------------------------------------------------------------------


class PacksPyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.c101 = make_stub_c101(self.sb.base / "c101", with_venv=False)
        self.json = self.c101 / "config" / "packs.json"

    def packs(self, *args: str, json_path: Optional[Path] = None) -> subprocess.CompletedProcess:
        proc = subprocess.run([PYTHON, "-I", str(LIB / "packs.py"), "--packs-json", str(json_path or self.json), *args],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, env=self.sb.env(),
                              cwd=str(self.sb.base), timeout=60)
        return proc

    def test_validate_accepts_known_packs_all_and_comma_lists(self) -> None:
        for args in (("rust",), ("rust", "go"), ("rust,go",), ("all",), ("core", "graphic-design,julia")):
            with self.subTest(args=args):
                res = self.packs("validate", *args)
                self.assertEqual((res.returncode, res.stdout), (0, ""), res.stderr)

    def test_validate_prints_each_unknown_name_and_exits_2(self) -> None:
        res = self.packs("validate", "rust", "nosuch", "go,other")
        self.assertEqual(res.returncode, 2, res.stderr)
        self.assertEqual(res.stdout.splitlines(), ["nosuch", "other"])

    def test_list_prints_the_21_packs_in_file_order(self) -> None:
        res = self.packs("list")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(), list(PACK_NAMES))
        self.assertEqual(len(PACK_NAMES), 21)

    def test_resolve_adds_the_transitive_requirements_core_first(self) -> None:
        for names, want in ((("python",), "core,coding-core,python"),
                            (("containers",), "core,coding-core,devops,containers"),
                            (("graphic-design",), "core,image,graphic-design"),
                            (("rust,go",), "core,rust,go"),
                            (("core",), "core")):
            with self.subTest(names=names):
                res = self.packs("resolve", *names)
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertEqual(res.stdout.strip(), want)

    def test_resolve_without_names_or_with_all_is_every_pack(self) -> None:
        for args in ((), ("all",)):
            with self.subTest(args=args):
                res = self.packs("resolve", *args)
                self.assertEqual(res.returncode, 0, res.stderr)
                listed = res.stdout.strip().split(",")
                self.assertEqual(listed[0], "core")
                self.assertEqual(sorted(listed), sorted(PACK_REQUIRES))
                self.assertEqual(len(listed), len(set(listed)))

    def test_resolve_reports_unknown_names_like_validate(self) -> None:
        res = self.packs("resolve", "rust", "nosuch")
        self.assertEqual((res.returncode, res.stdout.splitlines()), (2, ["nosuch"]))

    def test_exit_1_is_a_broken_input_never_an_unknown_pack(self) -> None:
        """Exit 2 always means "unknown pack name": a missing file, bad JSON, no packs object or a usage error is exit 1."""
        junk = self.sb.base / "junk.json"
        junk.write_text("not json {", encoding="utf-8")
        nopacks = self.sb.base / "nopacks.json"
        nopacks.write_text(json.dumps({"packs": {}}), encoding="utf-8")
        for label, res in (("missing", self.packs("list", json_path=self.sb.base / "absent.json")),
                           ("not json", self.packs("list", json_path=junk)),
                           ("no packs", self.packs("list", json_path=nopacks)),
                           ("no command", self.packs())):
            with self.subTest(label):
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("cannot read", self.packs("list", json_path=self.sb.base / "absent.json").stderr)

    def test_help_exits_0(self) -> None:
        res = self.packs("--help")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("validate", res.stdout)


# ---- lib/presets.sh through bash -------------------------------------------------------------------------------


class PresetsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.c101 = make_stub_c101(self.sb.base / "c101", with_venv=False)
        self.packs_json = self.c101 / "config" / "packs.json"

    def bash(self, body: str, *, stdin: Optional[str] = None, **env: str) -> Run:
        kwargs = {"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin.encode("utf-8")}
        proc = subprocess.run([BASH, "-c", presets_script(body)], capture_output=True, env=self.sb.env(**env),
                              cwd=str(self.sb.base), timeout=120, **kwargs)
        return Run(decoded(proc))

    def resolve(self, profile_preset: str = "", profile_packs: str = "", cli_preset: str = "", cli_packs: str = "",
                pick: int = 0, extra: str = "", **env: str) -> Run:
        """presets_resolve with the five arguments install-linux.sh passes, then `extra`, then show."""
        args = " ".join(shlex.quote(a) for a in (profile_preset, profile_packs, cli_preset, cli_packs, str(pick)))
        return self.bash("presets_resolve %s\n%sshow\n" % (args, extra), **env)


@needs_linux
class PresetsLibTests(PresetsCase):
    # selection -------------------------------------------------------------------------------------------------

    def test_every_preset_resolves_to_its_packs_with_core_first(self) -> None:
        for name, packs in D1_PRESETS.items():
            with self.subTest(preset=name):
                run = self.resolve(cli_preset=name)
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual(run.rep["SOURCE"], "cli")
                if not packs:
                    self.assertEqual((run.rep["ALL"], run.rep["PACKS"], run.rep["RESOLVED"]), ("1", "", "everything"))
                    continue
                self.assertEqual((run.rep["ALL"], run.rep["RESOLVED"]), ("0", name))
                self.assertEqual(run.packs, with_core(*packs))

    def test_preset_union_with_packs(self) -> None:
        """preset-union-with-packs: --preset and --packs are one command-line selection, unioned."""
        run = self.resolve(cli_preset="science", cli_packs="rust,go")
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.packs, with_core(*D1_PRESETS["science"], "rust", "go"))
        self.assertEqual((run.rep["RESOLVED"], run.rep["SOURCE"], run.rep["ALL"]), ("science", "cli", "0"))
        self.assertEqual(run.rep["SUMMARY"], "preset=science packs=%s (cli)" % ",".join(run.packs))

    def test_several_presets_are_unioned_in_first_seen_order(self) -> None:
        for spec in ("research,writing", "research writing", "research, writing"):
            with self.subTest(spec=spec):
                run = self.resolve(cli_preset=spec)
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual(run.packs, ["core", "coding-core", "writing"])
                self.assertEqual(run.rep["RESOLVED"], "research,writing")
        overlap = self.resolve(cli_preset="coding,devops,coding")
        self.assertEqual(overlap.packs, with_core(*D1_PRESETS["coding"]))
        self.assertEqual(overlap.rep["RESOLVED"], "coding,devops")

    def test_everything_means_all(self) -> None:
        """everything-means-all: an empty pack list is every pack, whatever else is selected next to it."""
        for cli_preset, cli_packs in (("everything", ""), ("research,everything", ""), ("everything", "rust"),
                                      ("writing,everything", "go")):
            with self.subTest(preset=cli_preset, packs=cli_packs):
                run = self.resolve(cli_preset=cli_preset, cli_packs=cli_packs)
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual((run.rep["ALL"], run.rep["PACKS"], run.rep["RESOLVED"]), ("1", "", "everything"))
        self.assertEqual(self.resolve(cli_preset="everything").rep["SUMMARY"], "preset=everything packs=all (cli)")

    def test_pack_all_and_no_selection_at_all_mean_all(self) -> None:
        for kwargs in ({"cli_packs": "all"}, {"cli_packs": "rust,all"}, {}):
            with self.subTest(kwargs=kwargs):
                run = self.resolve(**kwargs)
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual((run.rep["ALL"], run.rep["PACKS"], run.rep["RESOLVED"]), ("1", "", "everything"))
        self.assertEqual(self.resolve().rep["SOURCE"], "profile")

    def test_command_line_selection_replaces_the_profile_preset(self) -> None:
        """D9: --preset/--packs REPLACE the profile's PRESET and C101_PACKS (otherwise they would do nothing on the
        profiles whose PRESET is everything); without a command-line selection the profile applies, unioned."""
        cases = (
            (dict(profile_preset="everything", cli_preset="writing"), with_core("writing"), "writing", "cli"),
            (dict(profile_preset="everything", cli_packs="rust,go"), with_core("rust", "go"), "", "cli"),
            (dict(profile_preset="image", profile_packs="rust", cli_preset="writing"), with_core("writing"), "writing", "cli"),
            (dict(profile_preset="image", profile_packs="core"), with_core("image", "graphic-design"), "image", "profile"),
            (dict(profile_preset="image", profile_packs="rust"), with_core("image", "graphic-design", "rust"), "image", "profile"),
        )
        for kwargs, packs, resolved, source in cases:
            with self.subTest(kwargs=kwargs):
                run = self.resolve(**kwargs)
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual((run.packs, run.rep["RESOLVED"], run.rep["SOURCE"]), (packs, resolved, source))
        everything = self.resolve(profile_preset="everything")
        self.assertEqual((everything.rep["ALL"], everything.rep["SOURCE"]), ("1", "profile"))
        self.assertEqual(self.resolve(cli_packs="rust,go").rep["SUMMARY"], "preset=- packs=core,rust,go (cli)")

    # adding the image pack ---------------------------------------------------------------------------------------

    def test_adding_the_image_preset_adds_image_and_graphic_design(self) -> None:
        """What install-linux.sh does for an effective image backend of fal (D3): presets_add_preset image."""
        run = self.resolve(cli_preset="writing", extra="presets_add_preset image\n")
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.packs, with_core("writing", "image", "graphic-design"))
        self.assertEqual(run.rep["RESOLVED"], "writing,image")
        again = self.resolve(cli_preset="writing", extra="presets_add_preset image\npresets_add_preset image\n")
        self.assertEqual((again.packs, again.rep["RESOLVED"]), (run.packs, "writing,image"))

    def test_adding_a_pack_or_preset_is_a_no_op_when_every_pack_is_selected(self) -> None:
        run = self.resolve(cli_preset="everything", extra="presets_add_pack image\npresets_add_preset image\n")
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual((run.rep["ALL"], run.rep["PACKS"], run.rep["RESOLVED"]), ("1", "", "everything"))

    def test_presets_add_pack_adds_one_pack_and_all_selects_everything(self) -> None:
        run = self.resolve(cli_preset="writing", extra="presets_add_pack rust go\n")
        self.assertEqual(run.packs, with_core("writing", "rust", "go"))
        self.assertEqual(run.rep["RESOLVED"], "writing")  # packs added by hand do not change the preset names
        everything = self.resolve(cli_preset="writing", extra="presets_add_pack all\n")
        self.assertEqual((everything.rep["ALL"], everything.rep["PACKS"]), ("1", ""))

    # errors: all exit 2 -------------------------------------------------------------------------------------------

    def test_unknown_preset_exit_2(self) -> None:
        """unknown-preset-exit-2: the message names the preset and lists the real ones; no result is half-set."""
        for kwargs, message in ((dict(cli_preset="nosuch"), "--preset: unknown preset 'nosuch'"),
                                (dict(cli_preset="writing,nosuch"), "unknown preset 'nosuch'"),
                                (dict(profile_preset="bogus"), "profile PRESET: unknown preset 'bogus'")):
            with self.subTest(kwargs=kwargs):
                run = self.resolve(**kwargs)
                self.assertEqual(run.rc, 2, run.out + run.err)
                self.assertIn(message, run.err)
                self.assertIn("presets: " + PRESET_LINE, run.err)
                self.assertEqual(run.rep, {})  # show never ran

    def test_invalid_preset_and_pack_names_exit_2(self) -> None:
        for kwargs, message in ((dict(cli_preset="Research"), "invalid preset name 'Research'"),
                                (dict(cli_preset="-research"), "invalid preset name '-research'"),
                                (dict(cli_packs="Bad Pack"), "invalid pack name 'Bad'"),
                                (dict(cli_packs="rust;ls"), "invalid pack name 'rust;ls'"),
                                (dict(profile_packs="Rust"), "invalid pack name 'Rust'")):
            with self.subTest(kwargs=kwargs):
                run = self.resolve(**kwargs)
                self.assertEqual(run.rc, 2, run.out + run.err)
                self.assertIn(message, run.err)
        for body, message in (("presets_add_pack Bad_Name\n", "invalid pack name 'Bad_Name'"),
                              ("presets_add_preset nosuch\n", "unknown preset 'nosuch'")):
            with self.subTest(body=body):
                run = self.bash(body)
                self.assertEqual(run.rc, 2, run.err)
                self.assertIn(message, run.err)

    def test_unknown_pack_exits_2_against_the_c101_packs_json(self) -> None:
        """Pack names are only checked for syntax while resolving; presets_validate_packs (after the checkout exists)
        compares every name met so far with c101's packs.json through lib/packs.py and exits 2 on an unknown one."""
        run = self.bash('presets_resolve "" "" "writing" "rust,nosuchpack" 0\npresets_validate_packs %s\nshow\n'
                        % shlex.quote(str(self.packs_json)))
        self.assertEqual(run.rc, 2, run.out + run.err)
        self.assertIn("unknown pack(s): nosuchpack", run.err)
        self.assertIn("c101 packs: core, image, rust", run.err)
        known = self.bash('presets_resolve "" "" "writing" "rust,go" 0\npresets_validate_packs %s\nshow\n'
                          % shlex.quote(str(self.packs_json)))
        self.assertEqual(known.rc, 0, known.err)
        self.assertEqual(known.packs, with_core("writing", "rust", "go"))

    def test_every_preset_and_the_image_preset_validate_against_the_stub_packs_json(self) -> None:
        for name in D1_PRESETS:
            with self.subTest(preset=name):
                run = self.bash('presets_resolve "" "" %s "" 0\npresets_add_preset image\npresets_validate_packs %s\nshow\n'
                                % (shlex.quote(name), shlex.quote(str(self.packs_json))))
                self.assertEqual(run.rc, 0, run.err)

    def test_pack_validation_is_skipped_until_the_json_exists_and_an_unreadable_one_is_an_error(self) -> None:
        missing = self.bash('presets_resolve "" "" "" "rust" 0\npresets_validate_packs /nonexistent/packs.json\nshow\n')
        self.assertEqual(missing.rc, 0, missing.err)
        self.assertIn("pack names not checked yet", missing.out)
        junk = self.sb.base / "junk.json"
        junk.write_text("not json {", encoding="utf-8")
        broken = self.bash('presets_resolve "" "" "" "rust" 0\npresets_validate_packs %s\n' % shlex.quote(str(junk)))
        self.assertEqual(broken.rc, 1, broken.out + broken.err)
        self.assertIn("could not check pack names", broken.err)

    # the picker ---------------------------------------------------------------------------------------------------

    def test_pick_preset_without_a_terminal_is_a_usage_error(self) -> None:
        for stdin in (None, "1\n"):
            with self.subTest(stdin=stdin):
                run = self.bash('presets_resolve "" "" "" "" 1\nshow\n', stdin=stdin)
                self.assertEqual(run.rc, 2, run.out + run.err)
                self.assertIn("--pick-preset needs an interactive terminal on stdin", run.err)
                self.assertIn("--preset NAME[,NAME]", run.err)
                self.assertEqual(run.rep, {})

    def run_on_terminal(self, body: str, typed: bytes) -> Run:
        """`body` with stdin on a pseudo terminal that already holds `typed` (what the person would type)."""
        master, slave = pty.openpty()
        try:
            os.write(master, typed)
            proc = subprocess.run([BASH, "-c", presets_script(body)], stdin=slave, capture_output=True, env=self.sb.env(),
                                  cwd=str(self.sb.base), timeout=60, start_new_session=True)
        finally:
            os.close(master)
            os.close(slave)
        return Run(decoded(proc))

    @unittest.skipIf(pty is None, "needs the pty module")
    def test_picker_takes_numbers_and_names_and_unions_them(self) -> None:
        for typed, picked, packs in ((b"1,3\n", "research,image", with_core("coding-core", "image", "graphic-design")),
                                     (b"writing science\n", "writing,science", with_core("writing", *D1_PRESETS["science"])),
                                     (b"3 3, image\n", "image", with_core("image", "graphic-design"))):
            with self.subTest(typed=typed):
                run = self.run_on_terminal('presets_resolve "" "" "" "" 1\nshow\n', typed)
                self.assertEqual(run.rc, 0, run.err)
                self.assertEqual(run.rep["PICKED"], picked)
                self.assertEqual((run.packs, run.rep["RESOLVED"], run.rep["SOURCE"]), (packs, picked, "cli"))
                for name in D1_PRESETS:
                    self.assertIn(name, run.err)  # the menu lists every preset
                self.assertIn("Presets [numbers or names; Enter keeps", run.err)

    @unittest.skipIf(pty is None, "needs the pty module")
    def test_picker_enter_keeps_the_profile_default(self) -> None:
        run = self.run_on_terminal('presets_resolve "writing" "" "" "" 1\nshow\n', b"\n")
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual((run.rep["PICKED"], run.rep["SOURCE"], run.rep["RESOLVED"]), ("", "profile", "writing"))
        self.assertIn("Enter keeps the profile default (writing)", run.err)
        self.assertEqual(run.packs, with_core("writing"))

    @unittest.skipIf(pty is None, "needs the pty module")
    def test_picker_gives_up_after_three_bad_answers(self) -> None:
        run = self.run_on_terminal('presets_resolve "" "" "" "" 1\nshow\n', b"9\nfoo\n0\n")
        self.assertEqual(run.rc, 2, run.out + run.err)
        self.assertIn("not a preset: 9 (pick 1-7 or a name)", run.err)
        self.assertIn("no valid choice after 3 tries", run.err)
        self.assertEqual(run.rep, {})

    # the preset file ---------------------------------------------------------------------------------------------

    def test_presets_file_hook_and_names(self) -> None:
        custom = self.sb.base / "presets.conf"
        custom.write_text("# my presets\n\nmine|rust,go|my own\nnothing||every pack\n", encoding="utf-8")
        run = self.bash('printf \'@@NAMES=%s\\n\' "$(presets_names)"\npresets_resolve "" "" mine "" 0\nshow\n',
                        C101L_PRESETS_FILE=str(custom))
        self.assertEqual(run.rc, 0, run.err)
        self.assertEqual(run.rep["NAMES"], "mine, nothing")
        self.assertEqual(run.packs, with_core("rust", "go"))
        everything = self.resolve(cli_preset="nothing", C101L_PRESETS_FILE=str(custom))
        self.assertEqual((everything.rep["ALL"], everything.rep["RESOLVED"]), ("1", "everything"))
        self.assertEqual(self.bash("printf '@@NAMES=%s\\n' \"$(presets_names)\"\n").rep["NAMES"], PRESET_LINE)

    def test_a_malformed_presets_file_is_an_installer_bug_and_dies(self) -> None:
        for label, text, message in (("no separators", "mine\n", "expected name|packs|description"),
                                     ("upper case name", "Bad|rust|x\n", "invalid preset name 'Bad'"),
                                     ("spaces in packs", "bad|rust, go|x\n", "packs must be a comma list"),
                                     ("duplicate", "dup|rust|a\ndup|go|b\n", "duplicate preset 'dup'"),
                                     ("empty", "# nothing\n", "defines no presets")):
            with self.subTest(label):
                custom = self.sb.base / "bad.conf"
                custom.write_text(text, encoding="utf-8")
                run = self.resolve(cli_preset="", C101L_PRESETS_FILE=str(custom))
                self.assertEqual(run.rc, 1, run.out + run.err)
                self.assertIn(message, run.err)


# ---- install-linux.sh --print-config ---------------------------------------------------------------------------


@needs_linux
class InstallerPresetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)
        self.stub = make_stub_c101(self.sb.base / "c101")

    def install(self, *args: str, stdin: Optional[str] = None, **env: str) -> subprocess.CompletedProcess:
        kwargs = {"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin}
        return subprocess.run(installer_args(*args), capture_output=True, text=True, env=self.sb.env(**env),
                              cwd=str(self.sb.base), timeout=120, **kwargs)

    def config(self, *args: str, **env: str) -> Dict[str, str]:
        res = self.install("--print-config", *args, **env)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)

    def packs_of(self, cfg: Dict[str, str]) -> List[str]:
        return cfg["PACKS"].split(",")

    def test_profile_defaults_resolve_to_the_decided_packs(self) -> None:
        """D3: minimal and ci install the image pack (PRESET=image); standard, full and headless install every pack."""
        for profile, preset, packs in (("minimal", "image", with_core("image", "graphic-design")),
                                       ("ci", "image", with_core("image", "graphic-design")),
                                       ("standard", "everything", None), ("full", "everything", None),
                                       ("headless", "everything", None)):
            with self.subTest(profile=profile):
                cfg = self.config("--profile", profile)
                self.assertEqual(cfg["PRESET"], preset)
                if packs is None:
                    self.assertEqual(cfg["PACKS"], "all")
                    self.assertNotIn("--packs", cfg["C101_FLAGS"])  # c101's own default: every pack
                else:
                    self.assertEqual(self.packs_of(cfg), packs)
                    self.assertIn("--packs " + ",".join(packs), cfg["C101_FLAGS"])

    def test_install_linux_preset_and_packs_are_unioned(self) -> None:
        cfg = self.config("--image-backend", "none", "--preset", "science", "--packs", "rust,go")
        self.assertEqual(self.packs_of(cfg), with_core(*D1_PRESETS["science"], "rust", "go"))
        self.assertEqual(cfg["PRESET"], "science")
        repeated = self.config("--image-backend", "none", "--preset", "research", "--preset=writing")
        self.assertEqual(self.packs_of(repeated), ["core", "coding-core", "writing"])
        self.assertEqual(repeated["PRESET"], "research,writing")

    def test_install_linux_command_line_selection_replaces_the_profile(self) -> None:
        """D9 end to end: the standard profile's PRESET=everything must not swallow --packs or --preset."""
        packs_only = self.config("--profile", "standard", "--image-backend", "none", "--packs", "rust,go")
        self.assertEqual((packs_only["PRESET"], self.packs_of(packs_only)), ("", ["core", "rust", "go"]))
        preset = self.config("--profile", "headless", "--image-backend", "none", "--preset", "writing")
        self.assertEqual((preset["PRESET"], self.packs_of(preset)), ("writing", ["core", "writing"]))
        every = self.config("--profile", "minimal", "--preset", "everything")
        self.assertEqual((every["PRESET"], every["PACKS"]), ("everything", "all"))

    def test_image_backend_adds_image_pack(self) -> None:
        """image-backend-adds-image-pack (D3): a fal image backend implies the image pack (image + graphic-design) next to
        whatever else was selected; --image-backend none opts out and removes that implication."""
        fal = self.config("--profile", "standard", "--image-backend", "fal", "--preset", "writing")
        self.assertEqual(self.packs_of(fal), with_core("writing", "image", "graphic-design"))
        self.assertEqual(fal["PRESET"], "writing,image")
        self.assertEqual(fal["IMAGE_BACKEND"], "fal")
        default = self.config("--profile", "standard", "--preset", "writing")  # the profiles default to fal
        self.assertEqual(self.packs_of(default), self.packs_of(fal))
        packs_only = self.config("--profile", "standard", "--packs", "rust,go")
        self.assertEqual(self.packs_of(packs_only), with_core("rust", "go", "image", "graphic-design"))
        for backend in ("none", "keep"):
            with self.subTest(backend=backend):
                cfg = self.config("--profile", "standard", "--image-backend", backend, "--preset", "writing")
                self.assertEqual(self.packs_of(cfg), ["core", "writing"])
                self.assertEqual(cfg["PRESET"], "writing")
        every = self.config("--profile", "standard", "--image-backend", "fal")
        self.assertEqual(every["PACKS"], "all")  # every pack already includes the image pack

    def test_keep_uses_the_backend_recorded_by_an_earlier_run(self) -> None:
        """--image-backend keep never forgets a recorded fal: the image pack stays; a recorded none does not add it."""
        state = self.sb.home / ".local" / "state" / "c101-linux" / "state.json"
        state.parent.mkdir(parents=True)
        for recorded, want in (("fal", with_core("writing", "image", "graphic-design")), ("none", ["core", "writing"])):
            with self.subTest(recorded=recorded):
                state.write_text(json.dumps({"version": 1, "image_backend": recorded}) + "\n", encoding="utf-8")
                cfg = self.config("--profile", "standard", "--image-backend", "keep", "--preset", "writing")
                self.assertEqual(self.packs_of(cfg), want)

    def test_install_linux_unknown_preset_exits_2(self) -> None:
        for args, message in ((("--preset", "nosuch"), "unknown preset 'nosuch'"),
                              (("--preset", "writing,nosuch"), "unknown preset 'nosuch'"),
                              (("--preset", "Writing"), "invalid preset name 'Writing'"),
                              (("--packs", "Rust"), "--packs takes a comma list of pack names")):
            with self.subTest(args=args):
                res = self.install("--print-config", *args)
                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
                self.assertIn(message, res.stderr)
                self.assertEqual(res.stdout, "")

    def test_install_linux_unknown_pack_exits_2_before_c101_runs(self) -> None:
        res = self.install("--dry-run", "--profile", "minimal", "--packs", "nosuchpack", "--source", str(self.stub))
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("unknown pack(s): nosuchpack", res.stderr)
        self.assertFalse((self.stub / "calls.log").exists())

    def test_install_linux_pick_preset_without_a_terminal_is_a_usage_error(self) -> None:
        for stdin in (None, "1\n"):
            with self.subTest(stdin=stdin):
                res = self.install("--print-config", "--pick-preset", stdin=stdin)
                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
                self.assertIn("--pick-preset needs an interactive terminal on stdin", res.stderr)

    def test_install_linux_pick_preset_with_keys_only_is_a_usage_error(self) -> None:
        res = self.install("--keys-only", "--pick-preset")
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertIn("--pick-preset has no effect with --keys-only", res.stderr)

    def test_help_names_the_presets_and_the_preset_flags(self) -> None:
        res = self.install("--help")
        self.assertEqual(res.returncode, 0, res.stderr)
        for flag in ("--preset", "--pick-preset", "--image-backend", "--packs"):
            self.assertIn(flag, res.stdout)
        self.assertIn(PRESET_LINE, res.stdout)


if __name__ == "__main__":
    unittest.main()
