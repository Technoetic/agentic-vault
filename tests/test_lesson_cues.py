"""Lesson cues: the ledger compiler, the standalone hook and its adapter report."""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/agentic-vault/scripts'
sys.path.insert(0, str(SCRIPTS))
ASSETS = ROOT / 'assets/adapters/lesson-cues'
HOOK = ASSETS / 'lesson-cues.py'

import vault_adapters  # noqa: E402

HINT = {'kind': 'hint', 'text': 'A pipe hid the exit code in a past run', 'command_contains': [' | tail']}
GUARD = {'kind': 'guard', 'text': 'A paid call ran after a failed step', 'tools': ['Bash'],
         'command_prefix': ['paid-tool']}


def marker(identifier, cues=None, count=1, state='active'):
    record = {'id': identifier, 'count': count, 'state': state, 'references': []}
    if cues:
        record['cues'] = cues
    return ' <!-- agentic-vault:lesson ' + json.dumps(record, separators=(',', ':'), sort_keys=True) + ' -->'


def row(identifier, status, cues, count=1, state='active'):
    return (f'- 2026-10-01 | 횟수 {count} | [{identifier}] Lesson {identifier} | 근거: note | 상태: {status}'
            + marker(identifier, cues, count, state) + '\n')


def load_hook():
    spec = importlib.util.spec_from_file_location('lesson_cues_asset', HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / 'vault'
        self.write('00-meta/vault-config.json', json.dumps({'deny_zones': ['private'], 'exclude_dirs': ['excluded']}))

    def write(self, name, text):
        path = self.vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        return path

    def ledger(self, *rows):
        return self.write('00-meta/lessons.md', '---\ntitle: Lessons\n---\n## 대장\n' + ''.join(rows))


class BuildTests(VaultCase):
    def setUp(self):
        super().setUp()
        self.module = importlib.import_module('vault_lesson_cues')

    def test_only_rows_in_force_are_compiled_with_guards_first(self):
        self.ledger(row('L-001', '관찰중', [HINT], count=2),
                    row('L-002', '승격완료(검증중→규칙, 2026-10-01, abc)', [GUARD]),
                    row('L-003', '승격제안', [HINT]),
                    row('L-004', 'active', [HINT], count=5),
                    row('L-005', '철회', [HINT]), row('L-006', '후보', [HINT]), row('L-007', '기각', [HINT]),
                    row('L-008', '롤백(역효과)', [HINT]), row('L-009', '알 수 없음', [HINT]),
                    row('L-010', 'retired', [HINT], state='retired'),
                    '- 2026-10-01 | 횟수 1 | No cue row | 근거: x | 상태: 관찰중\n',
                    '- 2026-10-02 | 횟수 1 | Six cells | 근거: x | 상태: 관찰중 | 단서: y\n')
        result = self.module.build_cues(self.vault)
        self.assertEqual([cue['id'] for cue in result['document']['cues']], ['L-002', 'L-004', 'L-001', 'L-003'])
        first = result['document']['cues'][0]
        self.assertEqual(first, {'id': 'L-002', 'count': 1, **GUARD})
        self.assertEqual(result['compiled'], 4)
        self.assertEqual(result['skipped'], {'retired': 1, 'status': 5})
        self.assertEqual(result['ignored_rows'], 1)

    def test_a_heading_record_uses_its_marker_state(self):
        self.ledger('## L-020 — Heading lesson\n<!-- agentic-vault:lesson '
                    + json.dumps({'id': 'L-020', 'count': 2, 'state': 'active', 'references': [], 'cues': [HINT]})
                    + ' -->\n')
        self.assertEqual(self.module.build_cues(self.vault)['compiled'], 1)

    def test_build_is_a_dry_run_and_write_is_stable(self):
        target = self.ledger(row('L-001', '관찰중', [HINT]))
        before = target.read_bytes()
        result = self.module.build_cues(self.vault)
        output = self.vault / self.module.OUTPUT_REL
        self.assertFalse(output.exists())
        written = self.module.write_cues(self.vault)
        self.assertTrue(written['written'])
        first = output.read_bytes()
        self.module.write_cues(self.vault)
        self.assertEqual(output.read_bytes(), first)
        self.assertEqual(json.loads(first.decode('utf-8')), result['document'])
        self.assertEqual(target.read_bytes(), before)

    def test_invalid_ledger_is_a_fixed_code_and_nothing_is_written(self):
        self.ledger('- 2026-10-01 | 횟수 0 | x | 근거: y | 상태: 관찰중\n')
        with self.assertRaises(self.module.LessonCueError) as caught:
            self.module.write_cues(self.vault)
        self.assertEqual(str(caught.exception), 'invalid_lesson_counter')
        self.assertFalse((self.vault / self.module.OUTPUT_REL).exists())

    def test_denied_ledger_is_refused_without_its_path(self):
        self.write('private/lessons.md', '## 대장\n')
        with self.assertRaises(self.module.LessonCueError) as caught:
            self.module.build_cues(self.vault, 'private/lessons.md')
        self.assertNotIn('lessons', str(caught.exception))

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlinks are needed')
    def test_a_linked_output_is_refused(self):
        self.ledger(row('L-001', '관찰중', [HINT]))
        outside = Path(self.tmp.name) / 'outside.json'
        outside.write_text('keep\n', encoding='utf-8')
        try:
            (self.vault / self.module.OUTPUT_REL).symlink_to(outside)
        except OSError:
            self.skipTest('symlink creation is not allowed here')
        with self.assertRaises(self.module.LessonCueError):
            self.module.write_cues(self.vault)
        self.assertEqual(outside.read_text(encoding='utf-8'), 'keep\n')

    def test_cli_prints_ascii_json(self):
        self.ledger(row('L-001', '관찰중', [HINT]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.module.main(['--vault', str(self.vault), 'build']), 0)
        self.assertTrue(out.getvalue().isascii())
        self.assertEqual(json.loads(out.getvalue())['compiled'], 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.module.main(['--vault', str(self.vault), 'build', '--ledger', 'absent.md']), 2)
        self.assertNotIn('absent', out.getvalue())


class HookCase(VaultCase):
    def setUp(self):
        super().setUp()
        self.hook = load_hook()
        self.state = Path(self.tmp.name) / 'state'
        self.state.mkdir()

    def cues(self, *cues):
        document = {'version': 1, 'source': 'x', 'source_sha256': '0' * 64, 'cues': list(cues)}
        self.write('00-meta/lesson-cues.json', json.dumps(document))

    def entry(self, identifier, cue, count=1):
        return {'id': identifier, 'count': count, **cue}

    def payload(self, command=None, tool='Bash', session='s1', **fields):
        tool_input = dict(fields)
        if command is not None:
            tool_input['command'] = command
        return {'session_id': session, 'tool_name': tool, 'tool_input': tool_input, 'hook_event_name': 'PreToolUse'}

    def decide(self, payload):
        return self.hook.hook_output(*self.hook.select(
            self.hook.load_cues(str(self.vault)), payload['tool_name'], payload['tool_input'], str(self.vault),
            payload.get('session_id'), str(self.state)))


class MatchingTests(HookCase):
    def matches(self, cue, payload):
        return self.hook.cue_matches(cue, payload['tool_name'], payload['tool_input'], str(self.vault))

    def test_prefix_at_the_start_or_after_a_separator(self):
        cue = {'command_prefix': ['git commit']}
        for command in ('git commit -m x', 'cd x && git commit -m x', 'a; git commit', 'a | git commit', 'a\ngit commit',
                        '  GIT COMMIT'):
            with self.subTest(command=command):
                self.assertTrue(self.matches(cue, self.payload(command)))
        self.assertFalse(self.matches(cue, self.payload('echo git commit')))

    def test_contains_sees_text_inside_quotes(self):
        cue = {'command_contains': ['; paid']}
        self.assertTrue(self.matches(cue, self.payload('powershell -Command "a; paid"', tool='PowerShell')))

    def test_path_glob_uses_the_vault_relative_path_and_the_name(self):
        cue = {'path_glob': ['00-meta/*.md', '**/index.md', 'secret*.txt']}
        vault = str(self.vault)
        for path in (vault + '/00-meta/log.md', vault + '\\00-meta\\log.md', vault + '/a/b/index.md',
                     'index.md', 'x/secret-1.txt'):
            with self.subTest(path=path):
                self.assertTrue(self.matches(cue, self.payload(tool='Read', file_path=path)))
        self.assertFalse(self.matches(cue, self.payload(tool='Read', file_path=vault + '/notes/log.md')))

    def test_tools_restrict_a_cue_and_a_command_matcher_needs_a_command_tool(self):
        cue = {'tools': ['Bash'], 'command_contains': ['x']}
        self.assertFalse(self.matches(cue, self.payload('x', tool='PowerShell')))
        self.assertFalse(self.matches({'command_contains': ['x']}, self.payload('x', tool='Read')))
        self.assertFalse(self.matches({'command_contains': ['x']}, self.payload(tool='Monitor', ws='x')))

    def test_a_pattern_is_literal_not_a_regular_expression(self):
        self.assertFalse(self.matches({'command_contains': ['a.c']}, self.payload('abc')))
        self.assertFalse(self.matches({'command_prefix': ['a.*']}, self.payload('abc')))


class DecisionTests(HookCase):
    def test_a_hint_is_context_without_a_decision(self):
        self.cues(self.entry('L-001', HINT, count=3))
        result = self.decide(self.payload('make | tail -3'))['hookSpecificOutput']
        self.assertEqual(result['hookEventName'], 'PreToolUse')
        self.assertNotIn('permissionDecision', result)
        self.assertEqual(result['additionalContext'],
                         'Vault lesson record L-001, recorded 3 times: ' + HINT['text'])

    def test_a_guard_denies_once_per_session_and_never_allows(self):
        self.cues(self.entry('L-002', GUARD))
        first = self.decide(self.payload('paid-tool run'))['hookSpecificOutput']
        self.assertEqual(first['permissionDecision'], 'deny')
        self.assertTrue(first['permissionDecisionReason'].startswith('lesson-cues: '))
        self.assertIn(GUARD['text'], first['permissionDecisionReason'])
        self.assertIsNone(self.decide(self.payload('paid-tool run')))
        self.assertEqual(self.decide(self.payload('paid-tool run', session='s2'))['hookSpecificOutput']
                         ['permissionDecision'], 'deny')

    def test_a_hint_also_fires_once_per_session(self):
        self.cues(self.entry('L-001', HINT))
        self.assertIsNotNone(self.decide(self.payload('a | tail')))
        self.assertIsNone(self.decide(self.payload('b | tail')))

    def test_guards_come_first_and_keep_any_hint_as_context(self):
        self.cues(self.entry('L-001', {'kind': 'hint', 'text': 'h', 'command_contains': ['x']}),
                  self.entry('L-002', {'kind': 'guard', 'text': 'g', 'command_contains': ['x']}))
        result = self.decide(self.payload('x'))['hookSpecificOutput']
        self.assertEqual(result['permissionDecision'], 'deny')
        self.assertIn('L-002', result['permissionDecisionReason'])
        self.assertIn('L-001', result['additionalContext'])

    def test_per_call_budget_defers_the_rest_to_a_later_call(self):
        self.cues(*[self.entry(f'L-00{n}', {'kind': 'hint', 'text': f't{n}', 'command_contains': ['x']})
                    for n in range(1, 6)])
        first = self.decide(self.payload('x'))['hookSpecificOutput']['additionalContext']
        self.assertEqual(len(first.splitlines()), 3)
        second = self.decide(self.payload('x'))['hookSpecificOutput']['additionalContext']
        self.assertEqual([line.split()[3].rstrip(':') for line in second.splitlines()], ['L-004', 'L-005'])
        self.assertIsNone(self.decide(self.payload('x')))

    def test_character_budget_holds_and_a_skipped_cue_stays_unfired(self):
        long_text = 'x' * 900
        self.cues(self.entry('L-001', {'kind': 'hint', 'text': long_text, 'command_contains': ['q']}),
                  self.entry('L-002', {'kind': 'hint', 'text': long_text, 'command_contains': ['q']}))
        first = self.decide(self.payload('q'))['hookSpecificOutput']['additionalContext']
        self.assertLessEqual(len(first), 1500)
        self.assertIn('L-001', first)
        self.assertIn('L-002', self.decide(self.payload('q'))['hookSpecificOutput']['additionalContext'])

    def test_the_budget_covers_the_whole_message_of_a_guard(self):
        text = 'x' * 740
        self.cues(self.entry('L-001', {'kind': 'guard', 'text': text, 'command_contains': ['q']}),
                  self.entry('L-002', {'kind': 'guard', 'text': text, 'command_contains': ['q']}))
        result = self.decide(self.payload('q'))['hookSpecificOutput']
        self.assertLessEqual(len(result['permissionDecisionReason']) + len(result.get('additionalContext', '')), 1500)

    def test_without_a_session_record_a_guard_is_only_a_hint(self):
        self.cues(self.entry('L-002', GUARD))
        for payload, state_dir in ((self.payload('paid-tool', session=None), str(self.state)),
                                   (self.payload('paid-tool'), None)):
            with self.subTest(state=state_dir):
                result = self.hook.hook_output(*self.hook.select(
                    self.hook.load_cues(str(self.vault)), 'Bash', payload['tool_input'], str(self.vault),
                    payload.get('session_id'), state_dir))['hookSpecificOutput']
                self.assertNotIn('permissionDecision', result)
                self.assertIn(GUARD['text'], result['additionalContext'])

    def test_unusable_files_mean_no_cue(self):
        self.assertEqual(self.hook.load_cues(str(self.vault)), [])
        for text in ('{bad', '[]', '{"version":2,"cues":[]}', '{"version":1,"cues":"x"}'):
            with self.subTest(text=text):
                self.write('00-meta/lesson-cues.json', text)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(self.hook.load_cues(str(self.vault)), [])
        self.write('00-meta/lesson-cues.json', json.dumps({'version': 1, 'cues': [
            {'kind': 'block', 'id': 'x', 'text': 'y'}, 'x', {'kind': 'hint', 'text': 1, 'id': 'x'}]}))
        self.assertEqual(self.hook.load_cues(str(self.vault)), [])
        self.write('00-meta/lesson-cues.json', ' ' * (self.hook.MAX_FILE_BYTES + 1))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.hook.load_cues(str(self.vault)), [])


class EndToEndTests(HookCase):
    def test_a_cue_written_through_a_delta_reaches_the_hook_and_retiring_removes_it(self):
        delta = importlib.import_module('vault_lesson_delta')
        compiler = importlib.import_module('vault_lesson_cues')
        self.ledger()
        proposal = delta.prepare_delta(self.vault, '00-meta/lessons.md', [
            {'op': 'ADD', 'text': 'Exit codes hide behind pipes', 'date': '2026-10-07', 'cues': [HINT]}])
        delta.apply_delta(self.vault, proposal, approve=True)
        identifier = proposal['changes'][0]['id']
        compiler.write_cues(self.vault)
        result = self.decide(self.payload('make | tail -1'))['hookSpecificOutput']
        self.assertIn(HINT['text'], result['additionalContext'])
        delta.apply_delta(self.vault, delta.prepare_delta(self.vault, '00-meta/lessons.md', [
            {'op': 'INC', 'id': identifier}]), approve=True)
        self.assertEqual(compiler.build_cues(self.vault)['document']['cues'][0]['count'], 2)
        delta.apply_delta(self.vault, delta.prepare_delta(self.vault, '00-meta/lessons.md', [
            {'op': 'RETIRE', 'id': identifier}]), approve=True)
        compiler.write_cues(self.vault)
        self.assertIsNone(self.decide(self.payload('other | tail -1', session='s2')))


class ProcessTests(HookCase):
    """The hook as Claude Code starts it: python -X utf8 -I, a copy, no vault_* module."""

    def run_hook(self, stdin, vault=None):
        scripts = self.vault / '00-meta/scripts'
        scripts.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HOOK, scripts / 'lesson-cues.py')
        env = {**os.environ, 'TMP': str(self.state), 'TEMP': str(self.state), 'TMPDIR': str(self.state)}
        env.pop('CLAUDE_PROJECT_DIR', None)
        if vault is not None:
            env['CLAUDE_PROJECT_DIR'] = vault
        return subprocess.run([sys.executable, '-X', 'utf8', '-I', str(scripts / 'lesson-cues.py')], input=stdin,
                              capture_output=True, env=env, cwd=str(self.vault))

    def test_round_trip_with_a_session_record_and_ascii_output(self):
        self.cues(self.entry('L-002', {**GUARD, 'text': '비용이 든 호출이 있었다'}))
        stdin = json.dumps(self.payload('paid-tool run')).encode('utf-8')
        first = self.run_hook(stdin, str(self.vault))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(first.stdout.isascii())
        result = json.loads(first.stdout)['hookSpecificOutput']
        self.assertEqual(result['permissionDecision'], 'deny')
        self.assertIn('비용이 든 호출이 있었다', result['permissionDecisionReason'])
        again = self.run_hook(stdin, str(self.vault))
        self.assertEqual((again.returncode, again.stdout), (0, b''))

    def test_every_failure_is_silent_on_stdout_and_exits_zero(self):
        self.cues(self.entry('L-002', GUARD))
        good = json.dumps(self.payload('paid-tool')).encode('utf-8')
        cases = ((b'{bad', str(self.vault)), (b'[]', str(self.vault)),
                 (json.dumps({'tool_name': 'Bash'}).encode(), str(self.vault)),
                 (good, None), (good, str(self.vault / 'absent')))
        for stdin, vault in cases:
            with self.subTest(stdin=stdin[:12], vault=vault is None):
                run = self.run_hook(stdin, vault)
                self.assertEqual((run.returncode, run.stdout), (0, b''), run.stderr)
                self.assertNotIn(str(self.vault).encode(), run.stderr)

    def test_no_cue_file_means_no_output(self):
        run = self.run_hook(json.dumps(self.payload('anything')).encode(), str(self.vault))
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, b'', b''))


class AssetTests(unittest.TestCase):
    def test_hook_is_ascii_stamped_and_standalone(self):
        text = HOOK.read_text(encoding='utf-8')
        self.assertTrue(text.isascii())
        stamp = vault_adapters._stamp(text, vault_adapters.CUES_STAMP_RE)
        self.assertRegex(stamp, vault_adapters.VERSION_RE)
        self.assertNotIn('import vault_', text)

    def test_settings_block_never_blocks_on_failure_and_is_not_a_default_hook(self):
        data = json.loads((ASSETS / 'settings-hooks.json').read_text(encoding='utf-8'))
        group = data['hooks']['PreToolUse'][0]
        handler = group['hooks'][0]
        self.assertEqual(handler['args'], ['-X', 'utf8', '-I', '${CLAUDE_PROJECT_DIR}/00-meta/scripts/lesson-cues.py'])
        self.assertNotIn('onFailure', handler)
        self.assertEqual(vault_adapters._cues_problems(group, handler), [])
        self.assertNotIn('lesson-cues', (ROOT / 'hooks/hooks.json').read_text(encoding='utf-8'))

    def test_guide_states_the_limits(self):
        text = (ASSETS / 'guide.md').read_text(encoding='utf-8')
        for literal in ('https://code.claude.com/docs/en/hooks', 'next model request', 'alongside the tool result',
                        'cannot change', 'advisory', 'not been measured', 'onFailure', '00-meta/lesson-cues.json',
                        'vault_lesson_cues.py', 'factual statements'):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)

    def test_commands_describe_the_adapter(self):
        init = (ROOT / 'commands/vault-init.md').read_text(encoding='utf-8')
        doctor = (ROOT / 'commands/vault-doctor.md').read_text(encoding='utf-8')
        for literal in ('--offer-lesson-cues', 'assets/adapters/lesson-cues/', '00-meta/scripts/lesson-cues.py'):
            self.assertIn(literal, init)
        self.assertIn('adapters.lesson_cues', doctor)
        self.assertNotIn('--offer-lesson-cues', doctor.replace('`--offer-lesson-cues`를 붙이지 않으므로', ''))


class AdapterReportTests(VaultCase):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(vault_adapters.shutil, 'which', return_value=None),
                        mock.patch.object(vault_adapters, '_platform', return_value='windows')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def bundled(self):
        return HOOK.read_text(encoding='utf-8')

    def merge(self, handler_changes=None, group_changes=None):
        data = json.loads((ASSETS / 'settings-hooks.json').read_text(encoding='utf-8'))
        data['hooks']['PreToolUse'][0]['hooks'][0].update(handler_changes or {})
        data['hooks']['PreToolUse'][0].update(group_changes or {})
        self.write('.claude/settings.local.json', json.dumps(data))

    def report(self, **options):
        report, code = vault_adapters.diagnose(self.vault, **options)
        self.assertEqual(code, 0)
        return report

    def test_a_vault_without_the_adapter_reports_exactly_as_before(self):
        report = self.report()
        self.assertEqual(set(report['adapters']), {'aside', 'deny_guard'})
        self.assertNotIn('lesson_cues', vault_adapters._format_text(report))

    def test_offer_is_only_listed_on_request(self):
        entry = self.report(offer_lesson_cues=True)['adapters']['lesson_cues']
        self.assertEqual((entry['state'], entry['actions']), ('absent', ['offer_lesson_cues']))

    def test_full_install_reports_installed_with_text_lines(self):
        self.write(vault_adapters.CUES_REL, self.bundled())
        self.write(vault_adapters.CUES_FILE_REL, '{}')
        self.merge()
        report = self.report()
        entry = report['adapters']['lesson_cues']
        self.assertEqual((entry['state'], entry['script']['state'], entry['hook']['state'], entry['actions']),
                         ('installed', 'current', 'configured', []))
        self.assertTrue(entry['compiled_present'])
        self.assertIn('lesson_cues.state: installed', vault_adapters._format_text(report))

    def test_missing_compiled_file_asks_for_a_build(self):
        self.write(vault_adapters.CUES_REL, self.bundled())
        self.merge()
        entry = self.report()['adapters']['lesson_cues']
        self.assertEqual((entry['state'], entry['actions']), ('installed', ['build_lesson_cues']))

    def test_partial_and_changed_installs(self):
        self.write(vault_adapters.CUES_REL, self.bundled() + '\n# local\n')
        entry = self.report()['adapters']['lesson_cues']
        self.assertEqual((entry['state'], entry['script']['state']), ('incomplete', 'modified'))
        self.assertIn('review_lesson_cues', entry['actions'])
        stamp = vault_adapters._stamp(self.bundled(), vault_adapters.CUES_STAMP_RE)
        self.write(vault_adapters.CUES_REL, self.bundled().replace('engine=' + stamp, 'engine=0.1.0', 1))
        self.merge()
        self.assertIn('offer_cues_update', self.report()['adapters']['lesson_cues']['actions'])

    def test_a_hook_that_blocks_or_names_the_vault_bare_needs_review(self):
        self.write(vault_adapters.CUES_REL, self.bundled())
        self.merge({'onFailure': 'block'})
        entry = self.report()['adapters']['lesson_cues']
        self.assertEqual(entry['state'], 'needs_review')
        self.assertIn('onFailure', entry['hook']['problems'][0])
        self.merge({'args': ['-I', '$CLAUDE_PROJECT_DIR/00-meta/scripts/lesson-cues.py']})
        entry = self.report()['adapters']['lesson_cues']
        self.assertEqual(entry['state'], 'needs_review')
        self.assertIn('$CLAUDE_PROJECT_DIR', entry['hook']['problems'][0])

    def test_the_deny_guard_is_not_mistaken_for_the_cue_hook(self):
        data = json.loads((ROOT / 'assets/adapters/deny-guard/settings-hooks.json').read_text(encoding='utf-8'))
        self.write('.claude/settings.local.json', json.dumps(data))
        self.assertNotIn('lesson_cues', self.report()['adapters'])

    def test_the_report_never_writes(self):
        self.write(vault_adapters.CUES_REL, self.bundled())
        self.merge()
        before = sorted(p.relative_to(self.vault).as_posix() for p in self.vault.rglob('*'))
        self.report(offer_lesson_cues=True)
        self.assertEqual(sorted(p.relative_to(self.vault).as_posix() for p in self.vault.rglob('*')), before)


if __name__ == '__main__':
    unittest.main()
