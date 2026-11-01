"""Opt-in withdrawal across actual consumers, with unaffected context retained."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/agentic-vault/scripts"
sys.path.insert(0, str(SCRIPTS))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))


class RevocationConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name).resolve()
        self.vault = self.parent / "vault"
        self.vault.mkdir()
        self.config = {"required_keys": ["title"], "enums": {},
                       "deny_zones": ["private"], "exclude_dirs": [],
                       "hot_note": "00-meta/hot.md", "handoff_note": "00-meta/handoff.md",
                       "memory_revocation_file": "00-meta/revocations.json"}
        self.nodes = []
        for ident, path, label in (
            ("source", "20-knowledge/source.md", "old"),
            ("summary", "20-knowledge/summary.md", "copy"),
            ("hot", "00-meta/hot.md", "hot"),
            ("handoff", "00-meta/handoff.md", "handoff"),
        ):
            raw = f"# Context\nREVOKED marker-{label}\nSAFE benign-{label}\n".encode()
            self.write(path, raw)
            self.nodes.append({"id": ident, "path": path,
                               "sha256": hashlib.sha256(raw).hexdigest(), "start_line": 2, "end_line": 2})
        self.registry = {"schema_version": 1, "nodes": self.nodes,
                         "derivations": [{"parent": "source", "child": n["id"]} for n in self.nodes[1:]],
                         "revoked": ["source"]}
        self.write("00-meta/revocations.json", json.dumps(self.registry))
        self.write("00-meta/vault-config.json", json.dumps(self.config))
        self.recall = importlib.import_module("vault_recall")
        self.memory = importlib.import_module("vault_memory")
        self.okf = importlib.import_module("vault_okf")

    def write(self, path, data):
        p = self.vault / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
        return p

    def hook(self, source="startup"):
        return subprocess.run([sys.executable, "-B", str(ROOT / "hooks/session_start.py"),
                               "--vault", str(self.vault)], input=json.dumps({"source": source}),
                              text=True, encoding="utf-8", capture_output=True, timeout=20)

    def unit(self, line, quote, value):
        return {"subject": "Device", "predicate": "mode", "value": value,
                "time": {"valid_from": "2026-11-01"}, "conditions": [], "uncertainty": "reported",
                "sources": [{"path": "20-knowledge/source.md", "line": line, "quote": quote}]}

    def test_lexical_and_advanced_recall_mask_target_and_descendants(self):
        for backend in ("lexical", "bm25", "hybrid"):
            with self.subTest(backend=backend):
                result = self.recall.recall(self.vault, "REVOKED", backend=backend)
                self.assertEqual(result["context"], "")
                self.assertEqual(result["matches"], [])
                safe = self.recall.recall(self.vault, "SAFE", backend=backend)
                self.assertIn("benign-old", safe["context"])
                self.assertTrue(all(m["line"] == 3 for m in safe["matches"]))

    def test_units_cannot_reintroduce_revoked_quote_but_keep_valid_proof_line(self):
        bad = self.memory.compile_units(self.vault, [self.unit(2, "REVOKED marker-old", "marker-old")])
        self.assertEqual(bad["context"], "")
        self.assertEqual(bad["units"], [])
        good = self.memory.compile_units(self.vault, [self.unit(3, "SAFE benign-old", "benign-old")])
        self.assertEqual(len(good["units"]), 1)
        proof = good["units"][0]["sources"][0]
        self.assertEqual(proof["line"], 3)
        self.assertEqual(proof["binding"]["sha256"], self.nodes[0]["sha256"])

    def test_startup_and_compact_exclude_revoked_context_without_deleting_benign(self):
        for event in ("startup", "compact"):
            with self.subTest(source=event):
                p = self.hook(event)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn("REVOKED", p.stdout)
                self.assertIn("benign-hot", p.stdout)
                self.assertIn("benign-handoff", p.stdout)

    def test_okf_future_export_masks_content_and_preserves_raw_source_fingerprint(self):
        destination = self.parent / "bundle"
        result = self.okf.export_bundle(self.vault, ["20-knowledge/source.md", "20-knowledge/summary.md"], destination)
        content = "\n".join(p.read_text(encoding="utf-8") for p in destination.rglob("*.md"))
        self.assertNotIn("REVOKED", content)
        self.assertIn("benign-old", content)
        self.assertIn("benign-copy", content)
        self.assertEqual(result["sources"][0]["sha256"], self.nodes[0]["sha256"])

    def test_stale_source_is_omitted_rather_than_showing_shifted_target(self):
        self.write("20-knowledge/source.md", "# New\nSAFE new\nREVOKED moved\n")
        for backend in ("lexical", "bm25"):
            self.assertNotIn("moved", self.recall.recall(self.vault, "REVOKED", backend=backend)["context"])
        with self.assertRaises(self.okf.ExportError):
            self.okf.export_bundle(self.vault, ["20-knowledge/source.md"], self.parent / "bundle")
        self.assertFalse((self.parent / "bundle").exists())

    def test_malformed_configured_registry_fails_closed_for_every_consumer(self):
        self.write("00-meta/revocations.json", '{"revoked":[],"revoked":["source"]}')
        self.assertEqual(self.recall.recall(self.vault, "SAFE")["context"], "")
        self.assertEqual(self.recall.recall(self.vault, "SAFE", backend="bm25")["context"], "")
        self.assertEqual(self.hook().stdout, "")
        self.assertEqual(self.memory.compile_units(self.vault, [self.unit(3, "SAFE benign-old", "benign-old")])["context"], "")
        with self.assertRaises(self.okf.ExportError):
            self.okf.export_bundle(self.vault, ["20-knowledge/source.md"], self.parent / "bundle")

    def test_registry_swap_during_recall_render_discards_entire_context(self):
        render = self.recall._render_context
        def changed(*args, **kwargs):
            answer = render(*args, **kwargs)
            self.write("00-meta/revocations.json", "{}")
            return answer
        with mock.patch.object(self.recall, "_render_context", changed):
            self.assertEqual(self.recall.recall(self.vault, "SAFE")["context"], "")

    def test_registry_swap_during_advanced_recall_render_discards_context(self):
        render = self.recall._render_context
        def changed(*args, **kwargs):
            answer = render(*args, **kwargs)
            self.write("00-meta/revocations.json", "{}")
            return answer
        with mock.patch.object(self.recall, "_render_context", changed):
            self.assertEqual(self.recall.recall(self.vault, "SAFE", backend="hybrid")["context"], "")

    def test_registry_swap_during_export_never_publishes_partial_output(self):
        project = self.okf._project
        def changed(*args, **kwargs):
            answer = project(*args, **kwargs)
            self.write("00-meta/revocations.json", "{}")
            return answer
        with mock.patch.object(self.okf, "_project", changed), self.assertRaises(self.okf.ExportError):
            self.okf.export_bundle(self.vault, ["20-knowledge/source.md"], self.parent / "bundle")
        self.assertFalse((self.parent / "bundle").exists())

    def test_compact_tasks_and_constraints_use_same_guard(self):
        self.config.update(tasks_note="00-meta/tasks.md", constraints_note="00-meta/constraints.md")
        for key in ("tasks", "constraints"):
            raw = b"# Blocked\n- REVOKED extra-marker\n- SAFE retained-extra\n"
            self.write(f"00-meta/{key}.md", raw)
            self.registry["nodes"].append({"id": key, "path": f"00-meta/{key}.md",
                "sha256": hashlib.sha256(raw).hexdigest(), "start_line": 2, "end_line": 2})
            self.registry["derivations"].append({"parent": "source", "child": key})
        self.write("00-meta/revocations.json", json.dumps(self.registry))
        self.write("00-meta/vault-config.json", json.dumps(self.config))
        output = self.hook("compact")
        self.assertNotIn("REVOKED", output.stdout)
        self.assertIn("retained-extra", output.stdout)

    def test_bom_crlf_hash_is_bound_before_normalizing_hook_and_recall(self):
        raw = b"\xef\xbb\xbf# Context\r\nREVOKED marker-old\r\nSAFE benign-old\r\n"
        self.write("20-knowledge/source.md", raw)
        self.registry["nodes"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
        self.write("00-meta/revocations.json", json.dumps(self.registry))
        for backend in ("lexical", "bm25"):
            self.assertEqual(self.recall.recall(self.vault, "REVOKED", backend=backend)["context"], "")
            self.assertIn("benign-old", self.recall.recall(self.vault, "SAFE", backend=backend)["context"])

    def test_hook_final_config_swap_discards_previously_composed_context(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("revocation_test_hook", ROOT / "hooks/session_start.py")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        compose = hook.compose_context
        def changed(*args, **kwargs):
            result = compose(*args, **kwargs)
            self.config["deny_zones"].append("00-meta")
            self.write("00-meta/vault-config.json", json.dumps(self.config))
            return result
        with mock.patch.object(hook, "compose_context", changed), mock.patch.object(hook, "_read_hook_event", return_value="startup"), mock.patch.object(hook, "_write_stdout") as output:
            self.assertEqual(hook.main(["--vault", str(self.vault)]), 0)
            output.assert_not_called()

    def test_missing_registry_setting_keeps_existing_behavior(self):
        self.config.pop("memory_revocation_file")
        self.write("00-meta/vault-config.json", json.dumps(self.config))
        self.assertIn("marker-old", self.recall.recall(self.vault, "REVOKED")["context"])
        self.assertIn("marker-hot", self.hook().stdout)

    def test_link_diffs_refuse_affected_source_and_mask_candidate_snippets(self):
        links = importlib.import_module("vault_links")
        refused = links.propose_links(self.vault, "20-knowledge/source.md", "SAFE")
        self.assertEqual(refused["diff"], "")
        self.assertNotIn("marker-old", json.dumps(refused))
        self.write("20-knowledge/new.md", "# New\nSAFE origin\n")
        proposed = links.propose_links(self.vault, "20-knowledge/new.md", "SAFE REVOKED")
        self.assertTrue(proposed["proposals"])
        self.assertNotIn("marker-old", json.dumps(proposed))
        self.assertIn("benign-old", json.dumps(proposed))

    def test_final_revocation_io_cannot_bypass_advanced_config_freshness(self):
        revocation = importlib.import_module("vault_revocation")
        original = revocation.RevocationPolicy.recheck
        def changed(policy):
            original(policy)
            self.config["deny_zones"].append("20-knowledge")
            self.write("00-meta/vault-config.json", json.dumps(self.config))
        with mock.patch.object(revocation.RevocationPolicy, "recheck", changed):
            self.assertEqual(self.recall.recall(self.vault, "SAFE", backend="hybrid")["context"], "")

    def test_final_revocation_io_cannot_bypass_export_config_freshness(self):
        revocation = importlib.import_module("vault_revocation")
        original = revocation.RevocationPolicy.recheck
        def changed(policy):
            original(policy)
            self.config["deny_zones"].append("20-knowledge")
            self.write("00-meta/vault-config.json", json.dumps(self.config))
        with mock.patch.object(revocation.RevocationPolicy, "recheck", changed), self.assertRaises(self.okf.ExportError):
            self.okf.export_bundle(self.vault, ["20-knowledge/source.md"], self.parent / "bundle")
        self.assertFalse((self.parent / "bundle").exists())


class AdditionalConsumerTests(unittest.TestCase):
    def registry(self, vault, path, start, *, roots=("selected",)):
        info = {"id": "selected", "path": path,
                "sha256": hashlib.sha256((vault / path).read_bytes()).hexdigest(),
                "start_line": start, "end_line": start}
        data = {"schema_version": 1, "nodes": [info], "derivations": [], "revoked": list(roots)}
        (vault / "00-meta/revocations.json").write_text(json.dumps(data), encoding="utf-8")

    def test_procedure_current_run_cannot_authorize_revoked_instruction(self):
        module = importlib.import_module("test_vault_procedures")
        f = module.ProcedureRecallTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.vault = f.vault.resolve()
        config_path = f.vault / "00-meta/vault-config.json"
        config = json.loads(config_path.read_text())
        config["memory_revocation_file"] = "00-meta/revocations.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.registry(f.vault, "notes/instructions.md", 3, roots=())
        f.run_id = f.native_run(version="revocation")
        f.procedure["run_ids"] = [f.run_id]
        f.save_catalog()
        self.assertEqual(f.lookup()["status"], "matches", "control must have a current successful run")
        self.registry(f.vault, "notes/instructions.md", 3)
        result = f.lookup()
        self.assertEqual(result["matches"], [])
        self.assertNotIn("Check the queue state.", json.dumps(result))

    def test_assembly_masks_revoked_row_but_retains_later_valid_row(self):
        module = importlib.import_module("test_vault_assembly")
        f = module.VaultAssemblyTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.vault = f.vault.resolve()
        f.prepare([f.row(), f.row(subject="Birch", value="12 months")])
        config_path = f.vault / "00-meta/vault-config.json"
        config = json.loads(config_path.read_text())
        config["memory_revocation_file"] = "00-meta/revocations.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.registry(f.vault, "notes/ledger.md", 4)
        self.assertIsNone(f.run_assembly()["value"])
        self.assertNotIn("24 months", json.dumps(module.assembly.extract(f.vault, "input/request.json")))
        f.spec["query"]["subject"] = "Birch"
        f.save()
        self.assertEqual(f.run_assembly()["value"], "12 months")

    def test_assembly_final_withdrawn_row_without_newline_keeps_prior_value(self):
        module = importlib.import_module("test_vault_assembly")
        f = module.VaultAssemblyTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.vault = f.vault.resolve()
        f.prepare([f.row(), f.row(subject="Birch", value="12 months")])
        path = f.vault / "notes/ledger.md"
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        binding = {"path": "notes/ledger.md", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        f.authority = [binding]
        f.spec["sources"] = [binding]
        f.save()
        config_path = f.vault / "00-meta/vault-config.json"
        config = json.loads(config_path.read_text())
        config["memory_revocation_file"] = "00-meta/revocations.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.registry(f.vault, "notes/ledger.md", 5)
        self.assertEqual(f.run_assembly()["value"], "24 months")
        self.assertNotIn("12 months", json.dumps(module.assembly.extract(f.vault, "input/request.json")))


if __name__ == "__main__":
    unittest.main()
