"""Same-word Korean procedure queries whose intended first result changes with direction."""
from pathlib import Path
import tempfile
import unittest

from tests.test_vault_retrieval import retrieval


BACKENDS = (
    {"backend": "lexical"},
    {"backend": "bm25"},
    {"backend": "bm25", "lexical_ngrams": "hangul2"},
    {"backend": "hybrid"},
)


class KoreanOrderRecallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.vault = Path(temp.name)
        (self.vault / "00-meta").mkdir()
        (self.vault / "00-meta/vault-config.json").write_text(
            '{"deny_zones": ["private"], "exclude_dirs": []}', encoding="utf-8")

    def write(self, filename, body):
        (self.vault / filename).write_text("# 순서\n" + body + "\n", encoding="utf-8")

    def search(self, query, options, limit=2):
        result = retrieval._legacy.recall(self.vault, query, limit=limit, **options)
        self.assertEqual(result["diagnostics"]["status"], "ok")
        return result["matches"]

    def test_both_directions_rank_first_and_cite_the_order_line(self):
        for first, second in [("검수", "출고"), ("포장", "봉인"), ("검토", "승인")]:
            self.write("a.md", first + " 후 " + second)
            self.write("b.md", second + " 후 " + first)
            for options in BACKENDS:
                for query, expected in [
                    (f"{first} 다음 {second}", "a.md"), (f"{second} 다음 {first}", "b.md"),
                    (f"{second} 전에 {first}?", "a.md"), (f"{first} 전에 {second}?", "b.md"),
                ]:
                    with self.subTest(options=options, query=query):
                        best, runner_up = self.search(query, options)
                        self.assertEqual(best["path"], expected)
                        self.assertEqual(best["line"], 2)
                        self.assertGreaterEqual(best["score"], runner_up["score"])

    def test_positive_evidence_is_rendered_even_when_other_lines_share_more_terms(self):
        self.write("a.md", "검수 다음 출고 관련 색인\n- 출고 이전에 검수.")
        for options in BACKENDS:
            with self.subTest(options=options):
                best = self.search("검수 다음 출고", options)[0]
                self.assertEqual(best["line"], 3)
                self.assertEqual(best["snippet"], "- 출고 이전에 검수.")
                if "source" in best:
                    self.assertEqual(best["source"]["line"], 3)
                    self.assertIn("출고 이전에 검수", best["source"]["quote"])

    def test_order_free_queries_keep_the_existing_path_tie_break(self):
        self.write("a.md", "출고 후 검수")
        self.write("b.md", "검수 후 출고")
        for options in BACKENDS:
            for query in ("검수 출고", "검수 그리고 출고"):
                with self.subTest(options=options, query=query):
                    self.assertEqual(self.search(query, options)[0]["path"], "a.md")

    def test_direction_does_not_override_a_stronger_channel_score_or_exclusions(self):
        self.write("a.md", "검수 다음 출고 안내\n검수 다음 출고 안내")
        self.write("b.md", "검수 후 출고")
        private = self.vault / "private"
        private.mkdir()
        (private / "denied.md").write_text("# 순서\n검수 다음 출고", encoding="utf-8")
        for options in BACKENDS:
            with self.subTest(options=options):
                matches = self.search("검수 다음 출고", options, limit=3)
                self.assertEqual([item["path"] for item in matches], ["a.md", "b.md"])

    def test_negation_multiline_and_conflicts_do_not_supply_order(self):
        order = retrieval._legacy._korean_step_order("검수 다음 출고")
        for body in ("검수 후 출고 금지", "검수 후\n출고", "검수 후 출고\n출고 후 검수",
                     "검수 후 출고?", "> 검수 후 출고", "---\nnotes: |\n  검수 후 출고\n---",
                     "```text\n검수 후 출고\n```", "    검수 후 출고"):
            with self.subTest(body=body):
                self.assertIsNone(retrieval._legacy._order_evidence(body, order))


if __name__ == "__main__":
    unittest.main()
