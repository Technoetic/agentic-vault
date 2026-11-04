"""Strict input and evidence boundaries for the optional routing grader."""
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

try:
    from _winfs import make_junction
except ImportError:  # run as tests.<module> from repository root
    from tests._winfs import make_junction


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/skill_routing_contract.py"


def catalog_document():
    return {
        "kind": "skill_routing_catalog", "schema_version": 1,
        "source_kind": "synthetic",
        "skills": [
            {"id": name, "name": name.split(":")[1],
             "description": "Human-authored test skill.",
             "source_sha256": "a" * 64, "source_path": name.replace(":", "/") + "/SKILL.md"}
            for name in ("test:prepare", "test:implement", "test:helper", "test:forbidden")
        ],
    }


def suite_document():
    return {
        "kind": "skill_routing_suite", "schema_version": 1, "suite_id": "contract-tests",
        "cases": [{
            "id": "compose", "prompt": "Prepare and implement.",
            "context": {"in_vault": False, "request_mode": "implementation"},
            "expect": {
                "required": ["test:prepare", "test:implement"],
                "allowed": ["test:helper"], "forbidden": ["test:forbidden"],
                "before": [["test:prepare", "test:implement"]],
                "entrypoints": ["test:prepare"],
            },
        }],
    }


def canonical_digest(value):
    """Independent binding builder for fixtures, not the implementation helper."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def trace_document(catalog, suite, invoked=None, *, observations=None):
    return {
        "kind": "skill_routing_trace", "schema_version": 1,
        "suite_sha256": canonical_digest(suite), "catalog_sha256": canonical_digest(catalog),
        "source_kind": "scripted_control", "observation_kind": "skill_invocation",
        "runtime": {"host": "scripted", "version": "1", "model": "none"},
        "observations": observations if observations is not None else [{
            "case_id": "compose", "status": "complete",
            "invoked": ["test:prepare", "test:implement"] if invoked is None else invoked,
        }],
    }


class RoutingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("skill_routing_contract", SCRIPT)
        cls.contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.contract)

    def setUp(self):
        self.catalog = catalog_document()
        self.suite = suite_document()
        self.trace = trace_document(self.catalog, self.suite)

    def assert_invalid(self, function, *args):
        with self.assertRaises(self.contract.RoutingError) as caught:
            function(*args)
        self.assertIsInstance(caught.exception.code, str)
        self.assertTrue(caught.exception.code)

    def evaluate(self, invoked=None, **kwargs):
        trace = trace_document(self.catalog, self.suite, invoked)
        return self.contract.evaluate(self.catalog, self.suite, trace, **kwargs)

    def test_composed_required_skills_pass_with_neutral_helper_and_repeats(self):
        report = self.evaluate(["test:helper", "test:prepare", "test:implement", "test:prepare"])
        self.assertEqual(report["summary"], {
            "cases": 1, "scored": 1, "unmeasured": 0, "passed": 1, "failed": 0})
        row = report["cases"][0]
        self.assertEqual(row["status"], "pass")
        self.assertEqual(row["observed"], ["test:helper", "test:prepare", "test:implement", "test:prepare"])
        self.assertEqual(row["reason_codes"], [])
        self.assertFalse(report["model_run_performed"])
        self.assertEqual(report["source_kind"], "scripted_control")

    def test_all_missing_forbidden_and_unexpected_invocations_are_reported(self):
        row = self.evaluate(["test:forbidden", "outside:unknown", "outside:unknown"])["cases"][0]
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["missing_required"], ["test:prepare", "test:implement"])
        self.assertEqual(row["forbidden"], ["test:forbidden"])
        self.assertEqual(row["unexpected"], ["outside:unknown"])
        self.assertEqual(row["entrypoint_violations"], ["test:forbidden"])
        self.assertEqual(row["reason_codes"], ["missing_required", "forbidden", "unexpected", "entrypoint_violation"])

    def test_every_later_invocation_requires_an_earlier_predecessor(self):
        row = self.evaluate(["test:implement", "test:prepare", "test:implement"])["cases"][0]
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["order_violations"], [["test:prepare", "test:implement"]])
        self.assertEqual(row["entrypoint_violations"], ["test:implement"])

    def test_required_list_is_not_a_single_expected_target(self):
        row = self.evaluate(["test:prepare"])["cases"][0]
        self.assertEqual(row["missing_required"], ["test:implement"])
        self.assertEqual(row["status"], "fail")

    def test_nontrigger_requires_a_complete_empty_observation(self):
        self.suite["cases"][0]["expect"] = {
            "required": [], "allowed": [], "forbidden": [], "before": [], "entrypoints": []}
        report = self.evaluate([])
        self.assertEqual(report["cases"][0]["status"], "pass")
        self.assertEqual(self.evaluate(["test:helper"])["cases"][0]["unexpected"], ["test:helper"])

    def test_absent_row_is_unmeasured_never_an_empty_pass(self):
        trace = trace_document(self.catalog, self.suite, observations=[])
        report = self.contract.evaluate(self.catalog, self.suite, trace)
        self.assertEqual(report["summary"], {
            "cases": 1, "scored": 0, "unmeasured": 1, "passed": 0, "failed": 0})
        self.assertEqual(report["cases"][0]["reason_codes"], ["missing_observation"])
        self.assertEqual(report["cases"][0]["missing_required"], [])

    def test_error_and_unavailable_are_unmeasured(self):
        for status in ("error", "unavailable"):
            with self.subTest(status=status):
                trace = trace_document(self.catalog, self.suite, observations=[{
                    "case_id": "compose", "status": status, "invoked": []}])
                report = self.contract.evaluate(self.catalog, self.suite, trace)
                self.assertEqual(report["cases"][0]["status"], "unmeasured")
                self.assertEqual(report["cases"][0]["reason_codes"], ["observation_" + status])

    def test_self_report_cannot_be_scored_even_when_it_claims_success(self):
        self.trace["observation_kind"] = "self_report"
        report = self.contract.evaluate(self.catalog, self.suite, self.trace)
        self.assertEqual(report["observation_kind"], "self_report")
        self.assertEqual(report["summary"]["scored"], 0)
        self.assertEqual(report["cases"][0]["reason_codes"], ["unsupported_observation_kind"])
        self.assert_invalid(self.contract.evaluate, self.catalog, self.suite, self.trace, "self_report")

    def test_file_reads_require_selection_and_keep_their_label(self):
        self.trace["observation_kind"] = "skill_file_read"
        report = self.contract.evaluate(self.catalog, self.suite, self.trace)
        self.assertEqual(report["summary"]["scored"], 0)
        report = self.contract.evaluate(self.catalog, self.suite, self.trace, "skill_file_read")
        self.assertEqual(report["summary"]["passed"], 1)
        self.assertEqual(report["observation_kind"], "skill_file_read")
        self.assertFalse(report["model_run_performed"])

    def test_requesting_file_reads_cannot_relabel_invocation_evidence(self):
        report = self.contract.evaluate(self.catalog, self.suite, self.trace, "skill_file_read")
        self.assertEqual(report["summary"]["scored"], 0)
        self.assertEqual(report["observation_kind"], "skill_invocation")

    def test_report_does_not_disclose_prompt_or_context(self):
        self.suite["cases"][0]["prompt"] = "private-prompt-value"
        self.suite["cases"][0]["context"]["secret"] = "private-context-value"
        text = json.dumps(self.evaluate())
        self.assertNotIn("private-prompt-value", text)
        self.assertNotIn("private-context-value", text)

    def test_validation_and_reports_return_independent_values(self):
        before = copy.deepcopy((self.catalog, self.suite, self.trace))
        catalog = self.contract.validate_catalog(self.catalog)
        suite = self.contract.validate_suite(self.suite, self.catalog)
        trace = self.contract.validate_trace(self.trace, self.suite, self.catalog)
        catalog["skills"][0]["name"] = "changed"
        suite["cases"][0]["context"]["in_vault"] = True
        trace["runtime"]["host"] = "changed"
        report = self.contract.evaluate(self.catalog, self.suite, self.trace)
        report["runtime"]["host"] = "changed"
        report["cases"][0]["observed"].clear()
        self.assertEqual((self.catalog, self.suite, self.trace), before)

    def test_catalog_binding_includes_description_and_source_path(self):
        for field in ("description", "source_path", "source_sha256"):
            changed = copy.deepcopy(self.catalog)
            changed["skills"][0][field] += "changed" if field != "source_sha256" else ""
            if field == "source_sha256":
                changed["skills"][0][field] = "b" * 64
            with self.subTest(field=field):
                self.assert_invalid(self.contract.evaluate, changed, self.suite, self.trace)

    def test_suite_binding_includes_context_prompt_and_case_order(self):
        for field in ("prompt", "context", "expect"):
            changed = copy.deepcopy(self.suite)
            if field == "context":
                changed["cases"][0][field]["in_vault"] = True
            elif field == "expect":
                changed["cases"][0][field]["entrypoints"] = []
            else:
                changed["cases"][0][field] += " changed"
            with self.subTest(field=field):
                self.assert_invalid(self.contract.evaluate, self.catalog, changed, self.trace)

    def test_canonical_digest_is_utf8_sorted_and_has_no_newline(self):
        value = {"z": "한글", "a": [1, True, None]}
        expected = hashlib.sha256('{"a":[1,true,null],"z":"한글"}'.encode("utf-8")).hexdigest()
        self.assertEqual(self.contract.digest_document(value), expected)

    def test_catalog_owned_ids_are_unique_but_names_can_be_ambiguous(self):
        changed = copy.deepcopy(self.catalog)
        changed["skills"][1]["name"] = changed["skills"][0]["name"]
        self.assertEqual(len(self.contract.validate_catalog(changed)["skills"]), 4)
        changed["skills"][1]["id"] = changed["skills"][0]["id"]
        self.assert_invalid(self.contract.validate_catalog, changed)

    def test_conflicting_expectations_and_duplicate_list_members_are_rejected(self):
        for mode in ("overlap", "duplicate", "unknown", "entrypoint", "duplicate_entrypoint"):
            changed = copy.deepcopy(self.suite)
            expect = changed["cases"][0]["expect"]
            if mode == "overlap":
                expect["forbidden"].append("test:prepare")
            elif mode == "duplicate":
                expect["required"].append("test:prepare")
            elif mode == "unknown":
                expect["allowed"].append("missing:skill")
            elif mode == "entrypoint":
                expect["entrypoints"] = ["test:helper"]
            else:
                expect["entrypoints"].append("test:prepare")
            with self.subTest(mode=mode):
                self.assert_invalid(self.contract.validate_suite, changed, self.catalog)

    def test_before_rejects_cycles_self_pairs_duplicates_and_outside_skills(self):
        for before in (
                [["test:prepare", "test:implement"], ["test:implement", "test:prepare"]],
                [["test:prepare", "test:prepare"]],
                [["test:prepare", "test:implement"], ["test:prepare", "test:implement"]],
                [["test:forbidden", "test:implement"]],
                [["missing:skill", "test:implement"]],
                [["test:prepare"]],
        ):
            changed = copy.deepcopy(self.suite)
            changed["cases"][0]["expect"]["before"] = before
            with self.subTest(before=before):
                self.assert_invalid(self.contract.validate_suite, changed, self.catalog)

    def test_nonadjacent_cycle_is_rejected(self):
        changed = copy.deepcopy(self.suite)
        changed["cases"][0]["expect"]["before"] = [
            ["test:prepare", "test:implement"], ["test:implement", "test:helper"],
            ["test:helper", "test:prepare"]]
        self.assert_invalid(self.contract.validate_suite, changed, self.catalog)

    def test_unknown_schema_fields_are_rejected_at_every_boundary(self):
        for target in ("catalog", "skill", "suite", "case", "expect", "trace", "runtime", "observation"):
            catalog, suite, trace = copy.deepcopy((self.catalog, self.suite, self.trace))
            subjects = {
                "catalog": catalog, "skill": catalog["skills"][0], "suite": suite,
                "case": suite["cases"][0], "expect": suite["cases"][0]["expect"],
                "trace": trace, "runtime": trace["runtime"], "observation": trace["observations"][0]}
            subjects[target]["unsupported"] = "private-extra"
            with self.subTest(target=target):
                if target in ("catalog", "skill"):
                    self.assert_invalid(self.contract.validate_catalog, catalog)
                elif target in ("suite", "case", "expect"):
                    self.assert_invalid(self.contract.validate_suite, suite, catalog)
                else:
                    self.assert_invalid(self.contract.validate_trace, trace, suite, catalog)

    def test_required_fields_and_boolean_schema_versions_are_rejected(self):
        for source, function, args in (
                (self.catalog, self.contract.validate_catalog, ()),
                (self.suite, self.contract.validate_suite, (self.catalog,)),
                (self.trace, self.contract.validate_trace, (self.suite, self.catalog))):
            for mode in ("missing", "bool", "future"):
                value = copy.deepcopy(source)
                if mode == "missing":
                    del value["schema_version"]
                else:
                    value["schema_version"] = True if mode == "bool" else 2
                with self.subTest(kind=source["kind"], mode=mode):
                    self.assert_invalid(function, value, *args)

    def test_malformed_ids_and_digests_fail_before_grading(self):
        for identifier in ("unowned", "Upper:skill", "owner:has space", "owner:../skill", "x:y:z", "한글:skill"):
            catalog = copy.deepcopy(self.catalog)
            catalog["skills"][0]["id"] = identifier
            with self.subTest(identifier=identifier):
                self.assert_invalid(self.contract.validate_catalog, catalog)
        for value in ("a" * 63, "A" * 64, "g" * 64, None):
            trace = copy.deepcopy(self.trace)
            trace["suite_sha256"] = value
            with self.subTest(digest=value):
                self.assert_invalid(self.contract.validate_trace, trace, self.suite, self.catalog)

    def test_source_paths_are_locators_and_cannot_traverse_or_be_absolute(self):
        for locator in ("../SKILL.md", "a/../SKILL.md", "/SKILL.md", "a\\SKILL.md", "C:/SKILL.md", "a//SKILL.md", "./SKILL.md"):
            catalog = copy.deepcopy(self.catalog)
            catalog["skills"][0]["source_path"] = locator
            with self.subTest(locator=locator):
                self.assert_invalid(self.contract.validate_catalog, catalog)

    def test_duplicate_and_unknown_observation_cases_are_invalid(self):
        for mode in ("duplicate", "unknown"):
            trace = copy.deepcopy(self.trace)
            extra = copy.deepcopy(trace["observations"][0])
            if mode == "unknown":
                extra["case_id"] = "unknown"
            trace["observations"].append(extra)
            with self.subTest(mode=mode):
                self.assert_invalid(self.contract.validate_trace, trace, self.suite, self.catalog)

    def test_noncomplete_observation_cannot_smuggle_invocations(self):
        for status in ("unavailable", "error", "budget_error", "success"):
            trace = copy.deepcopy(self.trace)
            trace["observations"][0]["status"] = status
            with self.subTest(status=status):
                self.assert_invalid(self.contract.validate_trace, trace, self.suite, self.catalog)

    def test_wrong_types_are_not_coerced(self):
        for path, replacement in (
                (("skills",), {}), (("skills", 0, "name"), 42),
                (("skills", 0, "source_sha256"), False)):
            value = copy.deepcopy(self.catalog)
            target = value
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = replacement
            with self.subTest(path=path):
                self.assert_invalid(self.contract.validate_catalog, value)
        for field, replacement in (("context", []), ("prompt", 42), ("id", False)):
            suite = copy.deepcopy(self.suite)
            suite["cases"][0][field] = replacement
            with self.subTest(field=field):
                self.assert_invalid(self.contract.validate_suite, suite, self.catalog)
        for runtime in ([], {"host": "x", "version": "1", "model": None}):
            trace = copy.deepcopy(self.trace)
            trace["runtime"] = runtime
            self.assert_invalid(self.contract.validate_trace, trace, self.suite, self.catalog)

    def test_collection_and_prompt_limits_are_enforced(self):
        for count in (0, 257):
            catalog = copy.deepcopy(self.catalog)
            catalog["skills"] = [{**catalog["skills"][0], "id": "test:s" + str(i)} for i in range(count)]
            self.assert_invalid(self.contract.validate_catalog, catalog)
        for count in (0, 1001):
            suite = copy.deepcopy(self.suite)
            suite["cases"] = [{**suite["cases"][0], "id": "case-" + str(i)} for i in range(count)]
            self.assert_invalid(self.contract.validate_suite, suite, self.catalog)
        suite = copy.deepcopy(self.suite)
        suite["cases"][0]["prompt"] = "x" * 4097
        self.assert_invalid(self.contract.validate_suite, suite, self.catalog)
        trace = copy.deepcopy(self.trace)
        trace["observations"][0]["invoked"] = ["test:prepare"] * 129
        self.assert_invalid(self.contract.validate_trace, trace, self.suite, self.catalog)

    def test_prompt_may_be_empty_or_at_the_character_limit(self):
        for prompt in ("", "한" * 4096):
            suite = copy.deepcopy(self.suite)
            suite["cases"][0]["prompt"] = prompt
            with self.subTest(length=len(prompt)):
                result = self.contract.validate_suite(suite, self.catalog)
                self.assertEqual(result["cases"][0]["prompt"], prompt)

    def test_duplicate_suite_cases_are_rejected(self):
        self.suite["cases"].append(copy.deepcopy(self.suite["cases"][0]))
        self.assert_invalid(self.contract.validate_suite, self.suite, self.catalog)

    def test_context_depth_and_size_are_bounded(self):
        for context in ({"value": "x" * 16385}, {"items": list(range(1100))}):
            suite = copy.deepcopy(self.suite)
            suite["cases"][0]["context"] = context
            self.assert_invalid(self.contract.validate_suite, suite, self.catalog)
        context = {}
        for _ in range(18):
            context = {"nested": context}
        suite = copy.deepcopy(self.suite)
        suite["cases"][0]["context"] = context
        self.assert_invalid(self.contract.validate_suite, suite, self.catalog)

    def test_nonfinite_and_nonjson_python_values_cannot_be_digested(self):
        for value in (math.nan, math.inf, -math.inf, {"x": object()}, {1: "value"}, (1, 2), "\ud800"):
            with self.subTest(value=repr(value)):
                self.assert_invalid(self.contract.digest_document, value)

    def test_nonfinite_context_cannot_enter_validation(self):
        self.suite["cases"][0]["context"]["bad"] = math.nan
        self.assert_invalid(self.contract.validate_suite, self.suite, self.catalog)

    def test_status_counts_and_case_order_are_suite_bound(self):
        second = copy.deepcopy(self.suite["cases"][0])
        second["id"] = "second"
        self.suite["cases"].append(second)
        trace = trace_document(self.catalog, self.suite, observations=[
            {"case_id": "second", "status": "complete", "invoked": []},
            {"case_id": "compose", "status": "complete", "invoked": ["test:prepare", "test:implement"]}])
        report = self.contract.evaluate(self.catalog, self.suite, trace)
        self.assertEqual([row["id"] for row in report["cases"]], ["compose", "second"])
        self.assertEqual(report["summary"], {
            "cases": 2, "scored": 2, "unmeasured": 0, "passed": 1, "failed": 1})

    def test_host_observation_is_an_attestation_not_a_model_execution(self):
        self.trace["source_kind"] = "host_observation"
        report = self.contract.evaluate(self.catalog, self.suite, self.trace)
        self.assertEqual(report["source_kind"], "host_observation")
        self.assertFalse(report["model_run_performed"])


class RoutingDocumentReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("skill_routing_contract_read", SCRIPT)
        cls.contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.contract)

    def test_utf8_json_is_read_without_canonicalizing_file_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.json"
            path.write_text('{ "value": "한글" }\n', encoding="utf-8")
            self.assertEqual(self.contract.read_document(path), {"value": "한글"})

    def test_raw_digest_binds_the_single_read_including_whitespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.json"
            raw = b'{ "value": 1 }\n'
            path.write_bytes(raw)
            value, digest = self.contract.read_document_with_sha256(path)
            self.assertEqual(value, {"value": 1})
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            self.assertNotEqual(digest, canonical_digest(value))

    def test_duplicate_keys_nonfinite_invalid_utf8_and_trailing_data_are_rejected(self):
        documents = (b'{"a":1,"a":2}', b'{"outer":{"a":1,"a":2}}',
                     b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}',
                     b'{"a":"\xff"}', b'{}{}', b'')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.json"
            for raw in documents:
                path.write_bytes(raw)
                with self.subTest(raw=raw), self.assertRaises(self.contract.RoutingError):
                    self.contract.read_document(path)

    def test_oversized_and_deep_documents_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.json"
            for raw in (b' ' * (1024 * 1024 + 1), b'[' * 2000 + b']' * 2000):
                path.write_bytes(raw)
                with self.subTest(size=len(raw)), self.assertRaises(self.contract.RoutingError):
                    self.contract.read_document(path)

    def test_file_boundary_errors_preserve_bounded_diagnostic_codes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "document.json"
            path.write_bytes(b" " * (1024 * 1024 + 1))
            with self.assertRaises(self.contract.RoutingError) as caught:
                self.contract.read_document(path)
            self.assertEqual(caught.exception.code, "document_too_large")
            self.assertEqual(str(caught.exception), "document_too_large")

    @unittest.skipUnless(os.name == "nt", "Windows junction boundary")
    def test_ancestor_junction_is_rejected_without_symlink_privilege(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / "real"
            real.mkdir()
            (real / "source.json").write_text("{}", encoding="utf-8")
            junction = base / "junction"
            make_junction(junction, real)
            with self.assertRaises(self.contract.RoutingError) as caught:
                self.contract.read_document(junction / "source.json")
            self.assertEqual(caught.exception.code, "symlink_input")

    def test_traversal_directory_and_missing_input_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            path = base / "document.json"
            path.write_text("{}", encoding="utf-8")
            child = base / "child"
            child.mkdir()
            for target in (child / ".." / "document.json", base, base / "missing.json"):
                with self.subTest(target=target), self.assertRaises(self.contract.RoutingError):
                    self.contract.read_document(target)

    def test_file_and_ancestor_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / "real"
            real.mkdir()
            source = real / "source.json"
            source.write_text("{}", encoding="utf-8")
            link = base / "link.json"
            directory_link = base / "linked-directory"
            try:
                link.symlink_to(source)
                directory_link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest("Symlinks unavailable: " + str(error))
            for target in (link, directory_link / "source.json"):
                with self.subTest(target=target), self.assertRaises(self.contract.RoutingError):
                    self.contract.read_document(target)

    def test_network_device_and_ads_paths_fail_before_reading(self):
        for raw in ("//host/share/data.json", r"\\host\share\data.json", "file.json:stream",
                    r"\\?\C:\data.json", r"\\.\C:\data.json"):
            with self.subTest(raw=raw), self.assertRaises(self.contract.RoutingError):
                self.contract.read_document(raw)


if __name__ == "__main__":
    unittest.main()
