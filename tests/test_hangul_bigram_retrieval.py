"""Opt-in Hangul syllable-bigram BM25 channel (``lexical_ngrams="hangul2"``).

The default (``None``) path is pinned to v0.22.1 outputs on the existing
fixtures. Fixture copies are LF-normalized so source hashes and byte counts do
not depend on the checkout platform's line endings.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"


def _load(name: str, path: Path, *, script_dir: bool):
    if script_dir:
        sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if script_dir:
            sys.path.remove(str(SCRIPTS))


retrieval = _load("hangul2_retrieval_test", SCRIPTS / "vault_retrieval.py", script_dir=True)
evaluator = _load("hangul2_recall_evaluator", ROOT / "scripts" / "evaluate_recall.py", script_dir=False)

# --- Default-path golden outputs (generated with the unmodified v0.22.1 retriever) ---

MAPPING = "00-meta/query-map.txt"
PER_QUERY = ("as_of", "expand_links", "backend", "external_candidates", "excluded_sources")
_HARD_SCENARIOS = (("particles", 6), ("short", 6), ("mixed", 6), ("spacing", 6), ("update", 4), ("abstention", 4))
HARD_IDS = tuple(f"{language}-{scenario}-{number:02d}" for scenario, count in _HARD_SCENARIOS
                 for number in range(1, count + 1) for language in ("ko", "en"))
HARD_SUBSET = tuple(f"{language}-{scenario}-01" for scenario, _ in _HARD_SCENARIOS for language in ("ko", "en"))
ADVANCED_IDS = ("en-current", "ko-current", "en-historical", "ko-historical", "en-graph", "ko-graph",
                "en-ambiguous", "ko-ambiguous", "en-bm25", "ko-bm25", "en-sidecar", "ko-sidecar",
                "en-unknown", "ko-unknown", "en-retired", "ko-retired")
RECALL_IDS = ("en-deploy", "en-canary", "en-index", "en-cache", "en-oauth", "en-backup", "en-worktree",
              "en-handoff", "ko-incident", "ko-graph", "ko-budget", "ko-decision", "ko-index", "ko-cache",
              "ko-backup", "ko-worktree")
# name: (fixture, reader, query ids, limit, options added to each query's own options).
# The first, second, fifth and last rows mirror the retrieval arms of tests.yml.
DEFAULT_CONFIGS = {
    "hard_ablation_top5": ("recall_hard", "retrieve", HARD_IDS, 5, {}),
    "hard_improved_top5": ("recall_hard", "retrieve", HARD_IDS, 5, {"expand_query": True, "query_mapping": MAPPING}),
    "hard_bm25_expanded_top5": ("recall_hard", "retrieve", HARD_SUBSET, 5,
                                {"backend": "bm25", "expand_query": True, "query_mapping": MAPPING}),
    "hard_hybrid_expanded_top5": ("recall_hard", "retrieve", HARD_SUBSET, 5,
                                  {"backend": "hybrid", "expand_query": True, "query_mapping": MAPPING}),
    "advanced_per_query_top3": ("recall_advanced", "retrieve", ADVANCED_IDS, 3, {}),
    "advanced_bm25_top3": ("recall_advanced", "retrieve", ADVANCED_IDS, 3, {"backend": "bm25"}),
    "recall_legacy_top3": ("recall", "recall", RECALL_IDS, 3, {}),
}
# If a fixture vault digest changes, the fixture itself changed: regenerate the
# output digests with the v0.22.1 retriever (git show v0.22.1:<path>) rather
# than with the code under test.
GOLDEN_VAULT_DIGESTS = {
    "recall": "b2f84986e3ed6adbb62a245b2be1731b81471db88cf9dfaaa2c1f42b1cb50aaf",
    "recall_advanced": "a0faa9f40555edf9bebac00e6fed14b8c73891b746622d5e5ec155c79a988d98",
    "recall_hard": "7a67ddb047bef73e3b9c7b92092b62a29fb1ee90a18d8b1c47bdfb6bc899b32c",
}
GOLDEN_OUTPUT_DIGESTS = {
    "hard_ablation_top5": "2603571c3753e484d5ea438339c2d2fb5181389a7684edfa6bc9c67e5a02981c",
    "hard_improved_top5": "5f87c782cce6586dbb3337b7e0c761cb59a0b089717d22b907ad75bf2f86284a",
    "hard_bm25_expanded_top5": "fb632d059af8b12e502761d296a817259198ac396788d673c8f4e9308e74d555",
    "hard_hybrid_expanded_top5": "ea2eb93e1e07ef3817d16d9c94717d86c7a8b7c2d1bb23d8e7e156ed11eb9a88",
    "advanced_per_query_top3": "ecbaefbbfae9ede5323d342faeacb4cf59ffed5b3ad6d91cf6d42bb8efbb3ca8",
    "advanced_bm25_top3": "5977435601efe9a60330083a883c2a107b04438177abeafa8292fcddba62d623",
    "recall_legacy_top3": "aa2970efb385eadb9a5aea091bdbd91747589b39ca3c418c0028e3235d9dd26a",
}
_IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _lf_copy(source: Path, target: Path) -> None:
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.name not in _IGNORED_NAMES:
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def _vault_digest(vault: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((p for p in vault.rglob("*") if p.is_file()), key=lambda p: p.relative_to(vault).as_posix()):
        digest.update(path.relative_to(vault).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


# v0.26.0 (listing fingerprint, limit A11) changed only the query-expansion
# inventory_verification diagnostic: new listing_* fields and the method and
# assurance wording. Map it back to the earlier shape so the pinned digests
# keep proving that ranking, context and every other field are unchanged.
LEGACY_INVENTORY_WORDING = {
    "method": "bounded marks from the original scan; no second scan or payload reads",
    "assurance": "metadata_generation_only; preserved marks can evade checks; "
                 "initial source hashes do not certify current inventory bytes",
}


def legacy_inventory_verification(value):
    if not isinstance(value, dict) or "listing_diagnostics" not in value:
        return value
    if value["listing_diagnostics"] or value.get("listing_diagnostics_omitted"):
        raise AssertionError("fixture runs must not observe listing changes")
    legacy = {key: item for key, item in value.items()
              if key not in {"listing_entries", "listing_diagnostics", "listing_diagnostics_omitted"}}
    return {**legacy, **LEGACY_INVENTORY_WORDING}


def _canonical(value):
    # Ten significant digits absorb last-ulp libm differences between CI platforms.
    if isinstance(value, float):
        return format(value, ".10g")
    if isinstance(value, dict):
        return {key: _canonical(legacy_inventory_verification(item) if key == "inventory_verification" else item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def default_output_digests(module, root: Path, extra: dict) -> dict:
    """Digest default-path outputs; ``extra`` holds keyword arguments added to every call."""
    vaults, outputs = {}, {}
    for fixture in sorted({config[0] for config in DEFAULT_CONFIGS.values()}):
        _lf_copy(FIXTURES / fixture / "vault", root / fixture)
        vaults[fixture] = _vault_digest(root / fixture)
    for name, (fixture, reader, ids, limit, options) in DEFAULT_CONFIGS.items():
        items = {item["id"]: item for item in
                 json.loads((FIXTURES / fixture / "queries.json").read_text(encoding="utf-8"))["queries"]}
        results = []
        for query_id in ids:
            item = items[query_id]
            if reader == "recall":
                result = module._legacy.recall(root / fixture, item["query"], limit=limit, max_tokens=1500, **extra)
            else:
                call = {key: item[key] for key in PER_QUERY if key in item}
                call.update(options)
                result = module.retrieve(root / fixture, item["query"], limit=limit, max_tokens=1500, **call, **extra)
            results.append([query_id, result])
        text = json.dumps(_canonical(results), ensure_ascii=False, separators=(",", ":"))
        outputs[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"vaults": vaults, "outputs": outputs}


class DefaultPathEquivalenceTests(unittest.TestCase):
    def test_default_none_outputs_match_v0_22_1_on_existing_fixtures(self):
        with tempfile.TemporaryDirectory() as temporary:
            observed = default_output_digests(retrieval, Path(temporary), {"lexical_ngrams": None})
        self.assertEqual(observed["vaults"], GOLDEN_VAULT_DIGESTS,
                         "fixture vault changed; regenerate the v0.22.1 golden digests")
        for name, expected in GOLDEN_OUTPUT_DIGESTS.items():
            with self.subTest(config=name):
                self.assertEqual(observed["outputs"][name], expected)
        self.assertEqual(set(observed["outputs"]), set(GOLDEN_OUTPUT_DIGESTS))


def tokens(text: str) -> list[str]:
    return retrieval._hangul2_tokens(retrieval._legacy._normalize(text))


class HangulBigramTokenizerTests(unittest.TestCase):
    def test_particle_attached_word_shares_bigrams_with_bare_word(self):
        self.assertEqual(tokens("핸드오프를"), ["핸드", "드오", "오프", "프를"])
        self.assertEqual(tokens("핸드오프"), ["핸드", "드오", "오프"])
        self.assertEqual(set(tokens("핸드오프를")) & set(tokens("핸드오프")), {"핸드", "드오", "오프"})

    def test_two_syllable_word_is_one_bigram(self):
        self.assertEqual(tokens("대장"), ["대장"])
        self.assertIn("대장", tokens("핵심사실대장"))

    def test_mixed_script_word_splits_at_the_hangul_boundary(self):
        self.assertEqual(tokens("handoff를"), ["handoff", "를"])
        self.assertEqual(tokens("Handoff를 정리"), ["handoff", "를", "정리"])
        self.assertIn("handoff", tokens("handoff"))

    def test_single_syllable_runs_and_other_word_tokens_are_kept(self):
        self.assertEqual(tokens("를"), ["를"])
        self.assertEqual(tokens("2단계 a_b 격리절차: 점검"), ["2", "단계", "a", "b", "격리", "리절", "절차", "점검"])
        self.assertEqual(tokens("LOTO's"), retrieval._legacy._WORD_RE.findall("loto's"))

    def test_only_precomposed_syllables_are_split(self):
        first, last = chr(0xAC00), chr(0xD7A3)
        self.assertEqual(tokens(first + last), [first + last])
        # NFKC turns compatibility jamo into conjoining jamo; neither is a syllable run.
        jamo = retrieval._legacy._normalize("ㅋㅋ")
        self.assertEqual(tokens("ㅋㅋ"), [jamo])
        self.assertTrue(all(not first <= char <= last for char in jamo))

    def test_query_terms_are_unique_original_first_and_bounded(self):
        terms, omitted = retrieval._hangul2_query_terms(("핸드오프를", "핸드오프", "handoff를"))
        self.assertEqual((terms, omitted), (["핸드", "드오", "오프", "프를", "handoff", "를"], 0))
        long_run = "".join(chr(0xAC00 + index) for index in range(300))
        terms, omitted = retrieval._hangul2_query_terms((long_run,))
        self.assertEqual(len(terms), retrieval.MAX_NGRAM_QUERY_TERMS)
        self.assertEqual(omitted, 299 - retrieval.MAX_NGRAM_QUERY_TERMS)
        self.assertEqual(terms[0], long_run[:2])


class HangulBigramRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        self.write("00-meta/vault-config.json", json.dumps({
            "deny_zones": ["20-knowledge/_archive", "90-assets", ".obsidian"],
            "exclude_dirs": ["scratch"],
        }))

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_retrieval(self, query, **kwargs):
        return retrieval.retrieve(self.vault, query, **kwargs)

    def paths(self, result):
        return [match["path"] for match in result["matches"]]

    def test_particle_attached_query_needs_the_opt_in_channel(self):
        self.write("20-knowledge/handoff.md", "세션 종료 때 핸드오프 문서를 남긴다.\n")
        self.write("20-knowledge/other.md", "관련 없는 기록이다.\n")
        for backend in ("bm25", "hybrid"):
            with self.subTest(backend=backend):
                self.assertEqual(self.paths(self.run_retrieval("핸드오프를", backend=backend)), [])
                result = self.run_retrieval("핸드오프를", backend=backend, lexical_ngrams="hangul2")
                self.assertEqual(self.paths(result), ["20-knowledge/handoff.md"])
                match = result["matches"][0]
                self.assertEqual(match["scores"]["bm25"], 0.0)
                self.assertGreater(match["scores"]["bm25_hangul2"], 0)
                self.assertAlmostEqual(match["scores"]["rrf_bm25_hangul2"], 1 / 61)
                self.assertAlmostEqual(match["score"], 1 / 61)
                self.assertEqual(match["ranks"]["bm25_hangul2"], 1)
                self.assertEqual(match["source"]["quote"], "세션 종료 때 핸드오프 문서를 남긴다.")
                self.assertIn("핸드오프", result["context"])
                self.assertEqual((match["review_status"], match["source_authority"]), ("needs_review", "unverified"))

    def test_two_syllable_and_mixed_script_queries_match_inside_longer_tokens(self):
        self.write("20-knowledge/ledger.md", "핵심사실대장 갱신 기록\n")
        self.write("20-knowledge/handoff-en.md", "The handoff note lists open tasks.\n")
        for query, expected in (("대장", "20-knowledge/ledger.md"), ("handoff를", "20-knowledge/handoff-en.md")):
            with self.subTest(query=query):
                self.assertEqual(self.paths(self.run_retrieval(query, backend="bm25")), [])
                self.assertEqual(self.paths(self.run_retrieval(query, backend="bm25", lexical_ngrams="hangul2")), [expected])

    def test_single_syllable_query_stays_a_unigram(self):
        self.write("20-knowledge/mixed.md", "handoff를 정리한다\n")
        self.write("20-knowledge/plain.md", "정리 목록\n")
        self.assertEqual(self.paths(self.run_retrieval("를", backend="bm25")), [])
        self.assertEqual(self.paths(self.run_retrieval("를", backend="bm25", lexical_ngrams="hangul2")), ["20-knowledge/mixed.md"])

    def test_rrf_sums_word_and_bigram_ranks(self):
        self.write("20-knowledge/a.md", "handoff 핸드오프 기록\n")
        self.write("20-knowledge/b.md", "unrelated 내용\n")
        plain = self.run_retrieval("handoff 핸드오프", backend="bm25")["matches"][0]
        self.assertEqual(plain["score"], plain["scores"]["bm25"])
        bm25 = self.run_retrieval("handoff 핸드오프", backend="bm25", lexical_ngrams="hangul2")["matches"][0]
        self.assertAlmostEqual(bm25["scores"]["rrf_bm25"], 1 / 61)
        self.assertAlmostEqual(bm25["scores"]["rrf_bm25_hangul2"], 1 / 61)
        self.assertEqual(bm25["scores"]["rrf_lexical"], 0.0)
        self.assertAlmostEqual(bm25["score"], 2 / 61)
        hybrid = self.run_retrieval("handoff 핸드오프", backend="hybrid", lexical_ngrams="hangul2")["matches"][0]
        self.assertAlmostEqual(hybrid["score"], 3 / 61)
        self.assertEqual(hybrid["score"], sum(hybrid["scores"][key] for key in ("rrf_lexical", "rrf_bm25", "rrf_external", "rrf_bm25_hangul2")))

    def test_bigram_only_candidates_cite_a_line_with_matching_bigrams(self):
        self.write("20-knowledge/a.md", "관련 없는 머리말\n핸드오프 절차를 기록한다.\n")
        query_terms = set(retrieval._hangul2_query_terms(retrieval._legacy._terms("핸드오프를"))[0])
        for backend in ("bm25", "hybrid"):
            with self.subTest(backend=backend):
                match = self.run_retrieval("핸드오프를", backend=backend, lexical_ngrams="hangul2")["matches"][0]
                self.assertEqual(match["line"], 2)
                self.assertEqual(match["source"]["quote"], "핸드오프 절차를 기록한다.")
                self.assertTrue(set(tokens(match["source"]["quote"])) & query_terms)
        # A lexical hit in hybrid keeps the existing lexical evidence line.
        plain = self.run_retrieval("핸드오프", backend="hybrid")["matches"][0]
        both = self.run_retrieval("핸드오프", backend="hybrid", lexical_ngrams="hangul2")["matches"][0]
        self.assertEqual((both["line"], both["snippet"], both["source"]), (plain["line"], plain["snippet"], plain["source"]))

    def test_lexical_backend_ignores_channel_with_diagnostic(self):
        self.write("20-knowledge/handoff.md", "세션 종료 때 핸드오프 문서를 남긴다.\n")
        plain = self.run_retrieval("핸드오프", as_of="2026-01-01")
        ignored = self.run_retrieval("핸드오프", as_of="2026-01-01", lexical_ngrams="hangul2")
        self.assertEqual(self.paths(ignored), ["20-knowledge/handoff.md"])
        self.assertEqual((ignored["matches"], ignored["context"]), (plain["matches"], plain["context"]))
        channel = ignored["diagnostics"]["lexical_ngram_channel"]
        self.assertEqual((channel["status"], channel["query_terms_used"], channel["candidates"]), ("ignored_lexical_backend", 0, 0))
        self.assertTrue(ignored["diagnostics"]["ranking"].endswith("lexical_ngrams=hangul2 ignored: lexical backend has no BM25 rank"))
        rest = {key: value for key, value in ignored["diagnostics"].items() if key not in ("lexical_ngrams", "lexical_ngram_channel", "ranking")}
        self.assertEqual(rest, {key: value for key, value in plain["diagnostics"].items() if key != "ranking"})
        self.assertNotIn("hangul2", json.dumps(ignored["matches"]))

    def test_default_none_adds_no_fields_and_equals_the_omitted_argument(self):
        self.write("20-knowledge/a.md", "# Rollback\nrollback 복구 절차\n")
        self.write("20-knowledge/b.md", "rollback 대안\n")
        for backend in ("bm25", "hybrid"):
            with self.subTest(backend=backend):
                omitted = self.run_retrieval("rollback 복구", backend=backend)
                explicit = self.run_retrieval("rollback 복구", backend=backend, lexical_ngrams=None)
                self.assertEqual(json.dumps(omitted, ensure_ascii=False), json.dumps(explicit, ensure_ascii=False))
                self.assertNotIn("lexical_ngrams", omitted["diagnostics"])
                self.assertNotIn("lexical_ngram_channel", omitted["diagnostics"])
                self.assertEqual(omitted["diagnostics"]["ranking"], "lexical/BM25; hybrid uses RRF k=60; graph decays 0.5 per hop")
                self.assertTrue(omitted["matches"])
                for match in omitted["matches"]:
                    self.assertEqual(list(match["scores"]), ["lexical", "bm25", "rrf_lexical", "rrf_bm25", "rrf_external", "graph"])
                    self.assertEqual(list(match["ranks"]), ["lexical", "bm25", "external"])
        legacy = retrieval._legacy._legacy_recall(self.vault, "rollback")
        self.assertEqual(retrieval._legacy.recall(self.vault, "rollback", lexical_ngrams=None), legacy)
        self.assertEqual(self.run_retrieval("rollback", lexical_ngrams=None), legacy)

    def test_invalid_lexical_ngram_modes_fail_before_note_reads(self):
        self.write("20-knowledge/a.md", "핸드오프 PRIVATE\n")

        class Mode(str):
            pass

        for value in ("hangul3", "", "HANGUL2", " hangul2", "hangul2 ", "bigram", 2, 0, True, False,
                      ["hangul2"], ("hangul2",), b"hangul2", {"mode": "hangul2"}, Mode("hangul2")):
            for backend in ("lexical", "bm25", "hybrid"):
                with self.subTest(value=value, backend=backend):
                    for result in (self.run_retrieval("핸드오프", backend=backend, lexical_ngrams=value),
                                   retrieval._legacy.recall(self.vault, "핸드오프", backend=backend, lexical_ngrams=value)):
                        self.assertEqual(result["diagnostics"]["status"], "invalid_options")
                        self.assertEqual(result["diagnostics"]["files_read"], 0)
                        self.assertNotIn("lexical_ngrams", result["diagnostics"])
                        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_active_channel_diagnostics_and_ranking_string(self):
        self.write("20-knowledge/handoff.md", "세션 종료 때 핸드오프 문서를 남긴다.\n")
        self.write("20-knowledge/other.md", "관련 없는 기록이다.\n")
        result = self.run_retrieval("핸드오프를 정리", backend="bm25", lexical_ngrams="hangul2")
        diagnostics = result["diagnostics"]
        self.assertEqual((diagnostics["status"], diagnostics["search_complete"]), ("ok", True))
        self.assertEqual(diagnostics["lexical_ngrams"], "hangul2")
        self.assertEqual(diagnostics["lexical_ngram_channel"], {
            "status": "active", "rank_key": "rrf_bm25_hangul2", "tokenizer": "hangul_syllable_bigram",
            "query_terms_used": 5, "query_terms_omitted": 0, "candidates": 1,
            "index": "in_memory_per_call", "limits": {"query_terms": retrieval.MAX_NGRAM_QUERY_TERMS}})
        self.assertEqual(diagnostics["ranking"], "lexical/BM25; hybrid uses RRF k=60; graph decays 0.5 per hop; "
                         "lexical_ngrams=hangul2 adds a Hangul syllable-bigram BM25 rank to RRF k=60")
        self.assertEqual(diagnostics["query_terms_used"], 2)
        long_run = "".join(chr(0xAC00 + index) for index in range(300))
        bounded = self.run_retrieval(long_run, backend="bm25", lexical_ngrams="hangul2")["diagnostics"]
        self.assertEqual(bounded["status"], "ok")
        self.assertEqual(bounded["lexical_ngram_channel"]["query_terms_used"], retrieval.MAX_NGRAM_QUERY_TERMS)
        self.assertEqual(bounded["lexical_ngram_channel"]["query_terms_omitted"], 299 - retrieval.MAX_NGRAM_QUERY_TERMS)

    def test_channel_respects_policy_temporal_bounds_and_exclusions(self):
        self.write("20-knowledge/_archive/old.md", "핸드오프 ARCHIVE_SECRET\n")
        self.write("scratch/draft.md", "핸드오프 SCRATCH_SECRET\n")
        self.write("20-knowledge/expired.md", "---\nvalid_until: 2025-01-01\n---\n핸드오프 EXPIRED_MARKER\n")
        self.write("20-knowledge/hidden.md", "핸드오프 HIDDEN_MARKER\n")
        self.write("20-knowledge/current.md", "핸드오프 현재 절차\n")
        for backend in ("bm25", "hybrid"):
            with self.subTest(backend=backend):
                result = self.run_retrieval("핸드오프를", backend=backend, lexical_ngrams="hangul2", as_of="2026-01-01",
                                            excluded_sources=["20-knowledge/hidden.md"])
                self.assertEqual(self.paths(result), ["20-knowledge/current.md"])
                text = json.dumps(result, ensure_ascii=False)
                for marker in ("ARCHIVE_SECRET", "SCRATCH_SECRET", "EXPIRED_MARKER", "HIDDEN_MARKER"):
                    self.assertNotIn(marker, text)

    def test_policy_drift_still_discards_bigram_results(self):
        self.write("20-knowledge/a.md", "핸드오프 DRIFT_SECRET\n")
        original = retrieval._legacy._read_markdown
        changed = False

        def read_then_restrict(*args, **kwargs):
            nonlocal changed
            text = original(*args, **kwargs)
            if not changed:
                changed = True
                self.write("00-meta/vault-config.json", json.dumps({"deny_zones": ["20-knowledge"]}))
            return text

        with mock.patch.object(retrieval._legacy, "_read_markdown", side_effect=read_then_restrict):
            result = self.run_retrieval("핸드오프를", backend="hybrid", lexical_ngrams="hangul2")
        self.assertEqual(result["diagnostics"]["status"], "policy_changed")
        self.assertNotIn("DRIFT_SECRET", json.dumps(result))

    def test_no_persistent_index_or_cache_is_written(self):
        self.write("20-knowledge/a.md", "세션 종료 때 핸드오프 문서를 남긴다.\n")
        before = sorted((path.relative_to(self.vault).as_posix(), path.is_file() and path.read_bytes())
                        for path in self.vault.rglob("*"))
        for backend in ("bm25", "hybrid"):
            self.assertTrue(self.run_retrieval("핸드오프를", backend=backend, lexical_ngrams="hangul2")["matches"])
        after = sorted((path.relative_to(self.vault).as_posix(), path.is_file() and path.read_bytes())
                       for path in self.vault.rglob("*"))
        self.assertEqual(after, before)


class RecallCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "vault"
        (self.vault / "00-meta").mkdir(parents=True)
        (self.vault / "00-meta/vault-config.json").write_text("{}", encoding="utf-8")
        (self.vault / "20-knowledge").mkdir()
        (self.vault / "20-knowledge/handoff.md").write_text("세션 종료 때 핸드오프 문서를 남긴다.\n", encoding="utf-8")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "vault_recall.py"), "--vault", str(self.vault), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_cli_passes_the_mode_and_rejects_unknown_modes(self):
        run = self.run_cli("--query", "핸드오프를", "--backend", "bm25", "--lexical-ngrams", "hangul2", "--format", "json")
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual([match["path"] for match in result["matches"]], ["20-knowledge/handoff.md"])
        self.assertEqual(result["diagnostics"]["lexical_ngrams"], "hangul2")
        rejected = self.run_cli("--query", "핸드오프를", "--backend", "bm25", "--lexical-ngrams", "hangul3", "--format", "json")
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(rejected.stdout, "")

    def test_cli_default_output_is_the_legacy_output(self):
        run = self.run_cli("--query", "핸드오프", "--format", "json")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), retrieval._legacy._legacy_recall(self.vault, "핸드오프"))
        self.assertIsNone(retrieval._legacy._parser().parse_args(["--vault", "v", "--query", "q"]).lexical_ngrams)

    def test_cli_text_mode_notes_an_ignored_channel(self):
        ignored = self.run_cli("--query", "핸드오프", "--lexical-ngrams", "hangul2")
        self.assertEqual(ignored.returncode, 0, ignored.stderr)
        self.assertIn("recall note: --lexical-ngrams is ignored by the lexical backend", ignored.stderr)
        self.assertIn("핸드오프", ignored.stdout)
        for args in (("--query", "핸드오프"), ("--query", "핸드오프를", "--backend", "bm25", "--lexical-ngrams", "hangul2")):
            with self.subTest(args=args):
                run = self.run_cli(*args)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertNotIn("recall note", run.stderr)


class EvaluationPlumbingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = Path(self.tmp.name)
        vault = self.fixture / "vault"
        (vault / "00-meta").mkdir(parents=True)
        (vault / "00-meta/vault-config.json").write_text("{}", encoding="utf-8")
        (vault / "note.md").write_text("# 점검\n세션 종료 때 핸드오프 문서를 남긴다.\n", encoding="utf-8")
        queries = [{"id": str(i), "language": "ko", "scenario": "particles", "query": "핸드오프를",
                    "expected_paths": ["note.md"], "relevance": {"note.md": 3}} for i in range(12)]
        (self.fixture / "queries.json").write_text(json.dumps({"queries": queries}), encoding="utf-8")

    def main(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = evaluator.main(["--fixture", str(self.fixture), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_flag_requires_advanced_or_compare(self):
        code, stdout, stderr = self.main("--lexical-ngrams", "hangul2")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("--lexical-ngrams requires --advanced or --compare", stderr)
        code, stdout, _ = self.main("--advanced", "--backend", "bm25")
        self.assertEqual((code, json.loads(stdout)["recall_at_3"]), (1, 0.0))
        code, stdout, _ = self.main("--advanced", "--backend", "bm25", "--lexical-ngrams", "hangul2")
        report = json.loads(stdout)
        self.assertEqual((code, report["recall_at_3"], report["evaluation_errors"]), (0, 1.0, []))
        self.assertTrue(all(row["retrieval_options"]["lexical_ngrams"] == "hangul2" for row in report["per_query"]))
        self.assertTrue(all(row["diagnostics"]["lexical_ngram_channel"]["status"] == "active" for row in report["per_query"]))

    def test_all_lexical_backend_prints_a_note_without_changing_the_exit_code(self):
        plain_code, plain_stdout, plain_stderr = self.main("--advanced")
        code, stdout, stderr = self.main("--advanced", "--lexical-ngrams", "hangul2")
        report = json.loads(stdout)
        self.assertTrue(all(row["diagnostics"]["lexical_ngram_channel"]["status"] == "ignored_lexical_backend"
                            for row in report["per_query"]))
        self.assertEqual(stderr.strip(), evaluator.LEXICAL_NGRAMS_IGNORED_NOTE)
        self.assertEqual(plain_stderr, "")
        self.assertEqual((code, report["recall_at_3"]), (plain_code, json.loads(plain_stdout)["recall_at_3"]))
        _, _, compared = self.main("--compare", "--lexical-ngrams", "hangul2")
        self.assertEqual(compared.strip(), evaluator.LEXICAL_NGRAMS_IGNORED_NOTE)
        for args in (("--advanced", "--backend", "bm25", "--lexical-ngrams", "hangul2"),
                     ("--compare", "--backend", "hybrid", "--lexical-ngrams", "hangul2")):
            with self.subTest(args=args):
                _, _, stderr = self.main(*args)
                self.assertNotIn("recall evaluation note", stderr)

    def test_flag_reaches_only_advanced_retrieval_calls(self):
        module = evaluator._load_recall_module()
        advanced_calls, legacy_calls = [], []
        original_retrieve, original_recall = module._advanced_retrieval.retrieve, module.recall

        def advanced(vault, query, **options):
            advanced_calls.append(options)
            return original_retrieve(vault, query, **options)

        def legacy(vault, query, **options):
            legacy_calls.append(options)
            return original_recall(vault, query, **options)

        with mock.patch.object(evaluator, "_load_recall_module", return_value=module), \
             mock.patch.object(module._advanced_retrieval, "retrieve", side_effect=advanced), \
             mock.patch.object(module, "recall", side_effect=legacy):
            evaluator.evaluate(self.fixture, advanced=True, backend="bm25")
            self.assertTrue(advanced_calls)
            self.assertFalse(any("lexical_ngrams" in call for call in advanced_calls))
            advanced_calls.clear()
            evaluator.compare(self.fixture, top_k=3, backend="bm25", lexical_ngrams="hangul2")
        self.assertEqual(len(advanced_calls), 12)
        self.assertTrue(all(call["lexical_ngrams"] == "hangul2" for call in advanced_calls))
        self.assertEqual(len(legacy_calls), 12)
        self.assertTrue(all(set(call) == {"limit", "max_tokens"} for call in legacy_calls))

    def test_invalid_mode_is_rejected(self):
        for value in ("hangul3", "", 2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluator.evaluate(self.fixture, advanced=True, lexical_ngrams=value)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            evaluator.main(["--fixture", str(self.fixture), "--advanced", "--lexical-ngrams", "hangul3"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
