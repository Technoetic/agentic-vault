from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
DOCTOR = SCRIPTS / 'vault_doctor.py'


def settled_mtime(path):
    """Return a directory time that stays unchanged across two reads."""
    for _ in range(50):
        first = os.stat(path).st_mtime_ns
        time.sleep(0.005)
        if os.stat(path).st_mtime_ns == first:
            return first
    raise AssertionError('directory time did not settle')


def restore_mtime(path, mtime_ns):
    """Put a directory time back and require it to stay there briefly."""
    for _ in range(50):
        os.utime(path, ns=(mtime_ns, mtime_ns))
        time.sleep(0.002)
        if os.stat(path).st_mtime_ns == mtime_ns:
            time.sleep(0.005)
            if os.stat(path).st_mtime_ns == mtime_ns:
                return
    raise AssertionError('directory time could not be restored')


class InstructionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve()
        self.config = {'deny_zones': ['private'], 'exclude_dirs': ['.git'],
                       'hot_note': '', 'handoff_note': ''}

    def write(self, relative, text):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode('utf8'))
        return path

    def module(self):
        self.assertTrue((SCRIPTS / 'vault_instruction_budget.py').is_file(),
                        'The new read-only instruction diagnostic is missing')
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        return importlib.import_module('vault_instruction_budget')

    def diagnose(self):
        return self.module().diagnose_instructions(self.vault, self.config)

    def paths(self, report):
        return {row['path']: row for row in report['files']}

    def test_recursive_repeated_import_counted_once_without_content_output(self):
        self.write('AGENTS.md', '@notes/shared.md\n@notes/shared.md\n')
        self.write('notes/shared.md', 'shared\n')
        report = self.diagnose()
        self.assertEqual(report['totals']['characters'], 41)
        self.assertEqual(report['totals']['unique_files'], 2)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['runtime_loading'], 'unverified')
        self.assertNotIn('shared\\n', json.dumps(report))

    def test_agents_preferred_over_legacy_claude(self):
        self.write('AGENTS.md', 'primary\n')
        self.write('CLAUDE.md', 'legacy should not be included\n')
        report = self.diagnose()
        self.assertEqual(report['entrypoint'], 'AGENTS.md')
        self.assertEqual(set(self.paths(report)), {'AGENTS.md'})
        self.assertEqual(report['totals']['characters'], 8)

    def test_legacy_claude_fallback_is_reported(self):
        self.write('CLAUDE.md', 'legacy\n')
        report = self.diagnose()
        self.assertEqual(report['entrypoint'], 'CLAUDE.md')
        self.assertEqual(report['totals']['characters'], 7)

    def test_nested_rules_distinguish_conditions_and_agent_candidates(self):
        self.write('AGENTS.md', 'root\n')
        self.write('.claude/rules/group/always.md', 'always\n')
        self.write('.claude/rules/group/python.md', '---\npaths:\n  - "**/*.py"\n---\nconditional\n')
        self.write('.agents/instructions/group/team.md', 'team\n')
        report = self.diagnose()
        paths = self.paths(report)
        self.assertEqual(paths['.claude/rules/group/always.md']['classification'], 'always')
        self.assertEqual(paths['.claude/rules/group/python.md']['classification'], 'conditional')
        self.assertEqual(paths['.agents/instructions/group/team.md']['classification'], 'candidate')
        self.assertEqual(report['totals']['by_classification']['candidate']['files'], 1)

    def test_imports_relative_to_source_and_ignore_examples_or_email(self):
        self.write('AGENTS.md', 'Read @notes/a.md\n`@missing.md`\nname@example.md\n```\n@absent.md\n```\n')
        self.write('notes/a.md', '@./sub/b.md\n')
        self.write('notes/sub/b.md', '@../end.md\n')
        self.write('notes/end.md', '끝\n')
        report = self.diagnose()
        self.assertEqual(set(self.paths(report)), {'AGENTS.md', 'notes/a.md', 'notes/sub/b.md', 'notes/end.md'})
        self.assertEqual(report['issues'], [])

    def test_cycle_reported_without_recounting_files(self):
        self.write('AGENTS.md', '@notes/a.md\n')
        self.write('notes/a.md', '@../AGENTS.md\n')
        report = self.diagnose()
        self.assertEqual(report['totals']['unique_files'], 2)
        self.assertIn('import_cycle', {row['code'] for row in report['issues']})

    def test_denied_secret_and_escaping_imports_never_opened(self):
        self.write('AGENTS.md', '@private/hidden.md\n@secrets/token.md\n@.env\n@../outside.md\n')
        self.write('private/hidden.md', 'FORBIDDEN_VALUE\n')
        self.write('secrets/token.md', 'SECRET_VALUE\n')
        self.write('.env', 'SECRET_ENV\n')
        module = self.module()
        real_open = os.open
        opened = []
        def observed_open(path, *args, **kwargs):
            opened.append(Path(path).name)
            return real_open(path, *args, **kwargs)
        with mock.patch('os.open', side_effect=observed_open):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertEqual(set(self.paths(report)), {'AGENTS.md'})
        self.assertNotIn('hidden.md', opened)
        self.assertNotIn('token.md', opened)
        self.assertNotIn('.env', opened)
        self.assertNotIn('SECRET', json.dumps(report))
        self.assertEqual(report['status'], 'partial')

    def test_denied_directory_not_enumerated(self):
        self.write('AGENTS.md', 'root\n')
        self.write('.claude/rules/private/hidden.md', 'FORBIDDEN\n')
        module = self.module()
        real_scandir = os.scandir
        enumerated = []
        def observed_scandir(path):
            enumerated.append(Path(path).name)
            return real_scandir(path)
        with mock.patch('os.scandir', side_effect=observed_scandir):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertNotIn('private', enumerated)
        self.assertNotIn('.claude/rules/private/hidden.md', self.paths(report))

    def test_hardlink_rejected(self):
        target = self.write('notes/original.md', 'linked\n')
        self.write('AGENTS.md', '@notes/linked.md\n')
        try:
            os.link(target, self.vault / 'notes/linked.md')
        except OSError as exc:
            self.skipTest(str(exc))
        report = self.diagnose()
        self.assertNotIn('notes/linked.md', self.paths(report))
        self.assertIn('unsafe_file', {row['code'] for row in report['issues']})

    def test_symlink_rejected_without_following(self):
        target = self.write('notes/original.md', 'linked\n')
        self.write('AGENTS.md', '@linked.md\n')
        try:
            (self.vault / 'linked.md').symlink_to(target)
        except OSError as exc:
            self.skipTest(str(exc))
        report = self.diagnose()
        self.assertNotIn('linked.md', self.paths(report))
        self.assertEqual(report['status'], 'partial')

    def test_limits_bound_files_and_bytes_with_partial_report(self):
        self.write('AGENTS.md', '@a.md\n@b.md\n@c.md\n')
        self.write('a.md', 'a\n')
        self.write('b.md', 'b\n')
        self.write('c.md', 'c\n')
        module = self.module()
        with mock.patch.object(module, 'MAX_FILES', 2):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertLessEqual(report['totals']['unique_files'], 2)
        self.assertEqual(report['status'], 'partial')
        with mock.patch.object(module, 'MAX_FILE_BYTES', 4):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertEqual(report['totals']['unique_files'], 0)
        self.assertIn('input_too_large', {row['code'] for row in report['issues']})

    def test_change_after_import_read_not_presented_as_complete(self):
        self.write('AGENTS.md', '@notes/a.md\n')
        self.write('notes/a.md', 'first\n')
        module = self.module()
        original_read = module.stable_read
        changed = False
        def changing_read(resolver, limit, **kwargs):
            nonlocal changed
            result = original_read(resolver, limit, **kwargs)
            if resolver().name == 'a.md' and not changed:
                changed = True
                self.write('AGENTS.md', 'changed\n')
            return result
        with mock.patch.object(module, 'stable_read', side_effect=changing_read):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertEqual(report['status'], 'partial')
        self.assertNotIn('AGENTS.md', self.paths(report))
        self.assertIn('file_changed_during_diagnosis', {row['code'] for row in report['issues']})

    def test_invalid_configuration_does_not_expose_untrusted_values(self):
        self.config['deny_zones'] = '../LEAK_VALUE'
        report = self.diagnose()
        self.assertEqual(report['status'], 'invalid_config')
        self.assertNotIn('LEAK_VALUE', json.dumps(report))

    def test_invalid_utf8_and_missing_import_reported_without_content(self):
        self.write('AGENTS.md', '@invalid.md\n@missing.md\n')
        self.write('invalid.md', b'\xff\x00')
        report = self.diagnose()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(set(self.paths(report)), {'AGENTS.md'})
        self.assertIn('invalid_encoding', {row['code'] for row in report['issues']})
        self.assertIn('missing_import', {row['code'] for row in report['issues']})

    def test_diagnosis_leaves_vault_files_and_config_unchanged(self):
        self.write('AGENTS.md', '한글😀\n')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        before = {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob('*') if p.is_file()}
        config_before = json.dumps(self.config, sort_keys=True)
        report = self.diagnose()
        after = {p.relative_to(self.vault).as_posix(): p.read_bytes() for p in self.vault.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(json.dumps(self.config, sort_keys=True), config_before)
        self.assertEqual(report['totals']['characters'], 4)

    def test_doctor_option_adds_instruction_diagnosis_only_when_requested(self):
        self.write('AGENTS.md', 'instruction\n')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        command = [sys.executable, str(DOCTOR), '--vault', str(self.vault), '--format', 'json']
        legacy = subprocess.run(command, capture_output=True, encoding='utf8', timeout=15)
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        self.assertNotIn('instructions', json.loads(legacy.stdout))
        opted = subprocess.run(command + ['--instructions'], capture_output=True, encoding='utf8', timeout=15)
        self.assertEqual(opted.returncode, 0, opted.stderr)
        report = json.loads(opted.stdout)
        self.assertEqual(report['instructions']['totals']['characters'], 12)
        self.assertEqual(report['instructions']['runtime_loading'], 'unverified')

    def test_denied_entrypoint_does_not_prevent_allowed_rules_inventory(self):
        self.write('AGENTS.md', 'FORBIDDEN\n')
        self.write('.claude/rules/allowed.md', 'rule\n')
        self.config['deny_zones'].append('AGENTS.md')
        report = self.diagnose()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(set(self.paths(report)), {'.claude/rules/allowed.md'})

    def test_overlapping_rule_roots_do_not_upgrade_conditional_classification(self):
        self.write('AGENTS.md', 'root\n')
        self.write('.claude/rules/group/python.md', '---\npaths: ["*.py"]\n---\npython\n')
        self.config['rules_dir'] = '.claude/rules/group'
        report = self.diagnose()
        self.assertEqual(self.paths(report)['.claude/rules/group/python.md']['classification'], 'conditional')

    def test_previously_walked_directory_changes_make_report_partial(self):
        self.write('AGENTS.md', 'root\n')
        self.write('.claude/rules/first.md', 'first\n')
        self.write('.agents/last.md', 'last\n')
        module = self.module()
        read = module.stable_read
        changed = False
        def changing_read(resolver, limit, **kwargs):
            nonlocal changed
            result = read(resolver, limit, **kwargs)
            if resolver().name == 'last.md' and not changed:
                changed = True
                self.write('.claude/rules/new.md', 'new\n')
            return result
        with mock.patch.object(module, 'stable_read', side_effect=changing_read):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertEqual(report['status'], 'partial')
        self.assertIn('directory_changed_during_diagnosis', {row['code'] for row in report['issues']})

    def test_windows_junction_directory_not_followed(self):
        if os.name != 'nt':
            self.skipTest('Windows junction integration')
        self.write('AGENTS.md', 'root\n')
        self.write('target/hidden.md', 'FORBIDDEN\n')
        parent = self.vault / '.claude/rules'
        parent.mkdir(parents=True)
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(parent / 'linked'), str(self.vault / 'target')],
                                capture_output=True, timeout=10)
        if result.returncode:
            self.skipTest('junction creation unavailable')
        report = self.diagnose()
        self.assertNotIn('.claude/rules/linked/hidden.md', self.paths(report))
        self.assertEqual(report['status'], 'partial')

    def test_doctor_config_change_during_diagnosis_not_silently_accepted(self):
        self.write('AGENTS.md', 'root\n')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        module = self.module()
        doctor = importlib.import_module('vault_doctor')
        original = module.diagnose_instructions
        def changed_config(vault, config):
            report = original(vault, config)
            self.write('00-meta/vault-config.json', json.dumps({**self.config, 'rules_dir': 'other'}))
            return report
        with mock.patch.object(module, 'diagnose_instructions', side_effect=changed_config):
            report, code = doctor.diagnose(self.vault, instructions=True)
        self.assertEqual(report['instructions']['status'], 'partial')
        self.assertEqual(code, 1)
        self.assertIn('configuration_changed', {row['code'] for row in report['instructions']['issues']})

    def test_known_secret_basenames_and_noninstruction_extensions_never_opened(self):
        names = ['secrets.json', 'credentials.json', 'token.json', 'service-account.json',
                 'auth-config.json', 'secrets.md', 'credentials.md', 'token.md',
                 'service-account.md', 'secret-config.md', 'auth-config.md']
        self.write('AGENTS.md', ''.join('@' + name + '\n' for name in names))
        for name in names:
            self.write(name, 'HIDDEN_CREDENTIAL_VALUE\n')
        module = self.module()
        real_open = os.open
        opened = []
        def observed_open(path, *args, **kwargs):
            opened.append(Path(path).name)
            return real_open(path, *args, **kwargs)
        with mock.patch('os.open', side_effect=observed_open):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertEqual(set(self.paths(report)), {'AGENTS.md'})
        self.assertFalse(set(names) & set(opened))
        self.assertNotIn('HIDDEN_CREDENTIAL_VALUE', json.dumps(report))

    def test_total_source_byte_bound_does_not_read_additional_rule(self):
        self.write('AGENTS.md', 'root\n')
        self.write('.claude/rules/extra.md', 'extra\n')
        module = self.module()
        with mock.patch.object(module, 'MAX_TOTAL_BYTES', 5):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertEqual(report['totals']['bytes'], 5)
        self.assertEqual(set(self.paths(report)), {'AGENTS.md'})
        self.assertIn('total_byte_limit', {row['code'] for row in report['issues']})

    def test_import_depth_and_count_bounds_report_unexamined_scope(self):
        self.write('AGENTS.md', '@a.md\n@b.md\n')
        self.write('a.md', '@c.md\n')
        self.write('b.md', 'b\n')
        self.write('c.md', 'c\n')
        module = self.module()
        with mock.patch.object(module, 'MAX_DEPTH', 0):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertEqual(set(self.paths(report)), {'AGENTS.md'})
        self.assertIn('import_depth_limit', {row['code'] for row in report['issues']})
        with mock.patch.object(module, 'MAX_IMPORTS_PER_FILE', 1):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertNotIn('b.md', self.paths(report))
        self.assertIn('import_count_limit', {row['code'] for row in report['issues']})

    def test_directory_entry_budget_stops_before_complete_inventory(self):
        self.write('AGENTS.md', 'root\n')
        self.write('.claude/rules/a.md', 'a\n')
        self.write('.claude/rules/b.md', 'b\n')
        module = self.module()
        with mock.patch.object(module, 'MAX_ENTRIES', 1):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertLessEqual(report['totals']['unique_files'], 2)
        self.assertIn('directory_entry_limit', {row['code'] for row in report['issues']})

    def test_quoted_import_preserves_spaces_without_external_path_echo(self):
        self.write('AGENTS.md', '@"notes/with spaces.md"\n@C:\\PRIVATE_VALUE\\outside.md\n')
        self.write('notes/with spaces.md', 'space\n')
        report = self.diagnose()
        self.assertIn('notes/with spaces.md', self.paths(report))
        self.assertNotIn('PRIVATE_VALUE', json.dumps(report))

    def test_quoted_yaml_paths_keys_are_conditional(self):
        self.write('AGENTS.md', 'root\n')
        for key in ['"paths"', "'paths'", '"pa\\u0074hs"']:
            with self.subTest(key=key):
                self.write('.claude/rules/scoped.md', '---\n' + key + ': ["**/*.py"]\n---\npython\n')
                report = self.diagnose()
                self.assertEqual(self.paths(report)['.claude/rules/scoped.md']['classification'], 'conditional')

    def test_unsupported_yaml_key_syntax_is_candidate_not_false_always(self):
        self.write('AGENTS.md', 'root\n')
        for header in ['"\\x70aths": ["*.py"]', '? paths\n: ["*.py"]', '<<: *scope']:
            with self.subTest(header=header):
                self.write('.claude/rules/scoped.md', '---\n' + header + '\n---\npython\n')
                report = self.diagnose()
                self.assertEqual(self.paths(report)['.claude/rules/scoped.md']['classification'], 'candidate')
                self.assertEqual(report['status'], 'partial')

    # Listing fingerprint (limit A11). The directory time is restored after
    # each entry change, reproducing a coarse or lagging Windows directory time.
    def listing_fixture(self):
        self.write('AGENTS.md', 'root\n')
        self.write('.claude/rules/first.md', 'first\n')
        self.write('.claude/rules/notes.txt', 'notes\n')
        self.write('.agents/last.md', 'last\n')
        return settled_mtime(self.vault / '.claude/rules')

    def listing_mutations(self):
        folder = self.vault / '.claude/rules'
        return {
            'add': lambda: self.write('.claude/rules/new.md', 'new\n'),
            'remove': lambda: (folder / 'notes.txt').unlink(),
            'rename': lambda: (folder / 'notes.txt').rename(folder / 'renamed.txt'),
        }

    def diagnose_with_change(self, mutate, mtime_ns):
        module = self.module()
        read = module.stable_read
        changed = False
        def changing_read(resolver, limit, **kwargs):
            nonlocal changed
            result = read(resolver, limit, **kwargs)
            if resolver().name == 'last.md' and not changed:
                changed = True
                mutate()
                restore_mtime(self.vault / '.claude/rules', mtime_ns)
            return result
        with mock.patch.object(module, 'stable_read', side_effect=changing_read):
            return module.diagnose_instructions(self.vault, self.config)

    def test_listing_change_behind_restored_directory_time_is_diagnosed(self):
        for kind in ('add', 'remove', 'rename'):
            with self.subTest(kind=kind):
                self.vault = Path(self.temp.name, kind).resolve()
                self.vault.mkdir()
                mtime_ns = self.listing_fixture()
                report = self.diagnose_with_change(self.listing_mutations()[kind], mtime_ns)
                # Stage 1: a listing-only difference is recorded, not enforced.
                self.assertEqual(report['status'], 'complete')
                self.assertEqual(report['issues'], [])
                self.assertEqual(report['listing_diagnostics'],
                                 [{'code': 'directory_listing_changed', 'path': '.claude/rules'}])

    def test_unchanged_listing_records_no_listing_diagnostic(self):
        self.listing_fixture()
        report = self.diagnose()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['listing_diagnostics'], [])

    def test_unreadable_or_over_cap_listing_at_finish_fails_closed(self):
        mtime_ns = self.listing_fixture()
        module = self.module()
        finish = module._Inventory.finish
        def unreadable(inventory):
            with mock.patch('os.scandir', side_effect=PermissionError('listing denied')):
                return finish(inventory)
        with mock.patch.object(module._Inventory, 'finish', unreadable):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertEqual(report['status'], 'partial')
        self.assertIn({'code': 'unreadable_instruction_directory', 'path': '.claude/rules'}, report['issues'])
        # The walk sees exactly three entries; one more at the recheck exceeds the cap.
        with mock.patch.object(module, 'MAX_ENTRIES', 3):
            self.assertEqual(module.diagnose_instructions(self.vault, self.config)['status'], 'complete')
            report = self.diagnose_with_change(lambda: self.write('.claude/rules/extra.txt', 'x\n'), mtime_ns)
        self.assertEqual(report['status'], 'partial')
        self.assertIn('directory_entry_limit', {row['code'] for row in report['issues']})

    def test_finish_lists_each_walked_directory_once(self):
        self.listing_fixture()
        module = self.module()
        finish = module._Inventory.finish
        real_scandir = os.scandir
        listed = []
        def counting_scandir(path, *args, **kwargs):
            listed.append(os.path.normcase(os.fspath(path)))
            return real_scandir(path, *args, **kwargs)
        def counted(inventory):
            with mock.patch('os.scandir', side_effect=counting_scandir):
                return finish(inventory)
        with mock.patch.object(module._Inventory, 'finish', counted):
            report = module.diagnose_instructions(self.vault, self.config)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(len(listed), 2)
        self.assertEqual(len(set(listed)), 2)

    def test_same_name_recreation_is_not_a_listing_change(self):
        # The name set cannot see a delete-and-recreate under the same name.
        # The finish rehash catches different bytes; identical bytes leave the
        # measured result unchanged and are documented as not reported.
        for data, expected in ((b'first\n', 'complete'), (b'FIRST!\n', 'partial')):
            with self.subTest(data=data):
                self.vault = Path(self.temp.name, expected).resolve()
                self.vault.mkdir()
                mtime_ns = self.listing_fixture()
                path = self.vault / '.claude/rules/first.md'
                def recreate():
                    times = path.stat()
                    path.unlink()
                    path.write_bytes(data)
                    os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
                report = self.diagnose_with_change(recreate, mtime_ns)
                self.assertEqual(report['status'], expected)
                self.assertEqual(report['listing_diagnostics'], [])
                codes = {row['code'] for row in report['issues']}
                self.assertEqual(codes, set() if expected == 'complete' else {'file_changed_during_diagnosis'})


if __name__ == '__main__':
    unittest.main()
