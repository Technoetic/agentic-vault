"""Tests for the explicit path-scoped commit tool, on synthetic repositories."""
from __future__ import annotations

import hashlib
import io
import json
import os
import ctypes
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agentic-vault/scripts"
SCRIPT = SCRIPTS / "vault_commit.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import vault_commit  # noqa: E402


HOT = "00-meta/hot.md"
LOG = "00-meta/log.md"
INDEX = "00-meta/index.md"
HANDOFF = "50-projects/demo/handoff.md"
SHARED = (HOT, LOG, INDEX, HANDOFF)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def always_retry_sleep(_seconds):
    return None


@unittest.skipUnless(shutil.which("git"), "git is required")
class VaultCommitCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            os.environ.pop(name, None)
        self.vault = Path(self.temp.name).resolve() / "vault"
        self.vault.mkdir()
        self.git("init", "-q")
        for key, value in (("user.name", "Synthetic Writer"),
                           ("user.email", "writer@example.invalid"),
                           ("core.autocrlf", "false"),
                           ("commit.gpgsign", "false")):
            self.git("config", key, value)
        self.write("00-meta/vault-config.json", json.dumps({
            "required_keys": ["title"], "enums": {},
            "hot_note": HOT, "log_note": LOG, "index_note": INDEX,
            "handoff_note": HANDOFF,
        }).encode())
        self.write(".gitignore", b"*.secret\n")
        self.write(HOT, b"# hot\nslot-a\nslot-b\n")
        self.write(LOG, b"# log\n")
        self.write(INDEX, b"# index\n")
        self.write(HANDOFF, b"# handoff\n")
        self.write("notes/mine.md", b"mine v1\n")
        self.write("notes/other.md", b"other v1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "seed")
        self.git_dir = Path(self.git("rev-parse", "--absolute-git-dir").stdout.decode().strip())

    def git(self, *args, check=True, input=None, env=None):
        merged = dict(os.environ)
        merged.update(env or {})
        result = subprocess.run(
            ["git", *args], cwd=self.vault, input=input, env=merged,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if check and result.returncode != 0:
            raise AssertionError(result.stderr.decode("utf-8", "replace"))
        return result

    def write(self, relative, data):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def entry(self, relative):
        return relative, sha((self.vault / relative).read_bytes())

    def commit(self, entries, **options):
        options.setdefault("sleep", always_retry_sleep)
        return vault_commit.commit_paths(
            self.vault, entries, options.pop("message", "note update"),
            options.pop("actor", "session-a"), **options)

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.decode().strip()

    def changed(self, revision):
        output = self.git("diff-tree", "--no-commit-id", "--root", "-r", "-z",
                          "--name-only", "--no-renames", revision).stdout
        return sorted(part.decode() for part in output.split(b"\0") if part)

    def staged(self):
        output = self.git("diff", "--cached", "--name-only", "-z").stdout
        return sorted(part.decode() for part in output.split(b"\0") if part)

    def show(self, spec):
        return self.git("show", spec).stdout

    def hook(self, name, body):
        path = self.git_dir / "hooks" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"#!/bin/sh\n" + body + b"\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class ScopeTests(VaultCommitCase):
    def test_commit_carries_only_the_declared_path(self):
        self.write("notes/mine.md", b"mine v2\n")
        self.write("notes/other.md", b"other unstaged edit\n")
        report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(report["reason"], None)
        self.assertEqual(report["commit"], self.head())
        self.assertEqual(self.changed("HEAD"), ["notes/mine.md"])
        self.assertEqual(self.show("HEAD:notes/mine.md"), b"mine v2\n")
        self.assertEqual(self.show("HEAD:notes/other.md"), b"other v1\n")
        self.assertEqual((self.vault / "notes/other.md").read_bytes(), b"other unstaged edit\n")

    def test_new_file_is_committed_without_prior_staging(self):
        self.write("notes/new note.md", "새 노트\n".encode())
        report = self.commit([self.entry("notes/new note.md")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(self.changed("HEAD"), ["notes/new note.md"])

    def test_another_sessions_staged_file_stays_staged_and_out(self):
        self.write("notes/other.md", b"staged by another session\n")
        self.git("add", "--", "notes/other.md")
        self.write("notes/mine.md", b"mine v2\n")
        report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(self.changed("HEAD"), ["notes/mine.md"])
        self.assertEqual(self.staged(), ["notes/other.md"])
        self.assertEqual(self.show(":notes/other.md"), b"staged by another session\n")

    def test_literal_pathspec_does_not_widen_to_a_glob_match(self):
        self.write("notes/a[b].md", b"bracket\n")
        self.write("notes/ab.md", b"plain\n")
        report = self.commit([self.entry("notes/a[b].md")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(self.changed("HEAD"), ["notes/a[b].md"])
        self.assertEqual(self.staged(), [])

    def test_hooks_do_not_inherit_literal_pathspecs(self):
        # The engine pre-commit hook uses pathspec magic, which a global
        # literal mode would silently turn into matching nothing.
        self.hook("pre-commit", b'test -z "$GIT_LITERAL_PATHSPECS"')
        self.write("notes/mine.md", b"mine v2\n")
        self.assertEqual(self.commit([self.entry("notes/mine.md")])["status"], "committed")

    def test_deletion_uses_the_absent_marker(self):
        (self.vault / "notes/mine.md").unlink()
        report = self.commit([("notes/mine.md", vault_commit.ABSENT)])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(self.changed("HEAD"), ["notes/mine.md"])
        self.assertEqual(self.git("cat-file", "-e", "HEAD:notes/mine.md", check=False).returncode, 128)

    def test_existing_file_with_absent_marker_is_refused(self):
        report = self.commit([("notes/mine.md", vault_commit.ABSENT)])
        self.assertEqual((report["status"], report["reason"]), ("refused", "hash_mismatch"))

    def test_second_writer_environment_index_is_ignored(self):
        self.write("notes/mine.md", b"mine v2\n")
        with mock.patch.dict(os.environ, {"GIT_INDEX_FILE": str(self.git_dir / "not-the-index")}):
            report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual(report["status"], "committed")
        self.assertFalse((self.git_dir / "not-the-index").exists())

    def test_message_and_actor_are_recorded(self):
        self.write("notes/mine.md", b"mine v2\n")
        report = self.commit([self.entry("notes/mine.md")], message="tidy note", actor="agent one")
        self.assertEqual(report["status"], "committed")
        text = self.git("log", "-1", "--format=%B", report["commit"]).stdout.decode()
        self.assertEqual(text.strip().splitlines(), ["tidy note", "", "Vault-Actor: agent one"])

    def test_linked_worktree_commits_on_its_own_index(self):
        self.write("notes/mine.md", b"mine v1 local\n")
        linked = Path(self.temp.name).resolve() / "linked"
        self.git("worktree", "add", "-q", "-b", "side", str(linked))
        (linked / "notes/other.md").write_bytes(b"from linked worktree\n")
        report = vault_commit.commit_paths(
            linked, [("notes/other.md", sha(b"from linked worktree\n"))],
            "linked note", "session-b", sleep=always_retry_sleep)
        self.assertEqual(report["status"], "committed")
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r",
                                  report["commit"]).stdout.strip(), b"notes/other.md")
        self.assertEqual(self.staged(), [])


class PreconditionTests(VaultCommitCase):
    def test_hash_mismatch_refuses_before_touching_the_index(self):
        self.write("notes/mine.md", b"mine v2\n")
        before = self.head()
        report = self.commit([("notes/mine.md", sha(b"something else"))])
        self.assertEqual((report["status"], report["reason"]), ("refused", "hash_mismatch"))
        self.assertEqual(report["path_index"], 0)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.staged(), [])

    def test_missing_file_with_a_hash_is_a_mismatch(self):
        report = self.commit([("notes/missing.md", sha(b"x"))])
        self.assertEqual((report["status"], report["reason"]), ("refused", "hash_mismatch"))

    def test_merge_in_progress_is_refused(self):
        self.git("checkout", "-q", "-b", "side")
        self.write("notes/other.md", b"side\n")
        self.git("add", "--", "notes/other.md")
        self.git("commit", "-q", "-m", "side")
        self.git("checkout", "-q", "-")
        self.write("notes/mine.md", b"mine main\n")
        self.git("add", "--", "notes/mine.md")
        self.git("commit", "-q", "-m", "main")
        self.git("merge", "--no-commit", "--no-ff", "side")
        self.assertTrue((self.git_dir / "MERGE_HEAD").exists())
        self.write("notes/new.md", b"new\n")
        before = self.head()
        report = self.commit([self.entry("notes/new.md")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "operation_in_progress"))
        self.assertEqual(self.head(), before)
        self.assertNotIn("notes/new.md", self.staged())

    def test_rebase_in_progress_is_refused(self):
        for name in ("rebase-merge", "rebase-apply"):
            with self.subTest(name=name):
                marker = self.git_dir / name
                marker.mkdir()
                self.addCleanup(lambda m=marker: m.exists() and m.rmdir())
                self.write("notes/new.md", b"new\n")
                report = self.commit([self.entry("notes/new.md")])
                self.assertEqual((report["status"], report["reason"]),
                                 ("refused", "operation_in_progress"))
                marker.rmdir()

    def test_every_shared_file_is_refused_by_default(self):
        for relative in SHARED:
            with self.subTest(relative=relative):
                self.write(relative, b"edited\n")
                before = self.head()
                report = self.commit([self.entry(relative)])
                self.assertEqual((report["status"], report["reason"]),
                                 ("refused", "shared_file_refused"))
                self.assertEqual(self.head(), before)
                self.assertEqual(self.staged(), [])

    def test_shared_match_ignores_case_and_separator(self):
        data = (self.vault / LOG).read_bytes()
        report = self.commit([("00-META\\LOG.MD", sha(data))])
        self.assertEqual((report["status"], report["reason"]),
                         ("refused", "shared_file_refused"))

    def test_deny_zone_is_refused_even_when_the_file_exists(self):
        self.write("90-assets/scan.md", b"binary-like\n")
        for relative in ("90-assets/scan.md", ".git/config"):
            with self.subTest(relative=relative):
                report = self.commit([(relative, sha(b"x"))])
                self.assertEqual((report["status"], report["reason"]), ("refused", "denied_path"))

    def test_folder_paths_are_refused(self):
        report = self.commit([("notes", sha(b"x"))])
        self.assertEqual((report["status"], report["reason"]), ("refused", "folder_path"))

    def test_folder_path_with_absent_marker_is_refused(self):
        shutil.rmtree(self.vault / "notes")
        report = self.commit([("notes", vault_commit.ABSENT)])
        self.assertEqual((report["status"], report["reason"]), ("refused", "folder_path"))
        self.assertEqual(self.staged(), [])

    def test_absent_marker_needs_a_tracked_path(self):
        report = self.commit([("notes/never.md", vault_commit.ABSENT)])
        self.assertEqual((report["status"], report["reason"]), ("refused", "absent_not_tracked"))

    def test_unsafe_paths_are_refused(self):
        for relative in ("../escape.md", "notes/../mine.md", "/abs.md", "C:/x.md", "a:b.md",
                         ":(glob)**", "notes/*.md", ""):
            with self.subTest(relative=relative):
                report = self.commit([(relative, sha(b"x"))])
                self.assertEqual((report["status"], report["reason"]), ("refused", "unsafe_path"))

    def test_duplicates_and_empty_input_are_refused(self):
        entry = self.entry("notes/mine.md")
        report = self.commit([entry, ("NOTES/MINE.MD", entry[1])])
        self.assertEqual((report["status"], report["reason"]), ("refused", "duplicate_path"))
        self.assertEqual(self.commit([])["reason"], "no_paths")

    def test_invalid_hash_message_actor_and_bounds(self):
        entry = self.entry("notes/mine.md")
        self.assertEqual(self.commit([("notes/mine.md", "abc")])["reason"], "invalid_hash")
        self.assertEqual(self.commit([entry], message="")["reason"], "invalid_message")
        self.assertEqual(self.commit([entry], message="two\nlines")["reason"], "invalid_message")
        self.assertEqual(self.commit([entry], message=" padded ")["reason"], "invalid_message")
        self.assertEqual(self.commit([entry], actor="")["reason"], "invalid_actor")
        self.assertEqual(self.commit([entry], actor="a\nb")["reason"], "invalid_actor")
        self.assertEqual(self.commit([entry], max_attempts=0)["reason"], "invalid_retry_limit")
        self.assertEqual(self.commit([entry], max_delay_ms=-1)["reason"], "invalid_retry_limit")

    def test_ignored_files_are_not_forced_in(self):
        self.write("notes/key.secret", b"synthetic\n")
        report = self.commit([self.entry("notes/key.secret")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "add_rejected"))
        self.assertEqual(self.staged(), [])

    def test_vault_below_the_repository_root_is_refused(self):
        nested = self.vault / "nested"
        self.write("nested/00-meta/vault-config.json", b'{"required_keys":["title"],"enums":{}}')
        self.write("nested/a.md", b"a\n")
        report = vault_commit.commit_paths(nested, [("a.md", sha(b"a\n"))], "m", "a",
                                           sleep=always_retry_sleep)
        self.assertEqual((report["status"], report["reason"]),
                         ("refused", "vault_not_repository_root"))

    def test_not_a_repository_and_missing_config(self):
        plain = Path(self.temp.name).resolve() / "plain"
        (plain / "00-meta").mkdir(parents=True)
        (plain / "00-meta/vault-config.json").write_bytes(b'{"required_keys":["title"],"enums":{}}')
        (plain / "a.md").write_bytes(b"a\n")
        report = vault_commit.commit_paths(plain, [("a.md", sha(b"a\n"))], "m", "a",
                                           sleep=always_retry_sleep)
        self.assertEqual((report["status"], report["reason"]), ("refused", "not_a_repository"))
        (plain / "00-meta/vault-config.json").unlink()
        report = vault_commit.commit_paths(plain, [("a.md", sha(b"a\n"))], "m", "a",
                                           sleep=always_retry_sleep)
        self.assertEqual((report["status"], report["reason"]), ("refused", "invalid_vault_config"))


class RetryTests(VaultCommitCase):
    def test_a_lock_that_clears_is_retried(self):
        self.write("notes/mine.md", b"mine v2\n")
        lock = self.git_dir / "index.lock"
        lock.write_bytes(b"")
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            if len(waits) == 3:
                lock.unlink()

        report = self.commit([self.entry("notes/mine.md")], sleep=sleep)
        self.assertEqual(report["status"], "committed")
        self.assertEqual(report["attempts"], 4)
        self.assertEqual(report["lock_failures"], 3)
        self.assertEqual(len(waits), 3)
        self.assertEqual(self.changed("HEAD"), ["notes/mine.md"])

    def test_delay_uses_jitter_under_the_cap(self):
        self.write("notes/mine.md", b"mine v2\n")
        lock = self.git_dir / "index.lock"
        lock.write_bytes(b"")
        waits = []
        draws = iter([0.5, 1.0, 0.25, 1.0])

        def sleep(seconds):
            waits.append(seconds)

        with mock.patch.object(vault_commit.random, "random", lambda: next(draws)):
            report = self.commit([self.entry("notes/mine.md")], sleep=sleep,
                                 max_attempts=5, max_delay_ms=60)
        self.assertEqual(report["reason"], "index_lock_timeout")
        self.assertEqual(waits, [0.01, 0.04, 0.015, 0.06])

    def test_a_stale_lock_is_reported_and_never_removed(self):
        self.write("notes/mine.md", b"mine v2\n")
        lock = self.git_dir / "index.lock"
        lock.write_bytes(b"held")
        before = self.head()
        report = self.commit([self.entry("notes/mine.md")], max_attempts=3)
        self.assertEqual((report["status"], report["reason"]), ("failed", "index_lock_timeout"))
        self.assertEqual(report["attempts"], 3)
        self.assertEqual(report["lock_failures"], 3)
        self.assertTrue(report["lock"]["present"])
        self.assertIsInstance(report["lock"]["age_seconds"], int)
        self.assertEqual(lock.read_bytes(), b"held")
        self.assertEqual(self.head(), before)

    def test_vanished_stage_entry_restarts_from_git_add(self):
        self.write("notes/new.md", b"new\n")
        real = vault_commit._run_git
        calls = []

        def run(vault, args, **options):
            calls.append(args[0])
            if args[0] == "commit" and calls.count("commit") == 1:
                self.git("rm", "-q", "--cached", "--", "notes/new.md")
            return real(vault, args, **options)

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([self.entry("notes/new.md")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(report["pathspec_restarts"], 1)
        self.assertEqual([c for c in calls if c in ("add", "commit")],
                         ["add", "commit", "add", "commit"])

    def test_pathspec_retries_stay_inside_the_limit(self):
        self.write("notes/new.md", b"new\n")
        real = vault_commit._run_git
        commits = []

        def run(vault, args, **options):
            if args[0] == "commit":
                commits.append(1)
                self.git("rm", "-q", "--cached", "--", "notes/new.md")
            return real(vault, args, **options)

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([self.entry("notes/new.md")], max_attempts=4)
        self.assertEqual((report["status"], report["reason"]),
                         ("failed", "pathspec_retries_exhausted"))
        self.assertEqual(len(commits), 4)

    def test_file_rewritten_during_retries_is_refused(self):
        self.write("notes/mine.md", b"mine v2\n")
        expected = self.entry("notes/mine.md")
        lock = self.git_dir / "index.lock"
        lock.write_bytes(b"")

        def sleep(_seconds):
            lock.unlink()
            self.write("notes/mine.md", b"rewritten by another writer\n")

        report = self.commit([expected], sleep=sleep)
        self.assertEqual((report["status"], report["reason"]), ("refused", "hash_mismatch"))
        self.assertEqual(self.staged(), [])

    def test_failure_classifier(self):
        cases = (
            (b"", b"fatal: Unable to create '/r/.git/index.lock': File exists.", "lock"),
            (b"", b"fatal: cannot lock ref 'HEAD': is at aaa but expected bbb", "lock"),
            (b"", b"fatal: update_ref failed for ref 'HEAD': cannot lock ref 'HEAD': is at a", "lock"),
            (b"", b"error: cannot lock ref 'refs/heads/x': Unable to create '/r/x.lock'", "lock"),
            (b"", b"fatal: Unable to create '/r/.git/HEAD.lock': File exists.", "lock"),
            (b"", b"fatal: .git/index: index file open failed: Permission denied", "lock"),
            (b"", b"hook line\nfatal: Unable to write new index file\n", "lock"),
            (b"", b"error: pathspec 'x' did not match any file(s) known to git", "pathspec"),
            (b"", b"fatal: pathspec 'x' did not match any files", "pathspec"),
            (b"nothing to commit, working tree clean", b"", "nothing"),
            (b"On branch main\nno changes added to commit\n", b"", "nothing"),
            (b"nothing added to commit but untracked files present", b"", "nothing"),
            (b"", b"hook said no", None),
            (b"", b"index.lock\nnothing to commit\ndid not match any file\n", None),
            (b"", b"error: note index.lock has no title", None),
            (b"\tmodified:   nothing to commit.md\n", b"", None),
        )
        for stdout, stderr, expected in cases:
            with self.subTest(stdout=stdout, stderr=stderr):
                self.assertEqual(vault_commit.classify_failure(stdout, stderr), expected)


class VerificationTests(VaultCommitCase):
    def test_uses_the_output_hash_when_another_commit_lands_first(self):
        self.write("notes/mine.md", b"mine v2\n")
        real = vault_commit._run_git

        def run(vault, args, **options):
            result = real(vault, args, **options)
            if args[0] == "commit":
                self.write("notes/other.md", b"other session\n")
                self.git("commit", "-q", "-m", "other session", "--", "notes/other.md")
            return result

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual(report["status"], "committed")
        self.assertNotEqual(report["commit"], self.head())
        self.assertEqual(self.changed(report["commit"]), ["notes/mine.md"])

    def test_unchanged_declared_path_marks_the_commit_as_mismatched(self):
        self.write("notes/mine.md", b"mine v2\n")
        report = self.commit([self.entry("notes/mine.md"), self.entry("notes/other.md")])
        self.assertEqual((report["status"], report["reason"]),
                         ("committed_mismatch", "paths_differ"))
        self.assertEqual(report["commit"], self.head())
        self.assertEqual(report["missing"], 1)
        self.assertEqual(report["undeclared"], 0)

    def test_undeclared_commit_content_is_detected(self):
        self.write("notes/mine.md", b"mine v2\n")
        real = vault_commit._run_git

        def run(vault, args, **options):
            if args[0] == "diff-tree":
                return subprocess.CompletedProcess(
                    args, 0, b"notes/mine.md\0notes/other.md\0", b"")
            return real(vault, args, **options)

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]),
                         ("committed_mismatch", "paths_differ"))
        self.assertEqual(report["undeclared"], 1)

    def test_content_written_after_the_hash_check_is_detected(self):
        # git commit -- PATH re-reads the working file, so a later writer's
        # bytes reach the commit. The expected blob catches the sweep.
        self.write("notes/mine.md", b"mine v2\n")
        expected = self.entry("notes/mine.md")
        real = vault_commit._run_git

        def run(vault, args, **options):
            if args[0] == "commit":
                self.write("notes/mine.md", b"mine v2\nswept line\n")
            return real(vault, args, **options)

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([expected])
        self.assertEqual((report["status"], report["reason"]),
                         ("committed_mismatch", "content_differs"))
        self.assertEqual(report["content_mismatch"], 1)

    def test_commit_message_swap_is_detected(self):
        self.hook("commit-msg", b"echo swapped > \"$1\"")
        self.write("notes/mine.md", b"mine v2\n")
        report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]),
                         ("committed_mismatch", "message_differs"))

    def test_expected_blob_respects_line_ending_conversion(self):
        self.git("config", "core.autocrlf", "true")
        self.write("notes/crlf.md", b"line one\r\nline two\r\n")
        report = self.commit([self.entry("notes/crlf.md")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(self.show("HEAD:notes/crlf.md"), b"line one\nline two\n")

    def test_no_change_reports_nothing_to_commit(self):
        report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "nothing_to_commit"))

    def test_hook_rejection_keeps_the_gate_and_reports_left_staged(self):
        seen = []
        real = vault_commit._run_git

        def run(vault, args, **options):
            seen.append(list(args))
            return real(vault, args, **options)

        self.hook("pre-commit", b"echo refused >&2\nexit 1")
        self.write("notes/mine.md", b"mine v2\n")
        before = self.head()
        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "commit_rejected"))
        self.assertTrue(report["left_staged"])
        self.assertEqual(self.head(), before)
        self.assertEqual(self.staged(), ["notes/mine.md"])
        flat = [word for args in seen for word in args]
        self.assertNotIn("--no-verify", flat)
        self.assertNotIn("-n", flat)


class OwnLinesTests(VaultCommitCase):
    def edit(self, path, old, new):
        return {"path": path, "old": old, "new": new}

    def test_only_my_lines_reach_the_commit_and_the_worktree_is_untouched(self):
        working = b"# hot\nslot-a mine\nslot-b\nhalf writ"
        self.write(HOT, working)
        report = self.commit([self.entry(HOT)],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(self.show(f"HEAD:{HOT}"), b"# hot\nslot-a mine\nslot-b\n")
        self.assertEqual((self.vault / HOT).read_bytes(), working)
        self.assertEqual(self.changed("HEAD"), [HOT])
        self.assertEqual(self.staged(), [])
        self.assertEqual(self.show(f":{HOT}"), b"# hot\nslot-a mine\nslot-b\n")

    def test_a_later_plain_commit_keeps_my_lines(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\nother session\n")
        report = self.commit([self.entry(HOT)],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed")
        self.write("notes/other.md", b"other v2\n")
        self.git("add", "--", "notes/other.md")
        self.git("commit", "-q", "-m", "plain commit by another session")
        self.assertEqual(self.show(f"HEAD:{HOT}"), b"# hot\nslot-a mine\nslot-b\n")
        status = self.git("status", "--porcelain", "--", HOT).stdout
        self.assertEqual(status.strip(), f"M {HOT}".encode())

    def test_shared_and_ordinary_files_travel_together(self):
        self.write(INDEX, b"# index\n- new note\n- other session\n")
        self.write("notes/new.md", b"new\n")
        (self.vault / "notes/other.md").unlink()
        report = self.commit(
            [self.entry(INDEX), self.entry("notes/new.md"), ("notes/other.md", vault_commit.ABSENT)],
            edits=[self.edit(INDEX, "# index\n", "# index\n- new note\n")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(self.changed("HEAD"), sorted([INDEX, "notes/new.md", "notes/other.md"]))
        self.assertEqual(self.show(f"HEAD:{INDEX}"), b"# index\n- new note\n")
        self.assertEqual(self.staged(), [])
        self.assertEqual(self.show(":notes/new.md"), b"new\n")

    def test_moved_head_is_retried_on_the_new_head(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        real = vault_commit._run_git
        moved = []

        def run(vault, args, **options):
            if args[0] == "update-ref" and not moved:
                moved.append(1)
                self.write("notes/other.md", b"other session\n")
                self.git("commit", "-q", "-m", "other session", "--", "notes/other.md")
            return real(vault, args, **options)

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([self.entry(HOT)],
                                 edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(report["attempts"], 2)
        self.assertEqual(report["head_conflicts"], 1)
        self.assertEqual(self.show("HEAD:notes/other.md"), b"other session\n")
        self.assertEqual(self.show(f"HEAD:{HOT}"), b"# hot\nslot-a mine\nslot-b\n")
        self.assertEqual(self.git("rev-parse", "HEAD~1^{commit}").returncode, 0)
        self.assertEqual(self.changed("HEAD"), [HOT])

    def test_head_conflicts_stay_inside_the_limit(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        real = vault_commit._run_git
        counter = []

        def run(vault, args, **options):
            if args[0] == "update-ref":
                counter.append(1)
                self.git("commit", "-q", "--allow-empty", "-m", f"tick {len(counter)}")
            return real(vault, args, **options)

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([self.entry(HOT)], max_attempts=3,
                                 edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "head_conflict_timeout"))
        self.assertEqual(len(counter), 3)

    def test_pre_commit_gate_runs_on_the_proposed_tree(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        self.hook("pre-commit", b'git diff --cached --name-only | grep -q hot.md || exit 3\nexit 1')
        before = self.head()
        report = self.commit([self.entry(HOT)],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "commit_rejected"))
        self.assertEqual(self.head(), before)
        self.assertEqual(self.staged(), [])

    def test_passing_gate_allows_the_commit(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        self.hook("pre-commit", b"git diff --cached --name-only | grep -q hot.md")
        report = self.commit([self.entry(HOT)],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed")

    def test_other_sessions_stage_on_the_same_file_is_counted(self):
        self.write(HOT, b"# hot\nslot-a\nslot-b\nstaged by other\n")
        self.git("add", "--", HOT)
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\nstaged by other\n")
        report = self.commit([self.entry(HOT)],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(report["staged_replaced"], 1)
        self.assertEqual(self.show(f":{HOT}"), b"# hot\nslot-a mine\nslot-b\n")
        self.assertIn(b"staged by other", (self.vault / HOT).read_bytes())

    def test_other_staged_paths_survive(self):
        self.write("notes/other.md", b"staged by other\n")
        self.git("add", "--", "notes/other.md")
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        report = self.commit([self.entry(HOT)],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed")
        self.assertEqual(self.changed("HEAD"), [HOT])
        self.assertEqual(self.staged(), ["notes/other.md"])

    def test_refusals(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        self.write("notes/mine.md", b"mine v2\n")
        before = self.head()
        cases = (
            ([self.entry("notes/mine.md")], self.edit("notes/mine.md", "mine", "yours"),
             "own_lines_not_shared"),
            ([self.entry("notes/mine.md")], self.edit(HOT, "slot-a\n", "slot-a mine\n"),
             "own_lines_undeclared"),
            ([self.entry(HOT)], self.edit(HOT, "missing\n", "slot-a mine\n"),
             "patch_anchor_not_unique"),
            ([self.entry(HOT)], self.edit(HOT, "slot", "slot-a mine\n"),
             "patch_anchor_not_unique"),
            ([self.entry(HOT)], self.edit(HOT, "slot-a\n", "not in the worktree\n"),
             "patch_not_in_worktree"),
            ([self.entry(HOT)], self.edit(HOT, "", "slot-a mine\n"), "invalid_patch"),
            ([self.entry(HOT)], {"path": HOT, "old": "x"}, "invalid_patch"),
            ([self.entry(HOT)], "not an object", "invalid_patch"),
        )
        for entries, edit, reason in cases:
            with self.subTest(reason=reason, edit=edit):
                report = self.commit(entries, edits=[edit])
                self.assertEqual((report["status"], report["reason"]), ("refused", reason))
                self.assertEqual(self.head(), before)
                self.assertEqual(self.staged(), [])

    def test_shared_file_without_a_patch_is_refused_in_own_lines_mode(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        self.write(LOG, b"# log\nmine\n")
        report = self.commit([self.entry(HOT), self.entry(LOG)],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "shared_file_refused"))

    def test_working_hash_still_applies(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        report = self.commit([(HOT, sha(b"stale"))],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "hash_mismatch"))

    def test_merge_in_progress_is_refused(self):
        (self.git_dir / "MERGE_HEAD").write_bytes(self.head().encode() + b"\n")
        self.addCleanup(lambda: (self.git_dir / "MERGE_HEAD").unlink())
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        report = self.commit([self.entry(HOT)],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "operation_in_progress"))

    def test_lock_on_the_shared_index_is_reported_after_the_commit(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        lock = self.git_dir / "index.lock"
        lock.write_bytes(b"")
        self.addCleanup(lambda: lock.exists() and lock.unlink())
        report = self.commit([self.entry(HOT)], max_attempts=2,
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]),
                         ("committed_index_stale", "shared_index_not_updated"))
        self.assertEqual(report["commit"], self.head())
        self.assertTrue(lock.exists())
        self.assertTrue(report["lock"]["present"])

    def test_ignored_new_file_is_not_forced_in(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        self.write("notes/key.secret", b"synthetic\n")
        before = self.head()
        report = self.commit([self.entry(HOT), self.entry("notes/key.secret")],
                             edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "add_rejected"))
        self.assertEqual(self.head(), before)

    def test_index_follows_a_head_that_moves_during_the_sync(self):
        # A slower writer must not leave its older blob behind a newer commit.
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\nnewer writer\n")
        real = vault_commit._run_git
        newer = b"# hot\nslot-a mine\nslot-b\nnewer writer\n"
        moved = []

        def run(vault, args, **options):
            result = real(vault, args, **options)
            if args[:2] == ["update-index", "-z"] and not options.get("env") and not moved:
                moved.append(1)
                scratch = {"GIT_INDEX_FILE": str(self.git_dir / "test-scratch")}
                blob = self.git("hash-object", "-w", "--stdin", input=newer)
                oid = blob.stdout.decode().strip()
                self.git("read-tree", "HEAD", env=scratch)
                self.git("update-index", "--cacheinfo", f"100644,{oid},{HOT}", env=scratch)
                tree = self.git("write-tree", env=scratch).stdout.decode().strip()
                commit = self.git("commit-tree", tree, "-p", "HEAD", "-m", "newer writer")
                self.git("update-ref", "HEAD", commit.stdout.decode().strip())
            return result

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([self.entry(HOT)],
                                 edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show(f":{HOT}"), newer)
        self.assertEqual(self.staged(), [])

    def test_scratch_index_is_removed(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        self.commit([self.entry(HOT)], edits=[self.edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(list(self.git_dir.glob("vault-commit-*")), [])

    def test_unborn_branch_is_refused(self):
        fresh = Path(self.temp.name).resolve() / "fresh"
        (fresh / "00-meta").mkdir(parents=True)
        config = json.dumps({"required_keys": ["title"], "enums": {}, "hot_note": HOT}).encode()
        (fresh / "00-meta/vault-config.json").write_bytes(config)
        (fresh / HOT).write_bytes(b"# hot\n")
        subprocess.run(["git", "init", "-q"], cwd=fresh, check=True)
        report = vault_commit.commit_paths(
            fresh, [(HOT, sha(b"# hot\n"))], "m", "a", sleep=always_retry_sleep,
            edits=[self.edit(HOT, "# hot\n", "# hot\n")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "no_head"))


def edit(path, old, new):
    return {"path": path, "old": old, "new": new}


class LinkedWorktreeTests(VaultCommitCase):
    """Nothing under a linked working tree is a path of this vault."""

    TREE = ".claude/worktrees/s1"

    def setUp(self):
        super().setUp()
        self.git("worktree", "add", "-q", "--detach", self.TREE)

    def assert_refused(self, report, before):
        self.assertEqual((report["status"], report["reason"]), ("refused", "denied_path"))
        self.assertEqual(self.head(), before)
        self.assertEqual(self.staged(), [])
        tree = self.git("ls-tree", "-r", "--name-only", "HEAD", ".claude").stdout
        self.assertEqual(tree, b"")

    def test_paths_in_a_linked_tree_are_refused_in_both_modes(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        before = self.head()
        for relative in (f"{self.TREE}/90-assets/scan.md", f"{self.TREE}/notes/mine.md",
                         f"{self.TREE}/{LOG}"):
            self.write(relative, b"copy text\n")
            with self.subTest(relative=relative, mode="path"):
                report = self.commit([self.entry(relative)])
                self.assert_refused(report, before)
                self.assertEqual(report["path_index"], 0)
            with self.subTest(relative=relative, mode="own-lines"):
                report = self.commit([self.entry(HOT), self.entry(relative)],
                                     edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
                self.assert_refused(report, before)
                self.assertEqual(report["path_index"], 1)

    def test_a_case_variant_of_the_tree_is_refused(self):
        relative = f"{self.TREE}/notes/mine.md"
        variant = relative.upper()
        self.write(relative, b"copy text\n")
        if not (self.vault / variant).exists():
            self.skipTest("the file system tells case apart")
        before = self.head()
        self.assert_refused(self.commit([(variant, sha(b"copy text\n"))]), before)

    def test_the_patch_file_may_not_come_from_a_linked_tree(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        patch = json.dumps({"schema_version": 1,
                            "edits": [edit(HOT, "slot-a\n", "slot-a mine\n")]}).encode()
        before = self.head()
        for relative in (f"{self.TREE}/90-assets/p.json", f"{self.TREE}/notes/p.json"):
            with self.subTest(relative=relative):
                path = self.write(relative, patch)
                self.assert_refused(self.commit([self.entry(HOT)], patch_file=path), before)

    def test_a_vault_that_is_a_linked_tree_commits_its_own_files(self):
        tree = self.vault / self.TREE
        data = b"mine in the tree\n"
        (tree / "notes/mine.md").write_bytes(data)
        report = vault_commit.commit_paths(tree, [("notes/mine.md", sha(data))], "m", "a",
                                           sleep=always_retry_sleep)
        self.assertEqual(report["status"], "committed", report)


class RerunTests(VaultCommitCase):
    """A patch that already landed is refused, never applied a second time."""

    def setUp(self):
        super().setUp()
        self.write(LOG, b"# log\n- my line\n")
        self.patch = [edit(LOG, "# log\n", "# log\n- my line\n")]

    def blob(self):
        return self.show(f"HEAD:{LOG}")

    def test_the_same_patch_twice_is_refused(self):
        first = self.commit([self.entry(LOG)], edits=self.patch)
        self.assertEqual(first["status"], "committed")
        again = self.commit([self.entry(LOG)], edits=self.patch)
        self.assertEqual((again["status"], again["reason"]), ("refused", "already_applied"))
        self.assertEqual(again["path_index"], 0)
        self.assertEqual(self.head(), first["commit"])
        self.assertEqual(self.blob(), b"# log\n- my line\n")

    def test_retry_after_a_stale_index_is_refused(self):
        lock = self.git_dir / "index.lock"
        lock.write_bytes(b"")
        first = self.commit([self.entry(LOG)], edits=self.patch, max_attempts=2)
        lock.unlink()
        self.assertEqual(first["status"], "committed_index_stale")
        again = self.commit([self.entry(LOG)], edits=self.patch)
        self.assertEqual((again["status"], again["reason"]), ("refused", "already_applied"))
        self.assertEqual(self.head(), first["commit"])
        self.assertEqual(self.blob(), b"# log\n- my line\n")

    def test_rerun_after_a_death_between_the_ref_and_the_index_is_refused(self):
        real = vault_commit._run_git
        moved = []

        def run(vault, args, **options):
            if moved:
                raise OSError("the process ended here")
            result = real(vault, args, **options)
            if args[0] == "update-ref":
                moved.append(1)
            return result

        with mock.patch.object(vault_commit, "_run_git", run):
            first = self.commit([self.entry(LOG)], edits=self.patch)
        self.assertNotEqual(first["status"], "committed")
        again = self.commit([self.entry(LOG)], edits=self.patch)
        self.assertEqual((again["status"], again["reason"]), ("refused", "already_applied"))
        self.assertEqual(self.blob(), b"# log\n- my line\n")

    def test_a_replacing_edit_names_the_same_reason(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        patch = [edit(HOT, "slot-a\n", "slot-a mine\n")]
        self.assertEqual(self.commit([self.entry(HOT)], edits=patch)["status"], "committed")
        again = self.commit([self.entry(HOT)], edits=patch)
        self.assertEqual((again["status"], again["reason"]), ("refused", "already_applied"))

    def test_a_deleting_edit_names_the_same_reason(self):
        self.write(HOT, b"# hot\nslot-a\n")
        patch = [edit(HOT, "slot-b\n", "")]
        self.assertEqual(self.commit([self.entry(HOT)], edits=patch)["status"], "committed")
        self.assertEqual(self.show(f"HEAD:{HOT}"), b"# hot\nslot-a\n")
        again = self.commit([self.entry(HOT)], edits=patch)
        self.assertEqual((again["status"], again["reason"]), ("refused", "already_applied"))

    def test_an_edit_that_changes_nothing_is_refused(self):
        self.write(HOT, b"# hot\nslot-a\nslot-b\nmine\n")
        before = self.head()
        report = self.commit([self.entry(HOT)], edits=[edit(HOT, "slot-a\n", "slot-a\n")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "already_applied"))
        self.assertEqual(self.head(), before)

    def test_edits_cannot_chain_past_the_rerun_check(self):
        # The first run would land; a rerun would then pass each edit on its own.
        self.write(HOT, b"# hot\nslot-a\nsecond\nslot-b\n")
        report = self.commit([self.entry(HOT)], edits=[
            edit(HOT, "slot-a\n", "slot-a\nfirst\n"), edit(HOT, "first\n", "second\n")])
        self.assertEqual((report["status"], report["reason"]),
                         ("refused", "patch_anchor_not_unique"))

    def test_a_line_that_exists_elsewhere_can_still_be_added(self):
        self.write(HOT, b"# hot\nslot-a\nslot-b\nslot-a\n")
        report = self.commit([self.entry(HOT)], edits=[edit(HOT, "slot-b\n", "slot-b\nslot-a\n")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show(f"HEAD:{HOT}"), b"# hot\nslot-a\nslot-b\nslot-a\n")


class RemovedTextTests(VaultCommitCase):
    """Text taken out of HEAD and left in the working file would come back."""

    def setUp(self):
        super().setUp()
        self.write(LOG, b"# log\n- gone\n- keep\n")
        self.git("add", "--", LOG)
        self.git("commit", "-q", "-m", "lines", "--", LOG)

    def test_a_deletion_still_in_the_working_file_is_refused(self):
        before = self.head()
        report = self.commit([self.entry(LOG)], edits=[edit(LOG, "- gone\n", "")])
        self.assertEqual((report["status"], report["reason"]),
                         ("refused", "patch_not_in_worktree"))
        self.assertEqual(self.head(), before)

    def test_a_replaced_line_still_in_the_working_file_is_refused(self):
        self.write(LOG, b"# log\n- gone\n- keep\n- new\n")
        report = self.commit([self.entry(LOG)], edits=[edit(LOG, "- gone\n", "- new\n")])
        self.assertEqual((report["status"], report["reason"]),
                         ("refused", "patch_not_in_worktree"))

    def test_a_deletion_gone_from_the_working_file_lands(self):
        self.write(LOG, b"# log\n- keep\n- unfinished")
        report = self.commit([self.entry(LOG)], edits=[edit(LOG, "- gone\n", "")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show(f"HEAD:{LOG}"), b"# log\n- keep\n")
        self.assertEqual((self.vault / LOG).read_bytes(), b"# log\n- keep\n- unfinished")


class LineEndingTests(VaultCommitCase):
    CRLF = b"one\r\ntwo\r\n"

    def seed_crlf(self):
        self.write("notes/crlf.md", self.CRLF)
        self.git("add", "--", "notes/crlf.md")
        self.git("commit", "-q", "-m", "crlf blob", "--", "notes/crlf.md")

    def use_autocrlf(self):
        self.git("config", "core.autocrlf", "true")

    def use_text_auto(self):
        self.write(".gitattributes", b"*.md text=auto\n")
        self.git("add", "--", ".gitattributes")
        self.git("commit", "-q", "-m", "attributes", "--", ".gitattributes")

    def test_path_commit_of_a_crlf_blob_under_autocrlf_is_not_a_mismatch(self):
        self.seed_crlf()
        self.use_autocrlf()
        changed = self.CRLF + b"three\r\n"
        self.write("notes/crlf.md", changed)
        report = self.commit([self.entry("notes/crlf.md")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(report["content_mismatch"], 0)
        self.assertEqual(self.show("HEAD:notes/crlf.md"), changed)

    def test_path_commit_of_a_crlf_blob_under_text_auto_is_not_a_mismatch(self):
        self.seed_crlf()
        self.use_text_auto()
        changed = self.CRLF + b"three\r\n"
        self.write("notes/crlf.md", changed)
        report = self.commit([self.entry("notes/crlf.md")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show("HEAD:notes/crlf.md"), changed)

    def test_a_file_in_an_own_lines_commit_keeps_its_line_endings(self):
        self.seed_crlf()
        self.use_autocrlf()
        changed = self.CRLF + b"three\r\n"
        self.write("notes/crlf.md", changed)
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        report = self.commit([self.entry(HOT), self.entry("notes/crlf.md")],
                             edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show("HEAD:notes/crlf.md"), changed)

    def test_filters_still_run_where_git_add_runs_them(self):
        self.use_autocrlf()
        self.write("notes/new.md", b"new\r\nnote\r\n")
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        report = self.commit([self.entry(HOT), self.entry("notes/new.md")],
                             edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show("HEAD:notes/new.md"), b"new\nnote\n")

    def test_an_explicit_text_attribute_converts_a_crlf_blob(self):
        self.seed_crlf()
        self.write(".gitattributes", b"*.md text\n")
        self.git("add", "--", ".gitattributes")
        self.git("commit", "-q", "-m", "attributes", "--", ".gitattributes")
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        self.write("notes/crlf.md", self.CRLF + b"three\r\n")
        report = self.commit([self.entry(HOT), self.entry("notes/crlf.md")],
                             edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show("HEAD:notes/crlf.md"), b"one\ntwo\nthree\n")

    def test_a_file_rewritten_before_the_scratch_add_is_refused(self):
        self.write("notes/new.md", b"declared\n")
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        entries = [self.entry(HOT), self.entry("notes/new.md")]
        real = vault_commit._run_git
        before = self.head()

        def run(vault, args, **options):
            if args[0] == "add":
                self.write("notes/new.md", b"swept in by another writer\n")
            return real(vault, args, **options)

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit(entries, edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "hash_mismatch"))
        self.assertEqual(report["path_index"], 1)
        self.assertEqual(self.head(), before)

    def test_new_text_takes_the_line_ending_of_a_lf_blob(self):
        self.use_autocrlf()
        self.write(LOG, b"# log\r\n- mine\r\n")
        report = self.commit([self.entry(LOG)],
                             edits=[edit(LOG, "# log\n", "# log\r\n- mine\r\n")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show(f"HEAD:{LOG}"), b"# log\n- mine\n")

    def test_new_text_takes_the_line_ending_of_a_crlf_blob(self):
        self.write(LOG, b"# log\r\n- first\r\n")
        self.git("add", "--", LOG)
        self.git("commit", "-q", "-m", "crlf log", "--", LOG)
        self.write(LOG, b"# log\r\n- mine\r\n- first\r\n")
        patch = [edit(LOG, "# log\r\n", "# log\r\n- mine\n")]
        report = self.commit([self.entry(LOG)], edits=patch)
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show(f"HEAD:{LOG}"), b"# log\r\n- mine\r\n- first\r\n")
        again = self.commit([self.entry(LOG)], edits=patch)
        self.assertEqual((again["status"], again["reason"]), ("refused", "already_applied"))

    def test_a_mixed_blob_keeps_what_the_caller_wrote(self):
        self.write(LOG, b"# log\r\n- first\n")
        self.git("add", "--", LOG)
        self.git("commit", "-q", "-m", "mixed log", "--", LOG)
        self.write(LOG, b"# log\r\n- mine\r\n- first\n")
        report = self.commit([self.entry(LOG)],
                             edits=[edit(LOG, "# log\r\n", "# log\r\n- mine\r\n")])
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.show(f"HEAD:{LOG}"), b"# log\r\n- mine\r\n- first\n")


class ModeTests(VaultCommitCase):
    def test_own_lines_keeps_the_mode_of_every_declared_file(self):
        self.git("update-index", "--chmod=+x", "--", LOG)
        self.git("update-index", "--chmod=+x", "--", "notes/mine.md")
        self.git("commit", "-q", "-m", "executable")
        self.write(LOG, b"# log\n- mine\n")
        self.write("notes/mine.md", b"mine v2\n")
        report = self.commit([self.entry(LOG), self.entry("notes/mine.md")],
                             edits=[edit(LOG, "# log\n", "# log\n- mine\n")])
        self.assertEqual(report["status"], "committed", report)
        for relative in (LOG, "notes/mine.md"):
            with self.subTest(relative=relative):
                self.assertTrue(self.git("ls-tree", "HEAD", "--", relative).stdout
                                .startswith(b"100755 "))


class FailureReportTests(VaultCommitCase):
    """Hook output never decides what the report says."""

    def test_hook_text_is_not_a_lock_failure(self):
        lines = (b"echo nope index.lock", b"echo error: note index.lock is bad >&2",
                 b"echo did not match any file >&2", b"echo 'cannot lock ref' >&2")
        for number, line in enumerate(lines):
            with self.subTest(line=line):
                self.hook("pre-commit", line + b"\nexit 1")
                self.write("notes/mine.md", f"mine {number}\n".encode())
                report = self.commit([self.entry("notes/mine.md")])
                self.assertEqual((report["status"], report["reason"]),
                                 ("failed", "commit_rejected"))
                self.assertEqual(report["attempts"], 1)
                self.assertEqual(report["lock_failures"], 0)
                self.assertTrue(report["left_staged"])

    def test_hook_text_is_not_an_empty_commit(self):
        self.hook("pre-commit", b'echo "nothing to commit here"\nexit 1')
        self.write("notes/mine.md", b"mine v2\n")
        report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "commit_rejected"))
        self.assertTrue(report["left_staged"])
        self.assertEqual(self.staged(), ["notes/mine.md"])

    def test_a_real_empty_commit_is_still_named(self):
        report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "nothing_to_commit"))
        self.assertFalse(report["left_staged"])

    def test_hook_text_does_not_read_as_a_missing_hook_runner(self):
        self.hook("pre-commit", b'echo "not a git command" >&2\nexit 1')
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        report = self.commit([self.entry(HOT)], edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "commit_rejected"))

    def test_left_staged_reads_the_index_after_a_partial_add(self):
        self.write("notes/mine.md", b"mine v2\n")
        self.write("notes/key.secret", b"synthetic\n")
        report = self.commit([self.entry("notes/mine.md"), self.entry("notes/key.secret")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "add_rejected"))
        self.assertEqual(self.staged(), ["notes/mine.md"])
        self.assertTrue(report["left_staged"])

    def test_left_staged_is_false_when_the_add_changed_nothing(self):
        self.write("Cased/Alpha.md", b"alpha\n")
        self.git("add", "--", "Cased/Alpha.md")
        self.git("commit", "-q", "-m", "alpha", "--", "Cased/Alpha.md")
        if not (self.vault / "cased/alpha.md").exists():
            self.skipTest("the file system tells case apart")
        self.write("Cased/Alpha.md", b"alpha v2\n")
        report = self.commit([("cased/alpha.md", sha(b"alpha v2\n"))], max_attempts=2)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.staged(), [])
        self.assertFalse(report["left_staged"])

    def test_the_summary_line_is_the_last_one_that_matches(self):
        first, last = b"a" * 40, b"b" * 40
        output = (b"[decoy " + first + b"] printed by a hook\n[master " + last + b"] real\n"
                  b" 1 file changed\n")
        self.assertEqual(vault_commit._parse_commit_hash(output), last.decode())

    def test_an_unreadable_summary_is_reported_with_the_commit_kept(self):
        self.write("notes/mine.md", b"mine v2\n")
        real = vault_commit._run_git

        def run(vault, args, **options):
            result = real(vault, args, **options)
            if args[0] == "commit":
                return subprocess.CompletedProcess(args, 0, b"no summary here\n", b"")
            return result

        with mock.patch.object(vault_commit, "_run_git", run):
            report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]),
                         ("committed_mismatch", "commit_hash_unreadable"))
        self.assertFalse(report["left_staged"])


    def test_a_case_variant_cannot_add_a_second_entry_in_own_lines_mode(self):
        self.write("Cased/Alpha.md", b"alpha\n")
        self.git("add", "--", "Cased/Alpha.md")
        self.git("commit", "-q", "-m", "alpha", "--", "Cased/Alpha.md")
        if not (self.vault / "cased/alpha.md").exists():
            self.skipTest("the file system tells case apart")
        self.write("Cased/Alpha.md", b"alpha v2\n")
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        before = self.head()
        report = self.commit([self.entry(HOT), ("cased/alpha.md", sha(b"alpha v2\n"))],
                             edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "add_rejected"))
        self.assertEqual(self.head(), before)


class AliasTests(VaultCommitCase):
    """A second name for a file is not a declared path."""

    def short_name(self, relative):
        if os.name != "nt":
            self.skipTest("short names are a Windows feature")
        buffer = ctypes.create_unicode_buffer(1024)
        size = ctypes.windll.kernel32.GetShortPathNameW(str(self.vault / relative), buffer, 1024)
        name = os.path.basename(buffer.value) if size else ""
        if not name or name == os.path.basename(relative):
            self.skipTest("this volume makes no short names")
        return f"{os.path.dirname(relative)}/{name}"

    def test_a_resolved_name_that_differs_from_the_declared_one_is_refused(self):
        self.write("notes/mine.md", b"mine v2\n")
        other = (self.vault / "notes/other.md").resolve()
        before = self.head()
        real = vault_commit.resolve_note_path

        def resolve(vault, relative, denied):
            return other if relative == "notes/mine.md" else real(vault, relative, denied)

        with mock.patch.object(vault_commit, "resolve_note_path", resolve):
            report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "unsafe_path"))
        self.assertEqual(report["path_index"], 0)
        self.assertEqual(self.head(), before)

    def test_a_short_name_of_a_shared_note_is_refused_in_both_modes(self):
        relative = "00-meta/long shared handoff name.md"
        config = self.vault / "00-meta/vault-config.json"
        values = json.loads(config.read_bytes())
        values["handoff_note"] = relative
        config.write_bytes(json.dumps(values).encode())
        self.write(relative, b"# handoff\n")
        self.git("add", "--", relative)
        self.git("commit", "-q", "-m", "long name", "--", relative)
        alias = self.short_name(relative)
        data = b"# handoff\nedit\n"
        self.write(relative, data)
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        before = self.head()
        report = self.commit([(alias, sha(data))])
        self.assertEqual((report["status"], report["reason"]), ("refused", "unsafe_path"))
        report = self.commit([self.entry(HOT), (alias, sha(data))],
                             edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "unsafe_path"))
        self.assertEqual(self.head(), before)


class StateTests(VaultCommitCase):
    def conflict(self):
        self.write("notes/mine.md", b"stashed side\n")
        self.git("stash", "push", "-q")
        self.write("notes/mine.md", b"committed side\n")
        self.git("add", "--", "notes/mine.md")
        self.git("commit", "-q", "-m", "other side", "--", "notes/mine.md")
        self.git("stash", "pop", check=False)
        self.assertNotEqual(self.git("ls-files", "-u").stdout, b"")
        self.assertFalse(any((self.git_dir / marker).exists()
                             for marker in vault_commit.OPERATION_MARKERS))

    def test_an_unresolved_conflict_is_refused_in_both_modes(self):
        self.conflict()
        before = self.head()
        report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "unmerged_paths"))
        self.assertEqual(self.head(), before)
        self.assertNotEqual(self.git("ls-files", "-u").stdout, b"")
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        report = self.commit([self.entry(HOT)], edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
        self.assertEqual((report["status"], report["reason"]), ("refused", "unmerged_paths"))
        self.assertEqual(self.head(), before)

    def test_every_operation_marker_is_refused_in_both_modes(self):
        self.write("notes/new.md", b"new\n")
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        before = self.head()
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
            marker = self.git_dir / name
            marker.write_bytes(before.encode() + b"\n")
            self.addCleanup(lambda m=marker: m.exists() and m.unlink())
            with self.subTest(name=name):
                report = self.commit([self.entry("notes/new.md")])
                self.assertEqual((report["status"], report["reason"]),
                                 ("refused", "operation_in_progress"))
                report = self.commit([self.entry(HOT)],
                                     edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])
                self.assertEqual((report["status"], report["reason"]),
                                 ("refused", "operation_in_progress"))
            marker.unlink()
        self.assertEqual(self.head(), before)

    def test_nfc_and_nfd_spellings_are_one_path(self):
        nfc = unicodedata.normalize("NFC", "notes/caf" + chr(0xe9) + ".md")
        nfd = unicodedata.normalize("NFD", nfc)
        self.write(nfc, b"cafe\n")
        report = self.commit([(nfc, sha(b"cafe\n")), (nfd, sha(b"cafe\n"))])
        self.assertEqual((report["status"], report["reason"]), ("refused", "duplicate_path"))
        self.assertEqual(report["path_index"], 1)


class EnvironmentTests(VaultCommitCase):
    """A caller's environment cannot steer the commit away from this repository's gate."""

    def own_lines(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        return self.commit([self.entry(HOT)], edits=[edit(HOT, "slot-a\n", "slot-a mine\n")])

    def path_commit(self):
        self.write("notes/mine.md", b"mine v2\n")
        return self.commit([self.entry("notes/mine.md")])

    def test_config_variables_cannot_turn_the_hook_off(self):
        empty = Path(self.temp.name).resolve() / "no-hooks"
        empty.mkdir()
        cases = (
            {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
             "GIT_CONFIG_VALUE_0": str(empty)},
            {"GIT_CONFIG_PARAMETERS": f"'core.hooksPath={empty.as_posix()}'"},
        )
        self.hook("pre-commit", b"exit 1")
        for values in cases:
            for name, run in (("path", self.path_commit), ("own-lines", self.own_lines)):
                with self.subTest(mode=name, keys=sorted(values)):
                    before = self.head()
                    with mock.patch.dict(os.environ, values):
                        report = run()
                    self.assertEqual((report["status"], report["reason"]),
                                     ("failed", "commit_rejected"))
                    self.assertEqual(self.head(), before)
                    self.git("reset", "-q")

    def test_repository_variables_do_not_move_the_commit(self):
        other = Path(self.temp.name).resolve() / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=other, check=True)
        before = self.head()
        values = {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other),
                  "GIT_INDEX_FILE": str(other / "stray-index")}
        with mock.patch.dict(os.environ, values):
            report = self.path_commit()
        self.assertEqual(report["status"], "committed", report)
        self.assertEqual(self.git("rev-parse", "HEAD~1").stdout.decode().strip(), before)
        self.assertFalse((other / "stray-index").exists())

    def test_pathspec_variables_do_not_change_the_reading(self):
        for name in ("GIT_LITERAL_PATHSPECS", "GIT_ICASE_PATHSPECS"):
            with self.subTest(name=name):
                self.write("notes/mine.md", f"mine {name}\n".encode())
                with mock.patch.dict(os.environ, {name: "1"}):
                    report = self.commit([self.entry("notes/mine.md")])
                self.assertEqual(report["status"], "committed", report)

    def test_git_speaks_the_language_the_failure_codes_read(self):
        with mock.patch.dict(os.environ, {"LC_ALL": "ko_KR.UTF-8", "LANGUAGE": "ko"}):
            environment = vault_commit._git_environment({"GIT_INDEX_FILE": "scratch"})
        self.assertEqual((environment["LC_ALL"], environment["LANGUAGE"]), ("C", "C"))
        self.assertEqual(environment["GIT_INDEX_FILE"], "scratch")

    def test_git_runs_from_an_absolute_path(self):
        seen = []
        real = subprocess.run

        def run(command, **options):
            seen.append(command[0])
            return real(command, **options)

        with mock.patch.object(vault_commit.subprocess, "run", run):
            self.path_commit()
        self.assertTrue(seen)
        self.assertTrue(all(os.path.isabs(program) for program in seen), seen[:1])

    def test_a_missing_git_is_a_failure_code(self):
        self.write("notes/mine.md", b"mine v2\n")
        with mock.patch.object(vault_commit, "git_executable", lambda: None):
            report = self.commit([self.entry("notes/mine.md")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "git_unavailable"))


class EngineGateTests(VaultCommitCase):
    """The shipped pre-commit hook must still fire through both commit routes."""

    def setUp(self):
        super().setUp()
        repository = Path(__file__).resolve().parents[1]
        hooks = self.vault / "00-meta/scripts/git-hooks"
        hooks.mkdir(parents=True)
        for path in SCRIPTS.glob("*.py"):
            shutil.copy2(path, self.vault / "00-meta/scripts" / path.name)
        shutil.copy2(repository / "assets/git-hooks/pre-commit", hooks / "pre-commit")
        hook = hooks / "pre-commit"
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.write(".gitignore", b"*.secret\n__pycache__/\n00-meta/scripts/\n")
        self.git("add", "--", ".gitignore")
        self.git("commit", "-q", "-m", "ignore engine copy")
        self.git("config", "core.hooksPath", "00-meta/scripts/git-hooks")

    def test_path_mode_is_blocked_by_the_engine_gate(self):
        self.write("00-meta/no-frontmatter.md", b"plain body\n")
        before = self.head()
        report = self.commit([self.entry("00-meta/no-frontmatter.md")])
        self.assertEqual((report["status"], report["reason"]), ("failed", "commit_rejected"))
        self.assertEqual(self.head(), before)

    def test_own_lines_mode_is_blocked_by_the_engine_gate(self):
        self.write(INDEX, b"# index\n- new\n")
        self.write("00-meta/no-frontmatter.md", b"plain body\n")
        before = self.head()
        report = self.commit(
            [self.entry(INDEX), self.entry("00-meta/no-frontmatter.md")],
            edits=[{"path": INDEX, "old": "# index\n", "new": "# index\n- new\n"}])
        self.assertEqual((report["status"], report["reason"]), ("failed", "commit_rejected"))
        self.assertEqual(self.head(), before)
        self.assertEqual(self.staged(), [])

    def test_valid_note_passes_the_engine_gate(self):
        self.write("00-meta/good.md", b"---\ntitle: good\n---\nbody\n")
        report = self.commit([self.entry("00-meta/good.md")])
        self.assertEqual(report["status"], "committed", report)


class CliTests(VaultCommitCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = vault_commit.main(["--vault", str(self.vault), *args])
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_success_prints_one_json_object(self):
        self.write("notes/mine.md", b"mine v2\n")
        digest = sha(b"mine v2\n").upper()
        code, report, err = self.run_cli(
            "--path", f"notes/mine.md={digest}", "--message", "m", "--actor", "a")
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "committed")
        self.assertEqual(report["commit"], self.head())
        self.assertEqual(err, "")

    def test_equals_sign_in_a_path_splits_on_the_last_one(self):
        self.write("notes/a=b.md", b"x\n")
        digest = sha(b"x\n")
        code, report, _ = self.run_cli(
            "--path", f"notes/a=b.md={digest}", "--message", "m", "--actor", "a")
        self.assertEqual((code, report["status"]), (0, "committed"))

    def test_refusals_exit_one_without_the_path_or_body_in_the_report(self):
        digest = sha(b"x")
        code, report, err = self.run_cli(
            "--path", f"notes/secret-name.md={digest}", "--message", "m", "--actor", "a")
        self.assertEqual(code, 1)
        self.assertEqual(report["reason"], "hash_mismatch")
        self.assertNotIn("secret-name", json.dumps(report))
        self.assertEqual(err, "")

    def test_bad_arguments_exit_two(self):
        for arguments in (["--path", "no-separator", "--message", "m", "--actor", "a"],
                          ["--message", "m", "--actor", "a"],
                          ["--path", "a=b", "--message", "m", "--actor", "a", "--max-attempts", "x"],
                          ["--path", "a=b", "--path-file", "list", "--message", "m", "--actor", "a"],
                          ["--path", "a=b", "--message", "m", "--message=n", "--actor", "a"],
                          ["--path", "a=b", "--message", "m", "--act", "a"]):
            with self.subTest(arguments=arguments):
                code, report, _ = self.run_cli(*arguments)
                self.assertEqual(code, 2)
                self.assertEqual(report["reason"], "invalid_arguments")

    def test_no_option_reads_a_path_list_from_stdin_or_a_file(self):
        parser = vault_commit.build_parser()
        options = {action.dest for action in parser._actions}
        self.assertEqual(options, {"help", "vault", "paths", "message", "actor", "max_attempts",
                                   "max_delay_ms", "own_lines", "show_git_output"})

    def test_git_output_is_shown_only_on_request(self):
        self.hook("pre-commit", b"echo gate-detail >&2\nexit 1")
        self.write("notes/mine.md", b"mine v2\n")
        digest = sha(b"mine v2\n")
        arguments = ("--path", f"notes/mine.md={digest}",
                     "--message", "m", "--actor", "a")
        code, report, err = self.run_cli(*arguments)
        self.assertEqual((code, err), (1, ""))
        self.assertNotIn("gate-detail", json.dumps(report))
        code, report, err = self.run_cli(*arguments, "--show-git-output")
        self.assertEqual(code, 1)
        self.assertIn("gate-detail", err)
        self.assertNotIn("gate-detail", json.dumps(report))

    def test_own_lines_patch_file(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        patch = Path(self.temp.name).resolve() / "patch.json"
        patch.write_text(json.dumps({"schema_version": 1, "edits": [
            {"path": HOT, "old": "slot-a\n", "new": "slot-a mine\n"}]}), encoding="utf-8")
        code, report, _ = self.run_cli(
            "--path", "=".join(self.entry(HOT)),
            "--message", "m", "--actor", "a", "--own-lines", str(patch))
        self.assertEqual((code, report["status"]), (0, "committed"))
        self.assertEqual(self.show(f"HEAD:{HOT}"), b"# hot\nslot-a mine\nslot-b\n")

    def test_own_lines_patch_file_must_be_valid_and_outside_deny_zones(self):
        self.write(HOT, b"# hot\nslot-a mine\nslot-b\n")
        digest = sha((self.vault / HOT).read_bytes())
        inside = self.write("90-assets/patch.json", b'{"schema_version":1,"edits":[]}')
        bad = Path(self.temp.name).resolve() / "bad.json"
        bad.write_bytes(b'{"schema_version":2,"edits":[]}')
        for candidate, reason in ((inside, "denied_path"), (bad, "invalid_patch"),
                                  (bad.with_name("gone.json"), "invalid_patch")):
            with self.subTest(reason=reason):
                code, report, _ = self.run_cli(
                    "--path", f"{HOT}={digest}", "--message", "m", "--actor", "a",
                    "--own-lines", str(candidate))
                self.assertEqual(code, 1)
                self.assertEqual(report["reason"], reason)


class MultiProcessTests(VaultCommitCase):
    def spawn(self, *args):
        return subprocess.Popen(
            [sys.executable, "-B", str(SCRIPT), "--vault", str(self.vault), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def collect(self, processes):
        reports = []
        for process in processes:
            out, err = process.communicate(timeout=300)
            self.assertEqual(err, b"")
            report = json.loads(out)
            self.assertEqual(report["status"], "committed", report)
            self.assertEqual(process.returncode, 0)
            reports.append(report)
        return reports

    def test_concurrent_writers_lose_no_file_and_mix_no_paths(self):
        self.write("notes/other.md", b"staged by a bystander\n")
        self.git("add", "--", "notes/other.md")
        names = [f"notes/w{index}.md" for index in range(8)]
        for name in names:
            self.write(name, f"{name}\n".encode())
        processes = []
        for index, name in enumerate(names):
            digest = sha((self.vault / name).read_bytes())
            processes.append(self.spawn(
                "--path", f"{name}={digest}", "--message", f"add {name}",
                "--actor", f"writer-{index}", "--max-attempts", "400", "--max-delay-ms", "100"))
        reports = self.collect(processes)
        for name, report in zip(names, reports):
            self.assertEqual(self.changed(report["commit"]), [name])
            subject = self.git("log", "-1", "--format=%s", report["commit"]).stdout.decode().strip()
            self.assertEqual(subject, f"add {name}")
            self.assertEqual(self.show(f"HEAD:{name}"), f"{name}\n".encode())
        self.assertEqual(self.staged(), ["notes/other.md"])
        self.assertFalse((self.git_dir / "index.lock").exists())
        self.assertEqual(self.git("fsck", "--no-progress").returncode, 0)

    def test_concurrent_own_lines_writers_all_land(self):
        slots = [f"slot-{index}" for index in range(5)]
        self.write(LOG, ("# log\n" + "".join(f"{slot}\n" for slot in slots)).encode())
        self.git("add", "--", LOG)
        self.git("commit", "-q", "-m", "slots", "--", LOG)
        final = ("# log\n" + "".join(f"{slot} done\n" for slot in slots)).encode()
        self.write(LOG, final)
        digest = sha(final)
        scratch = Path(self.temp.name).resolve()
        processes = []
        for index, slot in enumerate(slots):
            patch = scratch / f"patch-{index}.json"
            patch.write_text(json.dumps({"schema_version": 1, "edits": [
                {"path": LOG, "old": f"{slot}\n", "new": f"{slot} done\n"}]}), encoding="utf-8")
            processes.append(self.spawn(
                "--path", f"{LOG}={digest}", "--message", f"fill {slot}",
                "--actor", f"writer-{index}", "--own-lines", str(patch),
                "--max-attempts", "400", "--max-delay-ms", "100"))
        self.collect(processes)
        self.assertEqual(self.show(f"HEAD:{LOG}"), final)
        self.assertEqual(self.show(f":{LOG}"), final)
        self.assertEqual(self.staged(), [])


if __name__ == "__main__":
    unittest.main()
