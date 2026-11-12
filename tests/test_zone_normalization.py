"""Deny zones ignore Unicode normalization as well as case.

A folder name can be stored composed (NFC) or decomposed (NFD); macOS volumes
and copies from them often leave the decomposed form. A zone written in one
form must cover the folder written in the other, in every deny-zone decider:
the shared fold, configuration validation, the health check's full mode,
recall and the lint warning extensions. The commit gate keeps its byte-exact
Git pathspecs, so a staged note in another spelling of a zone stays
schema-checked (a contract, as for case variants). Two segments are the same
name exactly when they are Unicode canonical caseless matches (NFD, casefold,
NFD), which the seeded property test checks against this Python's own data.

Every non-ASCII string is built from code points, so the source shows which
normalization form it means. Tests that need a folder on disk read the name
back and skip on a file system that stores another form than the one written.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

try:
    from _git_env import isolate_git
except ImportError:  # run as tests.<module> from the repository root
    from tests._git_env import isolate_git


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
HEALTHCHECK = SCRIPTS / "vault_healthcheck.py"

sys.path.insert(0, str(SCRIPTS))
try:
    import vault_healthcheck
    import vault_lint_extensions
    import vault_paths
    import vault_recall
finally:
    sys.path.remove(str(SCRIPTS))


def NFC(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def NFD(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def _text(*code_points: int) -> str:
    return "".join(map(chr, code_points))


BOGWAN = chr(0xBCF4) + chr(0xAD00)                   # Hangul word, 2 syllables
BIGONGGAE = chr(0xBE44) + chr(0xACF5) + chr(0xAC1C)  # Hangul word, 3 syllables
JARYO = chr(0xC790) + chr(0xB8CC)                    # Hangul word, 2 syllables
CAFE_NFC = "caf" + chr(0xE9)
CAFE_NFD = "cafe" + chr(0x301)
GREEK = chr(0x390)                                   # iota with dialytika and tonos
GREEK_UPPER_NFD = chr(0x399) + chr(0x308) + chr(0x301)
OMEGA_YPO_ZONE = chr(0x1FAA) + chr(0x352)            # omega with ypogegrammeni, then a mark
OMEGA_YPO_ZONE_PATH = chr(0x3A9) + chr(0x313) + chr(0x300) + chr(0x352) + chr(0x399)

# Report and gate wording of the health check, built from code points.
CHECKED_NOTES = _text(0xAC80, 0xC0AC, 0x20, 0xB178, 0xD2B8, 0x20, 0xC218)
MISSING_FRONT_MATTER = _text(0xD504, 0xB7F0, 0xD2B8, 0xB9E4, 0xD130, 0x20, 0xB204, 0xB77D)
MARKER = "SECRET_MARKER_F1"


def _make_folder_or_skip(case: unittest.TestCase, parent: Path, name: str) -> Path:
    """Create parent/name and skip when the file system lists it under another spelling."""
    folder = parent / name
    folder.mkdir(parents=True, exist_ok=True)
    if name not in os.listdir(parent):
        case.skipTest("file system normalizes names")
    return folder


class FoldTests(unittest.TestCase):
    """Pure string tests: no file system involved."""

    EQUAL = (
        (NFC(BOGWAN), NFD(BOGWAN)),
        (CAFE_NFC, CAFE_NFD),
        (CAFE_NFC, CAFE_NFD.upper()),
        (GREEK, GREEK_UPPER_NFD),
        # Canonical caseless matches that NFC-casefold-NFC folds differently.
        (OMEGA_YPO_ZONE, OMEGA_YPO_ZONE_PATH),
        ("Node_Modules", "node_modules"),
    )
    UNEQUAL = (
        ("cafe", CAFE_NFC),
        (NFC(BIGONGGAE), NFC(BIGONGGAE) + "-old"),
    )

    def test_fold_path_part_equalities(self) -> None:
        fold = vault_paths.fold_path_part
        for left, right in self.EQUAL:
            with self.subTest(left=ascii(left), right=ascii(right)):
                self.assertEqual(fold(left), fold(right))
        for left, right in self.UNEQUAL:
            with self.subTest(left=ascii(left), right=ascii(right)):
                self.assertNotEqual(fold(left), fold(right))
        copies = (vault_healthcheck.fold_path_part, vault_lint_extensions.fold_path_part)
        for value in {text for pair in (*self.EQUAL, *self.UNEQUAL) for text in pair}:
            with self.subTest(value=ascii(value)):
                folded = fold(value)
                self.assertEqual(fold(folded), folded)  # idempotent
                for copy in copies:
                    self.assertEqual(copy(value), folded)


class FoldPropertyTests(unittest.TestCase):
    """Seeded property test: fold equality is Unicode canonical caseless matching.

    The expected relation (NFD, casefold, NFD; Unicode D145) is computed with
    this interpreter's own unicodedata, never a stored table, so the test holds
    for the Unicode version of every Python it runs on. Greek letters with
    stacked marks, U+0345 among them, are where case changes reorder marks.
    """

    SEED = 7
    STRINGS = 2000
    MARKS = tuple(map(chr, (0x300, 0x301, 0x308, 0x313, 0x314, 0x342, 0x345, 0x352, 0x316, 0x323)))

    @staticmethod
    def caseless_key(text: str) -> str:
        return NFD(NFD(text).casefold())

    def test_fold_equality_is_canonical_caseless_matching(self) -> None:
        rng = random.Random(self.SEED)
        greek = [chr(c) for c in range(0x1F00, 0x2000) if unicodedata.category(chr(c)) != "Cn"]
        groups = []
        for _ in range(self.STRINGS):
            text = rng.choice(greek) + "".join(rng.choice(self.MARKS) for _ in range(rng.randint(1, 3)))
            variants = []
            for form in (text, NFC(text), NFD(text)):
                variants.extend((form, form.upper()))
            groups.append(variants)
        # Every pair of variants of one string, and one pair across neighbouring strings.
        pairs = [(group[i], group[j]) for group in groups
                 for i in range(len(group)) for j in range(i + 1, len(group))]
        pairs += [(left[0], right[0]) for left, right in zip(groups, groups[1:])]
        folds = (("vault_paths", vault_paths.fold_path_part),
                 ("vault_healthcheck", vault_healthcheck.fold_path_part),
                 ("vault_lint_extensions", vault_lint_extensions.fold_path_part))
        for name, fold in folds:
            with self.subTest(fold=name):
                wrong = [(ascii(a), ascii(b)) for a, b in pairs
                         if (fold(a) == fold(b)) != (self.caseless_key(a) == self.caseless_key(b))]
                self.assertEqual(wrong[:5], [], f"{len(wrong)} of {len(pairs)} pairs disagree")
                not_idempotent = [ascii(text) for group in groups for text in group
                                  if fold(fold(text)) != fold(text)]
                self.assertEqual(not_idempotent[:5], [], f"{len(not_idempotent)} strings")


class ConfigTests(unittest.TestCase):
    def test_config_path_in_another_spelling_of_a_zone_is_rejected(self) -> None:
        with self.assertRaises(vault_healthcheck.HealthcheckError) as caught:
            vault_healthcheck.validate_config({
                "deny_zones": [NFC(BIGONGGAE)],
                "hot_note": NFD(BIGONGGAE) + "/hot.md",
            })
        self.assertEqual(str(caught.exception), "hot_note must not point inside a deny zone")


class _VaultCase(unittest.TestCase):
    """A temporary vault with one valid note and one zone folder that holds a secret note."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name).resolve() / "vault"
        self.vault.mkdir()

    def write(self, relpath: str, text: str) -> Path:
        path = self.vault / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def make_vault(self, zone: str, folder: str, secret: str | None = None) -> None:
        self.write("00-meta/vault-config.json",
                   json.dumps({"deny_zones": [zone], "required_keys": ["title"]}, ensure_ascii=False))
        self.write("20-knowledge/ok.md", '---\ntitle: "ok"\n---\n\nA valid note.\n')
        zone_folder = _make_folder_or_skip(self, self.vault / "20-knowledge", folder)
        text = secret if secret is not None else f"{MARKER}\n"
        (zone_folder / "secret.md").write_text(text, encoding="utf-8")


class HealthcheckFullModeTests(_VaultCase):
    def run_full_check(self, zone: str, folder: str) -> None:
        self.make_vault(zone, folder)
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(HEALTHCHECK), "--vault", str(self.vault)],
            cwd=self.vault, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=120,
        )
        report_path = self.vault / "00-meta" / "health-report.md"
        report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + report)
        self.assertIn(f"- {CHECKED_NOTES}: 1 ", report)
        for name, text in (("report", report), ("stdout", result.stdout), ("stderr", result.stderr)):
            with self.subTest(output=name):
                self.assertNotIn(MARKER, text)

    def test_nfd_folder_under_nfc_zone_is_not_read(self) -> None:
        self.run_full_check("20-knowledge/" + NFC(BOGWAN), NFD(BOGWAN))

    def test_nfc_folder_under_nfd_zone_is_not_read(self) -> None:
        self.run_full_check("20-knowledge/" + NFD(BOGWAN), NFC(BOGWAN))


class RecallTests(_VaultCase):
    def test_nfd_zone_folder_contributes_nothing(self) -> None:
        self.make_vault("20-knowledge/" + NFC(BOGWAN), NFD(BOGWAN),
                        secret=f"# Secret\nzonequeryword {MARKER}\n")

        result = vault_recall.recall(self.vault, "zonequeryword", limit=10)

        self.assertEqual(result["matches"], [])
        self.assertNotIn(MARKER, json.dumps(result, ensure_ascii=False))
        self.assertGreaterEqual(result["diagnostics"]["skipped_denied"], 1)


class LintExtensionTests(unittest.TestCase):
    def test_nfd_keys_under_nfc_zone_supply_nothing(self) -> None:
        denied = "20-knowledge/" + NFD(BOGWAN) + "/t.md"
        notes = {"a.md": "[[20-knowledge/" + NFD(BOGWAN) + "/t#Hidden]]",
                 denied: "---\nupdated: bad\n---\n# Hidden\n"}
        config = {"deny_zones": ["20-knowledge/" + NFC(BOGWAN)]}
        # The analyzer imports its provenance, egress and ledger extensions lazily.
        with mock.patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
            findings = vault_lint_extensions.analyze_notes(notes, config)
        self.assertEqual(findings, [])

    def test_exclude_dirs_entry_in_another_spelling_is_folded(self) -> None:
        # The entry is stored decomposed; the path names the same folder composed, and the reverse.
        for entry, folder in ((NFD(BOGWAN), NFC(BOGWAN)), (NFC(BOGWAN), NFD(BOGWAN)),
                              (CAFE_NFD.upper(), CAFE_NFC)):
            with self.subTest(entry=ascii(entry), folder=ascii(folder)):
                config = {"deny_zones": [], "exclude_dirs": [entry]}
                self.assertTrue(vault_lint_extensions._denied("20-knowledge/" + folder + "/t.md", config))
        config = {"deny_zones": [], "exclude_dirs": [NFD(BOGWAN)]}
        self.assertFalse(vault_lint_extensions._denied("20-knowledge/" + NFC(JARYO) + "/t.md", config))


class StagedContractTests(unittest.TestCase):
    """Contract: the commit gate's pathspecs stay byte-exact, so another spelling is still checked."""

    def setUp(self) -> None:
        isolate_git(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve() / "staged vault"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "core.precomposeunicode", "false")
        self.git("config", "user.name", "Zone Fold Test")
        self.git("config", "user.email", "zone-fold@example.invalid")

    def git(self, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", *args], cwd=self.repo, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", check=False)
        if result.returncode != 0:
            self.fail(f"git {' '.join(args)} failed: {result.stderr}")
        return result

    def write(self, relpath: str, text: str) -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_nfd_spelling_of_a_zone_is_still_schema_checked(self) -> None:
        config = {
            "vault_name": "zone fold test vault",
            "required_keys": ["title"],
            "enums": {"status": ["active", "draft"]},
            "frontmatter_max_lines": 16,
            "deny_zones": ["20-knowledge/" + NFC(BOGWAN)],
            "exclude_dirs": ["node_modules", ".git"],
            "index_note": "00-meta/index.md",
            "log_note": "00-meta/log.md",
            "hot_note": "00-meta/hot.md",
            "handoff_note": "",
            "ssot_note": "",
            "health_report": "00-meta/health-report.md",
        }
        self.write("00-meta/vault-config.json", json.dumps(config, ensure_ascii=False))
        self.git("add", "00-meta/vault-config.json")
        self.git("commit", "-q", "-m", "seed staged gate")
        _make_folder_or_skip(self, self.repo / "20-knowledge", NFD(BOGWAN))
        relpath = "20-knowledge/" + NFD(BOGWAN) + "/v.md"
        self.write(relpath, "no front matter in another spelling of a zone\n")
        self.git("add", relpath)

        errors = vault_healthcheck.validate_staged(
            self.repo, vault_healthcheck.load_staged_config(self.repo))

        self.assertTrue(
            any(NFC(relpath) in NFC(error) and MISSING_FRONT_MATTER in error for error in errors),
            ascii(errors),
        )


if __name__ == "__main__":
    unittest.main()
