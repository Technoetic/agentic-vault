"""Replay synthetic risky commands against the published permissions.ask sample.

The matcher below is a test model of the rule grammar in the Claude Code
permissions documentation, not Claude Code itself. The documented examples in
GRAMMAR_ROWS keep the model honest; the replay sizes what built-in rules can
stop and what is left for a hook that reads more than the command text.
"""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / 'assets/adapters/deny-guard/guide.md'
WRAPPERS = {'timeout', 'time', 'nice', 'nohup', 'stdbuf', 'command', 'builtin', 'noglob'}
SEPARATORS = ('&&', '||', '|&', ';', '|', '&', '\n')
KEYWORDS = {'do', 'then', 'else', 'elif'}


def _subcommands(command):
    """Split on documented separators outside quotes; nested forms count too."""
    parts, current, quote, index = [], '', '', 0
    while index < len(command):
        character = command[index]
        if quote:
            if command.startswith('$(', index) and quote == '"':
                depth, end = 1, index + 2
                while end < len(command) and depth:
                    depth += {'(': 1, ')': -1}.get(command[end], 0)
                    end += 1
                parts.extend(_subcommands(command[index + 2:end - 1]))
                index = end
                continue
            quote = '' if character == quote else quote
        elif character in '\'"':
            quote = character
        else:
            separator = next((item for item in SEPARATORS if command.startswith(item, index)), None)
            if separator is not None:
                parts.append(current)
                current = ''
                index += len(separator)
                continue
        current += character
        index += 1
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def _stripped(command):
    """Drop leading assignments, keywords and the documented fixed wrappers."""
    words = command.split()
    while words:
        head = words[0]
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=\S*', head) or head in KEYWORDS:
            words.pop(0)
        elif head in WRAPPERS:
            words.pop(0)
            while head == 'timeout' and words and re.fullmatch(r'-\S+|\d+[smhd]?', words[0]):
                words.pop(0)
        elif head == 'xargs' and len(words) > 1 and not words[1].startswith('-'):
            words.pop(0)
        else:
            break
    return ' '.join(words)


def rule_matches(rule, command):
    body = re.fullmatch(r'Bash\((.*)\)', rule, re.DOTALL).group(1)
    if body.endswith(':*'):
        body = body[:-2] + ' *'
    pattern = '.*'.join(re.escape(piece) for piece in body.split('*'))
    if body.endswith(' *') and body.count('*') == 1:
        pattern = re.escape(body[:-2]) + r'(?: .*)?'
    return re.fullmatch(pattern, command, re.DOTALL) is not None


def stopped(rules, command):
    return any(rule_matches(rule, _stripped(part)) for rule in rules for part in _subcommands(command))


def sample_rules():
    text = GUIDE.read_text(encoding='utf-8')
    block = re.search(r'<!-- permissions-ask-sample -->\n```json\n(.*?)\n```', text, re.DOTALL)
    return json.loads(block.group(1))


GRAMMAR_ROWS = (
    ('Bash(npm run build)', 'npm run build', True),
    ('Bash(npm run build)', 'npm run build --watch', False),
    ('Bash(npm run *)', 'npm run', True),
    ('Bash(npm run *)', 'npm install', False),
    ('Bash(git log * main)', 'git log --oneline main', True),
    ('Bash(git log * main)', 'git log main', False),
    ('Bash(ls *)', 'ls', True),
    ('Bash(ls *)', 'lsof', False),
    ('Bash(ls*)', 'lsof', True),
    ('Bash(ls:*)', 'ls -la', True),
    ('Bash(* --help *)', 'npm --help x', True),
    ('Bash(* --help *)', 'npm --help', False),
    ('Bash(git clean *)', 'cd /tmp && git clean -f', True),
    ('Bash(git clean *)', 'echo "$(git clean -f)"', True),
    ('Bash(rm *)', 'FOO=bar rm -rf tmp/', True),
    ('Bash(git push *)', 'timeout 30 git push origin main', True),
    ('Bash(grep *)', 'xargs grep pattern', True),
    ('Bash(grep *)', 'xargs -n1 grep pattern', False),
    ('Bash(git push *)', 'git -C . push origin main', False),
    ('Bash(git push *)', "git 'push' origin main", False),
    ('Bash(curl *)', '/usr/bin/curl https://example.invalid', False),
    ('Bash(curl *)', "sh -c 'curl https://example.invalid'", False),
)

# Synthetic: (command, stopped by the sample, what is left when it is not).
INCIDENTS = (
    ('git push origin main', True, ''),
    ('git push --force origin main', True, ''),
    ('git add a.md && git commit -m note && git push', True, ''),
    ('timeout 60 git push origin main', True, ''),
    ('git reset --hard HEAD~1', True, ''),
    ('git clean -fd', True, ''),
    ('git checkout -- notes/a.md', True, ''),
    ('git restore notes/a.md', True, ''),
    ('git branch -D old-work', True, ''),
    ('rm -rf build/', True, ''),
    ('find . -name a.tmp | xargs rm', True, ''),
    ('for name in a b; do rm "$name"; done', True, ''),
    ('echo "$(git clean -f)"', True, ''),
    ('CI=1 npm publish', True, ''),
    ('gh release create v1.2.3', True, ''),
    ('gh pr merge 12 --squash', True, ''),
    ('curl -X POST https://example.invalid/api -d @body.json', True, ''),
    ('scp report.pdf host.invalid:/srv/in/', True, ''),
    ('python tools/write_register.py --commit', False, 'script'),
    ('node scripts/send_mail.mjs --to list', False, 'script'),
    ('./deploy.sh production', False, 'script'),
    ('make publish', False, 'script'),
    ("sh -c 'git push origin main'", False, 'indirect'),
    ("bash -c 'rm -rf build/'", False, 'indirect'),
    ('git -C . push origin main', False, 'indirect'),
    ('git -c push.default=current push origin main', False, 'indirect'),
    ('/usr/bin/curl https://example.invalid', False, 'indirect'),
    ('xargs -n1 rm', False, 'indirect'),
)


class PermissionAskSampleTests(unittest.TestCase):
    def test_model_matches_the_documented_examples(self):
        for rule, command, expected in GRAMMAR_ROWS:
            with self.subTest(rule=rule, command=command):
                self.assertEqual(stopped([rule], command), expected)

    def test_sample_is_ask_only_and_every_rule_is_well_formed(self):
        sample = sample_rules()
        self.assertEqual(list(sample), ['permissions'])
        self.assertEqual(list(sample['permissions']), ['ask'])
        rules = sample['permissions']['ask']
        self.assertEqual(len(rules), len(set(rules)))
        for rule in rules:
            self.assertRegex(rule, r'^(Bash|PowerShell)\([^()]+\)$')
            self.assertNotIn(rule, ('Bash(*)', 'PowerShell(*)'))

    def test_replay_stops_the_expected_commands(self):
        rules = [rule for rule in sample_rules()['permissions']['ask'] if rule.startswith('Bash(')]
        for command, expected, _left in INCIDENTS:
            with self.subTest(command=command):
                self.assertEqual(stopped(rules, command), expected)

    def test_guide_reports_the_replay_counts(self):
        total = len(INCIDENTS)
        caught = sum(1 for row in INCIDENTS if row[1])
        script = sum(1 for row in INCIDENTS if row[2] == 'script')
        indirect = sum(1 for row in INCIDENTS if row[2] == 'indirect')
        self.assertEqual(caught + script + indirect, total)
        text = GUIDE.read_text(encoding='utf-8')
        self.assertIn(f'예시를 옮긴 {len(GRAMMAR_ROWS)}줄', text)
        self.assertIn(f'합성 명령 {total}건 가운데 {caught}건', text)
        self.assertIn(f'스크립트 실행 {script}건', text)
        self.assertIn(f'우회 표기 {indirect}건', text)


if __name__ == '__main__':
    unittest.main()
