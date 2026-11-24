"""Unit tests for scripts/sync_result_tables.py."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync_result_tables.py"


def _load():
    spec = importlib.util.spec_from_file_location("sync_result_tables_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyncResultTablesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load()

    def _make_workspace(self, tmp: Path) -> tuple[Path, Path, Path]:
        metrics = tmp / "metrics.json"
        metrics.write_text(
            json.dumps(
                {
                    "arms": [
                        {"name": "legacy", "recall": 0.75, "count": 12, "ok": True},
                        {"name": "bm25", "recall": 0.91666, "count": 12, "ok": True},
                    ]
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        spec = tmp / "tables.json"
        spec.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "tables": {
                        "recall": {
                            "source": "metrics.json",
                            "rows_path": "arms",
                            "columns": [
                                {"header": "Arm", "key": "name"},
                                {"header": "Recall", "key": "recall", "fmt": "pct:1", "align": "right"},
                                {"header": "N", "key": "count", "fmt": "int", "align": "right"},
                            ],
                        }
                    },
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        doc = tmp / "report.md"
        doc.write_text(
            "# Report\n\nProse before.\n\n"
            "<!-- table:recall -->\n| stale |\n| --- |\n| 0 |\n<!-- /table:recall -->\n\n"
            "Prose after.\n",
            encoding="utf-8",
        )
        return spec, metrics, doc

    def test_check_exits_1_on_drift_write_updates_atomically_and_recheck_exits_0(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av-tsync-") as td:
            spec, _metrics, doc = self._make_workspace(Path(td))

            c1 = subprocess.run(
                [sys.executable, str(SCRIPT), "check", "--spec", str(spec), str(doc)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(c1.returncode, 1)
            r1 = json.loads(c1.stdout)
            self.assertTrue(r1["drifted"])
            self.assertFalse(r1["in_sync"])

            w = subprocess.run(
                [sys.executable, str(SCRIPT), "write", "--spec", str(spec), str(doc)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(w.returncode, 0, w.stderr)
            updated_bytes = doc.read_bytes()
            self.assertNotIn(b"\r\n", updated_bytes)
            text = updated_bytes.decode("utf-8")
            self.assertIn("Prose before.", text)
            self.assertIn("Prose after.", text)
            self.assertIn("| legacy | 75.0% | 12 |", text)
            self.assertIn("| bm25 | 91.7% | 12 |", text)

            c2 = subprocess.run(
                [sys.executable, str(SCRIPT), "check", "--spec", str(spec), str(doc)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(c2.returncode, 0, c2.stderr)
            r2 = json.loads(c2.stdout)
            self.assertFalse(r2["drifted"])
            self.assertTrue(r2["in_sync"])

    def test_rejects_duplicate_json_keys_nan_and_unknown_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av-tsync-err-") as td:
            tmp = Path(td)
            spec, metrics, doc = self._make_workspace(tmp)

            metrics.write_text('{"arms": [], "arms": []}', encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "check", "--spec", str(spec), str(doc)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(proc.returncode, 2)

            metrics.write_text('{"arms": [{"name": "a", "recall": NaN, "count": 1}]}', encoding="utf-8")
            proc2 = subprocess.run(
                [sys.executable, str(SCRIPT), "check", "--spec", str(spec), str(doc)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(proc2.returncode, 2)

            metrics.write_text('{"arms": [{"name": "a", "recall": 0.5, "count": 1}]}', encoding="utf-8")
            doc.write_text("<!-- table:missing -->\n<!-- /table:missing -->\n", encoding="utf-8")
            proc3 = subprocess.run(
                [sys.executable, str(SCRIPT), "check", "--spec", str(spec), str(doc)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(proc3.returncode, 2)

    def test_rejects_source_path_escaping_spec_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av-tsync-esc-") as td:
            tmp = Path(td)
            sub = tmp / "sub"
            sub.mkdir()
            (tmp / "outside.json").write_text('{"arms": []}', encoding="utf-8")
            spec = sub / "tables.json"
            spec.write_text(
                json.dumps({
                    "schema_version": 1,
                    "tables": {
                        "t1": {
                            "source": "../outside.json",
                            "rows_path": "arms",
                            "columns": [{"header": "A", "key": "a"}],
                        }
                    },
                }),
                encoding="utf-8",
            )
            doc = sub / "doc.md"
            doc.write_text("<!-- table:t1 -->\n<!-- /table:t1 -->\n", encoding="utf-8")
            with self.assertRaises(self.mod.TableSyncError):
                self.mod.run_sync(spec, [doc], write=False)

    def test_render_table_escapes_pipes_and_newlines(self) -> None:
        table = self.mod.render_table(
            [{"a": "x|y\nz", "b": 0.12345, "c": r"foo\|bar", "d": "a`b"}],
            [
                {"header": "A", "key": "a"},
                {"header": "B", "key": "b", "fmt": "fixed:3"},
                {"header": "C", "key": "c"},
                {"header": "D", "key": "d", "fmt": "code"},
            ],
        )
        self.assertIn(r"x\|y z", table)
        self.assertIn("0.123", table)
        self.assertIn(r"foo\\\|bar", table)
        self.assertIn("``a`b``", table)

    def test_rejects_malformed_duplicate_unclosed_orphan_markers_and_comment_injection(self) -> None:
        tables = {"a": "| X |\n| --- |\n| 1 |"}
        bad_docs = [
            "<!-- table:a -->\nIMPORTANT PROSE\n<!-- table:a -->\n| old |\n<!-- /table:a -->\n",
            "<!-- table:a -->\n| X |\n| --- |\n| 1 |\n<!-- /table:a -->\n<!-- table:a -->\n",
            "<!-- table:a -->\n| X |\n| --- |\n| 1 |\n<!-- /table:a -->\n<!-- /table:a -->\n",
            "<!-- table:a -->\n<!-- /table:b -->\n",
            "<!-- table:a -->\n| X |\n<!-- /table:a -->\n<!-- table:a -->\n| X |\n<!-- /table:a -->\n",
        ]
        for bad in bad_docs:
            with self.subTest(doc=bad[:40]):
                with self.assertRaises(self.mod.TableSyncError):
                    self.mod.sync_markdown_text(bad, tables)

        with self.assertRaises(self.mod.TableSyncError):
            self.mod.render_table(
                [{"a": "safe <!-- /table:a -->"}],
                [{"header": "A", "key": "a"}],
            )

    def test_rejects_drive_root_relative_paths_overflow_float_and_bool_for_numeric_fmt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av-tsync-sec-") as td:
            tmp = Path(td)
            for bad_src in ("/etc/passwd", "C:foo.json", r"sub\metrics.json"):
                with self.subTest(bad_src=bad_src):
                    with self.assertRaises(self.mod.TableSyncError):
                        self.mod._resolve_under(tmp, bad_src, "source")

            spec, metrics, doc = self._make_workspace(tmp)
            metrics.write_text('{"arms": [{"name": "a", "recall": 1e400, "count": 1}]}', encoding="utf-8")
            with self.assertRaises(self.mod.TableSyncError):
                self.mod.run_sync(spec, [doc], write=False)

            with self.assertRaises(self.mod.TableSyncError):
                self.mod.render_table([{"n": True}], [{"header": "N", "key": "n", "fmt": "int"}])

    def test_multi_target_write_validates_all_targets_before_writing_any(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av-tsync-mt-") as td:
            tmp = Path(td)
            spec, _metrics, doc1 = self._make_workspace(tmp)
            before1 = doc1.read_text(encoding="utf-8")
            doc2 = tmp / "bad.md"
            doc2.write_text("<!-- table:recall -->\nUNCLOSED\n", encoding="utf-8")
            with self.assertRaises(self.mod.TableSyncError):
                self.mod.run_sync(spec, [doc1, doc2], write=True)
            self.assertEqual(doc1.read_text(encoding="utf-8"), before1)

    def test_rejects_source_and_target_symlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="av-tsync-sym-") as td:
            tmp = Path(td)
            spec, metrics, doc = self._make_workspace(tmp)
            link_src = tmp / "link-metrics.json"
            link_doc = tmp / "link-doc.md"
            try:
                os.symlink(metrics, link_src)
                os.symlink(doc, link_doc)
            except (OSError, NotImplementedError):
                self.skipTest("OS does not allow unprivileged symlink creation")

            with self.assertRaises(self.mod.TableSyncError):
                self.mod.run_sync(spec, [link_doc], write=False)

            bad_spec = tmp / "sym-spec.json"
            bad_spec.write_text(
                spec.read_text(encoding="utf-8").replace("metrics.json", "link-metrics.json"),
                encoding="utf-8",
            )
            with self.assertRaises(self.mod.TableSyncError):
                self.mod.run_sync(bad_spec, [doc], write=False)

    def test_rejects_reparse_point_components_even_without_os_symlink_privilege(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="av-tsync-rp-") as td:
            tmp = Path(td)
            spec, metrics, doc = self._make_workspace(tmp)

            # Target leaf marked as reparse point must be refused fail-closed
            with patch.object(self.mod, "_is_link_or_reparse", side_effect=lambda p: p == doc):
                with self.assertRaises(self.mod.TableSyncError):
                    self.mod.run_sync(spec, [doc], write=False)

            # Source component under spec dir marked as reparse point must be refused fail-closed
            resolved_metrics = metrics.resolve()
            with patch.object(self.mod, "_is_link_or_reparse", side_effect=lambda p: p == resolved_metrics):
                with self.assertRaises(self.mod.TableSyncError):
                    self.mod.run_sync(spec, [doc], write=False)


if __name__ == "__main__":
    unittest.main()
