"""Release and cross-feature discoverability contracts for opt-in task workflows."""
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
class TaskWorkflowReleaseTests(unittest.TestCase):
    def test_current_upgrade_and_result_links_do_not_point_to_prior_release(self):
        security=(ROOT/'SECURITY.md').read_text(encoding='utf-8')
        summary=next(line for line in security.splitlines() if line.startswith('- 보안 수정 대상은 최신'))
        self.assertIn('0.48.0 이하는 0.49.0으로 갱신한다.',summary)
        self.assertIn('0.46.0부터 0.49.0까지',summary)
        readme=(ROOT/'README.md').read_text(encoding='utf-8')
        current=next(line for line in readme.splitlines() if line.startswith('현재 버전은 **v'))
        self.assertIn('[이번 변경·검증 범위](docs/releases/v0.49.0.md)',current)
        self.assertNotIn('[이번 변경·검증 범위](docs/releases/v0.48.0.md)',current)
    def test_current_manifests_and_healthcheck_identify_release(self):
        for name in ('.claude-plugin/plugin.json','.codex-plugin/plugin.json'):
            self.assertEqual(json.loads((ROOT/name).read_text(encoding='utf-8'))['version'],'0.49.0')
        self.assertIn('engine=0.49.0',(ROOT/'skills/agentic-vault/scripts/vault_healthcheck.py').read_text(encoding='utf-8').splitlines()[1])
    def test_all_opt_in_helpers_and_guides_are_discoverable(self):
        skill=(ROOT/'skills/agentic-vault/SKILL.md').read_text(encoding='utf-8')
        codex=(ROOT/'skills/agentic-vault/references/codex.md').read_text(encoding='utf-8')
        verifier=(ROOT/'scripts/verify_codex_plugin.py').read_text(encoding='utf-8')
        for stem,guide in [('vault_task_contracts','task-contracts'),('vault_staged_evaluation','staged-evaluation'),('vault_improvement_cycles','improvement-cycles')]:
            for text in (skill,codex,verifier):
                self.assertIn(stem+'.py',text)
            self.assertTrue((ROOT/'docs'/f'{guide}.md').is_file())
    def test_release_sources_and_scope_are_pinned(self):
        release=(ROOT/'docs/releases/v0.49.0.md').read_text(encoding='utf-8')
        for text in ['## 기본 결과 변경','## 검증','## 알려진 경계','54dad8e1dd93aee31858bde72493cd7a0f938739','https://github.com/Q00/ouroboros']:
            self.assertIn(text,release)
        self.assertIn('자동 실행',(ROOT/'docs/releases/v0.49.0.md').read_text(encoding='utf-8'))
    def test_no_active_model_or_network_dependency_is_added(self):
        for name in ['vault_task_contracts.py','vault_staged_evaluation.py','vault_improvement_cycles.py']:
            import ast
            tree=ast.parse((ROOT/'skills/agentic-vault/scripts'/name).read_text(encoding='utf-8'))
            forbidden={'requests','httpx','aiohttp','subprocess','socket','urllib','openai','anthropic'}
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    self.assertFalse({x.name.split('.')[0]for x in node.names}&forbidden)
                elif isinstance(node,ast.ImportFrom):
                    self.assertNotIn((node.module or'').split('.')[0],forbidden)
