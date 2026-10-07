from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "import_qmd_candidates.py"


def load_module():
    if not MODULE.is_file():
        return None
    spec = importlib.util.spec_from_file_location("qmd_candidate_adapter_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load_module()


class QmdCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(adapter, "qmd candidate normalizer is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def test_converts_official_array_shape_without_trusting_provider_content(self):
        result = adapter.convert([{"docid": "#6c90f0", "score": .89, "file": "qmd://qmd/20-knowledge/README.md", "explain": {"text": "FORGED"}, "snippet": "FORGED", "approved": True}], "qmd")
        self.assertEqual(result["provider"], "qmd")
        self.assertEqual(result["candidates"], [{"path": "20-knowledge/README.md", "rank": 1}])
        self.assertEqual(result["diagnostics"]["status"], "ok")
        self.assertNotIn("FORGED", json.dumps(result))
        self.assertNotIn("approved", json.dumps(result))

    def test_preserves_unicode_literal_paths_spaces_and_array_ranks(self):
        result = adapter.convert([{}, {"file": "qmd://NS/20-knowledge/한글 문서.md", "score": .1}, {"file": "qmd://NS/20-knowledge/second.md", "score": .99}], "NS")
        self.assertEqual(result["candidates"], [{"path": "20-knowledge/한글 문서.md", "rank": 2}, {"path": "20-knowledge/second.md", "rank": 3}])

    def test_rejects_unsafe_uri_rows_without_echoing_their_content(self):
        paths = ["https://qmd/a.md", "QMD://qmd/a.md", "qmd://other/a.md", "qmd://secret:password@qmd/a.md", "qmd://qmd:123/a.md", "qmd://qmd/a.md#fragment", "qmd://qmd/a.md?", "qmd://qmd/a.md#", "qmd://qmd/a%20b.md", "qmd://qmd/../a.md", "qmd://qmd/a/../../a.md", "qmd://qmd/./a.md", "qmd://qmd//a.md", "qmd://qmd/dir\\a.md", "qmd://qmd/a\x00.md", "qmd://qmd/a\n.md", "qmd://qmd/a\x7f.md", "qmd://qmd/a\u202e.md", "qmd://qmd/C:/a.md", "qmd://qmd/nul.md", "qmd://qmd/COM1.md", "qmd://qmd/conin$.md", "qmd://qmd/name./a.md", "qmd://qmd/name /a.md", "qmd://qmd/a.md ", "qmd://qmd/a.txt", "qmd://qmd/a?.md", " qmd://qmd/a.md"]
        result = adapter.convert([{"file": value} for value in paths] + [{"file": "qmd://qmd/safe.md"}], "qmd")
        self.assertEqual(result["candidates"], [{"path": "safe.md", "rank": len(paths) + 1}])
        self.assertEqual(result["diagnostics"]["skipped_invalid"], len(paths))
        self.assertFalse(result["diagnostics"]["candidate_coverage_complete"])
        self.assertTrue(result["diagnostics"]["processing_complete"])
        self.assertNotIn("password", json.dumps(result))

    def test_collection_is_explicit_safe_and_matches_literal_namespace(self):
        for collection in (None, "", "../qmd", "qmd/notes", "qmd\\notes", "qmd?x", "a b", "qmd%20", "qmd@evil", "CON", "x" * 65):
            with self.subTest(collection=collection):
                result = adapter.convert([], collection)
                self.assertEqual(result["diagnostics"]["status"], "invalid_collection")
                self.assertEqual(result["candidates"], [])
        result = adapter.convert([{"file": "qmd://NS/a.md"}, {"file": "qmd://ns/b.md"}], "NS")
        self.assertEqual(result["candidates"], [{"path": "a.md", "rank": 1}])

    def test_url_parser_errors_do_not_echo_unicode_credentials(self):
        result = adapter.convert([{"file": "qmd://SECRET：password@qmd/a.md"}], "qmd")
        self.assertEqual(result["candidates"], [])
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("password", json.dumps(result))

    def test_duplicate_casefold_paths_keep_first_array_rank(self):
        result = adapter.convert([{"file": "qmd://qmd/Note.md"}, {"file": "qmd://qmd/note.md", "score": 100}, {"file": "qmd://qmd/Other.md"}], "qmd")
        self.assertEqual(result["candidates"], [{"path": "Note.md", "rank": 1}, {"path": "Other.md", "rank": 3}])
        self.assertEqual(result["diagnostics"]["duplicates"], 1)

    def test_bad_array_roots_and_row_limit_fail_whole_input(self):
        for root in ({"candidates": []}, "text", None, 7, [{}] * 1001):
            with self.subTest(root_type=type(root)):
                result = adapter.convert(root, "qmd")
                self.assertEqual(result["diagnostics"]["status"], "invalid_input")
                self.assertEqual(result["candidates"], [])
                self.assertFalse(result["diagnostics"]["processing_complete"])

    def test_bad_rows_are_skipped_and_diagnostics_are_bounded(self):
        result = adapter.convert([None, True, "qmd://qmd/fake.md", {"file": 7}] * 200 + [{"file": "qmd://qmd/safe.md"}], "qmd")
        self.assertEqual(result["candidates"], [{"path": "safe.md", "rank": 801}])
        self.assertEqual(result["diagnostics"]["skipped_invalid"], 800)
        self.assertLessEqual(len(result["diagnostics"]["rejected_rows"]), 50)
        self.assertEqual(result["diagnostics"]["omitted_rejections"], 750)

    def test_output_candidate_cap_is_compatible_with_retrieval_sidecars(self):
        result = adapter.convert([{"file": f"qmd://qmd/notes/{index}.md"} for index in range(100)], "qmd")
        self.assertEqual(len(result["candidates"]), 50)
        self.assertEqual(result["candidates"][0], {"path": "notes/0.md", "rank": 1})
        self.assertEqual(result["candidates"][-1], {"path": "notes/49.md", "rank": 50})
        self.assertEqual(result["diagnostics"]["omitted_candidate_limit"], 50)
        self.assertFalse(result["diagnostics"]["candidate_coverage_complete"])

    def test_long_unicode_paths_keep_output_within_retrieval_sidecar_byte_limit(self):
        directory = "/".join(["한" * 200] * 4)
        result = adapter.convert([{"file": f"qmd://qmd/{directory}/{index}.md"} for index in range(50)], "qmd")
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")), 65536)
        self.assertGreater(result["diagnostics"]["omitted_candidate_byte_limit"], 0)

    def cli(self, arguments=(), content=None):
        return subprocess.run([sys.executable, str(MODULE), *arguments], input=content, text=True, encoding="utf-8", capture_output=True, check=False, timeout=5)

    def test_cli_stdin_outputs_only_safe_sidecar_json(self):
        run = self.cli(["--collection", "qmd"], json.dumps([{"file": "qmd://qmd/20-knowledge/a.md", "snippet": "FORGED"}]))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout)["candidates"], [{"path": "20-knowledge/a.md", "rank": 1}])
        self.assertNotIn("FORGED", run.stdout)

    def test_cli_explicit_input_file_is_unchanged(self):
        path = self.directory / "qmd.json"
        content = json.dumps([{"file": "qmd://qmd/a.md"}]).encode("utf-8")
        path.write_bytes(content)
        before = {item.name: item.read_bytes() for item in self.directory.iterdir()}
        run = self.cli(["--collection", "qmd", "--input", str(path)])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["candidates"], [{"path": "a.md", "rank": 1}])
        self.assertEqual(before, {item.name: item.read_bytes() for item in self.directory.iterdir()})

    def test_stable_export_allows_fd_and_path_creation_time_representation_difference(self):
        path = self.directory / "input.json"
        path.write_bytes(b"[]")
        actual = adapter.os.fstat
        def alternate_creation_time(descriptor):
            metadata = actual(descriptor)
            fields = {name: getattr(metadata, name) for name in dir(metadata) if name.startswith("st_")}
            fields["st_ctime_ns"] += 100
            return SimpleNamespace(**fields)
        with mock.patch.object(adapter.os, "fstat", side_effect=alternate_creation_time):
            try:
                raw = adapter._read_file(path)
            except ValueError as exc:
                self.fail(f"stable export was rejected for a stat representation difference: {exc}")
            self.assertEqual(raw, b"[]")

    def test_cli_rejects_oversized_stdin_and_oversized_regular_file(self):
        content = " " * (1024 * 1024 + 1)
        path = self.directory / "large.json"
        path.write_text(content, encoding="utf-8")
        for args, stdin in ((["--collection", "qmd"], content), (["--collection", "qmd", "--input", str(path)], None)):
            run = self.cli(args, stdin)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertEqual(json.loads(run.stdout)["candidates"], [])
            self.assertEqual(run.stderr, "")

    def test_cli_rejects_duplicate_fields_nonfinite_values_and_malformed_utf8(self):
        for value in ('[{"file":"qmd://qmd/a.md","file":"qmd://qmd/b.md"}]', '[{"file":"qmd://qmd/a.md","score":NaN}]', "{broken"):
            run = self.cli(["--collection", "qmd"], value)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(json.loads(run.stdout)["diagnostics"]["status"], "invalid_input")
        path = self.directory / "bad.json"
        path.write_bytes(b"[\xff]")
        run = self.cli(["--collection", "qmd", "--input", str(path)])
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["candidates"], [])

    def test_cli_requires_collection_and_reports_failure_json_on_stdout(self):
        run = self.cli([], "[]")
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stderr, "")
        self.assertEqual(json.loads(run.stdout)["candidates"], [])

    def test_cli_rejects_hardlinked_input_before_content_read(self):
        source = self.directory / "source.json"
        source.write_text('[{"file":"qmd://qmd/SECRET.md"}]', encoding="utf-8")
        linked = self.directory / "hard.json"
        os.link(source, linked)
        run = self.cli(["--collection", "qmd", "--input", str(linked)])
        self.assertEqual(run.returncode, 2)
        self.assertNotIn("SECRET", run.stdout)

    def test_cli_rejects_symlink_input_and_parent_directory(self):
        source = self.directory / "source.json"
        source.write_text('[{"file":"qmd://qmd/SECRET.md"}]', encoding="utf-8")
        link = self.directory / "linked.json"
        try:
            link.symlink_to(source)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        run = self.cli(["--collection", "qmd", "--input", str(link)])
        self.assertEqual(run.returncode, 2)
        self.assertNotIn("SECRET", run.stdout)
        directory_link = self.directory / "dir-link"
        directory_link.symlink_to(self.directory, target_is_directory=True)
        run = self.cli(["--collection", "qmd", "--input", str(directory_link / "source.json")])
        self.assertEqual(run.returncode, 2)

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_cli_rejects_junction_parent(self):
        real = self.directory / "real"
        real.mkdir()
        (real / "input.json").write_text('[{"file":"qmd://qmd/SECRET.md"}]', encoding="utf-8")
        linked = self.directory / "junction"
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(real)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.addCleanup(os.rmdir, linked)
        run = self.cli(["--collection", "qmd", "--input", str(linked / "input.json")])
        self.assertEqual(run.returncode, 2)
        self.assertNotIn("SECRET", run.stdout)

    def test_adapter_does_not_assert_current_vault_policy_or_authority(self):
        result = adapter.convert([{"file": "qmd://qmd/90-assets/denied.md"}], "qmd")
        self.assertEqual(result["candidates"], [{"path": "90-assets/denied.md", "rank": 1}])
        self.assertEqual(result["diagnostics"]["vault_policy"], "must_be_checked_by_retrieve")
        self.assertEqual(result["diagnostics"]["provider_status"], "normalization_only_unverified")


if __name__ == "__main__":
    unittest.main()
