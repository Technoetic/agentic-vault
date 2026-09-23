"""One rule, one source: the outbound secret filter and the vault path rules.

Each safety rule used to exist in several private copies that disagreed. These
tests feed the same inputs to every former copy and require the same verdict,
and they pin the few differences that are intended.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
BEGIN = "# --- BEGIN SHARED PATH RULE"
END = "# --- END SHARED PATH RULE ---"

sys.path.insert(0, str(SCRIPTS))
try:
    import backup_vault
    import jev_ask
    import jev_client
    import vault_healthcheck
    import vault_judge
    import vault_paths
    import vault_recall
finally:
    sys.path.remove(str(SCRIPTS))


SECRETS = (
    "sk-" + "a" * 24, "sk_" + "a" * 24, "ghp_" + "a" * 30, "github_pat_11AB" + "c" * 22,
    "AKIAIOSFODNN7EXAMPLE", "ts_" + "A" * 24, "typesafe_" + "A" * 24,
    "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10,
    "apikey_" + "a" * 32 + "_" + "b" * 64, "-----BEGIN PRIVATE KEY-----",
    "password = hunter2", "passwd: abcd1234", "client_secret: abcd1234efgh",
    "access_token=abcdefghijk", "refresh_token: abcd1234", 'MY_API_KEY="abcd1234"',
    "Authorization: Basic ZmFrZTpmYWtl", "Authorization: Bearer abcdefgh",
    # Shapes only the earlier vault_judge copy caught; the union keeps them.
    "authorization:bearerXXXXXXXX", "api_key: 'abc,defgh'",
    # Shapes both earlier copies let through (review of 0.15.1): the Telegram
    # bot token the bridge itself uses, Slack, Google, URL credentials, Korean
    # labels, and full-width or zero-width disguises of a known label.
    "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawZ",
    "xoxb-123456789012-1234567890123-AbCdEfGh",
    "https://hooks.slack.com/services/T00000000/B00000000/" + "X" * 24,
    "AIza" + "S" * 35,
    "postgres://admin:Sup3rS3cret@db:5432/app",
    "비밀번호: Sup3rS3cret!", "토큰 = abcd1234efgh", "인증키\uff1aabcd1234",
    "password\uff1aSup3rS3cret!", "pass\u200bword=Sup3rS3cret!",
    "\uff53\uff4b-" + "a" * 24,
)
BENIGN = (
    "CSV export is supported.",
    "The password policy is documented in the handbook.",
    "Ask the team about the secret santa draw.",
    "Tokens are counted per session.",
    '"R&D" 예산은 얼마인가?',
    "비밀번호: 8자 이상, 90일마다 변경한다.",
    "토큰 사용량은 세션마다 센다.",
    "암호화: AES-256으로 저장한다.",
    "Docs live at https://example.com:8443/guide and git@github.com:org/repo.git.",
    "회의는 12:30에 시작한다.",
)


def _choice_questions(instructions: str) -> dict:
    return {"claim": {"type": "choice", "instructions": instructions,
                      "criteria": {"yes": "Supported", "unknown": "Insufficient evidence"}}}


def _ask_request(text: str) -> bytes:
    return json.dumps({
        "schema_version": 1,
        "context": {"kind": "selected_text", "text": text},
        "questions": [{"id": "claim", "type": "choice", "instructions": "Is it supported?",
                       "criteria": {"yes": "Supported", "unknown": "Insufficient evidence"},
                       "abstain": "unknown"}],
    }, ensure_ascii=False).encode("utf-8")


class SecretFilterSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name)
        (self.vault / "00-meta").mkdir()
        (self.vault / "00-meta" / "vault-config.json").write_text(
            json.dumps({"vault_name": "Rule sources"}), encoding="utf-8")
        (self.vault / "20-knowledge").mkdir()
        self.note = self.vault / "20-knowledge" / "evidence.md"

    def judge_request(self, *, excerpt: str, instructions: str = "Is it supported?") -> dict:
        return {
            "schema_version": 1, "task": "support",
            "sources": [{"path": "20-knowledge/evidence.md", "excerpt": excerpt}],
            "questions": [{"id": "claim", "instructions": instructions,
                           "choices": {"yes": "Supported", "unknown": "Insufficient evidence"},
                           "abstain": "unknown"}],
        }

    def test_every_jev_path_shares_one_pattern_object(self) -> None:
        self.assertIs(jev_ask.SENSITIVE, jev_client.SENSITIVE)
        self.assertIs(vault_judge.SENSITIVE, jev_client.SENSITIVE)

    def test_only_jev_client_applies_the_raw_pattern(self) -> None:
        # contains_sensitive also checks the NFKC form without zero-width
        # characters; a direct SENSITIVE.search elsewhere would skip that.
        callers = sorted(
            path.relative_to(ROOT).as_posix()
            for directory in (SCRIPTS, ROOT / "hooks", ROOT / "scripts")
            for path in directory.glob("*.py")
            if "SENSITIVE.search" in path.read_text(encoding="utf-8")
            or "SENSITIVE.match" in path.read_text(encoding="utf-8")
            or "SENSITIVE.fullmatch" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(callers, ["skills/agentic-vault/scripts/jev_client.py"])

    def test_disguised_labels_are_caught_only_after_normalization(self) -> None:
        # The raw pattern misses these; contains_sensitive must not.
        for disguised in ("password\uff1aSup3rS3cret!", "pass\u200bword=Sup3rS3cret!",
                          "api\u2060_key = abcd1234efgh"):
            with self.subTest(text=disguised):
                self.assertIsNone(jev_client.SENSITIVE.search(disguised))
                self.assertTrue(jev_client.contains_sensitive(disguised))

    def test_no_other_script_defines_its_own_secret_pattern(self) -> None:
        owners = sorted(
            path.relative_to(ROOT).as_posix()
            for directory in (SCRIPTS, ROOT / "hooks", ROOT / "scripts")
            for path in directory.glob("*.py")
            if "PRIVATE KEY" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(owners, ["skills/agentic-vault/scripts/jev_client.py"])

    def test_secret_rejected_by_one_outbound_path_is_rejected_by_all(self) -> None:
        for secret in SECRETS:
            with self.subTest(secret=secret):
                self.assertTrue(jev_client.contains_sensitive({"nested": [secret]}))
                with self.assertRaises(jev_client.JevError) as raised:
                    jev_client.build_payload({"evidence": [secret]}, _choice_questions("Check."))
                self.assertEqual(raised.exception.code, "sensitive_input")
                with self.assertRaises(jev_client.JevError) as raised:
                    jev_client.build_payload({"evidence": ["ok"]}, _choice_questions(secret))
                self.assertEqual(raised.exception.code, "sensitive_input")

                asked = jev_ask.prepare(_ask_request(secret), api_key="")
                self.assertEqual(asked.get("error_code"), "sensitive_input")

                self.note.write_text(f"# Evidence\n{secret}\n", encoding="utf-8")
                from_file = vault_judge.prepare(self.vault, self.judge_request(excerpt=secret))
                self.assertEqual(from_file.get("error"), "sensitive_input")
                in_question = vault_judge.prepare(self.vault, self.judge_request(
                    excerpt="# Evidence", instructions="Check " + secret))
                self.assertEqual(in_question.get("error"), "sensitive_input")

                for result in (asked, from_file, in_question):
                    self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))

    def test_benign_text_passes_every_outbound_path(self) -> None:
        for text in BENIGN:
            with self.subTest(text=text):
                self.assertFalse(jev_client.contains_sensitive(text))
                self.assertIsInstance(
                    jev_client.build_payload({"evidence": [text]}, _choice_questions(text)), bytes)
                self.assertEqual(jev_ask.prepare(_ask_request(text), api_key="")["status"], "prepared")
                self.note.write_text(f"# Evidence\n{text}\n", encoding="utf-8")
                prepared = vault_judge.prepare(self.vault, self.judge_request(excerpt=text))
                self.assertEqual(prepared["status"], "prepared", prepared)

    def test_literal_api_key_is_still_rejected_by_jev_ask(self) -> None:
        key = "synthetic-rule-source-key"
        self.assertFalse(jev_client.contains_sensitive("prefix " + key))
        self.assertTrue(jev_client.contains_sensitive({"k": ["prefix " + key]}, (key,)))
        self.assertEqual(jev_ask.prepare(_ask_request("prefix " + key), api_key=key).get("error_code"),
                         "sensitive_input")


REJECTED = (
    "../outside.md", "notes/../outside.md", "notes/./note.md", ".", "..",
    "C:/outside.md", "C:outside.md", "/outside.md", "//server/share/note.md",
    "notes//note.md", "notes/", "notes/note.md.", "private./note.md", "private /note.md",
    "notes/item.md:stream", "CON", "con.md", "notes/aux.txt", "Prn", "NUL.tar.gz",
    "COM1.md", "com9", "COM\u00b9", "com\u00b2.txt", "LPT\u00b3.md", "lpt1",
    "CONIN$", "conout$.txt", "clock$", "Clock$.md", "notes/CLOCK$/note.md",
    "a?.md", "a|b.md", "a<b.md", "a>b.md", 'a"b.md', "a*.md",
    "tab\there.md", "line\nfeed.md", "nul\x00byte.md", "bell\x07.md",
)
ACCEPTED = (
    "00-meta/hot.md", "20-knowledge/\ub0b4 \uc790\ub8cc/\ud300 \ub178\ud2b8.md",
    "notes/console.md", "notes/con-artist.md", "com10.md", "lpt0x.md", "clockwork.md",
    "a.b.c.md", ".obsidian", "90-assets", "notes/ leading space.md", "[draft] note.md",
)


def _accepts(check, value) -> bool:
    try:
        check(value)
    except ValueError:  # BackupError subclasses ValueError
        return False
    except vault_healthcheck.HealthcheckError:
        return False
    return True


def _healthcheck(value):
    return vault_healthcheck._normalize_relative_path(value, "path", allow_empty=False)


def _vault_paths(value):
    return vault_paths.relative_parts(value, "path")


def _backup(value):
    return backup_vault._relative_name(value)


class PathRuleSourceTests(unittest.TestCase):
    def test_healthcheck_carries_a_verbatim_copy_of_the_shared_rule(self) -> None:
        def block(name: str) -> str:
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            start = text.index(BEGIN)
            body_start = text.index("\n", start) + 1
            return text[body_start:text.index(END)]

        self.assertEqual(block("vault_healthcheck.py"), block("vault_paths.py"))
        self.assertEqual(vault_healthcheck.RESERVED_DEVICE_NAMES, vault_paths.RESERVED_DEVICE_NAMES)
        self.assertEqual(vault_healthcheck.UNSAFE_SEGMENT_CHARACTERS,
                         vault_paths.UNSAFE_SEGMENT_CHARACTERS)

    def test_no_other_script_reimplements_the_segment_rule(self) -> None:
        owners = sorted(
            path.name for path in SCRIPTS.glob("*.py")
            if '<>:"|?*' in path.read_text(encoding="utf-8")
        )
        self.assertEqual(owners, ["vault_healthcheck.py", "vault_paths.py"])

    def test_all_relative_path_validators_agree(self) -> None:
        for value in REJECTED + ACCEPTED:
            expected = value in ACCEPTED
            with self.subTest(value=value):
                self.assertEqual(_accepts(_vault_paths, value), expected)
                self.assertEqual(_accepts(_healthcheck, value), expected)
                self.assertEqual(_accepts(_backup, value), expected)

    def test_config_trimming_is_normalization_that_vault_paths_accepts(self) -> None:
        # Intended difference: validate_config trims JSON text; vault_paths never
        # trims ("note.md " is a different Windows name). Every trimmed value that
        # the healthcheck returns is accepted by vault_paths, so readers agree.
        for value in (" 00-meta/hot.md", "00-meta/hot.md ", "\t90-assets\n"):
            with self.subTest(value=value):
                normalized = _healthcheck(value)
                self.assertEqual(normalized, value.strip())
                self.assertTrue(_accepts(_vault_paths, normalized))
        self.assertFalse(_accepts(_vault_paths, "00-meta/hot.md "))
        for value in ACCEPTED:
            with self.subTest(accepted=value):
                self.assertEqual(vault_paths.relative_parts(_healthcheck(value), "path"),
                                 vault_paths.relative_parts(value, "path"))

    def test_backup_refuses_backslashes_that_other_readers_treat_as_separators(self) -> None:
        # Intended difference: snapshot manifests store POSIX names only.
        for value in ("notes\\note.md", "a\\b\\c.md"):
            with self.subTest(value=value):
                self.assertTrue(_accepts(_vault_paths, value))
                self.assertTrue(_accepts(_healthcheck, value))
                self.assertFalse(_accepts(_backup, value))
        for value in ("\\\\server\\share\\note.md", "\\outside.md", "notes\\..\\x.md"):
            with self.subTest(value=value):
                self.assertFalse(_accepts(_vault_paths, value))
                self.assertFalse(_accepts(_healthcheck, value))
                self.assertFalse(_accepts(_backup, value))

    def test_validate_config_rejects_what_resolve_note_path_rejects(self) -> None:
        for value in ("50-projects/clock$.md", "50-projects/a?.md", "a|b.md", "a<b.md"):
            with self.subTest(value=value):
                with self.assertRaises(vault_healthcheck.HealthcheckError):
                    vault_healthcheck.validate_config({"hot_note": value})
                with self.assertRaises(vault_healthcheck.HealthcheckError):
                    vault_healthcheck.validate_config({"deny_zones": [value]})
                with self.assertRaises(ValueError):
                    vault_paths.relative_parts(value, "hot_note")

    def test_all_deny_zone_deciders_agree(self) -> None:
        zones = ["10-inbox/_processed", "90-assets", ".obsidian"]
        cases = {
            "10-inbox/_processed/a.md": True, "10-INBOX/_Processed/a.md": True,
            "10-inbox/_processed": True, "10-inbox/a.md": False,
            "notes/10-inbox/_processed/a.md": False, "notes/90-assets/b.md": True,
            "90-Assets/x.png": True, "90-assets-old/x.md": False,
            ".obsidian/workspace.md": True, "20-knowledge/a.md": False,
        }
        prefixes, names = vault_healthcheck.build_deny_rules(zones)
        for rel, expected in cases.items():
            parts = tuple(rel.split("/"))
            with self.subTest(rel=rel):
                self.assertIs(vault_healthcheck.is_denied(rel, prefixes, names), expected)
                self.assertIs(vault_paths._is_denied(parts, zones), expected)
                self.assertIs(vault_recall._classified_skip(parts, zones, ()) == "denied", expected)
                self.assertIs(vault_recall._classified_skip(parts, (), zones) == "excluded", expected)


class LinkedComponentSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.vault = base / "vault"
        (self.vault / "real").mkdir(parents=True)
        (self.vault / "real" / "note.md").write_text("inside\n", encoding="utf-8")
        self.outside = base / "outside"
        self.outside.mkdir()
        (self.outside / "note.md").write_text("OUTSIDE-SENTINEL\n", encoding="utf-8")

    def link_directory_or_skip(self, link: Path) -> None:
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(self.outside)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", check=False)
            if result.returncode != 0:
                self.skipTest(f"junction creation is unsupported: {result.stderr}")
            return
        try:
            link.symlink_to(self.outside, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation is unsupported: {exc}")

    def verdicts(self, rel: str) -> tuple[bool, bool]:
        try:
            vault_healthcheck._ensure_vault_path(self.vault, self.vault / rel, "note")
            healthcheck_ok = True
        except vault_healthcheck.HealthcheckError:
            healthcheck_ok = False
        try:
            vault_paths.resolve_note_path(self.vault, rel)
            paths_ok = True
        except ValueError:
            paths_ok = False
        return healthcheck_ok, paths_ok

    def test_link_and_escape_verdicts_match(self) -> None:
        self.link_directory_or_skip(self.vault / "linked")
        self.assertEqual(self.verdicts("real/note.md"), (True, True))
        self.assertEqual(self.verdicts("real/missing.md"), (True, True))
        self.assertEqual(self.verdicts("linked/note.md"), (False, False))
        self.assertEqual(self.verdicts("linked"), (False, False))
        self.assertEqual(self.verdicts("../outside/note.md"), (False, False))


if __name__ == "__main__":
    unittest.main()
