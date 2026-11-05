from __future__ import annotations

import copy
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agentic-vault/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class OntologyQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.write("00-meta/vault-config.json", {
            "required_keys": ["title"], "enums": {},
            "deny_zones": ["10-inbox/_processed"],
        })
        source = self.vault / "20-knowledge/terms.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"Example definitions.\n")

    def write(self, relative, value):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
        return path

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("vault_ontology_query"),
                             "the ontology query module has not been implemented")
        return importlib.import_module("vault_ontology_query")

    def proof(self):
        return [{"path": "20-knowledge/terms.md", "line": 1,
                 "quote": "Example definitions."}]

    def declarations(self):
        return {
            "schema_version": 1, "ontology_id": "example", "version": "one",
            "types": ["organization", "location"],
            "entities": [
                {"id": "factory", "type": "organization", "label": "Factory",
                 "aliases": ["Plant", "Caf\u00e9"], "review": "reviewed", "sources": self.proof()},
                {"id": "city", "type": "location", "label": "City",
                 "aliases": [], "review": "reviewed", "sources": self.proof()},
                {"id": "draft", "type": "location", "label": "Draft",
                 "aliases": ["Plant"], "review": "candidate", "sources": self.proof()},
            ],
            "relations": [{"id": "located_in", "label": "Located in",
                           "domain": "organization", "range": "location",
                           "review": "reviewed", "sources": self.proof()}],
            "links": [{"id": "factory_city", "subject": "factory", "predicate": "located_in",
                       "object": "city", "review": "reviewed", "sources": self.proof()}],
            "metrics": [{"id": "production", "label": "Production", "aliases": ["Output"],
                         "definition": "Reviewed production definition.",
                         "unit": "items", "grain": "day", "population": "factory",
                         "aggregation": "sum", "review": "reviewed", "sources": self.proof()}],
        }

    def capture(self, declarations=None):
        self.module()
        core = importlib.import_module("vault_ontology")
        self.write("00-meta/ontology.json", self.declarations() if declarations is None else declarations)
        result = core.capture_registry(self.vault, "00-meta/ontology.json")
        self.assertEqual(result.get("type"), "ontology_registry", result)
        return result

    def plan(self):
        return {
            "schema_version": 1, "entities": ["factory"],
            "relations": [{"subject": "factory", "predicate": "located_in", "object": "city"}],
            "metrics": [{"id": "production", "slots": {
                "unit": "items", "grain": "day", "population": "factory", "aggregation": "sum",
                "period": {"start": "2024-02-29", "end": "2024-03-01"},
            }}],
        }

    def context_ids(self, result):
        return [item["record"]["id"] for item in result["context"]]

    def assert_no_context(self, result, status):
        self.assertEqual(result["status"], status, result)
        self.assertEqual(result["context"], [])

    def codes(self, result):
        return [gap["code"] for gap in result["gaps"]]

    def test_search_exact_nfc_casefold_without_fuzzy_or_candidate_matches(self):
        module = self.module()
        snapshot = self.capture()
        result = module.search_terms(self.vault, snapshot, "CAFE\u0301")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matches"], [{"id": "factory", "kind": "entity", "label": "Factory"}])
        self.assertEqual(self.context_ids(result), ["factory"])
        self.assertEqual(result["context"][0]["record"]["aliases"], ["Plant", "Caf\u00e9"])
        for query in ("Fact", " Factory", "Draft"):
            with self.subTest(query=query):
                self.assert_no_context(module.search_terms(self.vault, snapshot, query), "not_found")
        self.assertEqual(module.search_terms(self.vault, snapshot, "Plant")["status"], "matched")

    def test_ambiguous_aliases_never_produce_context_or_merge_records(self):
        data = self.declarations()
        data["metrics"][0]["aliases"].append("Plant")
        snapshot = self.capture(data)
        result = self.module().search_terms(self.vault, snapshot, "plant")
        self.assert_no_context(result, "ambiguous")
        self.assertEqual([match["id"] for match in result["matches"]], ["factory", "production"])

    def test_get_link_returns_only_its_reviewed_dependency_closure_and_sources(self):
        snapshot = self.capture()
        result = self.module().get_term(self.vault, snapshot, "factory_city")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(set(self.context_ids(result)), {"factory", "city", "located_in", "factory_city"})
        self.assertNotIn("draft", self.context_ids(result))
        for item in result["context"]:
            self.assertEqual(item["record"]["sources"][0]["quote"], "Example definitions.")
            self.assertRegex(item["record"]["sources"][0]["span_sha256"], r"^[0-9a-f]{64}$")

    def test_get_supports_all_four_global_record_kinds_and_missing_ids(self):
        snapshot = self.capture()
        module = self.module()
        for term_id, kind in (("factory", "entity"), ("production", "metric"),
                              ("located_in", "relation"), ("factory_city", "link")):
            with self.subTest(term_id=term_id):
                result = module.get_term(self.vault, snapshot, term_id)
                self.assertEqual(result["status"], "matched")
                self.assertIn(kind, [item["kind"] for item in result["context"]])
        self.assert_no_context(module.get_term(self.vault, snapshot, "missing"), "not_found")

    def test_candidate_selected_or_dependency_blocks_get_context(self):
        for collection, term_id in (("entities", "factory"), ("relations", "located_in"),
                                    ("links", "factory_city")):
            with self.subTest(collection=collection):
                data = self.declarations()
                next(item for item in data[collection] if item["id"] == term_id)["review"] = "candidate"
                snapshot = self.capture(data)
                result = self.module().get_term(self.vault, snapshot, "factory_city")
                self.assert_no_context(result, "needs_review")
                self.assertIn("unreviewed_dependency", self.codes(result))
                self.assertEqual(result["matches"], [])
        snapshot = self.capture()
        self.assert_no_context(self.module().get_term(self.vault, snapshot, "draft"), "needs_review")

    def test_ready_for_evidence_contains_selected_closure_and_no_answer_claim(self):
        snapshot = self.capture()
        result = self.module().check_answerable(self.vault, snapshot, self.plan())
        self.assertEqual(result["status"], "ready_for_evidence")
        self.assertEqual(result["gaps"], [])
        self.assertEqual(set(self.context_ids(result)), {"factory", "city", "located_in", "factory_city", "production"})
        self.assertEqual(result["authority"], "data_only")
        self.assertEqual(result["registry_sha256"], snapshot["registry_sha256"])
        self.assertEqual(set(result), {"type", "schema_version", "authority", "registry_sha256",
                                       "status", "matches", "context", "gaps"})

    def test_metric_only_plan_needs_no_unrelated_entity_or_link_context(self):
        snapshot = self.capture()
        request = self.plan()
        request["entities"] = []
        request["relations"] = []
        result = self.module().check_answerable(self.vault, snapshot, request)
        self.assertEqual(result["status"], "ready_for_evidence")
        self.assertEqual(self.context_ids(result), ["production"])

    def test_empty_plan_has_gap(self):
        snapshot = self.capture()
        request = {"schema_version": 1, "entities": [], "relations": [], "metrics": []}
        result = self.module().check_answerable(self.vault, snapshot, request)
        self.assert_no_context(result, "needs_clarification")
        self.assertEqual(self.codes(result), ["empty_plan"])

    def test_missing_metric_slots_and_literal_conflicts_have_stable_gaps(self):
        snapshot = self.capture()
        request = self.plan()
        request["metrics"][0]["slots"] = {"unit": "ITEMS"}
        result = self.module().check_answerable(self.vault, snapshot, request)
        self.assert_no_context(result, "needs_clarification")
        self.assertEqual(result["gaps"], [
            {"code": "conflicting_slot", "id": "production", "slot": "unit"},
            {"code": "missing_slot", "id": "production", "slot": "grain"},
            {"code": "missing_slot", "id": "production", "slot": "population"},
            {"code": "missing_slot", "id": "production", "slot": "aggregation"},
            {"code": "missing_slot", "id": "production", "slot": "period"},
        ])
        self.assertEqual(result, self.module().check_answerable(self.vault, snapshot, request))

    def test_each_metric_dimension_requires_literal_equality(self):
        snapshot = self.capture()
        for slot in ("unit", "grain", "population", "aggregation"):
            with self.subTest(slot=slot):
                request = self.plan()
                request["metrics"][0]["slots"][slot] += " "
                result = self.module().check_answerable(self.vault, snapshot, request)
                self.assert_no_context(result, "needs_clarification")
                self.assertEqual(result["gaps"], [{"code": "conflicting_slot", "id": "production", "slot": slot}])

    def test_unknown_ids_and_unsupported_or_reversed_triples_produce_gaps(self):
        snapshot = self.capture()
        requests = []
        request = self.plan()
        request["entities"] = ["missing"]
        requests.append((request, "unknown_entity"))
        request = self.plan()
        request["metrics"][0]["id"] = "missing"
        requests.append((request, "unknown_metric"))
        request = self.plan()
        request["relations"][0]["predicate"] = "missing"
        requests.append((request, "unknown_relation"))
        request = self.plan()
        request["relations"][0] = {"subject": "city", "predicate": "located_in", "object": "factory"}
        requests.append((request, "unsupported_link"))
        request = self.plan()
        request["relations"][0]["object"] = "draft"
        requests.append((request, "unsupported_link"))
        for request, code in requests:
            with self.subTest(code=code):
                result = self.module().check_answerable(self.vault, snapshot, request)
                self.assert_no_context(result, "needs_clarification")
                self.assertIn(code, self.codes(result))

    def test_each_unreviewed_relation_dependency_blocks_preflight(self):
        for collection, term_id in (("entities", "factory"), ("entities", "city"),
                                    ("relations", "located_in"), ("links", "factory_city"),
                                    ("metrics", "production")):
            with self.subTest(term_id=term_id):
                data = self.declarations()
                next(item for item in data[collection] if item["id"] == term_id)["review"] = "candidate"
                result = self.module().check_answerable(self.vault, self.capture(data), self.plan())
                self.assert_no_context(result, "needs_clarification")
                self.assertIn({"code": "unreviewed_dependency", "id": term_id}, result["gaps"])

    def test_strict_period_dates_and_half_open_order_are_validated(self):
        snapshot = self.capture()
        invalid = [
            {"start": "2024-03-01", "end": "2024-03-01"},
            {"start": "2024-03-02", "end": "2024-03-01"},
            {"start": "2023-02-29", "end": "2024-03-01"},
            {"start": "20240229", "end": "2024-03-01"},
            {"start": "2024-2-29", "end": "2024-03-01"},
            {"start": "2024-02-29T00:00:00", "end": "2024-03-01"},
            {"start": "0000-01-01", "end": "2024-03-01"},
            {"start": "2024-02-29", "end": "2024-03-01", "inclusive": True},
            {"start": "2024-02-29"},
            ["2024-02-29", "2024-03-01"],
            None,
        ]
        for period in invalid:
            with self.subTest(period=period):
                request = self.plan()
                request["metrics"][0]["slots"]["period"] = period
                result = self.module().check_answerable(self.vault, snapshot, request)
                self.assert_no_context(result, "unavailable")
                self.assertEqual(result["reason"], "invalid_request")

    def test_complete_request_is_validated_before_any_registry_lookup(self):
        request = self.plan()
        request["entities"] = ["missing"]
        request["metrics"][0]["slots"]["period"] = {"start": "bad", "end": "bad"}
        result = self.module().check_answerable(self.vault, {}, request)
        self.assert_no_context(result, "unavailable")
        self.assertEqual(result["reason"], "invalid_request")

    def test_unknown_keys_wrong_types_duplicate_ids_and_duplicate_triples_are_invalid(self):
        snapshot = self.capture()
        invalid = []
        for key, value in (("schema_version", True), ("schema_version", 1.0),
                           ("entities", "factory"), ("entities", ["Factory"]),
                           ("entities", ["factory", "factory"]), ("relations", [self.plan()["relations"][0]] * 2),
                           ("metrics", [self.plan()["metrics"][0]] * 2)):
            request = self.plan()
            request[key] = value
            invalid.append(request)
        request = self.plan()
        request["extra"] = "no"
        invalid.append(request)
        request = self.plan()
        del request["entities"]
        invalid.append(request)
        request = self.plan()
        request["relations"][0]["extra"] = "no"
        invalid.append(request)
        request = self.plan()
        request["metrics"][0]["slots"]["extra"] = "no"
        invalid.append(request)
        request = self.plan()
        request["metrics"][0]["slots"]["unit"] = 3
        invalid.append(request)
        request = self.plan()
        request["metrics"][0]["extra"] = "no"
        invalid.append(request)
        invalid.extend((None, [], {"schema_version": 1}, {"schema_version": 1, "entities": [], "relations": [], "metrics": [None]}))
        for request in invalid:
            with self.subTest(request=request):
                result = self.module().check_answerable(self.vault, snapshot, request)
                self.assert_no_context(result, "unavailable")
                self.assertEqual(result["reason"], "invalid_request")

    def test_request_and_search_size_limits_fail_closed(self):
        snapshot = self.capture()
        module = self.module()
        for value in (None, "", " ", "x" * 8193, "bad\x00"):
            self.assert_no_context(module.search_terms(self.vault, snapshot, value), "unavailable")
        for value in (None, "Factory", "x" * 97, ""):
            self.assert_no_context(module.get_term(self.vault, snapshot, value), "unavailable")

    def test_each_plan_list_accepts_32_unique_items_and_rejects_33(self):
        snapshot = self.capture()
        for field in ("entities", "relations", "metrics"):
            with self.subTest(field=field):
                request = {"schema_version": 1, "entities": [], "relations": [], "metrics": []}
                if field == "entities":
                    items = ["missing" + str(index) for index in range(33)]
                elif field == "relations":
                    items = [{"subject": "factory", "predicate": "located_in", "object": "missing" + str(index)}
                             for index in range(33)]
                else:
                    items = [{"id": "missing" + str(index), "slots": {}} for index in range(33)]
                request[field] = items[:32]
                result = self.module().check_answerable(self.vault, snapshot, request)
                self.assert_no_context(result, "needs_clarification")
                request[field] = items
                self.assert_no_context(self.module().check_answerable(self.vault, snapshot, request), "unavailable")

    def test_request_total_json_budget_is_checked_before_registry_lookup(self):
        request = self.plan()
        request["metrics"] = [{"id": "metric" + str(index), "slots": {
            "unit": "x" * 8192, "grain": "x" * 8192, "population": "x" * 8192,
            "aggregation": "x" * 8192,
        }} for index in range(3)]
        result = self.module().check_answerable(self.vault, {}, request)
        self.assert_no_context(result, "unavailable")
        self.assertEqual(result["reason"], "invalid_request")

    def test_stale_unselected_definition_clears_all_query_context(self):
        snapshot = self.capture()
        data = self.declarations()
        data["entities"][2]["label"] = "Changed draft"
        self.write("00-meta/ontology.json", data)
        module = self.module()
        for result in (module.search_terms(self.vault, snapshot, "Factory"),
                       module.get_term(self.vault, snapshot, "factory"),
                       module.check_answerable(self.vault, snapshot, self.plan())):
            self.assert_no_context(result, "unavailable")
            self.assertEqual(result["matches"], [])

    def test_query_never_mutates_input_snapshot_request_or_vault_files(self):
        snapshot = self.capture()
        request = self.plan()
        before_snapshot, before_request = copy.deepcopy(snapshot), copy.deepcopy(request)
        before_files = {str(path.relative_to(self.vault)): (path.read_bytes(), path.stat().st_mtime_ns)
                        for path in self.vault.rglob("*") if path.is_file()}
        module = self.module()
        module.search_terms(self.vault, snapshot, "Factory")
        module.get_term(self.vault, snapshot, "factory_city")
        module.check_answerable(self.vault, snapshot, request)
        self.assertEqual(snapshot, before_snapshot)
        self.assertEqual(request, before_request)
        self.assertEqual(before_files, {str(path.relative_to(self.vault)): (path.read_bytes(), path.stat().st_mtime_ns)
                                       for path in self.vault.rglob("*") if path.is_file()})

    def test_source_change_during_each_query_clears_built_context_and_matches(self):
        module = self.module()
        original_context = module._context
        source = self.vault / "20-knowledge/terms.md"
        for operation in ("search", "get", "preflight"):
            with self.subTest(operation=operation):
                source.write_bytes(b"Example definitions.\n")
                snapshot = self.capture()

                def edit_after_context(*args):
                    result = original_context(*args)
                    source.write_bytes(b"Example definitions.\nConcurrent edit.\n")
                    return result

                with mock.patch.object(module, "_context", side_effect=edit_after_context):
                    if operation == "search":
                        result = module.search_terms(self.vault, snapshot, "Factory")
                    elif operation == "get":
                        result = module.get_term(self.vault, snapshot, "factory_city")
                    else:
                        result = module.check_answerable(self.vault, snapshot, self.plan())
                self.assert_no_context(result, "unavailable")
                self.assertEqual(result["matches"], [])
                self.assertEqual(result["gaps"], [])

    def test_preflight_freezes_caller_owned_plan_before_registry_work(self):
        snapshot = self.capture()
        request = self.plan()
        request["metrics"][0]["slots"]["unit"] = "wrong"
        core = importlib.import_module("vault_ontology")
        original_policy = core.policy

        def mutate_during_policy(*args):
            result = original_policy(*args)
            request["metrics"][0]["slots"]["unit"] = "items"
            return result

        with mock.patch.object(core, "policy", side_effect=mutate_during_policy):
            result = self.module().check_answerable(self.vault, snapshot, request)
        self.assert_no_context(result, "needs_clarification")
        self.assertEqual(result["gaps"], [{"code": "conflicting_slot", "id": "production", "slot": "unit"}])


if __name__ == "__main__":
    unittest.main()
