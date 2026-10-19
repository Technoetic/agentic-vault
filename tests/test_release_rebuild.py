"""Reproducible release ZIP builds and the report-only rebuild check.

Every repository here is temporary. Identity, dates and config isolation are
passed through the child environment and -c options only; the user's global
Git configuration is never read for setup or written.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_rebuild.py"
GIT = shutil.which("git")
TAG = "v1.2.3"
PREFIX = "agentic-vault-1.2.3/"
KOREAN_NAME = "문서/한글 이름.md"
CRLF_BYTES = b"first line\r\nsecond line\r\n"


def _load():
    spec = importlib.util.spec_from_file_location("release_rebuild_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolated_env(home: Path, tz: str | None = None) -> dict:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    empty = home / "empty.gitconfig"
    empty.touch()
    env.update({
        "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Release Tester", "GIT_AUTHOR_EMAIL": "tester@example.invalid",
        "GIT_COMMITTER_NAME": "Release Tester", "GIT_COMMITTER_EMAIL": "tester@example.invalid",
        "GIT_AUTHOR_DATE": "2026-01-02T03:04:05+00:00", "GIT_COMMITTER_DATE": "2026-01-02T03:04:05+00:00",
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("TZ", None)
    if tz is not None:
        env["TZ"] = tz
    return env


GIT_SETUP = ["-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
             "-c", "init.defaultBranch=main"]


def _make_repo(base: Path, files: dict, attributes: str | None = None) -> Path:
    repo = base / "repo"
    repo.mkdir()
    (base / "nohooks").mkdir(exist_ok=True)
    env = _isolated_env(base)
    setup = [*GIT_SETUP, "-c", f"core.hooksPath={base / 'nohooks'}"]

    def git(*args):
        subprocess.run([GIT, *setup, *args], cwd=repo, env=env, check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    git("init", "-q")
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if attributes is not None:
        (repo / ".gitattributes").write_bytes(attributes.encode("utf-8"))
    git("add", "-A")
    git("commit", "-q", "-m", "fixture")
    git("tag", "-a", TAG, "-m", "fixture release")
    return repo


BASE_FILES = {
    "README.txt": b"fixture readme\n",
    KOREAN_NAME: "한글 본문\n".encode("utf-8"),
    "crlf.txt": CRLF_BYTES,
    "pkg/tool.py": b"print('ok')\n",
    "data.bin": bytes(range(256)),
    "ignored.txt": b"not exported\n",
    "dropdir/inner.txt": b"not exported either\n",
}
BASE_ATTRIBUTES = "ignored.txt export-ignore\ndropdir export-ignore\n"
EOCD = b"PK\x05\x06"


def _drop_central_record(data: bytes, name: str) -> bytes:
    """Remove one central-directory record and fix the end record; its local entry stays in place."""
    eocd = data.rindex(EOCD)
    _sig, disk, disk_cd, entries_disk, entries, cd_size, cd_offset, comment_len = struct.unpack(
        "<4s4H2LH", data[eocd:eocd + 22])
    records, pos = [], cd_offset
    while pos < cd_offset + cd_size:
        name_len, extra_len, comment = struct.unpack("<3H", data[pos + 28:pos + 34])
        end = pos + 46 + name_len + extra_len + comment
        records.append((data[pos + 46:pos + 46 + name_len], data[pos:end]))
        pos = end
    kept = [raw for record_name, raw in records if record_name != name.encode("utf-8")]
    assert len(kept) == len(records) - 1
    central = b"".join(kept)
    end_record = struct.pack("<4s4H2LH", EOCD, disk, disk_cd, entries_disk - 1, entries - 1, len(central),
                             cd_offset, comment_len)
    return data[:cd_offset] + central + end_record + data[eocd + 22:]


class _Unseekable(io.RawIOBase):
    """A write-only stream, so zipfile writes data descriptors after each member."""

    def __init__(self) -> None:
        super().__init__()
        self.data = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, chunk) -> int:
        self.data += chunk
        return len(chunk)


@unittest.skipUnless(GIT, "git is not installed")
class ReleaseRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.repo = _make_repo(cls.base, BASE_FILES, BASE_ATTRIBUTES)
        cls.reference = cls.base / "reference.zip"
        result = cls._run_script(["build", "--repo", str(cls.repo), "--tag", TAG, "--out", str(cls.reference)],
                                 tz="KST-9")
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        cls.reference_spec = json.loads(result.stdout)["rebuild_spec"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @classmethod
    def _run_script(cls, args, tz=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cls.base,
                              env=_isolated_env(cls.base, tz), capture_output=True, text=True,
                              encoding="utf-8", timeout=300)

    def _check(self, zip_path: Path, *extra, tz=None) -> tuple[int, dict]:
        result = self._run_script(["check", "--repo", str(self.repo), "--tag", TAG, "--zip", str(zip_path), *extra],
                                  tz=tz)
        return result.returncode, json.loads(result.stdout)

    def _variant(self, name: str, mutate) -> Path:
        """Copy the reference ZIP member by member, letting mutate() edit the list of (info, data)."""
        with zipfile.ZipFile(self.reference) as source:
            entries = [(info, source.read(info)) for info in source.infolist()]
            comment = source.comment
        entries = mutate(entries)
        path = self.base / name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # duplicate names are intentional in one variant
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
                target.comment = comment
                for info, data in entries:
                    target.writestr(info, data)
        return path

    # --- build -----------------------------------------------------------------------------------

    def test_builds_under_different_ambient_time_zones_are_byte_identical(self) -> None:
        utc = self.base / "build-utc.zip"
        result = self._run_script(["build", "--repo", str(self.repo), "--tag", TAG, "--out", str(utc)], tz="UTC0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.reference.read_bytes(), utc.read_bytes())
        spec = json.loads(result.stdout)["rebuild_spec"]
        self.assertEqual(spec, self.reference_spec)
        self.assertEqual(spec["expected_sha256"], hashlib.sha256(utc.read_bytes()).hexdigest())

    def test_spec_records_the_exact_rebuild_parameters(self) -> None:
        spec = self.reference_spec
        commit = subprocess.run([GIT, "rev-parse", TAG + "^{commit}"], cwd=self.repo, capture_output=True,
                                text=True, check=True, env=_isolated_env(self.base)).stdout.strip()
        version = subprocess.run([GIT, "--version"], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(spec["schema"], "agentic-vault/rebuild-spec/v1")
        self.assertEqual(spec["tag"], TAG)
        self.assertEqual(spec["commit"], commit)
        self.assertEqual("git version " + spec["git_version"], version)
        self.assertEqual(spec["tz"], "UTC0")
        self.assertEqual(spec["prefix"], PREFIX)
        self.assertEqual(spec["core_autocrlf"], "false")
        self.assertEqual(spec["format"], "zip")
        self.assertEqual(spec["argv"], ["git", "-c", "core.autocrlf=false", "archive", "--format=zip",
                                        "--prefix=" + PREFIX, "-o", "agentic-vault-1.2.3.zip", TAG])
        self.assertEqual(spec["equivalence_levels"], ["L1_bytes", "L2_members_blobs"])
        self.assertNotIn(str(self.base), json.dumps(spec))

    def test_spec_alias_builds_the_same_bytes(self) -> None:
        out = self.base / "alias.zip"
        result = self._run_script(["spec", "--repo", str(self.repo), "--tag", TAG, "--out", str(out)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out.read_bytes(), self.reference.read_bytes())

    def test_build_keeps_crlf_bytes_korean_names_and_export_ignore(self) -> None:
        with zipfile.ZipFile(self.reference) as archive:
            names = set(archive.namelist())
            self.assertEqual(archive.read(PREFIX + "crlf.txt"), CRLF_BYTES)
            self.assertIn(PREFIX + KOREAN_NAME, names)
        self.assertNotIn(PREFIX + "ignored.txt", names)
        self.assertFalse(any(name.startswith(PREFIX + "dropdir") for name in names))

    def test_build_does_not_change_the_caller_environment(self) -> None:
        module = self.module
        seen = []
        real_run = module.subprocess.run

        def spy(argv, *args, **kwargs):
            if "archive" in argv:
                seen.append(kwargs["env"].get("TZ"))
            return real_run(argv, *args, **kwargs)

        for ambient in ("KST-9", None):
            with self.subTest(ambient=ambient):
                patch = {"TZ": ambient} if ambient else {}
                with mock.patch.dict(os.environ, patch), mock.patch.object(module.subprocess, "run", spy), \
                        mock.patch("sys.stdout", new_callable=io.StringIO):
                    if ambient is None:
                        os.environ.pop("TZ", None)
                    out = self.base / f"inproc-{ambient}.zip"
                    code = module.main(["build", "--repo", str(self.repo), "--tag", TAG, "--out", str(out)])
                    self.assertEqual(os.environ.get("TZ"), ambient)
                self.assertEqual(code, 0)
                self.assertEqual(seen[-1], "UTC0")
                self.assertEqual(out.read_bytes(), self.reference.read_bytes())

    def test_build_refuses_an_existing_output(self) -> None:
        result = self._run_script(["build", "--repo", str(self.repo), "--tag", TAG, "--out", str(self.reference)])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"], "output_exists")

    def test_bad_or_missing_tag_is_exit_2(self) -> None:
        for tag, code in (("v9.9.9", "tag_not_found"), ("not-a-tag", "invalid_tag"), ("v1..2", "invalid_tag")):
            for command in ("build", "check"):
                with self.subTest(tag=tag, command=command):
                    target = ["--out", str(self.base / f"never-{command}.zip")] if command == "build" \
                        else ["--zip", str(self.reference)]
                    result = self._run_script([command, "--repo", str(self.repo), "--tag", tag, *target])
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(json.loads(result.stdout)["error"], code)
                    self.assertNotIn(tag, result.stdout + result.stderr)
        self.assertFalse((self.base / "never-build.zip").exists())

    def test_usage_errors_are_exit_2_without_echo(self) -> None:
        cases = (["check", "--repo", str(self.repo), "--tag", TAG],
                 ["build", "--tag", TAG, "--out", str(self.base / "x.zip"), "--tz", "$(evil)"],
                 ["frobnicate"],
                 ["check", "--repo", str(self.repo), "--tag", TAG, "--zip", str(self.base / "absent.zip")])
        for args in cases:
            with self.subTest(args=args[:2]):
                result = self._run_script(args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["status"], "error")
                self.assertNotIn("evil", result.stdout + result.stderr)
                self.assertNotIn(str(self.base), result.stdout + result.stderr)

    # --- check -----------------------------------------------------------------------------------

    def test_check_reference_matches_at_both_levels(self) -> None:
        code, report = self._check(self.reference, tz="KST-9")
        self.assertEqual(code, 0)
        self.assertEqual(report["l1"]["status"], "match")
        self.assertEqual(report["l1"]["tz"], "UTC0")
        self.assertEqual(report["l2"]["status"], "match")
        counts = report["l2"]["counts"]
        self.assertEqual(counts["expected_files"], 6)
        self.assertEqual(counts["export_ignored"], 2)
        self.assertEqual(counts["matched"], 6)
        self.assertTrue(report["l2"]["archive_comment_is_commit"])
        self.assertEqual(report["l2"]["parser_ambiguity"], [])
        self.assertEqual(report["l2"]["conversion_unverified"], [])

    def test_plain_git_archive_in_another_zone_differs_at_l1_and_matches_at_l2(self) -> None:
        plain = self.base / "plain-kst.zip"
        subprocess.run([GIT, "-c", "core.autocrlf=false", "archive", "--format=zip", "--prefix=" + PREFIX,
                        "-o", str(plain), TAG], cwd=self.repo, env=_isolated_env(self.base, "KST-9"), check=True)
        self.assertNotEqual(plain.read_bytes(), self.reference.read_bytes())
        code, report = self._check(plain)
        self.assertEqual(code, 0)
        self.assertEqual(report["l1"]["status"], "mismatch")
        self.assertEqual(report["l1"]["given_sha256"], hashlib.sha256(plain.read_bytes()).hexdigest())
        self.assertEqual(report["l1"]["rebuilt_sha256"], self.reference_spec["expected_sha256"])
        self.assertEqual(report["l2"]["status"], "match")
        # A historical release made in KST-9 is reproduced byte for byte with --tz.
        code, report = self._check(plain, "--tz", "KST-9")
        self.assertEqual(code, 0)
        self.assertEqual(report["l1"]["status"], "match")
        self.assertEqual(report["l1"]["tz"], "KST-9")

    def test_without_the_prefix_l2_reports_every_member_outside_it(self) -> None:
        bare = self.base / "no-prefix.zip"
        subprocess.run([GIT, "-c", "core.autocrlf=false", "archive", "--format=zip", "-o", str(bare), TAG],
                       cwd=self.repo, env=_isolated_env(self.base, "UTC0"), check=True)
        code, report = self._check(bare)
        self.assertEqual(code, 0)
        self.assertEqual(report["l1"]["status"], "mismatch")
        self.assertEqual(report["l2"]["status"], "mismatch")
        self.assertEqual(report["l2"]["counts"]["matched"], 0)
        self.assertTrue(all("outside_prefix" in item["reasons"] for item in report["l2"]["unsafe"]))
        self.assertEqual(len(report["l2"]["missing"]), 6)

    def test_check_flags_extra_modified_and_missing_members(self) -> None:
        def extra(entries):
            return [*entries, (zipfile.ZipInfo(PREFIX + "extra.txt", (2026, 1, 1, 0, 0, 0)), b"extra\n")]

        def modified(entries):
            return [(info, data + b"tamper" if info.filename == PREFIX + "README.txt" else data)
                    for info, data in entries]

        def missing(entries):
            return [(info, data) for info, data in entries if info.filename != PREFIX + KOREAN_NAME]

        for name, mutate, field, member in (("extra", extra, "extra", "extra.txt"),
                                            ("modified", modified, "mismatched", "README.txt"),
                                            ("missing", missing, "missing", KOREAN_NAME)):
            with self.subTest(name=name):
                code, report = self._check(self._variant(f"{name}.zip", mutate))
                self.assertEqual(code, 0)
                self.assertEqual(report["l1"]["status"], "mismatch")
                self.assertEqual(report["l2"]["status"], "mismatch")
                self.assertEqual(report["l2"][field], [member])
                for other in {"extra", "mismatched", "missing"} - {field}:
                    self.assertEqual(report["l2"][other], [])
                self.assertEqual(report["l2"]["unsafe"], [])
                self.assertNotIn("tamper", json.dumps(report))
                self.assertNotIn("fixture readme", json.dumps(report))

    def test_check_flags_unsafe_entries(self) -> None:
        def add(name, data=b"x\n", attr=None):
            def mutate(entries):
                info = zipfile.ZipInfo("placeholder", (2026, 1, 1, 0, 0, 0))
                info.filename = name  # bypass ZipInfo's os.sep normalisation on Windows
                if attr is not None:
                    info.create_system = 3
                    info.external_attr = attr << 16
                return [*entries, (info, data)]
            return mutate

        def duplicate(changed):
            def mutate(entries):
                info, data = next((info, data) for info, data in entries if info.filename == PREFIX + "README.txt")
                # A fresh ZipInfo: writestr() updates the object it is given, so reusing it would point both
                # central records at the second copy.
                second = zipfile.ZipInfo(info.filename, info.date_time)
                return [*entries, (second, data + b"second copy" if changed else data)]
            return mutate

        backslash = chr(92)
        bidi = PREFIX + "rtl" + chr(0x202E) + "txt.exe"
        c1 = PREFIX + "c1" + chr(0x85) + ".txt"
        cases = (("dotdot", add(PREFIX + "../evil.txt"), PREFIX + "../evil.txt", "dot_dot_segment"),
                 ("symlink", add(PREFIX + "link", b"README.txt", 0o120777), PREFIX + "link", "symlink"),
                 ("duplicate", duplicate(False), PREFIX + "README.txt", "duplicate"),
                 ("duplicate-differs", duplicate(True), PREFIX + "README.txt", "duplicate"),
                 ("absolute", add("/abs.txt"), "/abs.txt", "absolute_path"),
                 ("backslash", add(PREFIX + "sub\\file.txt"), PREFIX + "sub\\file.txt", "backslash"),
                 ("drive", add("C:/drive.txt"), "C:/drive.txt", "drive_letter"),
                 ("outside", add("elsewhere/file.txt"), "elsewhere/file.txt", "outside_prefix"),
                 ("control", add(PREFIX + "a" + chr(1) + "b.txt"),
                  PREFIX + "a" + backslash + "u0001b.txt", "control_character"),
                 ("bidi", add(bidi), PREFIX + "rtl" + backslash + "u202etxt.exe", "control_character"),
                 ("c1", add(c1), PREFIX + "c1" + backslash + "u0085.txt", "control_character"),
                 ("empty-segment", add(PREFIX + "a//b.txt"), PREFIX + "a//b.txt", "empty_or_dot_segment"),
                 ("dot-segment", add(PREFIX + "./b.txt"), PREFIX + "./b.txt", "empty_or_dot_segment"))
        for name, mutate, member, reason in cases:
            with self.subTest(name=name):
                result = self._run_script(["check", "--repo", str(self.repo), "--tag", TAG, "--zip",
                                           str(self._variant(f"unsafe-{name}.zip", mutate))])
                code, report = result.returncode, json.loads(result.stdout)
                self.assertEqual(code, 0)
                self.assertEqual(report["l2"]["status"], "mismatch")
                flagged = {item["name"]: item["reasons"] for item in report["l2"]["unsafe"]}
                self.assertIn(member, flagged)
                self.assertIn(reason, flagged[member])
                # Format and C0/C1 control characters from ZIP names never reach the output raw.
                for raw in (chr(0x202E), chr(0x85), chr(1)):
                    self.assertNotIn(raw, result.stdout)
                if name.startswith("duplicate"):
                    self.assertEqual(report["l2"]["duplicates"], [PREFIX + "README.txt"])
                    self.assertEqual(report["l2"]["counts"]["matched"], 6)  # the first copy is compared
                    self.assertEqual(report["l2"]["mismatched"], [])

    def test_check_refuses_oversized_archives(self) -> None:
        module = self.module
        for constant, value in (("MAX_MEMBERS", 3), ("MAX_TOTAL_UNCOMPRESSED", 10)):
            with self.subTest(constant=constant):
                report = module.check_members(self.reference, repo=self.repo, commit=self.reference_spec["commit"],
                                              prefix=PREFIX, **{constant.lower(): value})
                self.assertEqual(report["status"], "refused")
                self.assertEqual(report["reason"], "members_over_limit" if constant == "MAX_MEMBERS"
                                 else "uncompressed_over_limit")
        report = module.check_members(self.reference, repo=self.repo, commit=self.reference_spec["commit"],
                                      prefix=PREFIX, max_zip_bytes=1)
        self.assertEqual((report["status"], report["reason"]), ("refused", "zip_over_limit"))
        small = self._variant("one-member.zip", lambda entries: entries[:1])
        report = module.check_members(small, repo=self.repo, commit=self.reference_spec["commit"], prefix=PREFIX,
                                      max_members=3)
        self.assertEqual((report["status"], report["reason"]), ("refused", "tree_over_limit"))

    def _ambiguous(self, name: str, data: bytes) -> dict:
        path = self.base / f"ambiguous-{name}.zip"
        path.write_bytes(data)
        code, report = self._check(path)
        self.assertEqual(code, 0)
        return report

    def test_check_flags_zip_parser_ambiguity(self) -> None:
        reference = self.reference.read_bytes()
        hostile = io.BytesIO()
        with zipfile.ZipFile(hostile, "w") as archive:
            archive.writestr(PREFIX + "README.txt", b"hostile readme\n")

        def inserted(position):
            def mutate(entries):
                extra = (zipfile.ZipInfo(PREFIX + "hidden.txt", (2026, 1, 1, 0, 0, 0)), b"hidden\n")
                return [*entries[:position], extra, *entries[position:]]
            return mutate

        middle = _drop_central_record(self._variant("hidden-middle.zip", inserted(1)).read_bytes(),
                                      PREFIX + "hidden.txt")
        last = _drop_central_record(self._variant("hidden-last.zip", inserted(10 ** 6)).read_bytes(),
                                    PREFIX + "hidden.txt")
        with zipfile.ZipFile(io.BytesIO(middle)) as archive:
            after_hidden = sorted(archive.infolist(), key=lambda info: info.header_offset)[1].filename
        with zipfile.ZipFile(self.reference) as archive:
            directory = next(info for info in archive.infolist() if info.is_dir())
        renamed = bytearray(reference)
        renamed[directory.header_offset + 30] ^= 0x03  # local name only; the central name is unchanged

        def unicode_path(entries):
            raw = PREFIX + "alias.txt"
            alias = (PREFIX + "README.txt").encode("utf-8")
            info = zipfile.ZipInfo(raw, (2026, 1, 1, 0, 0, 0))
            info.extra = struct.pack("<2HBL", 0x7075, 5 + len(alias), 1,
                                     zlib.crc32(raw.encode("ascii"))) + alias
            return [*entries, (info, b"alias\n")]

        aliased = self._variant("unicode-path.zip", unicode_path).read_bytes()
        cases = (("prepended-junk", b"JUNK" * 16 + reference, "data_before_first_entry", None),
                 ("prepended-zip", hostile.getvalue() + reference, "data_before_first_entry", None),
                 ("unreferenced-middle", middle, "gap_before_entry", after_hidden),
                 ("unreferenced-last", last, "data_before_central_directory", None),
                 ("trailing", reference + b"trailing bytes", "data_after_end_record", None),
                 ("local-name", bytes(renamed), "local_name_differs", directory.filename),
                 ("unicode-path", aliased, "unicode_path_field", PREFIX + "alias.txt"))
        for name, data, code, member in cases:
            with self.subTest(name=name):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:  # zipfile itself accepts every variant
                    self.assertGreater(len(archive.infolist()), 0)
                report = self._ambiguous(name, data)
                self.assertEqual(report["l2"]["status"], "mismatch")
                self.assertIn(code, report["l2"]["parser_ambiguity"])
                flagged = {item["name"]: item["reasons"] for item in report["l2"]["unsafe"]}
                if member is not None:
                    self.assertIn("parser_ambiguity", flagged.get(member, []))
                self.assertNotIn("hostile", json.dumps(report))
                self.assertNotIn("hidden", json.dumps(report))

    def test_check_accepts_zip64_data_descriptors(self) -> None:
        stream = _Unseekable()
        with zipfile.ZipFile(self.reference) as source:
            entries = [(info, source.read(info)) for info in source.infolist()]
            comment = source.comment
        with zipfile.ZipFile(stream, "w") as target:
            target.comment = comment
            for info, data in entries:
                copy = zipfile.ZipInfo(info.filename, info.date_time)
                if info.is_dir():
                    target.writestr(copy, b"")
                    continue
                copy.compress_type = zipfile.ZIP_DEFLATED
                with target.open(copy, "w", force_zip64=True) as member:
                    member.write(data)
        path = self.base / "zip64-descriptors.zip"
        path.write_bytes(bytes(stream.data))
        with zipfile.ZipFile(path) as archive:
            self.assertTrue(any(info.flag_bits & 0x08 for info in archive.infolist()))
        report = self.module.check_members(path, repo=self.repo, commit=self.reference_spec["commit"], prefix=PREFIX)
        self.assertEqual(report["parser_ambiguity"], [])
        self.assertEqual(report["unsafe"], [])
        self.assertEqual((report["status"], report["counts"]["matched"]), ("match", 6))

    def test_check_attr_failure_keeps_the_l1_report(self) -> None:
        module = self.module
        real_run = module.subprocess.run

        def fake(argv, *args, **kwargs):
            if "check-attr" in argv:
                return subprocess.CompletedProcess(argv, 129, b"", b"")
            return real_run(argv, *args, **kwargs)

        with mock.patch.object(module.subprocess, "run", fake), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = module.main(["check", "--repo", str(self.repo), "--tag", TAG, "--zip", str(self.reference)])
        self.assertEqual(code, 0)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["l1"]["status"], "match")
        self.assertEqual((report["l2"]["status"], report["l2"]["reason"]), ("refused", "git_check_attr_failed"))

    def test_ambient_core_autocrlf_true_does_not_change_the_zip(self) -> None:
        config = self.base / "autocrlf.gitconfig"
        config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
        env = _isolated_env(self.base)
        env["GIT_CONFIG_GLOBAL"] = str(config)
        plain = self.base / "plain-autocrlf.zip"
        subprocess.run([GIT, "archive", "--format=zip", "--prefix=" + PREFIX, "-o", str(plain), TAG],
                       cwd=self.repo, env=env, check=True)
        with zipfile.ZipFile(plain) as archive:  # the ambient setting is in effect without the -c option
            self.assertEqual(archive.read(PREFIX + "README.txt"), b"fixture readme\r\n")
        out = self.base / "build-autocrlf.zip"
        build = subprocess.run([sys.executable, str(SCRIPT), "build", "--repo", str(self.repo), "--tag", TAG,
                                "--out", str(out)], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertEqual(out.read_bytes(), self.reference.read_bytes())
        check = subprocess.run([sys.executable, str(SCRIPT), "check", "--repo", str(self.repo), "--tag", TAG,
                                "--zip", str(out)], env=env, capture_output=True, text=True, encoding="utf-8")
        report = json.loads(check.stdout)
        self.assertEqual((report["l1"]["status"], report["l2"]["status"]), ("match", "match"))

    def test_check_refuses_a_file_that_is_not_a_zip(self) -> None:
        bogus = self.base / "bogus.zip"
        bogus.write_bytes(b"secret note text, not a zip")
        code, report = self._check(bogus)
        self.assertEqual(code, 0)
        self.assertEqual(report["l1"]["status"], "mismatch")
        self.assertEqual(report["l2"]["status"], "refused")
        self.assertEqual(report["l2"]["reason"], "bad_zip")
        self.assertNotIn("secret", json.dumps(report))

    def test_check_reads_a_spec_block_from_a_receipt(self) -> None:
        receipt = self.base / "verification.json"
        receipt.write_text(json.dumps({"release": TAG, "rebuild_spec": self.reference_spec}), encoding="utf-8")
        result = self._run_script(["check", "--repo", str(self.repo), "--spec", str(receipt), "--zip",
                                   str(self.reference)])
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["l1"]["status"], "match")
        self.assertTrue(report["l1"]["given_matches_spec_sha256"])
        self.assertTrue(report["l1"]["spec_commit_matches"])
        bad = dict(self.reference_spec, format="tar")
        receipt.write_text(json.dumps({"rebuild_spec": bad}), encoding="utf-8")
        result = self._run_script(["check", "--repo", str(self.repo), "--spec", str(receipt), "--zip",
                                   str(self.reference)])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"], "invalid_spec")


class ReleaseRebuildDocTests(unittest.TestCase):
    DOC = ROOT / "docs" / "release-integrity.md"

    def setUp(self) -> None:
        self.doc = self.DOC.read_text(encoding="utf-8")

    def test_documents_the_utc_build_command_in_bash_and_powershell(self) -> None:
        argv = " ".join(_load()._argv("vVERSION", "agentic-vault-VERSION/", "agentic-vault-VERSION.zip"))
        self.assertIn("TZ=UTC0 " + argv, self.doc.splitlines())
        powershell = ("$savedTz = $env:TZ", "try {", "$env:TZ = 'UTC0'", "} finally {", "$env:TZ = $savedTz")
        lines = [line.strip() for line in self.doc.splitlines()]
        for literal in powershell:
            with self.subTest(literal=literal):
                self.assertIn(literal, lines)
        self.assertIn(argv, lines)

    def test_documents_the_spec_fields_levels_and_script(self) -> None:
        for literal in ("rebuild_spec", "schema", "git_version", "core_autocrlf", "expected_sha256",
                        "equivalence_levels", "L1_bytes", "L2_members_blobs", "export-ignore", "export-subst",
                        "](../scripts/release_rebuild.py)", "--tz KST-9",
                        "d39adcfa0e2b728d913ae001e2d754c9555884050bf6ede08900e1c9e7c88044",
                        "b53d4d94a2b9adc1a79c95e63b0386a92e6658b4cce98403940ab6ffd7012627",
                        "parser_ambiguity", "conversion_unverified", "git_check_attr_failed", "git 2.40"):
            with self.subTest(literal=literal):
                self.assertIn(literal, self.doc)

    def test_docs_index_mentions_the_rebuild_check(self) -> None:
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        row = next(line for line in index.splitlines() if "](release-integrity.md)" in line)
        self.assertIn("rebuild_spec", row)


@unittest.skipUnless(GIT, "git is not installed")
class ExportSubstTests(unittest.TestCase):
    def test_export_subst_members_are_reported_as_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _make_repo(base, {"version.txt": b"$Format:%H$\n", "plain.txt": b"plain\n"},
                              "version.txt export-subst\n")
            out = base / "subst.zip"
            env = _isolated_env(base, "UTC0")
            build = subprocess.run([sys.executable, str(SCRIPT), "build", "--repo", str(repo), "--tag", TAG,
                                    "--out", str(out)], env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(build.returncode, 0, build.stderr)
            check = subprocess.run([sys.executable, str(SCRIPT), "check", "--repo", str(repo), "--tag", TAG,
                                    "--zip", str(out)], env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(check.returncode, 0, check.stderr)
            report = json.loads(check.stdout)
            self.assertEqual(report["l1"]["status"], "match")
            self.assertEqual(report["l2"]["status"], "partial")
            self.assertEqual(report["l2"]["export_subst_unverified"], ["version.txt"])
            self.assertEqual(report["l2"]["counts"]["matched"], 2)  # .gitattributes and plain.txt

    def _build_and_check(self, files: dict, attributes: str, config: tuple = ()) -> tuple[Path, dict]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        repo = _make_repo(base, files, attributes)
        env = _isolated_env(base, "UTC0")
        for key, value in config:
            subprocess.run([GIT, "config", key, value], cwd=repo, env=env, check=True)
        out = base / "out.zip"
        build = subprocess.run([sys.executable, str(SCRIPT), "build", "--repo", str(repo), "--tag", TAG,
                                "--out", str(out)], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(build.returncode, 0, build.stderr)
        check = subprocess.run([sys.executable, str(SCRIPT), "check", "--repo", str(repo), "--tag", TAG,
                                "--zip", str(out)], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(check.returncode, 0, check.stderr)
        return out, json.loads(check.stdout)

    def test_export_attributes_follow_git_archive(self) -> None:
        files = {"keep.txt": b"keep\n", "kept-anyway.txt": b"string value\n", "dirpat/inner.txt": b"dropped\n",
                 "subst-string.txt": b"$Format:%H$\n", "win.txt": b"one\ntwo\n", "id.txt": b"$" + b"Id$\n",
                 "text-auto.txt": b"a\nb\n", "lf.txt": b"a\nb\n", "binary.txt": b"a\nb\n", "input.txt": b"a\nb\n"}
        attributes = ("dirpat/ export-ignore\nkept-anyway.txt export-ignore=no\n"
                      "subst-string.txt export-subst=yes\nwin.txt eol=crlf\nid.txt ident\n"
                      "text-auto.txt text=auto\nlf.txt text eol=lf\nbinary.txt -text eol=crlf\n"
                      "input.txt text=input\n")
        out, report = self._build_and_check(files, attributes)
        with zipfile.ZipFile(out) as archive:  # git archive converted win.txt and ignored only dirpat/
            self.assertEqual(archive.read(PREFIX + "win.txt"), b"one\r\ntwo\r\n")
            self.assertIn(PREFIX + "kept-anyway.txt", archive.namelist())
            self.assertFalse(any(name.startswith(PREFIX + "dirpat") for name in archive.namelist()))
        l2 = report["l2"]
        self.assertEqual(report["l1"]["status"], "match")
        for key in ("missing", "extra", "mismatched", "unsafe", "export_subst_unverified", "parser_ambiguity"):
            with self.subTest(key=key):
                self.assertEqual(l2.get(key), [])
        self.assertEqual(l2.get("conversion_unverified"), ["id.txt", "text-auto.txt", "win.txt"])
        self.assertEqual(l2["status"], "partial")
        self.assertEqual((l2["counts"]["export_ignored"], l2["counts"]["matched"]), (1, 7))

    def test_git_archive_data_descriptors_are_accepted(self) -> None:
        files = {"big.bin": bytes(range(256)) * 32, "big.txt": b"line\n" * 2000, "small.txt": b"small\n"}
        out, report = self._build_and_check(files, "", config=(("core.bigFileThreshold", "1k"),))
        with zipfile.ZipFile(out) as archive:
            streamed = [info.filename for info in archive.infolist() if info.flag_bits & 0x08]
        self.assertEqual(sorted(streamed), [PREFIX + "big.bin", PREFIX + "big.txt"])
        self.assertEqual(report["l1"]["status"], "match")
        self.assertEqual(report["l2"].get("parser_ambiguity"), [])
        self.assertEqual((report["l2"]["status"], report["l2"]["counts"]["matched"]), ("match", 4))


if __name__ == "__main__":
    unittest.main()
