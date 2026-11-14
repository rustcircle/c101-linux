#!/usr/bin/env python3
"""Safe reader and merger for c101's stack.env (API keys for the MCP servers). Stdlib only, Python 3.9+.

The stack reads this file at runtime: bin/mcp-run (stdio servers: image, fetch), bin/mcp-headers (HTTP headers for
exa, jina, spider, context7, fal) and c101's own parsers (stack/envfile.py, stack_mcp/image_router/config.py). This
helper is how the installer and the doctor touch it without ever showing a value.

  stackenv.py names --file F [--keys A,B] [--placeholder P]
  stackenv.py ops   --file F [--ops-file image-ops.json] [--placeholder P]
  stackenv.py check --file F
  stackenv.py merge --file F [--template T] [--preset P] [--placeholder your-fal-key] [--preset-key FAL_KEY]
                    [--overwrite] [--dry-run]            NAME=VALUE lines on STDIN ONLY

--file defaults to $CLAUDE_STACK_ENV, else ~/.claude/stack.env (the lookup bin/mcp-run uses). The file is PARSED, never
sourced. Every JSON result is one line on stdout and holds names, statuses and paths only, never a value; errors go to
stderr with the same rule. Values enter through stdin and nowhere else: no option takes a secret, nothing is placed in
argv, and a terminal on stdin is refused (it would echo the key).

names   JSON {NAME: "set"|"empty"|"absent"}. Without --keys: every name assigned in the file. With --keys: exactly
        those names, "absent" when unassigned. A value equal to --placeholder counts as "empty". A missing file
        gives {} (or all "absent"). The last assignment of a name decides (envfile.py and mcp-headers semantics).
ops     JSON {OP: {"status": "configured"|"key-empty"|"not-configured", "provider": "fal"|"other"|"none",
        "missing": [env names], "issues": [fixed codes]}} for GEN REF EDIT TRANSPARENT SEGMENT BG_REMOVAL UPSCALE
        VECTOR TRACE VECTOR_MANIPULATION (or the keys of --ops-file's "ops"), judged like c101's image router
        (config.py load_op): a tool exists once URL, KEY and MODEL are set. "key-empty" = only the KEY is missing.
        Provider "fal" = IMAGE_<OP>_API=fal, or no API and a fal.run / fal.ai URL host; "none" = no URL (and not
        local, not the agent-edited VECTOR_MANIPULATION default). Reads the file only, not the environment.
check   JSON {path, exists, is_symlink, target, dangling, mode, mode_ok, owner_ok, target_writable, readonly_fs,
        reason, dir, dir_issues, link_dir, link_dir_issues, dir_ok}. "target" is the realpath, mode/owner_ok describe
        the target, readonly_fs is true for /nix/store or a read-only mount, reason is
        "nix-store"|"read-only-mount"|"not-writable"|"dir-not-writable"|null. "dir" is the target's directory (its
        nearest existing ancestor when it does not exist yet), "link_dir" the directory of the link when --file is a
        symlink (else null); their *_issues list "foreign-owner" (neither you nor root), "group-writable" (not your
        private group) and "world-writable": another account could swap the file there. dir_ok = both lists empty.
        These fields only inform (the caller warns); they never change the exit code. Exit 4 when reason is set (the
        file cannot be rewritten), 0 otherwise; a missing file is exit 0 with exists=false.
merge   Reads NAME=VALUE lines from stdin (names ^[A-Z][A-Z0-9_]*$, one line each, no control character: C0, DEL,
        C1 (U+0080-U+009F, so U+0085), U+2028, U+2029, the same set lib/keys.sh drops; surrounding whitespace stripped;
        empty values and values equal to --placeholder are ignored and
        listed). An existing non-empty value is kept unless --overwrite; an empty value is filled. Comments, order and
        trailing inline comments survive: the last assignment of a name is replaced in place, new names are appended
        under a "# c101-linux keys" marker. A value is quoted ('...') only when it holds whitespace, "#" or starts with
        a quote, and every value is verified to read back identically under envfile.py, config.py, mcp-headers and
        mcp-run before anything is written (the bash readers modelled with their own line splitting: LF only).
        Line endings: bin/mcp-run and bin/mcp-headers split on LF only, c101's Python on every str.splitlines()
        break. A CRLF file is therefore rewritten with LF (crlf_normalized; the backup keeps the original); a file
        holding any other such break or a NUL (bare CR, VT, FF, \\x1c-\\x1e, U+0085, U+2028, U+2029) is refused
        (exit 3, "line-breaks"), since the readers would see different lines.
        --template T   create the file from T when it does not exist (no template: an empty file).
        --preset P     c101's config/stack.env.fal.example. Merges every IMAGE_* assignment of P. ONE fal key (the
                       --preset-key name, default FAL_KEY: stdin, else the file's FAL_KEY, else a fal op's key already
                       in the file) fills that name and every IMAGE_<OP>_KEY whose IMAGE_<OP>_URL is a fal queue URL:
                       scheme https, host exactly queue.fal.run, no userinfo, no backslash, port 443 or none. P's
                       --placeholder becomes that key only in the KEY of such an op; anywhere else it becomes empty.
                       An op whose existing IMAGE_<OP>_* values are non-empty and not the fal preset (other provider)
                       is kept whole (never mixed) unless --overwrite, and listed in kept_ops. A name supplied on stdin
                       (an IMAGE_<OP>_* from the environment) is never replaced by the preset, even with --overwrite;
                       an op with such a name that is not on fal is kept (kept_ops), and with --overwrite its other
                       fields still holding the preset's value (the fal key included) are cleared, so the fal key never
                       rides along to another provider. With no key anywhere the URL/MODEL/API/PARAMS lines are
                       still written with EMPTY KEY values and the empty names are listed in missing_keys.
        --overwrite    replace existing non-empty values (never with an empty one, except when an other-provider op is
                       replaced whole, where its API/PARAMS/KEY are cleared so no provider is mixed).
        --dry-run      compute and report, write nothing.
        Writes go through the symlink (realpath), via a temp file in the target's directory (umask 077, fchmod 600,
        os.replace), so a stow/chezmoi link survives; the file ends up mode 600. A file you own (or, as root, any) that
        group or others can access is tightened to 600 even when nothing else changes (mode_fixed; not in a dry run).
        When the content changes and the file existed, its old bytes are first saved to <target>.bak-<UTC> (mode 600);
        backups are never deleted here, "backups" counts the <target>.bak-* files now next to the target. Output:
        {file, target, created, wrote, would_write, dry_run, changed[], kept[], kept_ops[], overwritten_ops[],
        missing_keys[], ignored[], backup, backups, mode_fixed, crlf_normalized}.
        kept[] = names where a different non-empty value was offered and the existing one stayed.

Exit codes: 0 ok; 1 unexpected failure; 2 usage or invalid input (bad option, bad stdin line, stdin is a terminal);
3 file problem (unreadable, not UTF-8, too large, a directory, not owned by you, template or preset missing, line
breaks the readers split differently: "line-breaks");
4 read-only target (/nix/store, read-only mount, not writable): JSON {"refused": true, "reason": ...}.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from typing import Any, Dict, FrozenSet, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

MARKER = "# c101-linux keys"
FAL_HOST = "queue.fal.run"
DEFAULT_PLACEHOLDER = "your-fal-key"
DEFAULT_PRESET_KEY = "FAL_KEY"
MAX_FILE_BYTES = 1 << 20
MAX_STDIN_BYTES = 1 << 20
MAX_VALUE_CHARS = 16384
OP_FIELDS = ("URL", "KEY", "MODEL", "API", "PARAMS")
DEFAULT_OPS = ("GEN", "REF", "EDIT", "TRANSPARENT", "SEGMENT", "BG_REMOVAL", "UPSCALE", "VECTOR", "TRACE",
               "VECTOR_MANIPULATION")
LOCAL_OK_OPS = frozenset({"UPSCALE", "TRACE"})
DEPRECATED_ALIASES = {"SEGMENT": "MASK", "TRANSPARENT": "RGBA", "BG_REMOVAL": "RGBA"}  # config.py DEPRECATED_ALIASES
RESERVED_PARAM_KEYS = frozenset({"model", "prompt", "messages", "input_references", "image", "image[]", "mask",
                                 "reference_images"})
APIS = ("openrouter", "opper", "openai", "fal", "local")

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")  # what the stack's parsers accept
_STRICT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")  # what this tool writes
_OP_NAME_RE = re.compile(r"^IMAGE_(.+)_(URL|KEY|MODEL|API|PARAMS)$")
# Refused in a value: C0, DEL, C1 (U+0085 among them), U+2028, U+2029. That covers every str.splitlines() separator,
# and it is exactly the set lib/keys.sh (_keys_value_ok) drops before stdin, so one bad value never fails the batch.
_BAD_VALUE_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
# Line breaks c101's Python readers honour and the bash readers (read -r: LF only) do not, plus NUL (bash drops it);
# searched after CRLF has been turned into LF.
_SPLIT_MISMATCH_CHARS = re.compile(r"[\x00\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")
_BASH_SPACE = " \t\n\v\f\r"


class StackEnvError(Exception):
    """A failure with a fixed exit code. Messages never contain values."""

    exit_code = 1

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class UsageError(StackEnvError):
    exit_code = 2


class FileProblem(StackEnvError):
    exit_code = 3


class ReadOnlyTarget(StackEnvError):
    exit_code = 4


# --- the env-file grammar (mirrors stack/envfile.py, image_router/config.py, bin/mcp-headers, bin/mcp-run) ---------


class Assign:
    """One recognised assignment line."""

    __slots__ = ("indent", "export", "name", "raw", "tail", "quoted")

    def __init__(self, indent: str, export: bool, name: str, raw: str) -> None:
        self.indent = indent
        self.export = export
        self.name = name
        self.raw = raw
        self.quoted = bool(re.match(r'^"(.*)"$', raw) or re.match(r"^'(.*)'$", raw))
        m = None if self.quoted else re.search(r"\s+#", raw)
        self.tail = raw[m.start():] if m else ""  # trailing inline comment of an unquoted value, kept on rewrite


def parse_assignment(body: str) -> Optional[Assign]:
    stripped = body.lstrip()
    if not stripped or stripped.startswith("#"):
        return None
    export = False
    if stripped.startswith("export "):
        stripped = stripped[len("export "):]
        export = True
    if "=" not in stripped:
        return None
    name, raw = stripped.split("=", 1)
    if not _NAME_RE.match(name):
        return None
    return Assign(body[: len(body) - len(body.lstrip())], export, name, raw)


def decode_value(raw: str) -> str:
    """envfile.py / config.py: matching quotes are removed, else an inline ' # comment' is dropped and the ends stripped."""
    m = re.match(r'^"(.*)"$', raw) or re.match(r"^'(.*)'$", raw)
    if m:
        return m.group(1)
    return re.split(r"\s#", raw, maxsplit=1)[0].strip()


def decode_value_bash(raw: str) -> str:
    """The same rules as bin/mcp-run and bin/mcp-headers (bash [[ =~ ]] and ${var%%[[:space:]]#*})."""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    m = re.search("[%s]#" % re.escape(_BASH_SPACE), raw)
    if m:
        raw = raw[: m.start()]
    return raw.strip(_BASH_SPACE)


def render_value(value: str) -> str:
    """Quote only when the parsers would otherwise change the value (whitespace, '#', a leading quote)."""
    if not value:
        return ""
    if any(c.isspace() for c in value) or "#" in value or value[0] in "'\"":
        return "'" + value + "'"
    return value


def host_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        return (urlparse(url).hostname or "").lower() or None
    except ValueError:
        return None


def is_fal_url(url: Optional[str]) -> bool:
    """A fal queue endpoint the fal key may be sent to: https, host exactly queue.fal.run, port 443 or none, no
    userinfo and nothing urlparse and an HTTP client could read differently (backslash, whitespace, controls)."""
    if not url or "\\" in url or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return False
    try:
        p = urlparse(url)
        port = p.port
    except ValueError:
        return False
    if p.scheme.lower() != "https" or "@" in p.netloc or port not in (None, 443):
        return False
    return (p.hostname or "").lower() == FAL_HOST


def split_op_name(name: str) -> Optional[Tuple[str, str]]:
    m = _OP_NAME_RE.match(name)
    return (m.group(1), m.group(2)) if m else None


class Doc:
    """An env file as physical lines (split like str.splitlines(), which is how envfile.py sees them)."""

    def __init__(self, text: str) -> None:
        self.bodies = []  # type: List[str]
        self.terms = []  # type: List[str]
        self.assigns = []  # type: List[Optional[Assign]]
        for piece in text.splitlines(True):
            parts = piece.splitlines()
            body = parts[0] if parts else ""
            self.bodies.append(body)
            self.terms.append(piece[len(body):])
            self.assigns.append(parse_assignment(body))
        self.eol = "\r\n" if "\r\n" in text else "\n"
        self.index = {}  # type: Dict[str, List[int]]
        self._reindex()

    def _reindex(self) -> None:
        self.index = {}
        for i, a in enumerate(self.assigns):
            if a is not None:
                self.index.setdefault(a.name, []).append(i)

    def values(self) -> Dict[str, str]:
        """name -> value of its last assignment, in order of first appearance."""
        out = {}  # type: Dict[str, str]
        for a in self.assigns:
            if a is not None:
                out[a.name] = decode_value(a.raw)
        return out

    def text(self) -> str:
        return "".join(b + t for b, t in zip(self.bodies, self.terms))

    def _rewrite(self, i: int, value: str) -> None:
        a = self.assigns[i]
        assert a is not None
        rendered = render_value(value)
        keep_tail = a.tail if (a.tail and not a.quoted and rendered == value) else ""
        body = a.indent + ("export " if a.export else "") + a.name + "=" + rendered + keep_tail
        self.bodies[i] = body
        self.assigns[i] = parse_assignment(body)

    def set_value(self, name: str, value: str) -> None:
        """Replace the last assignment in place. Earlier duplicates holding a different non-empty value are brought
        in line too: bin/mcp-run takes the FIRST non-empty assignment, every other parser the LAST."""
        idxs = self.index[name]
        for i in idxs:
            a = self.assigns[i]
            assert a is not None
            old = decode_value(a.raw)
            if i == idxs[-1] or (old != "" and old != value):
                self._rewrite(i, value)

    def append(self, items: List[Tuple[str, str]]) -> None:
        if not items:
            return
        lines = [name + "=" + render_value(value) for name, value in items]
        marker_at = None
        for i, b in enumerate(self.bodies):
            if b.strip() == MARKER:
                marker_at = i
        if marker_at is None:
            pos = len(self.bodies)
            new = ([""] if self.bodies and self.bodies[-1].strip() else []) + [MARKER] + lines
        else:
            pos = marker_at + 1
            while pos < len(self.bodies) and self.assigns[pos] is not None:
                pos += 1
            new = lines
        if pos == len(self.bodies) and self.bodies and self.terms[-1] == "":
            self.terms[-1] = self.eol
        for k, line in enumerate(new):
            self.bodies.insert(pos + k, line)
            self.terms.insert(pos + k, self.eol)
            self.assigns.insert(pos + k, parse_assignment(line))
        self._reindex()


def python_reader_lines(text: str) -> List[str]:
    """Lines as stack/envfile.py and image_router/config.py see them: universal newlines, then str.splitlines()."""
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


def bash_reader_lines(text: str) -> List[str]:
    """Lines as bin/mcp-run and bin/mcp-headers see them: `IFS= read -r` splits on LF only (a CR stays in the line)."""
    return text.split("\n")


def parse_assignment_bash(line: str) -> Optional[Tuple[str, str]]:
    """(name, raw value) the way bin/mcp-run and bin/mcp-headers take one line apart, or None."""
    s = line.lstrip(_BASH_SPACE)
    if not s or s.startswith("#"):
        return None
    if s.startswith("export "):
        s = s[len("export "):]
    if "=" not in s:
        return None
    name, raw = s.split("=", 1)
    return (name, raw) if _NAME_RE.match(name) else None


def check_line_breaks(text: str, what: str) -> None:
    """Refuse text the Python and the bash readers would split into different lines (CRLF is fine: it is normalised)."""
    if _SPLIT_MISMATCH_CHARS.search(text.replace("\r\n", "\n")):
        raise FileProblem("%s holds a bare CR, NUL, VT, FF, \\x1c-\\x1e, U+0085, U+2028 or U+2029: c101's bash and "
                          "Python readers would see different lines; remove it, then re-run" % what, "line-breaks")


def verify_readback(text: str, expected: Dict[str, str]) -> None:
    """Every written value must read back identically under all four parsers, each modelled with its own line
    splitting: envfile.py and config.py (universal newlines + splitlines, last assignment wins), bin/mcp-headers (LF
    only, last wins) and bin/mcp-run (LF only, first non-empty wins). Raises without naming a value."""
    py_last = {}  # type: Dict[str, str]
    for line in python_reader_lines(text):
        a = parse_assignment(line)
        if a is not None:
            py_last[a.name] = decode_value(a.raw)
    sh_last = {}  # type: Dict[str, str]
    sh_first = {}  # type: Dict[str, str]
    for line in bash_reader_lines(text):
        got = parse_assignment_bash(line)
        if got is None:
            continue
        v = decode_value_bash(got[1])
        sh_last[got[0]] = v
        if v and got[0] not in sh_first:
            sh_first[got[0]] = v
    for name, value in expected.items():
        if name not in py_last or name not in sh_last:
            raise StackEnvError("consistency check failed for %s: assignment missing; nothing written" % name)
        if py_last[name] != value or sh_last[name] != value or sh_first.get(name, "") != value:
            raise StackEnvError("consistency check failed for %s: value would not read back identically; "
                                "nothing written" % name)


# --- files -------------------------------------------------------------------------------------------------------


def default_file() -> str:
    return os.environ.get("CLAUDE_STACK_ENV") or os.path.join(os.path.expanduser("~"), ".claude", "stack.env")


def read_text_file(path: str, what: str) -> Optional[Tuple[bytes, str]]:
    """(raw bytes, text), or None when the file does not exist."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        raise FileProblem("%s is a directory" % what, "is-directory") from None
    except OSError as exc:
        raise FileProblem("cannot read %s: %s" % (what, exc.strerror or "I/O error"), "unreadable") from None
    if len(raw) > MAX_FILE_BYTES:
        raise FileProblem("%s is larger than %d bytes" % (what, MAX_FILE_BYTES), "too-large")
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError:
        raise FileProblem("%s is not valid UTF-8" % what, "not-utf8") from None


def nearest_existing(path: str) -> str:
    cur = path
    while cur and not os.path.exists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return cur or os.sep


def write_block_reason(real: str) -> Optional[str]:
    """Why the realpath cannot be rewritten, or None (mirrors managed_block.resolve_for_write, plus mount flags)."""
    if real.startswith("/nix/store/"):
        return "nix-store"
    probe = real if os.path.exists(real) else nearest_existing(os.path.dirname(real))
    try:
        if os.statvfs(probe).f_flag & os.ST_RDONLY:
            return "read-only-mount"
    except (OSError, AttributeError):
        pass
    if os.path.exists(real):
        return None if os.access(real, os.W_OK) else "not-writable"
    return None if os.access(probe, os.W_OK | os.X_OK) else "dir-not-writable"


def _private_group(gid: int) -> bool:
    """Your user-private group (named like you, no other listed member): group-writable there is not shared, as with
    umask 002 on Fedora-style systems."""
    try:
        import grp
        import pwd

        g = grp.getgrgid(gid)
        me = pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError, OSError):
        return False
    return g.gr_name == me and all(m == me for m in g.gr_mem)


def dir_issues(directory: Optional[str]) -> List[str]:
    """Why another local account could replace a file in this directory: [] when only you (or root) can. stack.env
    decides what c101's servers run with (bin/mcp-run exports every name in it), so its directory matters."""
    if not directory:
        return []
    try:
        st = os.stat(directory)
    except OSError:
        return []
    issues = []  # type: List[str]
    if st.st_uid not in (os.geteuid(), 0):
        issues.append("foreign-owner")
    if st.st_mode & 0o020 and not _private_group(st.st_gid):
        issues.append("group-writable")
    if st.st_mode & 0o002:
        issues.append("world-writable")
    return issues


def tighten_mode(real: str) -> bool:
    """chmod 600 the target when group or others can access it and it is yours (or you are root). The file is opened
    without following a last-component symlink and changed through that descriptor. True when the mode changed."""
    try:
        fd = os.open(real, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not st.st_mode & 0o077:
            return False
        if st.st_uid != os.geteuid() and os.geteuid() != 0:
            return False
        os.fchmod(fd, 0o600)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def count_backups(real: str) -> int:
    """How many <target>.bak-* files sit next to the target (never deleted here; the caller prints a hint)."""
    directory, base = os.path.split(real)
    prefix = base + ".bak-"
    try:
        return sum(1 for n in os.listdir(directory or ".") if n.startswith(prefix))
    except OSError:
        return 0


@contextlib.contextmanager
def dir_lock(directory: str) -> Iterator[None]:
    """Best-effort advisory lock on the target's directory so two installers do not interleave read-modify-write."""
    fd = None  # type: Optional[int]
    try:
        import fcntl

        fd = os.open(directory, os.O_RDONLY)
        deadline = time.monotonic() + 10.0
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
    except (ImportError, OSError):
        if fd is not None:
            os.close(fd)
            fd = None
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)


def write_backup(real: str, old: bytes) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for n in range(1, 1000):
        dest = "%s.bak-%s%s" % (real, stamp, "" if n == 1 else "-%d" % n)
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(old)
            fh.flush()
            os.fsync(fh.fileno())
        return dest
    raise FileProblem("cannot pick a free backup name next to the target", "backup-failed")


def write_atomic(real: str, data: bytes, owner: Optional[Tuple[int, int]]) -> None:
    """Temp file next to the target (so the symlink survives), mode 600, then os.replace."""
    directory = os.path.dirname(real)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".stack.env-", dir=directory)
    done = False
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), 0o600)
            if owner is not None and os.geteuid() == 0:
                os.fchown(fh.fileno(), owner[0], owner[1])  # run as root: keep the user's ownership
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, real)
        done = True
    finally:
        if not done:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
    with contextlib.suppress(OSError):
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


# --- stdin -------------------------------------------------------------------------------------------------------


def read_supplied(placeholder: str) -> Tuple[Dict[str, str], List[str]]:
    """NAME=VALUE lines from stdin -> ({name: value}, names ignored because the value was empty or the placeholder)."""
    if sys.stdin is None:
        return {}, []
    if sys.stdin.isatty():
        raise UsageError("stdin must be a pipe or a file with NAME=VALUE lines (a terminal would echo the key); "
                         "use </dev/null for none", "stdin-tty")
    data = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(data) > MAX_STDIN_BYTES:
        raise UsageError("stdin is larger than %d bytes" % MAX_STDIN_BYTES, "stdin-too-large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise UsageError("stdin is not valid UTF-8", "stdin-not-utf8") from None
    supplied = {}  # type: Dict[str, str]
    ignored = []  # type: List[str]
    for number, line in enumerate(text.split("\n"), 1):
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise UsageError("stdin line %d: expected NAME=VALUE" % number, "stdin-format")
        name, value = line.split("=", 1)
        if not _STRICT_NAME_RE.match(name):
            raise UsageError("stdin line %d: name must match ^[A-Z][A-Z0-9_]*$" % number, "stdin-name")
        value = value.strip()
        if len(value) > MAX_VALUE_CHARS or _BAD_VALUE_CHARS.search(value):
            raise UsageError("stdin value for %s is too long or has a control or line-separator character" % name,
                             "stdin-value")
        if not value or (placeholder and value == placeholder):
            if name not in ignored:
                ignored.append(name)
            supplied.pop(name, None)
            continue
        supplied[name] = value
        if name in ignored:
            ignored.remove(name)
    return supplied, ignored


# --- the merge plan ----------------------------------------------------------------------------------------------


class Plan:
    """Decides, name by name, what changes. Works on a copy of the current values. `supplied` holds the names that came
    on stdin: the preset never replaces those (an IMAGE_<OP>_* from the environment is the user's explicit choice)."""

    def __init__(self, current: Dict[str, str], placeholder: str, overwrite: bool,
                 supplied: Optional[Iterable[str]] = None) -> None:
        self.working = dict(current)
        self.placeholder = placeholder
        self.overwrite = overwrite
        self.supplied = frozenset(supplied or ())  # type: FrozenSet[str]
        self.changes = {}  # type: Dict[str, str]
        self.kept = []  # type: List[str]
        self.kept_ops = []  # type: List[str]
        self.overwritten_ops = []  # type: List[str]
        self.missing_keys = []  # type: List[str]

    def _is_blank(self, v: str) -> bool:
        return v == "" or bool(self.placeholder and v == self.placeholder)

    def force(self, name: str, value: str) -> None:
        if self.working.get(name) != value:
            self.working[name] = value
            self.changes[name] = value

    def offer(self, name: str, value: str) -> None:
        cur = self.working.get(name)
        if cur == value:
            return
        if cur is None or self._is_blank(cur) or (value and self.overwrite):
            self.force(name, value)
        elif value and name not in self.kept:
            self.kept.append(name)

    def fal_key(self, preset_key: str) -> str:
        v = self.working.get(preset_key, "")
        if v and not self._is_blank(v):
            return v
        for name, val in self.working.items():
            sp = split_op_name(name)
            if sp and sp[1] == "KEY" and val and not self._is_blank(val):
                if is_fal_url(self.working.get("IMAGE_%s_URL" % sp[0])):
                    return val
        return ""


def apply_preset(plan: Plan, preset: Dict[str, str], preset_key: str) -> None:
    fk = plan.fal_key(preset_key)
    plan.offer(preset_key, fk)
    ops = {}  # type: Dict[str, Dict[str, str]]
    extras = []  # type: List[Tuple[str, str]]
    for name, value in preset.items():
        sp = split_op_name(name)
        if sp:
            ops.setdefault(sp[0], {})[sp[1]] = value
        elif name.startswith("IMAGE_"):
            extras.append((name, "" if (plan.placeholder and value == plan.placeholder) else value))
    fal_ops = []  # type: List[str]
    for op, pv in ops.items():
        names = {f: "IMAGE_%s_%s" % (op, f) for f in OP_FIELDS}
        # The placeholder stands for the fal key only in the KEY of an op whose preset URL is a fal queue URL;
        # anywhere else it becomes empty, so the key never lands in another op or field.
        for f in OP_FIELDS:
            if f in pv and plan.placeholder and pv[f] == plan.placeholder:
                pv[f] = ""
        if is_fal_url(pv.get("URL")):
            pv["KEY"] = fk
            fal_ops.append(op)
        user = [f for f in OP_FIELDS if names[f] in plan.supplied]
        existing = {}  # type: Dict[str, str]
        for f in OP_FIELDS:
            v = plan.working.get(names[f], "")
            existing[f] = "" if plan._is_blank(v) else v
        nonempty = {f: v for f, v in existing.items() if v}
        same_provider = (not nonempty or is_fal_url(existing["URL"])
                         or all(pv.get(f) == v for f, v in nonempty.items()))
        if not same_provider:
            if not plan.overwrite or user:
                # Kept whole: another provider in the file, or set from the environment (stdin), which the preset
                # never replaces. With --overwrite, fields of such an op still holding the preset's value (the fal
                # key included) are cleared, so no fal leftover is mixed into the user's provider.
                plan.kept_ops.append(op)
                if op in fal_ops:
                    fal_ops.remove(op)
                if plan.overwrite:
                    for f in OP_FIELDS:
                        if f not in user and existing[f] and pv.get(f) == existing[f]:
                            plan.force(names[f], "")
                continue
            for f in OP_FIELDS:
                if f in pv:
                    plan.force(names[f], pv[f])
                elif existing[f]:
                    plan.force(names[f], "")
            plan.overwritten_ops.append(op)
            continue
        for f in OP_FIELDS:
            if f in pv and f not in user:
                plan.offer(names[f], pv[f])
    for name, value in extras:
        if name not in plan.supplied:
            plan.offer(name, value)
    if not plan.working.get(preset_key) or plan._is_blank(plan.working[preset_key]):
        plan.missing_keys.append(preset_key)
    for op in fal_ops:
        key = "IMAGE_%s_KEY" % op
        v = plan.working.get(key, "")
        if plan._is_blank(v):
            plan.missing_keys.append(key)


# --- commands ----------------------------------------------------------------------------------------------------


def emit(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def state_of(value: Optional[str], placeholder: str) -> str:
    if value is None:
        return "absent"
    return "empty" if (value == "" or (placeholder and value == placeholder)) else "set"


def load_values(path: str) -> Dict[str, str]:
    got = read_text_file(os.path.realpath(path), "the env file")
    return Doc(got[1]).values() if got else {}


def cmd_names(a: argparse.Namespace) -> int:
    values = load_values(a.file or default_file())
    if a.keys is not None:
        wanted = [k.strip() for k in a.keys.split(",") if k.strip()]
        for k in wanted:
            if not _NAME_RE.match(k):
                raise UsageError("--keys holds an invalid name", "bad-keys")
        emit({k: state_of(values.get(k), a.placeholder) for k in wanted})
    else:
        emit({k: state_of(v, a.placeholder) for k, v in values.items()})
    return 0


def _is_loopback(host: Optional[str]) -> bool:
    return bool(host) and (host == "localhost" or host.endswith(".localhost") or host in ("127.0.0.1", "::1"))


def detect_api(url: Optional[str]) -> str:
    host = host_of(url) or ""
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        return "openrouter"
    if host == "opper.ai" or host.endswith(".opper.ai"):
        return "opper"
    if host in ("fal.run", "fal.ai") or host.endswith((".fal.run", ".fal.ai")):
        return "fal"
    return "openai"


def op_report(op: str, values: Dict[str, str], placeholder: str) -> Dict[str, Any]:
    """Mirror of image_router/config.py load_op, reduced to names and statuses."""
    alias = DEPRECATED_ALIASES.get(op)

    def get(field: str) -> str:
        v = (values.get("IMAGE_%s_%s" % (op, field)) or "").strip()
        if (not v or (placeholder and v == placeholder)) and alias:
            v = (values.get("IMAGE_%s_%s" % (alias, field)) or "").strip()
        return "" if (placeholder and v == placeholder) else v

    url, key, model, api_raw, params_raw = get("URL"), get("KEY"), get("MODEL"), get("API"), get("PARAMS")
    issues = []  # type: List[str]
    if params_raw:
        try:
            parsed = json.loads(params_raw)
        except ValueError:
            parsed = None
            issues.append("params-invalid")
        if parsed is not None:
            if not isinstance(parsed, dict):
                issues.append("params-invalid")
            elif set(parsed) & RESERVED_PARAM_KEYS:
                issues.append("params-reserved")
    api = None  # type: Optional[str]
    if api_raw:
        api = api_raw.lower()
        if api not in APIS:
            issues.append("api-invalid")
        elif api == "local" and op not in LOCAL_OK_OPS:
            issues.append("api-local-unsupported")
    elif url:
        api = detect_api(url)
    missing = []  # type: List[str]
    agent_edits_svg = op == "VECTOR_MANIPULATION" and (model or "claude").lower() == "claude"
    if agent_edits_svg or api == "local":
        pass
    else:
        if not url:
            missing.append("IMAGE_%s_URL" % op)
        if not model:
            missing.append("IMAGE_%s_MODEL" % op)
        if not key and not (api == "openai" and _is_loopback(host_of(url))):
            missing.append("IMAGE_%s_KEY" % op)
        if url and urlparse(url).scheme not in ("http", "https"):
            issues.append("url-scheme")
    if not missing and not issues:
        status = "configured"
    elif not issues and missing == ["IMAGE_%s_KEY" % op]:
        status = "key-empty"
    else:
        status = "not-configured"
    if agent_edits_svg or api is None:
        provider = "none"
    else:
        provider = "fal" if api == "fal" else "other"
    return {"status": status, "provider": provider, "missing": missing, "issues": issues}


def ops_from_file(path: Optional[str]) -> Tuple[str, ...]:
    if not path:
        return DEFAULT_OPS
    try:
        with open(path, encoding="utf-8") as fh:
            names = [str(k) for k in json.load(fh)["ops"]]
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_OPS
    return tuple(n for n in names if re.match(r"^[A-Z][A-Z0-9_]*$", n)) or DEFAULT_OPS


def cmd_ops(a: argparse.Namespace) -> int:
    values = load_values(a.file or default_file())
    emit({op: op_report(op, values, a.placeholder) for op in ops_from_file(a.ops_file)})
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    path = os.path.abspath(a.file or default_file())
    real = os.path.realpath(path)
    exists = os.path.exists(real)
    reason = write_block_reason(real)
    out = {
        "path": path,
        "exists": exists,
        "is_symlink": os.path.islink(path),
        "target": real,
        "dangling": os.path.islink(path) and not exists,
        "mode": None,
        "mode_ok": None,
        "owner_ok": None,
        "target_writable": reason is None,
        "readonly_fs": reason in ("nix-store", "read-only-mount"),
        "reason": reason,
    }  # type: Dict[str, Any]
    if exists:
        st = os.stat(real)
        out["mode"] = format(st.st_mode & 0o777, "03o")
        out["mode_ok"] = (st.st_mode & 0o077) == 0
        out["owner_ok"] = st.st_uid == os.geteuid()
    # Who else could swap the file: the target's directory and, for a link, the link's own directory (inform only).
    target_dir = nearest_existing(os.path.dirname(real))
    link_dir = os.path.dirname(path) if out["is_symlink"] else None
    out["dir"] = target_dir
    out["dir_issues"] = dir_issues(target_dir)
    out["link_dir"] = link_dir
    out["link_dir_issues"] = dir_issues(link_dir)
    out["dir_ok"] = not out["dir_issues"] and not out["link_dir_issues"]
    emit(out)
    return 4 if reason else 0


def cmd_merge(a: argparse.Namespace) -> int:
    os.umask(0o077)
    if not _STRICT_NAME_RE.match(a.preset_key):
        raise UsageError("--preset-key must match ^[A-Z][A-Z0-9_]*$", "bad-preset-key")
    supplied, ignored = read_supplied(a.placeholder)
    path = os.path.abspath(a.file or default_file())
    real = os.path.realpath(path)
    reason = write_block_reason(real)
    if reason:
        raise ReadOnlyTarget("%s cannot be rewritten (%s)" % (real, reason), reason)
    if os.path.isdir(real):
        raise FileProblem("the env file path is a directory", "is-directory")
    preset = None  # type: Optional[Dict[str, str]]
    if a.preset:
        got = read_text_file(a.preset, "the preset file")
        if got is None:
            raise FileProblem("the preset file does not exist", "preset-missing")
        preset = Doc(got[1]).values()

    with contextlib.ExitStack() as stack:
        if not a.dry_run:
            stack.enter_context(dir_lock(os.path.dirname(real)))
        cur = read_text_file(real, "the env file")
        owner = None  # type: Optional[Tuple[int, int]]
        old_mode = 0
        if cur is not None:
            st = os.stat(real)
            if st.st_uid != os.geteuid() and os.geteuid() != 0:
                raise FileProblem("the env file is not owned by the current user", "not-owner")
            owner = (st.st_uid, st.st_gid)
            old_mode = st.st_mode
            old_bytes, base_text = cur
        else:
            old_bytes, base_text = b"", ""
            if a.template:
                tpl = read_text_file(a.template, "the template")
                if tpl is None:
                    raise FileProblem("the template does not exist", "template-missing")
                base_text = tpl[1]
        # bin/mcp-run and bin/mcp-headers split on LF only: refuse other breaks, rewrite CRLF as LF (the backup keeps
        # the original bytes), so all four readers see the same lines.
        check_line_breaks(base_text, "the env file" if cur is not None else "the template")
        crlf = "\r\n" in base_text
        doc = Doc(base_text.replace("\r\n", "\n") if crlf else base_text)
        plan = Plan(doc.values(), a.placeholder, a.overwrite, supplied)
        for name, value in supplied.items():
            plan.offer(name, value)
        if preset is not None:
            apply_preset(plan, preset, a.preset_key)

        appended = []  # type: List[Tuple[str, str]]
        for name, value in plan.changes.items():
            if name in doc.index:
                doc.set_value(name, value)
            else:
                appended.append((name, value))
        doc.append(appended)
        new_text = doc.text()
        check_line_breaks(new_text, "the merged result (template or preset values)")
        verify_readback(new_text, plan.changes)

        exists = cur is not None
        would_write = (new_text != base_text) if exists else bool(new_text or a.template)
        backup = None  # type: Optional[str]
        wrote = False
        if would_write and not a.dry_run:
            if exists:
                backup = write_backup(real, old_bytes)
            write_atomic(real, new_text.encode("utf-8"), owner)
            wrote = True
        # A file group or others can access is tightened even when nothing else changed (the rewrite above already
        # left a mode-600 file). Never in a dry run.
        mode_fixed = False
        if exists and not a.dry_run and old_mode & 0o077:
            mode_fixed = True if wrote else tighten_mode(real)
        backups = count_backups(real)

    emit({
        "file": path,
        "target": real,
        "created": wrote and not exists,
        "wrote": wrote,
        "would_write": would_write,
        "dry_run": a.dry_run,
        "changed": list(plan.changes),
        "kept": plan.kept,
        "kept_ops": plan.kept_ops,
        "overwritten_ops": plan.overwritten_ops,
        "missing_keys": plan.missing_keys,
        "ignored": ignored,
        "backup": backup,
        "backups": backups,
        "mode_fixed": mode_fixed,
        "crlf_normalized": crlf and exists,
    })
    return 0


# --- entry point -------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stackenv.py", description="Names-only reader and stdin-only merger for stack.env.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(s: argparse.ArgumentParser) -> None:
        s.add_argument("--file", help="env file (default: $CLAUDE_STACK_ENV, else ~/.claude/stack.env)")
        s.add_argument("--placeholder", default=DEFAULT_PLACEHOLDER,
                       help="a value that counts as empty ('' disables; default %(default)s)")

    s = sub.add_parser("names", help="JSON of NAME -> set|empty|absent (never values)")
    common(s)
    s.add_argument("--keys", help="comma-separated names to report (default: every name in the file)")
    s.set_defaults(func=cmd_names)

    s = sub.add_parser("ops", help="JSON status of each image operation (never values)")
    common(s)
    s.add_argument("--ops-file", help="c101 config/image-ops.json: take the operation names from its 'ops' keys")
    s.set_defaults(func=cmd_ops)

    s = sub.add_parser("check", help="JSON about the file: mode, owner, symlink, read-only; exit 4 when not rewritable")
    common(s)
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("merge", help="merge NAME=VALUE lines from stdin (and a preset) into the file")
    common(s)
    s.add_argument("--template", help="create the file from this one when it does not exist")
    s.add_argument("--preset", help="c101 config/stack.env.fal.example: merge its IMAGE_* lines")
    s.add_argument("--preset-key", default=DEFAULT_PRESET_KEY,
                   help="name that carries the one fal key (default %(default)s)")
    s.add_argument("--overwrite", action="store_true", help="replace existing non-empty values")
    s.add_argument("--dry-run", action="store_true", help="report, write nothing")
    s.set_defaults(func=cmd_merge)
    return p


def main(argv: List[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except StackEnvError as exc:
        if isinstance(exc, ReadOnlyTarget):
            emit({"refused": True, "reason": exc.code})
        else:
            emit({"error": exc.code})
        sys.stderr.write("stackenv: %s\n" % exc)
        return exc.exit_code
    except BrokenPipeError:
        return 0
    except OSError as exc:
        emit({"error": "io-error"})
        sys.stderr.write("stackenv: I/O error: %s\n" % (exc.strerror or "unknown"))
        return 3
    except Exception as exc:  # process boundary: never let a traceback carry state out
        sys.stderr.write("stackenv: internal error (%s)\n" % type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
