"""Unquoted wiki links in front matter: the scanner shared by the commit gate and the full check.

v0.45.1 reported a front-matter line only when it began with `key:` or `-` and then `[[`.
That missed nested lists YAML reads elsewhere (after a key with a hyphen, inside a flow,
after a quoted item, on the next line) and it reported links that YAML places inside a
block scalar or a quoted scalar. The scanner reads the front matter line by line as YAML
does. A line v0.45.1 reported stops being reported only where YAML itself places it inside
a block scalar's content, a quoted scalar or a plain continuation of a key's or list item's
value; everywhere else, broken YAML included, the v0.45.1 report stays.

Every non-ASCII string is built from code points, so the source shows which form it means.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from _git_env import isolate_git
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import isolate_git


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HEALTHCHECK = SCRIPTS / "vault_healthcheck.py"
TEMPLATES = ROOT / "assets" / "templates"

sys.path.insert(0, str(SCRIPTS))
try:
    import vault_healthcheck
finally:
    sys.path.remove(str(SCRIPTS))


def scan(text: str) -> list[int]:
    return vault_healthcheck.unquoted_frontmatter_link_lines(text)


# (id, front matter text, 0-based indices into text.splitlines() to report)
CASES = [
    ('C01', 'related: [[B]]', [0]),  # value starts with [[
    ('C02', 'related: ["[[A]]", [[B]]]', [0]),  # evaluation form 1
    ('C03', 'related: [ [[B]] ]', [0]),  # evaluation form 2
    ('C04', 'see-also: [[B]]', [0]),  # evaluation form 3, hyphen key
    ('C05', 'see.also: [[B]]', [0]),  # dot key
    ('C06', 'see also: [[B]]', [0]),  # space in key
    ('C07', chr(0xAD00) + chr(0xB828) + ': [[B]]', [0]),  # non-ASCII key
    ('C08', '"quoted key": [[B]]', [0]),  # quoted key
    ('C09', 'related:\n  - [[B]]', [1]),  # indented list item
    ('C10', 'related:\n- [[B]]', [1]),  # list item at column 0
    ('C11', 'related:\n  [[B]]', [1]),  # value on the next line
    ('C12', 'related: [\n  "[[A]]",\n  [[B]]\n]', [2]),  # flow sequence over several lines
    ('C13', 'meta: {a: "[[A]]", b: [[B]]}', [0]),  # flow mapping
    ('C14', 'meta: {\n  b: [[B]]\n}', [1]),  # flow mapping over several lines
    ('C15', 'related: &r [[B]]', [0]),  # anchor before the value
    ('C16', 'related: !!seq [[B]]', [0]),  # tag before the value
    ('C17', 'list:\n  - - [[B]]', [1]),  # nested block sequence
    ('C18', 'list:\n  - key: [[B]]', [1]),  # mapping inside a list item
    ('C19', 'related:\t[[B]]', [0]),  # tab after the colon
    ('C20', 'related: [[B]] # note', [0]),  # trailing comment
    ('C21', 'related: [[]]', [0]),  # empty link
    ('C22', 'related: [a, [[B]]]', [0]),  # second item
    ('C23', 'related: [[A]], [[B]]', [0]),  # two bare links (YAML error)
    ('C24', '[[A]]: x', [0]),  # flow sequence as an implicit key
    ('C25', '? [[A]]\n: x', [0]),  # explicit key
    ('C26', 'related: [[B|Alias]]', [0]),  # link with alias
    ('C27', 'note: |\n  text\nrelated: [[B]]', [2]),  # after a block scalar ends
    ('C28', 'title: "a\n  b"\nrelated: [[B]]', [2]),  # after a multi-line quoted scalar ends
    ('C29', 'list:\n  - |\n    [[B]]\n  - [[C]]', [3]),  # list item after a block scalar item
    ('C30', 'matrix: [[1, 2], [3, 4]]', [0]),  # a real nested list; flagged by design
    ('C31', 'cover: ![[img.png]]', [0]),  # Obsidian embed (design choice)
    ('C32', 'related: "[[B]]"', []),  # double quoted
    ('C33', "related: '[[B]]'", []),  # single quoted
    ('C34', 'related: ["[[A]]", "[[B]]"]', []),  # quoted items
    ('C35', 'related:\n  - "[[B]]"', []),  # quoted list item
    ('C36', 'title: Notes on [[x]] syntax', []),  # plain scalar containing [[
    ('C37', 'title: a [[x]]', []),  # plain scalar ending in a link
    ('C38', 'note: |\n  - [[B]]', []),  # literal block scalar
    ('C39', 'note: >-\n  related: [[B]]', []),  # folded block scalar
    ('C40', '# related: [[B]]', []),  # comment line
    ('C41', 'related: "[[A]]" # [[B]]', []),  # link in a trailing comment
    ('C42', 'title: "multi\n  - [[B]]"', []),  # multi-line double quoted
    ('C43', "title: 'multi\n  related: [[B]]'", []),  # multi-line single quoted
    ('C44', 'title: Notes on\n  [[x]] syntax', []),  # multi-line plain scalar
    ('C45', "title: 'it''s [[x]]'", []),  # escaped single quote
    ('C46', 'title: "say \\"[[x]]\\""', []),  # escaped double quote
    ('C47', 'related: [a, # [[B]]\n  c]', []),  # comment inside a flow sequence
    ('C48', 'related: ["a [[B]] b"]', []),  # link inside a quoted flow item
    ('C49', 'matrix: [ [1, 2], [3, 4] ]', []),  # spaced nested list, not a link
    ('C50', 'note: |2\n   [[B]]', []),  # block scalar with indentation indicator
    ('C51', 'note: |\n\n  [[B]]', []),  # block scalar starting with a blank line
    ('C52', 'related:\n  some text\n  [[B]]', []),  # plain value on the next lines
    ('C53', 'title: "x' + chr(0x2028) + '- [[B]]"', []),  # U+2028 inside a quoted value
    ('C54', 'key:[[B]]', [0]),  # kept from v0.45.1: a plain scalar where a key is expected
    ('C55', '-[[B]]', [0]),  # kept from v0.45.1: a plain scalar where an item is expected
    ('C56', 'title: a\n  - [[b]]', []),  # plain continuation that looks like a list item
    ('C57', 'tags: [x, y]', []),  # ordinary flow list
    ('C58', 'title: a\n  b: [[c]]', [1]),  # YAML error: ': ' in a plain continuation
    ('C59', 'title: "unterminated [[B]]', []),  # unterminated quote (YAML error); not flagged
    ('C60', 'title: x\r\nrelated: [[B]]', [1]),  # CRLF line ends
    ('C61', 'note: |\r\n  - [[B]]', []),  # CRLF inside a block scalar
    ('C62', 'a:\n  b:\n    c: [[x]]', [2]),  # deep mapping
    ('C63', 'a:\n  b: |\n    [[x]]\n  c: [[y]]', [3]),  # nested block scalar then a sibling key
    ('C64', 'related: [\n  "multi\n  line [[A]]",\n  [[B]]\n]', [3]),  # multi-line quoted flow item
    ('C65', "related: ['it''s [[A]]', [[B]]]", [0]),  # escaped single quote inside a flow list
    ('C66', 'meta: {"k": [[B]]}', [0]),  # quoted key inside a flow mapping
    ('C67', 'a: &x "[[A]]"\nb: *x', []),  # anchor and alias of a quoted link
    ('C68', 'related: [*x, [[B]]]', [0]),  # alias then a link in a flow list
    ('C69', '- a: 1\n  b: [[B]]', [1]),  # second key of a mapping in a list item
    ('C70', 'note: |+ # keep\n  [[x]]\nnext: "[[y]]"', []),  # block scalar header with a comment
    ('C71', 'note: |\n  x\n[[y]]: z', [2]),  # line after a block scalar at the owner column
    ('C72', 'title: C# [[x]]', []),  # '#' not preceded by a space is text
    ('C73', 'title: x #[[y]]', []),  # link in a comment
    ('C74', 'source: https://example.com/[[x]]', []),  # colon not followed by a space
    ('C75', 'title: a: [[b]]', [0]),  # YAML error: second ': ' on the line
    ('C76', 'key: value\n  # comment\n  [[x]]', [2]),  # YAML error after a comment ends a plain scalar
    ('C77', 'related: [[B]]]', [0]),  # extra closing bracket (YAML error)
    ('C78', 'tags:\n  - a\n  - [[B]]\n  - "[[C]]"', [2]),  # only the unquoted item
    ('C79', 'note: |\n    deep\n  - [[B]]', [2]),  # less indented than the first content line
    ('C80', 'note: |2\n   x\n - [[B]]', [2]),  # less indented than the indentation indicator
    ('C81', 'a:\n  note: |\n      deep\n    b: [[B]]', [3]),  # nested block scalar, dedented key
    ('C82', 'list:\n  - |\n      x\n    - [[B]]', [3]),  # block scalar in a list item, dedented item
    ('C83', 'title: a # note\n  [[B]]', [1]),  # a comment ends the plain scalar (YAML error)
    ('C84', 'title: a # note\n  - [[B]]', [1]),  # a comment ends the plain scalar; list item
    ('C85', 'title: a\n  b # c\n  - [[B]]', [2]),  # a comment in a continuation line ends the scalar
    ('C86', 'title: x\n...\n- [[B]]', [2]),  # after a document end marker (YAML error)
    ('C87', 'title: x\nstray words\n- [[B]]', [2]),  # a second root node after a mapping
    ('C88', 'title: "My note\nrelated: [[B]]\ntags: [a]', [1]),  # ends inside a quote; rule (d)
    ('C89', 'related: [!!str "[[A]]", x]', []),  # tag before a quoted flow item
    ('C90', 'related: [&a "[[A]]"]', []),  # anchor before a quoted flow item
    ('C91', 'title: "a\n...\n- [[B]]\nx: "', [2]),  # document end marker inside a quoted scalar
    ('C92', '...\n- [[B]]', [1]),  # document end marker first, then a list item
    ('C93', '|\n  text\n  - [[B]]', [2]),  # root block scalar: a string, not a mapping; rule (c)
    ('C94', '|2\n    x\n  [[B]]', []),  # root block scalar with indentation indicator
]

# Next to C94: the indentation indicator of a root block scalar counts from the root (-1).
ROOT_INDICATOR_CASES = [
    ('C94a', '>1-\n      x\n  [[B]]', []),
    ('C94b', '|1\n   x\n [[B]]', []),
]

CASE = {case_id: (text, lines) for case_id, text, lines in CASES + ROOT_INDICATOR_CASES}

# Front matter YAML cannot load, where YAML places the v0.45.1 line in no scalar: a `...` line
# cuts an unterminated quote or flow (the quote stays open to the end, as in C88), or a value
# starts with a character that cannot start a plain scalar (YAML stops there).
INVALID_YAML_CASES = [
    ('Y1', 'title: "My note\nrelated: [[B]]\n...', [1]),  # C88 with a document end marker
    ('Y2', 'title: "My note\n- [[B]]\n...', [1]),  # the same with a list item
    ('Y3', 'related: ["a\n- [[B]]\n...', [1]),  # an open quote inside a flow, then '...'
    ('Y4', "title: 'x\n- [[B]]\n...\nnext: [[C]]", [1, 3]),  # single quote; a node after '...'
    ('Y5', 'related:\n  ]\n  - [[B]]', [2]),  # ']' cannot start a plain scalar
    ('Y6', 'aliases: @old\n  - [[B]]', [1]),  # '@' is reserved
    ('Y7', 'aliases: `old`\n  - [[B]]', [1]),  # '`' is reserved
    ('Y8', 'aliases: }x\n  - [[B]]', [1]),  # '}' outside a flow
    ('Y9', 'aliases: ,x\n  - [[B]]', [1]),  # ',' outside a flow
    ('Y10', 'aliases: %x\n  - [[B]]', [1]),  # '%' is a directive indicator
    ('Y11', 'note: |x\n  - [[B]]', [1]),  # '|' without a valid block scalar header
    ('Y12', 'list:\n  - @x\n    - [[B]]', [2]),  # reserved indicator in a list item
]

# The same characters after a dash, '?' or ':' that is not followed by a space start a plain
# scalar, so YAML loads these and the continuation lines stay unreported.
PLAIN_INDICATOR_CASES = [
    ('P1', 'title: -x\n  - [[B]]', []),
    ('P2', 'title: ?x\n  - [[B]]', []),
    ('P3', 'title: :x\n  - [[B]]', []),
]
NOTE_PATH = "20-knowledge/note.md"
LINK_MESSAGE = "스테이징 차단: " + NOTE_PATH + " — 프런트매터 내 따옴표 없는 위키링크: "


class CaseTableTests(unittest.TestCase):
    def test_every_case_reports_the_expected_lines(self) -> None:
        self.assertEqual(len(CASES), 94)
        self.assertEqual(len(CASE), len(CASES) + len(ROOT_INDICATOR_CASES))
        for case_id, text, lines in CASES + ROOT_INDICATOR_CASES:
            with self.subTest(case=case_id, text=text):
                self.assertEqual(scan(text), lines)

    def test_invalid_yaml_keeps_v0451_reports_outside_scalars(self) -> None:
        # Contract: no v0.45.1 report is lost outside a YAML scalar, broken YAML included.
        for case_id, text, lines in INVALID_YAML_CASES:
            with self.subTest(case=case_id, text=text):
                v0451 = [number for number, line in enumerate(text.splitlines())
                         if vault_healthcheck.UNQUOTED_FM_LINK_RE.match(line)]
                self.assertEqual(lines, v0451)
                self.assertEqual(scan(text), lines)
        for case_id, text, lines in PLAIN_INDICATOR_CASES:
            with self.subTest(case=case_id, text=text):
                self.assertEqual(scan(text), lines)

    def test_v0451_rule_already_reported_unicode_letter_keys(self) -> None:
        # Fact pin behind the v0.46.0 erratum: the v0.45.1 rule matched keys with `[\w_]+`, and
        # Python's `\w` takes every Unicode letter and digit, so a Hangul or Arabic-Indic key was
        # already reported. Only keys holding a non-word character are new in v0.46.0.
        already = (
            chr(0xAD00) + chr(0xB828) + ": [[B]]",  # Hangul key
            chr(0x0661) + chr(0x0662) + ": [[B]]",  # Arabic-Indic digits
        )
        new_in_v0460 = (
            "see-also: [[B]]",
            "a.b: [[B]]",
            "a b: [[B]]",
            '"a": [[B]]',
            "cafe" + chr(0x0301) + ": [[B]]",  # a combining mark is not a word character
        )
        for text in already:
            with self.subTest(v0451="matched", text=text):
                self.assertIsNotNone(vault_healthcheck.UNQUOTED_FM_LINK_RE.match(text))
        for text in new_in_v0460:
            with self.subTest(v0451="not matched", text=text):
                self.assertIsNone(vault_healthcheck.UNQUOTED_FM_LINK_RE.match(text))
        for text in already + new_in_v0460:
            with self.subTest(scanner=text):
                self.assertEqual(scan(text), [0])


class GateTests(unittest.TestCase):
    """The staged commit gate, one note per case, front matter `title` first."""

    config = vault_healthcheck.validate_config({"required_keys": ["title"]})

    def link_messages(self, case_id: str) -> list[str]:
        text, _lines = CASE[case_id]
        note = '---\ntitle: "T"\n' + text + "\n---\nbody\n"
        errors = vault_healthcheck._staged_schema_errors(NOTE_PATH, note, self.config)
        return [error for error in errors if "따옴표 없는 위키링크" in error]

    def assert_reported(self, case_ids: tuple[str, ...]) -> None:
        for case_id in case_ids:
            text, lines = CASE[case_id]
            with self.subTest(case=case_id):
                self.assertEqual(len(lines), 1)
                stripped = text.splitlines()[lines[0]].strip()
                self.assertEqual(self.link_messages(case_id), [LINK_MESSAGE + stripped])

    def assert_not_reported(self, case_ids: tuple[str, ...]) -> None:
        for case_id in case_ids:
            with self.subTest(case=case_id):
                self.assertEqual(self.link_messages(case_id), [])

    def test_evaluation_forms_block_a_staged_note(self) -> None:
        self.assert_reported(("C02", "C03", "C04", "C83"))

    def test_quoted_block_and_plain_lines_are_not_reported(self) -> None:
        self.assert_not_reported(("C38", "C42", "C56"))

    def test_zero_space_forms_stay_reported(self) -> None:
        # Contract: v0.45.1 reports these too.
        self.assert_reported(("C54", "C55"))

    def test_v0451_reports_outside_yaml_scalars_stay_reported(self) -> None:
        # Contract: no v0.45.1 report is lost outside a YAML scalar, broken YAML included.
        self.assert_reported(("C79", "C80", "C81", "C82", "C84", "C85", "C86", "C87", "C88",
                              "C91", "C92", "C93"))

    def test_invalid_yaml_after_a_document_end_or_a_reserved_indicator_blocks_a_staged_note(self) -> None:
        for case_id, text, lines in INVALID_YAML_CASES:
            with self.subTest(case=case_id):
                note = '---\ntitle: "T"\n' + text + "\n---\nbody\n"
                errors = vault_healthcheck._staged_schema_errors(NOTE_PATH, note, self.config)
                expected = [LINK_MESSAGE + text.splitlines()[line].strip() for line in lines]
                self.assertEqual([error for error in errors if "따옴표 없는 위키링크" in error], expected)

    def test_properties_before_quoted_flow_items_are_not_reported(self) -> None:
        # Contract: an anchor or tag before a quoted flow item leaves the quote a quote.
        self.assert_not_reported(("C89", "C90"))


class FullModeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        isolate_git(self, ceiling=root)
        self.vault = root / "vault"
        self.vault.mkdir()

    def write(self, relpath: str, text: str) -> None:
        path = self.vault / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_new_forms_are_fatal_in_the_full_check(self) -> None:
        self.write("00-meta/vault-config.json", json.dumps({"required_keys": ["title"]}))
        for number, case_id in enumerate(("C02", "C03", "C04"), start=1):
            text, _lines = CASE[case_id]
            self.write(f"20-knowledge/form {number}.md",
                       f'---\ntitle: "Form {number}"\n{text}\n---\n\nBody.\n')

        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(HEALTHCHECK), "--vault", str(self.vault)],
            cwd=self.vault, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=120,
        )

        report_path = self.vault / "00-meta" / "health-report.md"
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr + report)
        self.assertIn("## 4. 프런트매터 내 따옴표 없는 위키링크 — 치명·YAML 붕괴 위험 (3)", report)
        self.assertIn("치명 3건", result.stdout)
        for number, case_id in enumerate(("C02", "C03", "C04"), start=1):
            with self.subTest(case=case_id):
                self.assertIn(f"20-knowledge/form {number}.md → `{CASE[case_id][0]}`", report)


class TemplateTests(unittest.TestCase):
    def test_shipped_templates_report_nothing(self) -> None:
        checked = 0
        for path in sorted(TEMPLATES.glob("*.md")):
            fm, _body = vault_healthcheck.split_frontmatter(path.read_text(encoding="utf-8"))
            if fm is None:
                continue
            checked += 1
            with self.subTest(template=path.name):
                self.assertEqual(scan(fm), [])
        self.assertGreater(checked, 0)


def _shapes(n: int) -> list[tuple[str, str, list[int]]]:
    """The long inputs of the linear-time test with the lines each must report."""
    return [
        ("open flow", "k: " + "[" * n, [0]),
        ("list items", "k:\n" + "  - [[x]]\n" * (n // 10), list(range(1, n // 10 + 1))),
        ("block scalar", "k: |\n" + "  - [[x]]\n" * (n // 10), []),
        ("plain continuation", "t: a\n" + "  b\n" * (n // 4) + "  c: [[d]]", [n // 4 + 1]),
        ("nested dashes", "- " * (n // 2) + "[[x]]", [0]),
        ("nested keys", "a: " * (n // 3) + "[[x]]", [0]),
        ("root block scalar", "|\n" + "  - [[x]]\n" * (n // 10), list(range(1, n // 10 + 1))),
        ("open quote", 't: "a\n' + "- [[x]]\n" * (n // 10), list(range(1, n // 10 + 1))),
    ]


class TimingTests(unittest.TestCase):
    """Linear work without recursion, counted in executed lines rather than measured in seconds."""

    def executed_lines(self, text: str) -> int:
        scanner_file = vault_healthcheck.unquoted_frontmatter_link_lines.__code__.co_filename
        count = 0

        def local(frame, event, arg):
            nonlocal count
            if event == "line":
                count += 1
            return local

        def calls(frame, event, arg):
            return local if frame.f_code.co_filename == scanner_file else None

        previous = sys.gettrace()
        sys.settrace(calls)
        try:
            scan(text)
        finally:
            sys.settrace(previous)
        return count

    def test_linear_time_without_recursion(self) -> None:
        for n in (8000, 16000, 32000, 64000):
            for name, text, lines in _shapes(n):
                with self.subTest(n=n, shape=name):
                    self.assertEqual(scan(text), lines)
        self.assertEqual(scan("a: " * 20000 + "[[x]]"), [0])

        if sys.gettrace() is not None:
            self.skipTest("another tracer is active; the line count needs sys.settrace")
        for (name, small, _), (_, large, _) in zip(_shapes(2000), _shapes(4000)):
            with self.subTest(shape=name):
                self.assertLessEqual(self.executed_lines(large), 2.2 * self.executed_lines(small) + 50)


if __name__ == "__main__":
    unittest.main()
