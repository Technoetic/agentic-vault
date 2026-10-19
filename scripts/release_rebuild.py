#!/usr/bin/env python3
"""Build the release source ZIP reproducibly and check a ZIP against its tag (stdlib only).

build (alias: spec) runs exactly
    git -c core.autocrlf=false archive --format=zip --prefix=agentic-vault-VERSION/ -o OUT vVERSION
with TZ set only in a copy of the environment (default UTC0; git writes ZIP entry
times from the commit time through localtime, and ZIP time fields carry no zone),
then prints {"rebuild_spec": {...}} for the release receipt.

check is report-only and compares a given ZIP with the tag at two levels:
  L1_bytes          rebuild with the spec's parameters and compare SHA-256.
  L2_members_blobs  read the ZIP defensively (bounded entries and sizes; unsafe
                    names, duplicates and symlink entries are flagged; the local
                    entries must tile the file from offset 0 to the central
                    directory with the same names, so readers that walk the file
                    front to back see the same members) and compare every file
                    member, prefix stripped, with the Git blob id of the same path
                    in the tag tree. export-ignore paths are not expected;
                    export-subst members and members that git archive may convert
                    (eol, text, ident, filter, working-tree-encoding) are present
                    but their bytes are unverified.
Exit 0 for match or mismatch (a failing `git check-attr --source`, as with git older than 2.40,
refuses L2 and keeps exit 0); exit 2 for usage errors, a missing tag or other Git failures.
Output is JSON. File contents, Git stderr and caller-supplied paths are never printed;
member names from the ZIP or the tree appear only in bounded lists.
The repository is located only by --repo (default: this script's repository);
GIT_DIR-style variables are removed from the child environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
import zlib
from pathlib import Path

SPEC_SCHEMA = "agentic-vault/rebuild-spec/v1"
CHECK_SCHEMA = "agentic-vault/rebuild-check/v1"
PROJECT = "agentic-vault"
DEFAULT_TZ = "UTC0"
EQUIVALENCE_LEVELS = ["L1_bytes", "L2_members_blobs"]
REPO_ROOT = Path(__file__).resolve().parents[1]

MAX_ZIP_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
MAX_LISTED = 50
MAX_NAME_CHARS = 512
MAX_SPEC_BYTES = 4 * 1024 * 1024
GIT_TIMEOUT = 600
CHUNK = 1024 * 1024

_TAG = re.compile(r"v(\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?)\Z")
_TZ = re.compile(r"[A-Za-z0-9<>+:,./-]{1,64}\Z")
_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}/\Z")
_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_GIT_VERSION = re.compile(r"git version ([0-9A-Za-z.+_-]{1,64})\Z")
_DRIVE = re.compile(r"[A-Za-z]:")
# Characters escaped in printed names and flagged as control_character: C0/C1 controls, format
# characters (bidi overrides, zero-width characters) and line/paragraph separators.
_HIDDEN_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")
# Attributes git archive reads: export-ignore/export-subst, and those that drive convert_to_working_tree.
_ATTRIBUTES = ("export-ignore", "export-subst", "text", "eol", "crlf", "ident", "filter", "working-tree-encoding")

# ZIP records (APPNOTE.TXT 4.3).
_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_END_RECORD = struct.Struct("<4s4H2LH")
_ZIP64_LOCATOR = struct.Struct("<4sLQL")
_ZIP64_END = struct.Struct("<4sQ2H2L4Q")
_LOCAL_SIG = b"PK\x03\x04"
_DESCRIPTOR_SIG = b"PK\x07\x08"
_END_SIG = b"PK\x05\x06"
_ZIP64_LOCATOR_SIG = b"PK\x06\x07"
_ZIP64_END_SIG = b"PK\x06\x06"
_DESCRIPTOR_FLAG = 0x08
_UTF8_FLAG = 0x800
_ZIP64_EXTRA = 0x0001
_UNICODE_PATH_EXTRA = 0x7075
_MAX_32 = 0xFFFFFFFF
# Variables that would make git use a repository other than --repo.
_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class UsageError(Exception):
    """A content-free error code; exit 2."""


class _TreeOverLimit(Exception):
    """The tag tree has more entries than the member cap."""


class _CheckAttrFailed(Exception):
    """git check-attr --source failed (it needs git 2.40 or later)."""


class _LayoutError(Exception):
    """A ZIP record could not be read; the argument is the parser_ambiguity code."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Never echo caller arguments.
        raise UsageError("invalid_options")


# --- git -------------------------------------------------------------------------------------------

def _git_env(tz: str | None = None) -> dict:
    """Return a copy of the environment; os.environ itself is never changed."""
    env = dict(os.environ)
    for key in list(env):
        if key.upper() in _REPO_ENV:
            del env[key]
    if tz is not None:
        env["TZ"] = tz
    return env


def _git(repo: Path, *args: str, env: dict | None = None) -> bytes:
    if shutil.which("git") is None:
        raise UsageError("git_missing")
    try:
        result = subprocess.run(["git", *args], cwd=repo, env=env or _git_env(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UsageError("git_failed") from exc
    if result.returncode != 0:
        raise UsageError("git_failed")
    return result.stdout


def _git_version(repo: Path) -> str:
    match = _GIT_VERSION.fullmatch(_git(repo, "--version").decode("ascii", "replace").strip())
    if not match:
        raise UsageError("git_failed")
    return match.group(1)


def _resolve_tag(repo: Path, tag: str) -> str:
    if shutil.which("git") is None:
        raise UsageError("git_missing")
    try:
        exact = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"],
                               cwd=repo, env=_git_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UsageError("git_failed") from exc
    if exact.returncode == 128 and not exact.stdout:
        raise UsageError("git_failed")
    commit = exact.stdout.decode("ascii", "replace").strip()
    if exact.returncode != 0 or not _OID.fullmatch(commit):
        raise UsageError("tag_not_found")
    # The short name passed to git archive must resolve to the same commit as the tag.
    short = _git(repo, "rev-parse", "--verify", f"{tag}^{{commit}}").decode("ascii", "replace").strip()
    if short != commit:
        raise UsageError("ambiguous_tag")
    return commit


def _repo(value: str | None) -> Path:
    repo = Path(value) if value else REPO_ROOT
    if not repo.is_dir():
        raise UsageError("invalid_repo")
    return repo.resolve()


def _validate_tag(tag) -> str:
    if not isinstance(tag, str):
        raise UsageError("invalid_tag")
    match = _TAG.fullmatch(tag)
    if not match:
        raise UsageError("invalid_tag")
    return match.group(1)


def _validate_tz(tz) -> str:
    if not isinstance(tz, str) or not _TZ.fullmatch(tz):
        raise UsageError("invalid_tz")
    return tz


def _asset_name(version: str) -> str:
    return f"{PROJECT}-{version}.zip"


def _argv(tag: str, prefix: str, output: str) -> list:
    return ["git", "-c", "core.autocrlf=false", "archive", "--format=zip", f"--prefix={prefix}", "-o", output, tag]


def _archive(repo: Path, tag: str, prefix: str, tz: str, out: Path) -> None:
    if shutil.which("git") is None:
        raise UsageError("git_missing")
    try:
        result = subprocess.run(_argv(tag, prefix, str(out)), cwd=repo, env=_git_env(tz), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT,
                                check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UsageError("git_failed") from exc
    if result.returncode != 0 or not out.is_file():
        raise UsageError("git_failed")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rebuild_spec(repo: Path, tag: str, commit: str, prefix: str, tz: str, sha256: str) -> dict:
    version = _validate_tag(tag)
    return {
        "schema": SPEC_SCHEMA, "tag": tag, "commit": commit, "git_version": _git_version(repo), "tz": tz,
        "prefix": prefix, "core_autocrlf": "false", "format": "zip",
        "argv": _argv(tag, prefix, _asset_name(version)), "expected_sha256": sha256,
        "equivalence_levels": list(EQUIVALENCE_LEVELS),
    }


# --- L2: members against blobs -------------------------------------------------------------------

def _hidden(char: str) -> bool:
    return unicodedata.category(char) in _HIDDEN_CATEGORIES


def _escape(char: str) -> str:
    code = ord(char)
    return chr(92) + (f"u{code:04x}" if code <= 0xFFFF else f"U{code:08x}")


def _name(value: str) -> str:
    """A printable form of a name: hidden characters are escaped, long names are cut."""
    shown = "".join(_escape(char) if _hidden(char) else char for char in value)
    return shown if len(shown) <= MAX_NAME_CHARS else shown[:MAX_NAME_CHARS] + "..."


def _bounded(values) -> tuple[list, int]:
    ordered = sorted(values)
    return [_name(value) for value in ordered[:MAX_LISTED]], max(0, len(ordered) - MAX_LISTED)


def _name_reasons(raw: str, prefix: str) -> list:
    """Path-shape problems of one raw ZIP entry name (before any separator normalisation)."""
    reasons = []
    if "\\" in raw:
        reasons.append("backslash")
    if raw.startswith(("/", "\\")):
        reasons.append("absolute_path")
    if _DRIVE.match(raw):
        reasons.append("drive_letter")
    segments = re.split(r"[/\\]", raw)
    if ".." in segments:
        reasons.append("dot_dot_segment")
    inner = segments[:-1] if raw.endswith(("/", "\\")) else segments
    if raw.startswith(("/", "\\")):
        inner = inner[1:]
    if "." in inner or "" in inner:
        reasons.append("empty_or_dot_segment")
    if any(_hidden(char) for char in raw):
        reasons.append("control_character")
    if not raw.startswith(prefix):
        reasons.append("outside_prefix")
    return reasons


def _converts(attrs: dict) -> bool:
    """True when git archive may convert the blob on output under core.autocrlf=false.

    Mirrors convert.c (convert_attrs, output_eol): ident, a filter driver, a working-tree-encoding,
    eol=crlf on a non-binary path, or text/auto without eol=lf (then the platform's core.eol decides).
    """
    if attrs.get("ident") == "set":
        return True
    if attrs.get("filter", "unspecified") not in ("unspecified", "unset", "set"):
        return True
    if attrs.get("working-tree-encoding", "unspecified") not in ("unspecified", "unset"):
        return True
    text = attrs.get("text", "unspecified")
    if text not in ("set", "unset", "auto", "input"):
        text = attrs.get("crlf", "unspecified")  # the legacy attribute counts only when text is undefined
    if text == "unset":
        return False
    eol = attrs.get("eol")
    if eol in ("crlf", "lf"):
        return eol == "crlf"
    return text in ("set", "auto")


def _tree(repo: Path, commit: str, max_members: int) -> tuple[dict, set, set, set]:
    """Return ({path: oid} for exported files, export-subst paths, converted paths, export-ignored paths).

    Attributes are resolved as git archive does: directories are asked with a trailing '/', and
    export-ignore/export-subst count only when set (a string value such as export-ignore=no does not).
    """
    raw = _git(repo, "ls-tree", "-r", "-t", "-z", "--full-tree", commit)
    files, trees = {}, []
    for item in raw.split(b"\0"):
        if not item:
            continue
        meta, _, path_bytes = item.partition(b"\t")
        _mode, kind, oid = meta.decode("ascii").split()
        path = path_bytes.decode("utf-8")
        if kind == "tree":
            trees.append(path)
        elif kind == "blob":
            files[path] = oid
        # A "commit" entry is a submodule; git archive writes no file for it, so none is expected.
        if len(files) + len(trees) > max_members:
            raise _TreeOverLimit()
    values: dict = {}
    paths = [*(tree + "/" for tree in trees), *files]
    if paths:
        out = _git_check_attr(repo, commit, paths)
        fields = out.split(b"\0")
        for index in range(0, len(fields) - 2, 3):
            path, attribute, value = (fields[index].decode("utf-8"), fields[index + 1].decode("ascii"),
                                      fields[index + 2].decode("utf-8"))
            values.setdefault(path, {})[attribute] = value
    ignored_dirs = tuple(tree + "/" for tree in trees if values.get(tree + "/", {}).get("export-ignore") == "set")
    ignored = {path for path in files
               if values.get(path, {}).get("export-ignore") == "set" or path.startswith(ignored_dirs)}
    for path in ignored:
        del files[path]
    subst = {path for path in files if values.get(path, {}).get("export-subst") == "set"}
    converted = {path for path in files if _converts(values.get(path, {}))}
    return files, subst, converted, ignored


def _git_check_attr(repo: Path, commit: str, paths: list) -> bytes:
    if shutil.which("git") is None:
        raise UsageError("git_missing")
    payload = b"".join(path.encode("utf-8") + b"\0" for path in paths)
    try:
        result = subprocess.run(["git", "check-attr", f"--source={commit}", "-z", "--stdin", *_ATTRIBUTES],
                                cwd=repo, env=_git_env(), input=payload, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise _CheckAttrFailed() from exc
    if result.returncode != 0:
        raise _CheckAttrFailed()
    return result.stdout


def _object_format(repo: Path) -> str:
    value = _git(repo, "rev-parse", "--show-object-format").decode("ascii", "replace").strip()
    if value not in ("sha1", "sha256"):
        raise UsageError("git_failed")
    return value


def _blob_id(archive: zipfile.ZipFile, info: zipfile.ZipInfo, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    digest.update(b"blob %d\0" % info.file_size)
    read = 0
    with archive.open(info) as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            read += len(chunk)
            if read > info.file_size:
                raise ValueError("size_mismatch")
            digest.update(chunk)
    if read != info.file_size:
        raise ValueError("size_mismatch")
    return digest.hexdigest()


# --- L2: ZIP layout -------------------------------------------------------------------------------

def _read_at(stream, offset: int, length: int, code: str) -> bytes:
    if offset < 0:
        raise _LayoutError(code)
    stream.seek(offset)
    data = stream.read(length)
    if len(data) != length:
        raise _LayoutError(code)
    return data


def _extra_fields(extra: bytes) -> dict:
    """{header id: data} of a ZIP extra block; a malformed block raises _LayoutError."""
    fields, pos = {}, 0
    while pos < len(extra):
        if pos + 4 > len(extra):
            raise _LayoutError("bad_extra_field")
        header, length = struct.unpack_from("<2H", extra, pos)
        if pos + 4 + length > len(extra):
            raise _LayoutError("bad_extra_field")
        fields.setdefault(header, extra[pos + 4:pos + 4 + length])
        pos += 4 + length
    return fields


def _end_records(stream, size: int, comment: bytes) -> tuple[set, int, int, int]:
    """Find the end record as zipfile does; return (codes, central start, central offset field, entries)."""
    tail_len = min(size, _END_RECORD.size + 0xFFFF)
    tail = _read_at(stream, size - tail_len, tail_len, "bad_end_record")
    if tail[-_END_RECORD.size:-_END_RECORD.size + 4] == _END_SIG and tail[-2:] == b"\0\0":
        index = len(tail) - _END_RECORD.size
    else:
        index = tail.rfind(_END_SIG)
    if index < 0 or index + _END_RECORD.size > len(tail):
        raise _LayoutError("bad_end_record")
    (_sig, disk, disk_cd, entries_disk, entries, cd_size, cd_offset,
     comment_len) = _END_RECORD.unpack_from(tail, index)
    end = size - tail_len + index
    codes = set()
    if end + _END_RECORD.size + comment_len < size:
        codes.add("data_after_end_record")
    if (end + _END_RECORD.size + comment_len > size or disk or disk_cd or entries_disk != entries
            or tail[index + _END_RECORD.size:index + _END_RECORD.size + comment_len] != comment):
        codes.add("bad_end_record")
    central_end = end
    if end >= _ZIP64_LOCATOR.size and _read_at(stream, end - _ZIP64_LOCATOR.size, 4, "bad_end_record") \
            == _ZIP64_LOCATOR_SIG:
        _sig, locator_disk, record, disks = _ZIP64_LOCATOR.unpack(
            _read_at(stream, end - _ZIP64_LOCATOR.size, _ZIP64_LOCATOR.size, "bad_end_record"))
        central_end = end - _ZIP64_LOCATOR.size - _ZIP64_END.size
        (sig, record_size, _made_by, _needed, disk, disk_cd, entries_disk, entries, cd_size,
         cd_offset) = _ZIP64_END.unpack(_read_at(stream, central_end, _ZIP64_END.size, "bad_end_record"))
        if (sig != _ZIP64_END_SIG or record_size != _ZIP64_END.size - 12 or record != central_end
                or locator_disk or disks != 1 or disk or disk_cd or entries_disk != entries):
            codes.add("bad_end_record")
    return codes, central_end - cd_size, cd_offset, entries


def _central_name(info: zipfile.ZipInfo) -> bytes | None:
    try:
        return info.orig_filename.encode("utf-8" if info.flag_bits & _UTF8_FLAG else "cp437")
    except UnicodeError:
        return None


def _local_entry_end(stream, info: zipfile.ZipInfo) -> tuple[int, set]:
    """Check one local entry against its central record; return (end offset, codes)."""
    codes = set()
    offset = info.header_offset
    (sig, _version, flags, method, _time, _date, _crc, local_csize, local_usize, name_len,
     extra_len) = _LOCAL_HEADER.unpack(_read_at(stream, offset, _LOCAL_HEADER.size, "bad_local_header"))
    if sig != _LOCAL_SIG:
        raise _LayoutError("bad_local_header")
    name = _read_at(stream, offset + _LOCAL_HEADER.size, name_len, "bad_local_header")
    extra = _read_at(stream, offset + _LOCAL_HEADER.size + name_len, extra_len, "bad_local_header")
    if name != _central_name(info) or (flags & _UTF8_FLAG) != (info.flag_bits & _UTF8_FLAG):
        codes.add("local_name_differs")
    local_extra = _extra_fields(extra)
    if _UNICODE_PATH_EXTRA in local_extra or _UNICODE_PATH_EXTRA in _extra_fields(info.extra):
        codes.add("unicode_path_field")  # readers that honour it extract under another name
    zip64 = local_extra.get(_ZIP64_EXTRA)
    end = offset + _LOCAL_HEADER.size + name_len + extra_len + info.compress_size
    if method != info.compress_type or (flags & _DESCRIPTOR_FLAG) != (info.flag_bits & _DESCRIPTOR_FLAG):
        codes.add("local_header_differs")
    if flags & _DESCRIPTOR_FLAG:
        stream.seek(end)
        descriptor = stream.read(24)
        crc = struct.pack("<L", info.CRC)
        if descriptor[:4] == _DESCRIPTOR_SIG and descriptor[4:8] == crc:
            skip = 4
        elif descriptor[:4] == crc:
            skip = 0
        else:
            raise _LayoutError("bad_data_descriptor")
        sizes = struct.Struct("<QQ" if zip64 is not None else "<LL")
        if len(descriptor) < skip + 4 + sizes.size:
            raise _LayoutError("bad_data_descriptor")
        if sizes.unpack_from(descriptor, skip + 4) != (info.compress_size, info.file_size):
            codes.add("bad_data_descriptor")
        end += skip + 4 + sizes.size
    else:
        if zip64 is not None:
            values = iter(struct.unpack_from(f"<{len(zip64) // 8}Q", zip64))
            local_usize = next(values, None) if local_usize == _MAX_32 else local_usize
            local_csize = next(values, None) if local_csize == _MAX_32 else local_csize
        if (local_csize, local_usize) != (info.compress_size, info.file_size):
            codes.add("local_header_differs")
    return end, codes


def _layout(archive: zipfile.ZipFile, infos: list) -> tuple[set, dict]:
    """Return (parser_ambiguity codes, {raw member name: codes}).

    The local entries, ordered by offset, must tile [0, start of the central directory) exactly
    (data descriptors included), each local name must equal its central name, the central
    directory must sit where its end record says (no prepended or concatenated data), and
    nothing may follow the end record but its comment. Otherwise a reader that walks the
    file front to back can see members that zipfile, which trusts the central directory, does not.
    """
    stream = archive.fp
    codes, members = set(), {}
    try:
        size = stream.seek(0, os.SEEK_END)
        end_codes, central_start, central_offset, entries = _end_records(stream, size, archive.comment)
        codes |= end_codes
        if central_start != central_offset:
            codes.add("data_before_first_entry")
        if central_start != archive.start_dir:
            codes.add("bad_end_record")
        if entries != len(infos):
            codes.add("entry_count_differs")
        position = 0
        for info in sorted(infos, key=lambda item: item.header_offset):
            entry_codes = set()
            if info.header_offset > position:
                codes.add("data_before_first_entry" if position == 0 else "gap_before_entry")
                if position:
                    entry_codes.add("gap_before_entry")
            elif info.header_offset < position:
                entry_codes.add("overlapping_entries")
            try:
                end, found = _local_entry_end(stream, info)
            except _LayoutError as exc:
                codes.add(str(exc))
                members.setdefault(info.orig_filename, set()).add(str(exc))
                return codes, members
            entry_codes |= found
            if entry_codes:
                codes |= entry_codes
                members.setdefault(info.orig_filename, set()).update(entry_codes)
            position = max(position, end)
        if position < central_start:
            codes.add("data_before_central_directory")
        elif position > central_start:
            codes.add("overlapping_entries")
    except _LayoutError as exc:
        codes.add(str(exc))
    except (OSError, ValueError, struct.error):
        codes.add("bad_local_header")
    return codes, members


def _empty_l2(status: str, reason: str | None) -> dict:
    return {"level": "L2_members_blobs", "status": status, "reason": reason,
            "counts": {"entries": 0, "directories": 0, "files": 0, "expected_files": 0, "export_ignored": 0,
                       "matched": 0},
            "missing": [], "extra": [], "mismatched": [], "unsafe": [], "duplicates": [], "unreadable": [],
            "export_subst_unverified": [], "conversion_unverified": [], "parser_ambiguity": [],
            "omitted": {}, "archive_comment_is_commit": None,
            "limits": {"zip_bytes": MAX_ZIP_BYTES, "members": MAX_MEMBERS,
                       "total_uncompressed": MAX_TOTAL_UNCOMPRESSED, "listed": MAX_LISTED}}


def check_members(zip_path: Path, *, repo: Path, commit: str, prefix: str, max_members: int = MAX_MEMBERS,
                  max_total_uncompressed: int = MAX_TOTAL_UNCOMPRESSED, max_zip_bytes: int = MAX_ZIP_BYTES) -> dict:
    """L2: compare the ZIP's file members with the tag tree's blob ids (report only)."""
    try:
        size = os.stat(zip_path).st_size
    except OSError:
        return _empty_l2("refused", "unreadable_zip")
    if size > max_zip_bytes:
        return _empty_l2("refused", "zip_over_limit")
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError):
        return _empty_l2("refused", "bad_zip")
    with archive:
        infos = archive.infolist()
        report = _empty_l2("match", None)
        counts = report["counts"]
        counts["entries"] = len(infos)
        if len(infos) > max_members:
            report.update(status="refused", reason="members_over_limit")
            return report
        if sum(info.file_size for info in infos) > max_total_uncompressed:
            report.update(status="refused", reason="uncompressed_over_limit")
            return report
        ambiguity, ambiguous_members = _layout(archive, infos)
        report["parser_ambiguity"] = sorted(ambiguity)
        try:
            expected, subst, converted, ignored = _tree(repo, commit, max_members)
        except _CheckAttrFailed:
            report.update(status="refused", reason="git_check_attr_failed")
            return report
        except _TreeOverLimit:
            report.update(status="refused", reason="tree_over_limit")
            return report
        except ValueError:  # includes UnicodeDecodeError for non-UTF-8 paths
            report.update(status="refused", reason="tree_undecodable")
            return report
        algorithm = _object_format(repo)
        counts.update(expected_files=len(expected), export_ignored=len(ignored))
        report["archive_comment_is_commit"] = archive.comment == commit.encode("ascii")
        unsafe = {name: {"parser_ambiguity"} for name in ambiguous_members}
        duplicates, seen, members = set(), set(), {}
        for info in infos:
            raw = info.orig_filename
            reasons = _name_reasons(raw, prefix)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                reasons.append("symlink")
            if raw in seen:
                reasons.append("duplicate")
                duplicates.add(raw)
            seen.add(raw)
            if reasons:
                unsafe.setdefault(raw, set()).update(reasons)
            if raw.endswith(("/", "\\")):
                counts["directories"] += 1
                continue
            counts["files"] += 1
            comparable = all(reason in ("symlink", "duplicate") for reason in reasons)
            if comparable and "duplicate" not in reasons:
                members[raw[len(prefix):]] = info
        mismatched, unreadable, matched = [], [], 0
        for path, info in members.items():
            if path not in expected:
                continue
            if path in subst or path in converted:
                continue
            try:
                actual = _blob_id(archive, info, algorithm)
            except (zipfile.BadZipFile, ValueError, EOFError, OSError, zlib.error, NotImplementedError,
                    RuntimeError):
                unreadable.append(path)
                continue
            if actual == expected[path]:
                matched += 1
            else:
                mismatched.append(path)
    counts["matched"] = matched
    lists = {
        "missing": set(expected) - set(members), "extra": set(members) - set(expected),
        "mismatched": mismatched, "unreadable": unreadable, "duplicates": duplicates,
        "export_subst_unverified": subst & set(members), "conversion_unverified": converted & set(members),
    }
    for key, values in lists.items():
        report[key], omitted = _bounded(values)
        if omitted:
            report["omitted"][key] = omitted
    unsafe_names = sorted(unsafe)
    report["unsafe"] = [{"name": _name(name), "reasons": sorted(unsafe[name])} for name in unsafe_names[:MAX_LISTED]]
    if len(unsafe_names) > MAX_LISTED:
        report["omitted"]["unsafe"] = len(unsafe_names) - MAX_LISTED
    problems = (any(lists[key] for key in ("missing", "extra", "mismatched", "unreadable", "duplicates"))
                or unsafe or ambiguity)
    if problems:
        report["status"] = "mismatch"
    elif lists["export_subst_unverified"] or lists["conversion_unverified"]:
        report["status"] = "partial"
    return report


# --- spec input ----------------------------------------------------------------------------------

def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _load_spec(path: Path) -> dict:
    try:
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _REPARSE:
            raise UsageError("invalid_spec")
        if metadata.st_size > MAX_SPEC_BYTES:
            raise UsageError("invalid_spec")
        data = json.loads(path.read_bytes().decode("utf-8-sig"), object_pairs_hook=_unique)
    except UsageError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise UsageError("invalid_spec") from exc
    if isinstance(data, dict) and "rebuild_spec" in data:
        data = data["rebuild_spec"]
    if not isinstance(data, dict) or data.get("schema") != SPEC_SCHEMA:
        raise UsageError("invalid_spec")
    try:
        _validate_tag(data.get("tag"))
        _validate_tz(data.get("tz"))
    except UsageError as exc:
        raise UsageError("invalid_spec") from exc
    if (not isinstance(data.get("prefix"), str) or not _PREFIX.fullmatch(data["prefix"])
            or data.get("format") != "zip" or data.get("core_autocrlf") != "false"
            or not isinstance(data.get("commit"), str) or not _OID.fullmatch(data["commit"])
            or not isinstance(data.get("expected_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", data["expected_sha256"])
            or not isinstance(data.get("git_version"), str)):
        raise UsageError("invalid_spec")
    return data


def _regular_zip(path: Path) -> Path:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise UsageError("zip_not_found") from exc
    if not stat.S_ISREG(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & _REPARSE:
        raise UsageError("zip_not_regular_file")
    return path


# --- commands ------------------------------------------------------------------------------------

def _build(args) -> dict:
    version = _validate_tag(args.tag)
    tz = _validate_tz(args.tz or DEFAULT_TZ)
    repo = _repo(args.repo)
    out = Path(args.out)
    if os.path.lexists(out):
        raise UsageError("output_exists")
    if not out.parent.is_dir():
        raise UsageError("invalid_output")
    commit = _resolve_tag(repo, args.tag)
    prefix = f"{PROJECT}-{version}/"
    _archive(repo, args.tag, prefix, tz, out.absolute())
    return {"rebuild_spec": rebuild_spec(repo, args.tag, commit, prefix, tz, _sha256(out))}


def _check(args) -> dict:
    spec = _load_spec(Path(args.spec)) if args.spec else None
    tag = args.tag or (spec["tag"] if spec else None)
    if tag is None:
        raise UsageError("invalid_options")
    if spec and args.tag and args.tag != spec["tag"]:
        raise UsageError("tag_mismatch")
    version = _validate_tag(tag)
    tz = _validate_tz(args.tz or (spec["tz"] if spec else DEFAULT_TZ))
    prefix = spec["prefix"] if spec else f"{PROJECT}-{version}/"
    repo = _repo(args.repo)
    given = _regular_zip(Path(args.zip))
    commit = _resolve_tag(repo, tag)
    git_version = _git_version(repo)
    given_sha256 = _sha256(given)
    with tempfile.TemporaryDirectory(prefix="av-rebuild-") as tmp:
        rebuilt = Path(tmp) / _asset_name(version)
        _archive(repo, tag, prefix, tz, rebuilt)
        rebuilt_sha256 = _sha256(rebuilt)
    l1 = {"level": "L1_bytes", "status": "match" if given_sha256 == rebuilt_sha256 else "mismatch",
          "given_sha256": given_sha256, "rebuilt_sha256": rebuilt_sha256, "tz": tz, "prefix": prefix,
          "git_version": git_version, "argv": _argv(tag, prefix, _asset_name(version)),
          "given_matches_spec_sha256": None, "spec_commit_matches": None, "spec_git_version_matches": None}
    if spec:
        l1.update(given_matches_spec_sha256=given_sha256 == spec["expected_sha256"],
                  spec_commit_matches=commit == spec["commit"],
                  spec_git_version_matches=git_version == spec["git_version"])
    return {"schema": CHECK_SCHEMA, "tag": tag, "commit": commit, "report_only": True, "l1": l1,
            "l2": check_members(given, repo=repo, commit=commit, prefix=prefix)}


def main(argv=None) -> int:
    try:
        parser = _Parser(description="Reproducible release ZIP build and report-only rebuild check.")
        commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
        build = commands.add_parser("build", aliases=["spec"], help="build the ZIP and print its rebuild_spec")
        build.add_argument("--tag", required=True)
        build.add_argument("--out", required=True)
        build.add_argument("--repo")
        build.add_argument("--tz", help=f"TZ for the git child process (default {DEFAULT_TZ})")
        check = commands.add_parser("check", help="compare a ZIP with the tag (L1 bytes, L2 members/blobs)")
        check.add_argument("--zip", required=True)
        check.add_argument("--tag")
        check.add_argument("--spec", help="rebuild_spec JSON, or a receipt with a rebuild_spec block")
        check.add_argument("--repo")
        check.add_argument("--tz", help="override the spec's TZ, e.g. KST-9 for releases before v0.26.0")
        args = parser.parse_args(argv)
        result = _check(args) if args.command == "check" else _build(args)
        code = 0
    except UsageError as exc:
        result, code = {"status": "error", "error": str(exc)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())
