from __future__ import annotations

import importlib
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'agentic-vault' / 'scripts'
sys.path.insert(0, str(SCRIPTS))


class LintExtensionTests(unittest.TestCase):
    def analyzer(self):
        self.assertTrue((SCRIPTS / 'vault_lint_extensions.py').is_file(), 'warning analyzer is missing')
        return importlib.import_module('vault_lint_extensions').analyze_notes

    def test_missing_heading_and_block_have_source_locations_and_stable_ids(self):
        analyze = self.analyzer()
        notes = {'a.md': '[[b#Missing]]\n[[b#^absent]]', 'b.md': '# Present\nText ^block'}
        findings = analyze(notes, {})
        self.assertEqual([(x['code'], x['path'], x['line']) for x in findings],
                         [('missing-anchor', 'a.md', 1), ('missing-anchor', 'a.md', 2)])
        self.assertEqual([x['severity'] for x in findings], ['warning', 'warning'])
        self.assertTrue(all(x['issue_id'] for x in findings))
        self.assertEqual(findings, analyze(dict(reversed(list(notes.items()))), {}))

    def test_qualified_paths_distinguish_duplicates_and_do_not_guess(self):
        analyze = self.analyzer()
        notes = {'20-knowledge/source.md': '[[20-knowledge/a/Shared#One]]\n[[./b/Shared#Two]]\n[[Shared#One]]',
                 '20-knowledge/a/Shared.md': '# One', '20-knowledge/b/Shared.md': '# Two'}
        findings = analyze(notes, {})
        self.assertEqual([(x['code'], x['line']) for x in findings], [('ambiguous-link', 3)])
        self.assertEqual(findings[0]['details']['candidates'],
                         ['20-knowledge/a/Shared.md', '20-knowledge/b/Shared.md'])

    def test_wrong_qualified_path_does_not_resolve_by_basename(self):
        analyze = self.analyzer()
        findings = analyze({'a.md': '[[missing/b#Okay]]', 'actual/b.md': '# Okay'}, {})
        self.assertEqual([x['code'] for x in findings], ['missing-link-path'])

    def test_fenced_and_inline_examples_are_ignored_but_fake_headings_are_not_targets(self):
        analyze = self.analyzer()
        notes = {'a.md': '```md\n[[b#Absent]]\n```\n`[[b#Absent]]`\n[[b#Fake]]',
                 'b.md': '~~~~md\n# Fake\n~~~~\n# Real'}
        findings = analyze(notes, {})
        self.assertEqual([(x['code'], x['line']) for x in findings], [('missing-anchor', 5)])

    def test_unicode_heading_escaped_pipe_and_self_links_are_valid(self):
        analyze = self.analyzer()
        notes = {'한글.md': '# Cafe\u0301 👩‍💻 점검\n# A | B\n[[#Café 👩‍💻 점검]]\n[[#A \\| B|shown]]\nText ^한글-블록\n[[#^한글-블록]]'}
        self.assertEqual(analyze(notes, {}), [])

    def test_setext_headings_and_unclosed_fences_are_handled(self):
        analyze = self.analyzer()
        notes = {'a.md': '[[b#Setext]]\n[[b#Fake]]', 'b.md': 'Setext\n======\n```\n# Fake'}
        self.assertEqual([(x['code'], x['line']) for x in analyze(notes, {})], [('missing-anchor', 2)])

    def test_escaped_display_pipe_in_markdown_table_is_valid(self):
        analyze = self.analyzer()
        notes = {'a.md': '| Link |\n| --- |\n| [[b#Real\\|shown]] |', 'b.md': '# Real'}
        self.assertEqual(analyze(notes, {}), [])

    def test_invalid_empty_and_calendar_dates_warn_without_replacing_missing_key_gate(self):
        analyze = self.analyzer()
        notes = {'a.md': '---\ncreated: 2026-02-30\nupdated:\n---\nBody',
                 'b.md': '---\ncreated: "2024-02-29" # accepted\nupdated: 2026-10-07\n---\nBody',
                 'c.md': '---\ntitle: no dates\n---\nBody'}
        findings = analyze(notes, {})
        self.assertEqual([(x['code'], x['line'], x['details']['field']) for x in findings],
                         [('invalid-date', 2, 'created'), ('invalid-date', 3, 'updated')])

    def test_denied_excluded_and_exempt_notes_do_not_supply_anchor_evidence(self):
        analyze = self.analyzer()
        notes = {'a.md': '[[private/b#Hidden]]', 'private/b.md': '# Hidden\n---\nupdated: bad',
                 'scratch/raw.md': '---\nupdated: bad\n---', 'node_modules/skip.md': '[[missing/x]]'}
        config = {'deny_zones': ['private'], 'exclude_dirs': ['node_modules'],
                  'frontmatter_exempt_paths': ['scratch']}
        self.assertEqual(analyze(notes, config), [])

    def test_source_relative_denied_path_abstains_without_missing_path_warning(self):
        analyze = self.analyzer()
        notes = {'folder/a.md': '[[../20-knowledge/_archive/Secret#Hidden]]',
                 '20-knowledge/_archive/Secret.md': '# Hidden'}
        self.assertEqual(analyze(notes, {'deny_zones': ['20-knowledge/_archive']}), [])

    def test_engine_runtime_is_always_excluded_from_pure_note_inventory(self):
        analyze = self.analyzer()
        notes = {'a.md': '[[00-meta/.agentic-vault/runtime/state#Hidden]]',
                 '00-meta/.agentic-vault/runtime/state.md': '# Hidden\n[[#Missing]]'}
        self.assertEqual(analyze(notes, {'exclude_dirs': [], 'deny_zones': []}), [])

    def test_case_preserved_uppercase_markdown_target_is_resolved_by_qualified_path(self):
        analyze = self.analyzer()
        notes = {'a.md': '[[folder/Target.MD#Real]]\n[[folder/Target#Real]]\n[[folder/Target#Absent]]',
                 'folder/Target.MD': '# Real'}
        findings = analyze(notes, {})
        self.assertEqual([(x['code'], x['line']) for x in findings], [('missing-anchor', 3)])
        self.assertEqual(findings[0]['details']['target'], 'folder/Target.MD')

    def test_normalized_root_paths_abstain_for_denied_and_excluded_destinations(self):
        analyze = self.analyzer()
        notes = {'a.md': '[[x/../20-knowledge/_archive/Secret#Real]]\n[[x/../node_modules/Skip#Real]]'}
        config = {'deny_zones': ['20-knowledge/_archive'], 'exclude_dirs': ['node_modules']}
        self.assertEqual(analyze(notes, config), [])

    def test_warning_output_and_details_are_bounded_with_visible_truncation(self):
        analyze = self.analyzer()
        notes = {'a.md': '\n'.join('[[b#' + str(i) + 'x' * 1000 + ']]' for i in range(80)), 'b.md': '# Fine'}
        findings = analyze(notes, {'lint_max_warnings': 4})
        self.assertEqual(len(findings), 5)
        self.assertEqual(findings[-1]['code'], 'lint-incomplete')
        self.assertTrue(all(len(x['message']) <= 512 for x in findings))
        self.assertLess(len(json.dumps(findings)), 10000)

    def test_bundled_harmless_corpus_has_zero_findings(self):
        analyze = self.analyzer()
        corpus = json.loads((SCRIPTS / 'resources' / 'lint-benign-v1.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(corpus['cases']), 8)
        for case in corpus['cases']:
            with self.subTest(case=case['id']):
                self.assertEqual(analyze(case['notes'], case.get('config', {})), [])


class PromotionTests(unittest.TestCase):
    def policy_module(self):
        self.assertTrue((SCRIPTS / 'vault_warning_policy.py').is_file(), 'promotion validator is missing')
        return importlib.import_module('vault_warning_policy')

    def test_default_and_warning_levels_need_no_certificate(self):
        module = self.policy_module()
        self.assertTrue(module.validate_promotion({}, '', '')['valid'])
        self.assertTrue(module.validate_promotion({'levels': {'invalid-date': 'warning'}}, '', '')['valid'])

    def test_explicit_fatal_rejects_missing_or_stale_certificate(self):
        module = self.policy_module()
        for certificate in (None, {'false_positives': 0, 'checker_sha256': '0' * 64}):
            with self.subTest(certificate=certificate):
                result = module.validate_promotion({'levels': {'invalid-date': 'fatal'}, 'certificate': certificate},
                                                   module.checker_sha256(), module.corpus_sha256())
                self.assertFalse(result['valid'])
                self.assertEqual(result['promoted_codes'], [])

    def test_matching_replayed_zero_fp_certificate_promotes_only_its_codes(self):
        module = self.policy_module()
        certificate = module.build_promotion_certificate()
        result = module.validate_promotion({'levels': {'invalid-date': 'fatal'}, 'certificate': certificate},
                                           module.checker_sha256(), module.corpus_sha256())
        self.assertTrue(result['valid'], result)
        self.assertEqual(result['promoted_codes'], ['invalid-date'])
        self.assertEqual(certificate['false_positives'], 0)

    def test_self_asserted_counts_and_caller_hashes_cannot_authorize_promotion(self):
        module = self.policy_module()
        certificate = module.build_promotion_certificate()
        for mutation in ({'case_count': 0}, {'false_positives': False}, {'result_sha256': '0' * 64},
                         {'certified_codes': []}, {'checker_sha256': '1' * 64}):
            with self.subTest(mutation=mutation):
                changed = {**certificate, **mutation}
                result = module.validate_promotion({'levels': {'invalid-date': 'fatal'}, 'certificate': changed},
                                                   module.checker_sha256(), module.corpus_sha256())
                self.assertFalse(result['valid'])
        self.assertFalse(module.validate_promotion({'levels': {'invalid-date': 'fatal'}, 'certificate': certificate},
                                                    '2' * 64, module.corpus_sha256())['valid'])

    def test_unknown_codes_and_severities_fail_closed(self):
        module = self.policy_module()
        for levels in ({'made-up': 'fatal'}, {'invalid-date': 'error'}, [], None):
            with self.subTest(levels=levels):
                self.assertFalse(module.validate_promotion({'levels': levels}, '', '')['valid'])

    def test_actual_nonzero_replay_rejects_forged_zero_count(self):
        module = self.policy_module()
        with tempfile.TemporaryDirectory() as temporary:
            resource = Path(temporary) / 'corpus.json'
            cases = [{'id': str(i), 'notes': {'a.md': '[[#Missing]]'}} for i in range(8)]
            resource.write_text(json.dumps({'schema_version': 1, 'cases': cases}), encoding='utf-8')
            with mock.patch.object(module, 'RESOURCE', resource):
                certificate = module.build_promotion_certificate()
                self.assertEqual(certificate['false_positives'], 8)
                certificate['false_positives'] = 0
                result = module.validate_promotion({'levels': {'missing-anchor': 'fatal'}, 'certificate': certificate},
                                                   module.checker_sha256(), module.corpus_sha256())
                self.assertFalse(result['valid'])
                self.assertEqual(result['promoted_codes'], [])


class WarningCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Match healthcheck's resolved root when injecting synthetic walk errors.
        self.vault = Path(self.temp.name).resolve()
        self.git('init', '-q')
        self.config = {'required_keys': ['title'], 'enums': {}, 'deny_zones': ['private'],
                       'exclude_dirs': ['.git'], 'index_note': '', 'log_note': '', 'hot_note': '',
                       'handoff_note': '', 'ssot_note': '', 'rules_dir': '',
                       'health_report': '00-meta/health-report.md'}

    def write(self, path, content):
        target = self.vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')

    def git(self, *args):
        result = subprocess.run(['git', *args], cwd=self.vault, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def run_cli(self, *args):
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        return subprocess.run([sys.executable, str(SCRIPTS / 'vault_healthcheck.py'), '--vault',
                               str(self.vault), *args], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=30)

    def current_certificate_cli(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'vault_warning_policy.py'), '--certificate'],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_uppercase_qualified_and_extensionless_links_cannot_be_fatal_in_full_mode(self):
        self.config['warning_policy'] = {'levels': {'missing-link-path': 'fatal'},
                                          'certificate': self.current_certificate_cli()}
        self.write('20-knowledge/a.md', '---\ntitle: Source\n---\n[[20-knowledge/Target.MD#Real]]\n[[20-knowledge/Target#Real]]')
        self.write('20-knowledge/Target.MD', '---\ntitle: Target\n---\n# Real')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('[missing-link-path]', (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8'))

    def test_uppercase_qualified_and_extensionless_links_cannot_be_fatal_in_staged_mode(self):
        self.config['warning_policy'] = {'levels': {'missing-link-path': 'fatal'},
                                          'certificate': self.current_certificate_cli()}
        self.write('20-knowledge/a.md', '---\ntitle: Source\n---\n[[20-knowledge/Target.MD#Real]]\n[[20-knowledge/Target#Real]]')
        self.write('20-knowledge/Target.MD', '---\ntitle: Target\n---\n# Real')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        self.git('add', '00-meta/vault-config.json', '20-knowledge')
        result = self.run_cli('--staged')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('[missing-link-path]', result.stderr)

    def test_normalized_denied_and_excluded_links_cannot_be_fatal(self):
        self.config['deny_zones'] = ['20-knowledge/_archive']
        self.config['exclude_dirs'].append('node_modules')
        self.config['warning_policy'] = {'levels': {'missing-link-path': 'fatal'},
                                          'certificate': self.current_certificate_cli()}
        self.write('20-knowledge/a.md', '---\ntitle: Source\n---\n[[x/../20-knowledge/_archive/Secret#Real]]\n[[x/../node_modules/Skip#Real]]')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('[missing-link-path]', (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8'))

    def test_refused_collected_file_or_tree_disables_every_promotion(self):
        health = importlib.import_module('vault_healthcheck')
        self.config['warning_policy'] = {'levels': {'missing-link-path': 'fatal', 'invalid-date': 'fatal'},
                                          'certificate': self.current_certificate_cli()}
        for refused in ('b.md', 'unsafe'):
            with self.subTest(refused=refused):
                self.write('20-knowledge/a.md', '---\ntitle: Source\nupdated: bad\n---\n[[20-knowledge/unsafe/b#Real]]')
                self.write('20-knowledge/unsafe/b.md', '---\ntitle: Target\n---\n# Real')
                self.write('00-meta/vault-config.json', json.dumps(self.config))
                original = health._ensure_vault_path
                def ensure(vault, path, *args, **kwargs):
                    if path.name == refused:
                        raise health.HealthcheckError('simulated unsafe original')
                    return original(vault, path, *args, **kwargs)
                with mock.patch.object(health, '_ensure_vault_path', side_effect=ensure), \
                        mock.patch.object(sys, 'argv', ['healthcheck', '--vault', str(self.vault)]), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    result = health.main()
                self.assertEqual(result, 0)
                report = (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8')
                self.assertIn('[lint-incomplete]', report)
                self.assertNotIn('[missing-link-path]', report)
                self.assertIn('warning: 20-knowledge/a.md:3 [invalid-date]', report)

    def test_allowed_legacy_exempt_original_supplies_target_evidence_only(self):
        self.config['warning_policy'] = {'levels': {'missing-link-path': 'fatal', 'missing-anchor': 'fatal'},
                                          'certificate': self.current_certificate_cli()}
        self.write('20-knowledge/a.md', '---\ntitle: Source\n---\n[[docs/README#Real]]')
        self.write('docs/README.md', '# Real\n[[#Missing]]')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8')
        self.assertNotIn('[missing-link-path]', report)
        self.assertNotIn('[missing-anchor]', report)

    def test_legacy_exempt_target_evidence_read_is_bounded(self):
        health = importlib.import_module('vault_healthcheck')
        self.config['warning_policy'] = {'levels': {'missing-link-path': 'fatal', 'invalid-date': 'fatal'},
                                          'certificate': self.current_certificate_cli()}
        self.write('20-knowledge/a.md', '---\ntitle: Source\nupdated: bad\n---\n[[docs/README#Real]]')
        self.write('docs/README.md', '# Real\n' + 'x' * 1_000_001)
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        original = Path.open
        reads = []
        class CheckedReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def read(self, size=-1):
                self_case.assertGreater(size, 0, 'new target evidence read must be bounded')
                self_case.assertLessEqual(size, 1_000_001)
                reads.append(size)
                return self.stream.read(size)
        self_case = self
        def open_checked(path, *args, **kwargs):
            stream = original(path, *args, **kwargs)
            return CheckedReader(stream) if path.name == 'README.md' else stream
        with mock.patch.object(Path, 'open', open_checked), \
                mock.patch.object(sys, 'argv', ['healthcheck', '--vault', str(self.vault)]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = health.main()
        self.assertEqual(result, 0)
        self.assertEqual(len(reads), 1)
        report = (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8')
        self.assertIn('[lint-incomplete]', report)
        self.assertNotIn('[missing-link-path]', report)

    def test_walk_errors_disable_promotion_only_for_allowed_inventory(self):
        health = importlib.import_module('vault_healthcheck')
        self.config['exclude_dirs'].append('node_modules')
        self.config['warning_policy'] = {'levels': {'invalid-date': 'fatal'},
                                          'certificate': self.current_certificate_cli()}
        self.write('20-knowledge/a.md', '---\ntitle: Source\nupdated: bad\n---')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        for path, expected_exit in [('20-knowledge/unlisted-tree', 0), ('private/unlisted-tree', 1),
                                    ('node_modules/unlisted-tree', 1)]:
            with self.subTest(path=path):
                original = health.os.walk
                def walk(root, *args, **kwargs):
                    callback = kwargs.get('onerror')
                    if callback is not None:
                        callback(PermissionError(13, 'simulated inaccessible directory', str(self.vault / path)))
                    return original(root, *args, **kwargs)
                with mock.patch.object(health.os, 'walk', side_effect=walk), \
                        mock.patch.object(sys, 'argv', ['healthcheck', '--vault', str(self.vault)]), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    result = health.main()
                self.assertEqual(result, expected_exit)
                report = (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8')
                self.assertEqual('[lint-incomplete]' in report, expected_exit == 0)

    def test_full_warnings_report_locations_and_preserve_default_exit(self):
        self.write('20-knowledge/a.md', '---\ntitle: Test\ncreated: bad\n---\n[[#Missing]]')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8')
        self.assertIn('## 15.', report)
        self.assertIn('invalid-date', report)
        self.assertIn('missing-anchor', report)
        self.assertIn('20-knowledge/a.md:3', report)
        self.assertIn('"anchor": "Missing"', report)

    def test_staged_warnings_read_index_originals_and_do_not_write_report(self):
        self.write('20-knowledge/a.md', '---\ntitle: Test\nupdated: bad\n---\n[[b#Missing]]')
        self.write('20-knowledge/b.md', '---\ntitle: Target\n---\n# Real')
        self.write('private/secret.md', '---\ntitle: Denied\nupdated: bad\n---\n[[#Missing]]')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        self.git('add', '00-meta/vault-config.json', '20-knowledge', 'private')
        self.write('20-knowledge/a.md', '---\ntitle: Dirty\nupdated: 2026-10-07\n---')
        self.write('20-knowledge/b.md', '---\ntitle: Dirty\n---\n# Missing')
        result = self.run_cli('--staged')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('invalid-date', result.stderr)
        self.assertIn('missing-anchor', result.stderr)
        self.assertNotIn('private/secret', result.stderr)
        self.assertFalse((self.vault / '00-meta/health-report.md').exists())

    def test_matching_certificate_makes_promoted_staged_warning_fatal(self):
        module = importlib.import_module('vault_warning_policy')
        self.config['warning_policy'] = {'levels': {'invalid-date': 'fatal'},
                                          'certificate': module.build_promotion_certificate()}
        self.write('20-knowledge/a.md', '---\ntitle: Test\nupdated: bad\n---')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        self.git('add', '00-meta/vault-config.json', '20-knowledge')
        result = self.run_cli('--staged')
        self.assertEqual(result.returncode, 1)
        self.assertIn('invalid-date', result.stderr)

    def test_forged_promotion_config_is_rejected_before_escalation(self):
        self.config['warning_policy'] = {'levels': {'invalid-date': 'fatal'},
                                          'certificate': {'false_positives': 0}}
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn('promotion', result.stderr)

    def test_legacy_fatal_frontmatter_check_remains_fatal(self):
        self.write('20-knowledge/a.md', 'Plain content')
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)

    def test_single_engine_legacy_install_preserves_fatal_checks_and_rejects_promotion(self):
        copied = self.vault / '00-meta/scripts/vault_healthcheck.py'
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPTS / 'vault_healthcheck.py', copied)
        self.write('20-knowledge/a.md', 'Plain content')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        result = subprocess.run([sys.executable, str(copied), '--vault', str(self.vault)],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Traceback', result.stderr)
        report = self.vault / '00-meta/health-report.md'
        self.assertTrue(report.exists())
        self.assertIn('lint-incomplete', report.read_text(encoding='utf-8'))
        self.config['warning_policy'] = {'levels': {'invalid-date': 'fatal'}}
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        result = subprocess.run([sys.executable, str(copied), '--vault', str(self.vault)],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn('promotion', result.stderr)

    def test_single_engine_warning_levels_are_nonfatal_when_checks_unavailable(self):
        copied = self.vault / '00-meta/scripts/vault_healthcheck.py'
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPTS / 'vault_healthcheck.py', copied)
        self.config['warning_policy'] = {'levels': {'invalid-date': 'warning'}}
        self.write('20-knowledge/a.md', '---\ntitle: Fine\n---')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        result = subprocess.run([sys.executable, str(copied), '--vault', str(self.vault)],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('lint-incomplete', (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8'))

    def test_unreadable_full_target_is_unknown_and_cannot_trigger_promotion(self):
        health = importlib.import_module('vault_healthcheck')
        module = importlib.import_module('vault_warning_policy')
        self.config['warning_policy'] = {'levels': {'missing-link-path': 'fatal'},
                                          'certificate': module.build_promotion_certificate()}
        self.write('20-knowledge/a.md', '---\ntitle: Fine\n---\n[[20-knowledge/b#Real]]')
        self.write('20-knowledge/b.md', '---\ntitle: Target\n---\n# Real')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        original = health.read_note_text
        def read(path, *args):
            return (None, 'simulated unreadable') if path.name == 'b.md' else original(path, *args)
        with mock.patch.object(health, 'read_note_text', side_effect=read), \
                mock.patch.object(sys, 'argv', ['healthcheck', '--vault', str(self.vault)]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = health.main()
        self.assertEqual(result, 0)
        report = (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8')
        self.assertIn('lint-incomplete', report)
        self.assertNotIn('[missing-link-path]', report)

    def test_runtime_notes_are_excluded_from_full_scan_even_with_empty_exclusions(self):
        self.config['exclude_dirs'] = []
        self.config['deny_zones'] = []
        self.write('20-knowledge/a.md', '---\ntitle: Fine\n---')
        self.write('00-meta/.agentic-vault/runtime/state.md', 'no frontmatter [[#Missing]]')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('runtime/state.md', (self.vault / '00-meta/health-report.md').read_text(encoding='utf-8'))

    def test_runtime_notes_are_excluded_from_staged_gate_even_with_empty_exclusions(self):
        self.config['exclude_dirs'] = []
        self.config['deny_zones'] = []
        self.write('20-knowledge/a.md', '---\ntitle: Fine\n---')
        self.write('00-meta/.agentic-vault/runtime/state.md', 'no frontmatter [[#Missing]]')
        self.write('00-meta/vault-config.json', json.dumps(self.config))
        self.git('add', '00-meta/vault-config.json', '20-knowledge', '00-meta/.agentic-vault/runtime')
        result = self.run_cli('--staged')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('runtime/state.md', result.stderr)


if __name__ == '__main__':
    unittest.main()
