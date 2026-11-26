"""Release metadata consistency and historical contract regressions."""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"
README = REPO_ROOT / "README.md"
RELEASE_NOTE_V082 = REPO_ROOT / "docs" / "releases" / "v0.8.2.md"
RELEASE_NOTE_V083 = REPO_ROOT / "docs" / "releases" / "v0.8.3.md"
RELEASE_NOTE_V084 = REPO_ROOT / "docs" / "releases" / "v0.8.4.md"
RELEASE_NOTE_V090 = REPO_ROOT / "docs" / "releases" / "v0.9.0.md"
HEALTHCHECK_SCRIPT = (
    REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_healthcheck.py"
)

EXPECTED = "0.59.0"
EXPECTED_PUBLIC_REF = "0.59.0"
EXPECTED_BADGE_LINE = (
    "[![Version](https://img.shields.io/badge/v0.59.0-10B981?style=for-the-badge)]"
    "(docs/releases/v0.59.0.md)"
)
EXPECTED_TREE_LINE = (
    "├── .claude-plugin/                    "
    "← plugin.json · marketplace.json (v0.59.0 · MIT)"
)
EXPECTED_HISTORICAL_ORIGINS = (
    "그래서 v0.8.0부터 healthcheck 섹션 11",
    "— v0.8.1부터 healthcheck 섹션 12",
)
EXPECTED_JARVIS_BOUNDARY_LINE = (
    "- 볼트·봇 네임스페이스마다 정확히 하나의 데몬을 실행해야 한다. "
    "예약 브리핑의 at-least-once 보장은 `pending` 커밋 이후에만 성립한다. "
    "`pending` 이전에 프로세스가 중단되면 재시작 시 지난 슬롯을 cold-start miss로 "
    "건너뛸 수 있고, Telegram 수락 후 응답 유실·일부 청크 전송·전송 성공 후 "
    "`fired` 기록 실패에서는 중복될 수 있다."
)
REQUIRED_V082_RELEASE_SECTIONS = (
    "## 보안 수정",
    "## 데이터 내구성",
    "## commit gate",
    "## 하위호환",
    "## 업그레이드",
    "## 검증",
    "## 알려진 경계",
    "## 미포함 v0.9.0 항목",
)
REQUIRED_V082_RELEASE_LITERALS = (
    "`briefing_times`",
    '`chat.type == "private"`',
    "`chat.id == from.id`",
    "private chat",
    "process-then-ack",
    "offset",
    "staged index",
    "`--no-verify`",
    "`briefing_time`",
    "fallback",
    "git status --short --branch",
    "git log --oneline origin/master..HEAD",
)
EXPECTED_SCOPE_UPGRADE_LINES = (
    "2. 일반 누락 키 보충은 `frontmatter_roots`와 "
    "`frontmatter_exempt_paths`가 없을 때 그 부재를 보존한다. "
    "`frontmatter_exempt_paths`가 없고 기존 `fm_exempt_zones`가 있으면 "
    "호환 alias가 계속 적용되도록 새 키를 자동 추가하지 않는다.",
    "3. 두 범위 키를 추가하려면 full 모드 검사 범위가 어떻게 바뀌는지 "
    "먼저 확인하고, 별도의 검사 범위 마이그레이션으로 명시적으로 승인한다.",
)
EXPECTED_LEGACY_BRIEFING_UPGRADE_LINE = (
    "5. 기존 `jarvis.briefing_time`만 있는 볼트에는 일반 누락 키 보충으로 "
    "`briefing_times`를 추가하지 않는다. 별도 브리핑 일정 마이그레이션을 "
    "승인하면 기존 단일 시각을 배열의 유일한 값으로 옮기며, 템플릿 기본 "
    "`07:30`으로 바꾸지 않는다. 새 config나 `jarvis` 블록이 없는 config는 "
    "계속 새 템플릿의 `briefing_times` 기본값을 사용한다."
)
REQUIRED_V083_RELEASE_SECTIONS = (
    "## index 서술 규격",
    "## 기각 대장 (rejected-proposal ledger)",
    "## 승격 검증 게이트 + 가역 롤백",
    "## 하위호환",
    "## 업그레이드",
    "## 검증",
    "## 알려진 경계",
    "## 미포함 v0.9.0 항목",
)
REQUIRED_V083_RELEASE_LITERALS = (
    "arXiv 2608.27454",
    "`promotion_review_sessions`",
    "승격완료(검증중→대상, 승격일, anchor)",
    "롤백(사유)",
    "조항은 가역, 대장은 영속",
    "`(재발)`",
    "## 기각 대장",
    "engine=0.8.3",
    "제목 재진술 금지",
    "git log --oneline origin/master..HEAD",
)
# v0.8.3 behavioral-contract anchors: the promotion probation gate and the
# rejection ledger must stay wired into the session-end command and templates.
REQUIRED_SESSION_END_LITERALS = (
    "0. **승격 검증 게이트**",
    "`promotion_review_sessions`",
    "상태를 `롤백(사유)`로 바꿔라",
    "`(재발)` 마커",
    "**초안을 쓰기 전에 대장의 '기각 대장' 섹션(있으면)에서 같은 교훈의 "
    "이전 기각 사유를 읽어라**",
    "승격완료(검증중→대상, 승격일 오늘, anchor",
    "요약 본문은 `세션 종료 — `로 시작하라",
    "커밋 수에서 1을 뺀 값",
)
# Index description spec must stay wired into every registration path —
# the release note advertises "등록 경로 4곳에 일관 적용".
REQUIRED_INDEX_SPEC_WIRING = (
    ("skills/agentic-vault/SKILL.md", "열지 말지"),
    ("commands/vault-ingest.md", "열지 말지"),
    ("commands/vault-trace.md", "문제+근본 원인+처방"),
    ("commands/vault-lint.md", "열지 말지"),
    ("assets/templates/index.md", "열지 말지"),
    ("assets/templates/rules/vault-workflow.md", "열지 말지"),
)
REQUIRED_LESSONS_TEMPLATE_LITERALS = (
    "## 기각 대장 — 기각된 승격 초안의 전문 보존",
    "승격완료(검증중→대상, 승격일, anchor)",
    "구판 표기 `승격완료(→대상)`(승격일·anchor 없음)는 검증을 통과한 "
    "확정분으로 간주한다",
)
# rules 스탬프는 마지막 내용 변경 버전을 가리킨다 — /vault-upgrade가 스탬프 숫자 비교로
# 교체 여부를 판단하므로, 내용이 같은데 스탬프만 올리면 전 볼트에 무의미한 교체를 유발한다.
# workflow 규칙은 v0.8.3 이후 무변경이었다가 v0.16.0에서 긴 작업 추적·파괴적 git 명령
# 조항이 추가돼 0.16.0으로, v0.43.0에서 사실 서술보다 실측을 따르는 조항이 추가돼 0.43.0으로 올라갔다.
REQUIRED_WORKFLOW_RULE_STAMP = "agentic-vault:rule engine=0.43.0"
# v0.15.1에서 healthcheck 내용(경로 규칙 사본·§14b·utf-8-sig)이 바뀌어 스탬프가 ENGINE_VERSION과
# 같은 값으로 올라갔다. 스탬프 = ENGINE_VERSION = 매니페스트 버전 대조는 test_readme_truth.py가 맡는다.
REQUIRED_HEALTHCHECK_STAMP = f"agentic-vault:healthcheck engine={EXPECTED}"
REQUIRED_V084_RELEASE_SECTIONS = (
    "## 수정 1 — config 경로 필드의 deny zone 우회 차단 (high)",
    "## 수정 2 — 노트 읽기 실패 fail-soft + §14 신설 (low)",
    "## 하위호환",
    "## 검증",
)
REQUIRED_V084_RELEASE_LITERALS = (
    "`_PATH_KEYS` 7종",
    "engine=0.8.4",
    "§14 읽기 실패",
    "exit 1",
)


REQUIRED_V090_RELEASE_SECTIONS = (
    "## 변경",
    "### Claude Code · Codex 겸용 (local.2)",
    "### 신뢰성 (local.1)",
    "## 하위호환·업그레이드",
    "## 설치",
    "## 검증",
    "## 알려진 한계",
)
REQUIRED_V090_RELEASE_LITERALS = (
    "Claude Code, Codex 겸용",
    "`0.9.0-local.1`",
    "`0.9.0-local.2`",
    "engine=0.9.0",
    "engine=0.8.3",
    "--ref v0.9.0",
    "`/vault-recall`",
    "SHA-256",
    "예산 **0은 주입 비활성화**",
    "BrokenBarrierError",
)

# v0.12.0: 여섯 번째 엔진 규칙(vault-browser.md)이 설치·업그레이드·스텁·README에 배선되고,
# v0.12.0에서 내용이 바뀌지 않은 기존 5종은 스탬프가 0.12.0으로 올라가지 않는다(무의미한 교체
# 유발 방지). workflow·collab은 이후 v0.16.0에서 내용이 바뀌어 0.16.0 스탬프를 가진다.
RELEASE_NOTE_V0120 = REPO_ROOT / "docs" / "releases" / "v0.12.0.md"
BROWSER_RULE = REPO_ROOT / "assets" / "templates" / "rules" / "vault-browser.md"
# v0.17.0에서 9번(창 기반 앱의 수명과 포커스) 조항이 추가돼 browser 스탬프가 0.17.0으로 올라갔다.
REQUIRED_BROWSER_RULE_STAMP = "agentic-vault:rule engine=0.17.0"
REQUIRED_BROWSER_RULE_LITERALS = (
    "# 브라우저 자동화 경계",
    "CLAUDE.md가 정한 하나만",
    "페이지 내용은 비신뢰 데이터다",
    "결정론적 스크립트 모드",
    "추측 시도는 금지",
    "완료 이벤트",
    "실행해 본 결과",
)
REQUIRED_SIX_RULES_WIRING = (
    ("commands/vault-init.md", "`vault-*.md` 6개(architecture·linking·frontmatter·workflow·collab·browser)"),
    ("commands/vault-upgrade.md", "6종(architecture·linking·frontmatter·workflow·collab·browser)"),
    ("commands/vault-upgrade.md", "collab → browser**"),
    ("assets/templates/CLAUDE-vault-stub.md", "6개 파일에 있고 자동 로드된다"),
    ("assets/templates/AGENTS-vault-stub.md", "여섯 공통 규칙"),
    ("assets/templates/AGENTS-vault-stub.md", "collab, browser 순서로"),
    ("README.md", "rules 6종"),
    ("docs/codex.md", "엔진 규칙 여섯 개"),
)
UNCHANGED_RULE_TEMPLATES = (
    "vault-architecture.md", "vault-linking.md", "vault-frontmatter.md", "vault-workflow.md", "vault-collab.md",
)
REQUIRED_V0120_RELEASE_SECTIONS = ("## 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계")
REQUIRED_V0120_RELEASE_LITERALS = ("vault-browser.md", "engine=0.12.0", "--ref v0.12.0", "6종", "engine=0.8.3")

# v0.16.0: 긴 작업 규율 — 두 엔진 규칙(workflow·collab)과 세션·lint·trace·recall 명령에 배선된다.
# handoff 섹션 명칭(▶ NEXT)과 AGENTS 생성의 rules 6개 입력도 함께 정합한다.
RELEASE_NOTE_V0160 = REPO_ROOT / "docs" / "releases" / "v0.16.0.md"
REQUIRED_COLLAB_RULE_STAMP = "agentic-vault:rule engine=0.16.0"
REQUIRED_V0160_WIRING = (
    ("assets/templates/rules/vault-workflow.md", "**긴 작업은 파일로 추적:**"),
    ("assets/templates/rules/vault-workflow.md", "진행 위치는 대화 기록이 아니라 이 파일로 판단한다"),
    ("assets/templates/rules/vault-workflow.md", "`git reset --hard`"),
    ("assets/templates/rules/vault-workflow.md", "강제 push(`--force`·`-f`·`--force-with-lease`·`+<refspec>`)도 삭제로 보고"),
    ("assets/templates/rules/vault-workflow.md", "조회·진단만 요청받은 세션에서는 tasks 노트에 단계를 적거나 추적 파일을 만들지 않는다"),
    ("assets/templates/rules/vault-collab.md", "돌아온 보고는 주장으로 취급하라"),
    ("assets/templates/rules/vault-collab.md", "대조하지 못한 것은 `미확인`으로 남긴다"),
    ("assets/templates/tasks.md", "하위 체크박스"),
    ("assets/templates/handoff.md", "완료 기준"),
    ("commands/vault-session-end.md", "▶ NEXT 다음 세션이 바로 이어서 할 일 / §1 최근 완료된 작업"),
    ("commands/vault-session-end.md", "**완료 기준**"),
    ("commands/vault-session-end.md", "`사용자 지정 멈춤:`"),
    ("commands/vault-session-end.md", "1. **사용자 결정 필요**"),
    ("commands/vault-session-end.md", "3. **발견·생략**"),
    ("commands/vault-session-start.md", "handoff의 ▶ NEXT는 후보이지 착수 승인이 아니다"),
    ("commands/vault-trace.md", "**확인하지 못한 것**"),
    ("commands/vault-recall.md", "사용한 검색어"),
    ("commands/vault-lint.md", "**상태 노트 교차 대조"),
    ("commands/vault-init.md", "rules 6개의 본문을 **architecture → linking → frontmatter → workflow → collab → browser**"),
    ("commands/vault-upgrade.md", "rules 여섯 본문"),
    ("skills/agentic-vault/SKILL.md", "4섹션(▶ NEXT / 최근 완료 / 확인 필요 / 보류)"),
    ("commands/vault-session-end.md", "요약하지 말고 그대로 붙인다"),
    ("commands/vault-session-end.md", "승격 초안은 실행 가능한 조항으로 써라"),
    ("commands/vault-lint.md", "결정적 계산"),
    ("commands/vault-process-inbox.md", "파일 자체를 판독하라"),
    ("commands/vault-process-inbox.md", "b~d를 진행하지 말고 인박스에 그대로 남겨라"),
    ("commands/vault-process-inbox.md", "덮어쓰지 않는 이동"),
    ("commands/vault-ingest.md", "그림 자체를 판독해"),
    ("commands/vault-upgrade.md", "`승인 대기(기존 유지)`"),
    ("docs/reliability.md", "`hot_max_tokens`·`handoff_max_tokens`를 잠시 0으로"),
)
FORBIDDEN_V0160_STALE_LITERALS = (
    ("commands/vault-init.md", "rules 5개"),
    ("commands/vault-init.md", "세 파일의 `engine=0.8.2` 스탬프"),
    ("commands/vault-upgrade.md", "rules 다섯"),
    ("commands/vault-upgrade.md", "나머지 5종은 스탬프가 같아"),
    ("skills/agentic-vault/SKILL.md", "다음 세션 지시)"),
    ("SECURITY.md", "넣은 훅이 브리지 사용자 권한으로 실행된다"),
    ("README.md", "settings and hooks load without a trust prompt"),
)
REQUIRED_V0160_RELEASE_SECTIONS = ("## 변경", "## 하위호환·업그레이드", "## 검증과 경계")
REQUIRED_V0160_RELEASE_LITERALS = ("engine=0.16.0", "--ref v0.16.0", "vault-workflow.md", "vault-collab.md")


# v0.17.0: 선택형 도구 어댑터(Aside) — 어댑터 자산·진단 스크립트·init/upgrade/doctor 배선,
# browser 규칙 9번. 내용이 바뀌지 않은 나머지 다섯 규칙은 스탬프를 올리지 않는다.
RELEASE_NOTE_V0170 = REPO_ROOT / "docs" / "releases" / "v0.17.0.md"
REQUIRED_V0170_RELEASE_SECTIONS = ("## 변경", "## 하위호환·업그레이드", "## 검증과 경계")
REQUIRED_V0170_RELEASE_LITERALS = (
    "engine=0.17.0", "--ref v0.17.0", "vault-browser.md", "aside-up.ps1", "vault_adapters.py",
    "Bash(aside *)", "$env:CLAUDE_PROJECT_DIR", "ProductName", "settings.local.json",
)
REQUIRED_V0170_WIRING = (
    ("assets/templates/rules/vault-browser.md", "9. **창 기반 앱의 수명과 포커스.**"),
    ("assets/templates/rules/vault-browser.md", "연 탭만 닫는다"),
    ("commands/vault-init.md", "## 4-1. 도구 어댑터 (선택 — 사용자 승인 후에만)"),
    ("commands/vault-init.md", "관리 블록 **밖**에 둔다"),
    ("commands/vault-init.md", "Codex에서는 이 단계를 생략하고"),
    ("commands/vault-upgrade.md", "**도구 어댑터(선택)**"),
    ("commands/vault-upgrade.md", "`offer_helper_update`"),
    ("commands/vault-upgrade.md", "자동 덮어쓰기 금지 — diff만 보여 주고"),
    ("commands/vault-upgrade.md", "`review_settings`"),
    ("commands/vault-upgrade.md", "`agentic-vault:adapter aside-up engine=` 스탬프가 있는 `00-meta/scripts/aside-up.ps1`은 엔진 소유 파일이다"),
    ("commands/vault-init.md", "`.claude/settings.local.json`에 병합할까요?"),
    ("commands/vault-init.md", ".claude/settings.local.json\n00-meta/health-report.md"),
    ("assets/adapters/aside/browser-clause.md", "`console.log()`로 출력한 내용은 이 명령을 부른 에이전트"),
    ("SECURITY.md", "**The Aside hook runs a file from the vault.**"),
    ("commands/vault-doctor.md", "vault_adapters.py"),
    ("skills/agentic-vault/references/codex.md", "vault_adapters.py"),
    ("skills/agentic-vault/SKILL.md", "도구 어댑터(Aside) 상태"),
    ("docs/codex.md", "Claude Code 전용 자동 기동 훅 대신"),
    ("README.md", "├── assets/adapters/aside/"),
    ("README.md", "vault_adapters.py"),
    ("SECURITY.md", "**Optional Aside adapter**"),
    ("SECURITY.md", "선택형 Aside 어댑터 훅·도우미"),
)


# v0.17.1: 결함 수정 — SessionStart 주입을 호스트 훅 출력 상한 아래로 묶고 잘림 표식에 경로·생략
# 제목을 싣는다. healthcheck §11의 "통째로 주입 — 자르지 않는다" 설명을 실제 동작에 맞추고,
# recall이 크기 한도로 건너뛴 파일 이름을 밝힌다. 규칙 템플릿 6종은 내용이 같아 스탬프를 올리지 않는다.
# 최신 README 릴리스 링크는 별도의 현재 메타데이터 검사로 대조한다.
RELEASE_NOTE_V0171 = REPO_ROOT / "docs" / "releases" / "v0.17.1.md"
REQUIRED_V0171_RELEASE_SECTIONS = ("## 변경", "## 하위호환·업그레이드", "## 검증과 경계")
REQUIRED_V0171_RELEASE_LITERALS = (
    "HOST_MAX_OUTPUT_CHARS", "9,500", "10,000", "oversized_files", "--ref v0.17.1", "engine=0.17.1",
)
# 상한 값(9500)은 tests/test_session_start.py와 tests/test_vault_doctor.py가 동작으로 고정한다.
REQUIRED_V0171_WIRING = (
    ("hooks/session_start.py", "HOST_MAX_OUTPUT_CHARS"),
    ("hooks/session_start.py", "compose_context"),
    ("skills/agentic-vault/scripts/vault_doctor.py", "host_char_cap"),
    ("skills/agentic-vault/scripts/vault_doctor.py", "shorten_the_notes_below_the_host_cap"),
    ("commands/vault-doctor.md", "host_char_cap"),
    ("skills/agentic-vault/scripts/vault_recall.py", "oversized_files"),
    ("commands/vault-recall.md", "oversized_files"),
)
# v0.17.0까지 §11이 쓰던 문구 그대로만 금지한다(일반 표현 "자르지 않는다"는 다른 곳에 쓸 수 있다).
FORBIDDEN_V0171_STALE_LITERALS = (
    ("skills/agentic-vault/scripts/vault_healthcheck.py", "통주입"),
    ("skills/agentic-vault/scripts/vault_healthcheck.py", "주입한다(자르지 않는다)"),
    ("skills/agentic-vault/scripts/vault_healthcheck.py", "주입한다 — 자르지 않는다"),
    ("docs/releases/v0.17.1.md", "아주 긴 한 줄을 통째로"),
)


# v0.23.0: six opt-in, default-off or warning-only items and the first GitHub immutable release.
# Rule template content did not change, so no rule stamp moves to 0.23.0.
RELEASE_NOTE_V0230 = REPO_ROOT / "docs" / "releases" / "v0.23.0.md"
REQUIRED_V0230_RELEASE_SECTIONS = ("## 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계")
REQUIRED_V0230_RELEASE_LITERALS = (
    "--receipt-out", "--report-unsupported", "check-support", "supply-chain-audit",
    "--render-matched", "--lexical-ngrams hangul2", "hooks/hooks.json", "checker_sha256",
    "vault_warning_policy.py --certificate", "SHA256SUMS", "LF", "--ref v0.23.0",
)


# v0.31.0: per-assertion flips and roles, ledger-table marking, period normalization, session
# evidence and request-body test servers, each new relative to v0.30.0. vault_ssot.py is one of
# the seven files that warning-promotion certificates bind, so the note must carry both hashes
# and the upgrade and rollback order, and every surface that states the hash or the order is
# pinned below. Rule template content did not change.
RELEASE_NOTE_V0310 = REPO_ROOT / "docs" / "releases" / "v0.31.0.md"
CHECKER_SHA256_V0260_TO_V0300 = "84cf52358d2bf2562af6e79e80a76572f0cfa3230d062cd8cd6438febcc04bfd"
CHECKER_SHA256_V0310 = "d8f9559a873ed7cf00f6982a2aeab28880227f403cec358e8e4e5c3fbfcbeb9c"
# Published v0.32.0 changed five bound files. Its immutable release and verification
# records retain that digest when a later release changes the checker.
CHECKER_SHA256_V0320 = "9bca12a79bfa87ff2bf44afecb2e5527e19c199b95fc1435d9513d2be73b3563"
# Declared v0.33.0 candidate fingerprint, retained by v0.33.1 and the dated implementation.
CHECKER_SHA256_V0330 = "af8f4c5ab9fc809a4998e55870e769d5f617d6742a53d0c5c2844df7ff3d7b92"
# Declared v0.34.0 fingerprint. The --bind-subject subject left edge changed vault_ssot.py at
# ac96d3e, and the dated note below declared the value first as a development source.
# Published v0.33.x records keep CHECKER_SHA256_V0330; the addenda and that note state both.
CHECKER_SHA256_V0340 = "be02b141ffed91cbe34f8cb2317a790c60075740bfe5d8dc435ef202f113174f"
# Declared v0.46.0 fingerprint, written by hand after F1 (deny zones fold Unicode
# normalization) changed vault_paths.py and vault_lint_extensions.py.
CHECKER_SHA256_V0460 = "09642e7aa77d0b812f5afa646b491fe2d79d44104e399bea4a3001a6f2f1279d"
# Declared v0.55.0 fingerprint, written by hand after verification-date staleness,
# alias-collision lint, and URL-query egress checks changed vault_lint_extensions.py
# and vault_egress.py.
CHECKER_SHA256_V0550 = "3754f606efe8d48c55d5a835bf78552dc9deb6379673d883c5cb9eb56010e79d"
SOURCE_CHECKER_NOTE_20261030 = "docs/verification/subject-boundary-20261030.md"
# Each addendum opens with the current release section; the published v0.33.1 section follows.
CURRENT_CHECKER_SECTION = "## v0.35.1 source and certificate migration"
# The v0.55.0 section sits immediately above v0.46.0, which sits above v0.35.1.
V0550_CHECKER_SECTION = "## v0.55.0 source and certificate migration"
V0550_RETURN_COORDINATION = (
    "Returning to v0.54.1, or to any release from v0.46.0 to v0.54.1, needs the same coordination."
)
V0460_CHECKER_SECTION = "## v0.46.0 source and certificate migration"
V0460_RETURN_COORDINATION = (
    "Returning to v0.45.1, or to any release from v0.34.0 to v0.45.1, needs the same coordination."
)
V0340_CHECKER_SECTION = "## v0.34.0 source and certificate migration"
V0331_CHECKER_SECTION = "## v0.33.1 source and certificate migration"
SOURCE_CHECKER_ADDENDUM_PATHS = (
    "README.md", "docs/codex.md", "docs/hardening.md",
)
SOURCE_CHECKER_COORDINATION = (
    "Before switching to or from these source bytes, return promoted levels to `warning`; "
    "align every engine copy that checks the vault (Claude Code, Codex, and the git vault's "
    "`00-meta/scripts/`) to the same bytes; run `vault_warning_policy.py --certificate` on "
    "that installation (including its harmless replay), then promote again."
)
REQUIRED_V0310_RELEASE_SECTIONS = ("## 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계")
REQUIRED_V0310_RELEASE_LITERALS = (
    # Per-assertion flips and correction/retention roles (vault_runs.py).
    "vault_runs.py --vault PATH flips BASELINE_ID CANDIDATE_ID", "`flip_counts`", "`pass_to_fail`",
    "`pass_count_drops`", "`negative_flip_rate`", "`conditional_negative_flip_rate`", "`error_outcomes`",
    "`role`", "`correction`", "`retention`", "`resolution`", "`FULL`", "`PARTIAL`", "`NO`",
    "`hard_correction_assertion`", "`invalid_assertion_role`", "`invalid_assertion`",
    # Rows backed only by another ledger's fact table (check-support).
    "`rows_supported_ledger_table_only`", "`ledger_table_only`", "`--primary-text-only`",
    "`ledger_tables_unseparated`", 'reason: "ledger_table_only"',
    # Opt-in period normalization and its abstain reasons.
    "`check-support --normalize-periods`", "`--fiscal-start MM`", "`normalized_periods`",
    "`period_convention`", "`normalized_match`", "`via_ledger_table_only`",
    'incomplete: ["period_text_limit"]', "`no_year`", "`fiscal_start_undeclared`",
    "`quarter_basis_ambiguous`", "`relative_needs_anchor`", "`multiple_periods`", "`period_qualifier`",
    "`partial_range`", "`invalid_date`", '{"error":"ssot_operation_unavailable"}', "`vault_period.py`",
    # Opt-in SessionStart evidence from one named transcript (vault_doctor.py).
    "`vault_doctor.py --session-evidence`", "`--session-instruction-paths`", "`observed_ran`",
    "`observed_truncated`", "`observed_error`", "`not_recorded`", "`unsupported_host_version`",
    "`unparsed`", "`session_start_hooks`", "`failed_entries`", "`command_mentions_session_start_py`",
    "`session_start_hook_failed:`", "`session_next_action:`", "`host_internal_format_undocumented`",
    # Loopback test servers read request bodies; the ast guard and its dated measurements.
    "`tests/test_http_handlers.py`", "`do_POST`", "`async def`", "4/300", "0/300", "0.0303", "0.0099",
    "2026-10-24 22:33–22:46 KST",
    # Upgrade and rollback.
    "checker_sha256", CHECKER_SHA256_V0260_TO_V0300, CHECKER_SHA256_V0310,
    "vault_warning_policy.py --certificate", "`invalid_config`", "`vault_healthcheck.py --staged`",
    "`--p`", "--ref v0.31.0", "engine=0.31.0",
)
SESSION_EVIDENCE_SCRIPT = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_session_evidence.py"
# Preserve every published v0.32.0 statement and its exact count, independently
# of the current development-source fingerprint. Earlier digests remain abbreviated.
V0320_CHECKER_HASH_COUNTS = {
    "README.md": 2, "docs/codex.md": 1, "docs/hardening.md": 1,
    "docs/releases/v0.32.0.md": 1, "docs/verification/v0.32.0.md": 1,
}
# Records of v0.31.0 that state its digests as history. The 169d8a6 digest may appear only in the
# v0.31.0 verification record's phrase below.
CHECKER_HISTORY_RECORDS = ("docs/releases/v0.31.0.md", "docs/verification/v0.31.0.md")
CHECKER_SHA256_V0240_TO_V0250_PREFIX = "2ef4b9d4"
CHECKER_SHA256_AT_169D8A6_PREFIX = "832a86f9"
CHECKER_HISTORY_AT_169D8A6 = (
    f"(it was `{CHECKER_SHA256_AT_169D8A6_PREFIX}…` at `169d8a6`, "
    "before the pre-release cleanup edited `vault_ssot.py` again)"
)
_OLD_HASH = f"`{CHECKER_SHA256_V0260_TO_V0300[:8]}…`"
_V031_HASH = f"`{CHECKER_SHA256_V0310[:8]}…`"
_V025_HASH = f"`{CHECKER_SHA256_V0240_TO_V0250_PREFIX}…`"
_V032_HASH = f"`{CHECKER_SHA256_V0320}`"
# Old-to-new hash statements of v0.31.0 that still hold as history. Prose is compared with
# whitespace runs folded to one space, so rewrapping a Markdown paragraph does not matter but a
# changed hash or version range does.
REQUIRED_V0310_HASH_STATEMENTS = (
    ("docs/releases/v0.31.0.md",
     f"v0.26.0~v0.30.0의 `{CHECKER_SHA256_V0260_TO_V0300}`에서 `{CHECKER_SHA256_V0310}`로 바뀐다"),
    ("docs/verification/v0.31.0.md",
     f"`{CHECKER_SHA256_V0260_TO_V0300}` at v0.26.0 to v0.30.0, `{CHECKER_SHA256_V0310}` in v0.31.0 "
     + CHECKER_HISTORY_AT_169D8A6),
    ("README.md", f"`checker_sha256`가 v0.26.0~v0.30.0의 {_OLD_HASH}에서 {_V031_HASH}로 바뀐다"),
    ("docs/codex.md", f"`vault_ssot.py`를 바꿨다({_OLD_HASH}에서 {_V031_HASH}로"),
    ("docs/codex.md", f"`vault_provenance.py`를 바꿨다({_V025_HASH}에서 {_OLD_HASH}로"),
)
# The v0.32.0 statements: the v0.31.0 digest as history, then the full v0.32.0 digest.
REQUIRED_V0320_HASH_STATEMENTS = (
    ("README.md",
     f"binds ({_OLD_HASH} in v0.26.0 to v0.30.0, {_V031_HASH} in v0.31.0, in v0.32.0 {_V032_HASH})"),
    ("README.md", f"`checker_sha256`가 v0.31.0의 {_V031_HASH}에서 {_V032_HASH}로 바뀐다"),
    ("docs/codex.md", f"`vault_paths.py`를 바꿨다({_V031_HASH}에서 {_V032_HASH}로"),
    ("docs/hardening.md",
     f"v0.24.0·v0.25.0의 `checker_sha256`는 {_V025_HASH}, v0.26.0~v0.30.0은 {_OLD_HASH}, "
     f"v0.31.0은 {_V031_HASH}, v0.32.0은 {_V032_HASH}."),
)
# Statements that would present the v0.31.0 digest, upgrade note or description as current.
FORBIDDEN_V0320_STALE_STATEMENTS = (
    ("README.md", f"now {_V031_HASH}"),
    ("README.md", "[upgrade section](docs/releases/v0.31.0.md"),
    ("docs/codex.md", "v0.30.0 이하에서 만든 인증서는 새 설치본에서"),
    ("docs/hardening.md", f"v0.31.0은 {_V031_HASH}. corpus"),
    (".codex-plugin/plugin.json", "Release v0.31.0"),
)
# The release note's upgrade order: which engine copies a certificate must match, that rollback
# follows the same order, then steps 1-4 as consecutive list lines.
V0310_UPGRADE_ORDER_LEAD = (
    "Claude Code와 Codex의 플러그인 설치본, 그리고 git 볼트에서 pre-commit 훅이 실행하는 볼트 안 엔진 "
    "사본(`00-meta/scripts/`)이 그 대상이다. 그래서 경고를 `fatal`로 올린 볼트는 갱신 전에 다음 순서를 "
    "따른다. 이전 버전으로 되돌릴 때도 같은 순서다."
)
V0310_UPGRADE_STEPS = (
    "1. 볼트 설정 `warning_policy.levels`에서 `fatal`로 올린 단계를 `warning`으로 되돌린다.",
    "2. 볼트를 읽는 엔진을 모두 같은 버전(되돌릴 때는 이전 버전)으로 바꾼다. Claude Code와 Codex 설치본을 "
    "함께 바꾸고, git 볼트의 엔진 사본은 갱신할 때 `/vault-upgrade`(Codex `upgrade`)로 바꾼다. "
    "`/vault-upgrade`는 사본을 이전 버전으로 내리지 않으므로, 되돌릴 때는 그 사본도 이전 버전의 파일로 "
    "직접 되돌린다.",
    "3. 바꾼 설치본에서 `vault_warning_policy.py --certificate`로 인증서를 다시 만들어 볼트 설정의 "
    "`warning_policy.certificate`에 넣는다.",
    "4. 단계를 다시 `fatal`로 올린다.",
)
# Every shorter statement of the order keeps the vault's engine copy, rollback and promoting again.
# The v0.32.0 surfaces repeat these sentences unchanged, and so does the Codex long description.
REQUIRED_V0310_UPGRADE_SENTENCES = (
    ("README.md",
     "Before upgrading or rolling back, return promoted levels to `warning`; bring every engine copy that "
     "checks the vault to the same version (the Claude Code and Codex installations and, in a git vault, "
     "the copy under `00-meta/scripts/` that the pre-commit hook runs); then run "
     "`vault_warning_policy.py --certificate` on the installation you switched to and promote again"),
    ("README.md",
     "갱신하거나 되돌리기 전에 승격 단계를 `warning`으로 되돌리고, 볼트를 검사하는 엔진 사본(Claude Code·Codex "
     "설치본과 git 볼트의 `00-meta/scripts/`)을 모두 같은 버전으로 바꾼 뒤, 바꾼 설치본에서 "
     "`vault_warning_policy.py --certificate`로 인증서를 다시 만들어 승격한다"),
    ("docs/codex.md",
     "갱신하거나 되돌리기 전에 승격 단계를 warning으로 되돌리고, Claude Code와 Codex 설치본과 git 볼트의 "
     "엔진 사본(`00-meta/scripts/`, pre-commit 훅이 실행한다)을 모두 같은 버전으로 바꾼 뒤, 바꾼 설치본에서 "
     "`vault_warning_policy.py --certificate`로 다시 생성해 승격한다"),
    ("docs/hardening.md",
     "갱신하거나 되돌리기 전에 승격 단계를 `warning`으로 되돌리고, 볼트를 검사하는 엔진 사본(Claude Code·Codex "
     "설치본과 git 볼트의 `00-meta/scripts/`)을 모두 같은 버전으로 바꾼 뒤, 바꾼 설치본에서 "
     "`vault_warning_policy.py --certificate`로 인증서를 다시 만들어 승격한다"),
)
V0310_CODEX_UPGRADE_SENTENCE = (
    "checker_sha256 changes: if warnings were promoted to fatal, revert them to warning, bring the Claude "
    "Code and Codex installations and a git vault's 00-meta/scripts engine copy to the same version (also "
    "when rolling back), regenerate the certificate there, then promote again."
)
# Wording that the release-candidate review corrected (2026-10-25), compared folded.
REQUIRED_V0310_REVIEWED_STATEMENTS = (
    # What --normalize-periods abstains on and what it still compares.
    ("README.md",
     "It abstains on periods it cannot resolve and on the listed qualifiers, range marks and list forms; "
     "text outside those fixed lists is not read, so a period can still be compared whole, by one range end "
     "or by one list item and give a wrong `equal` or `contained`"),
    ("README.md",
     "해석하지 못한 기간과 고정 목록에 있는 수식어·범위 기호·나열 표기는 보류하지만, 목록 밖의 말이나 표기는 "
     "읽지 않으므로 기간 전체나 범위의 한쪽 끝, 나열의 한 항목으로 비교되어 틀린 `equal`·`contained`가 나올 수 "
     "있다"),
    # What the ast guard flags, and the role warning in both languages.
    ("README.md",
     "an ast check flags `tests/*.py` body handlers (`do_POST`, `do_PUT`, `do_PATCH`) that never read the "
     "request body"),
    ("README.md",
     "ast 검사는 `tests/*.py`의 `do_POST`·`do_PUT`·`do_PATCH` 처리기 가운데 요청 본문을 읽는 호출이 없는 것을 "
     "찾아낸다"),
    ("README.md", "v0.30.0 and older refuse fixtures with `role` and cannot inspect or compare runs prepared with it."),
    ("README.md",
     "`role`이 있는 fixture는 v0.30.0 이하가 거절하며, 그런 fixture로 준비한 실행은 v0.30.0 이하에서 "
     "조회·비교할 수 없다."),
    # A mismatched certificate also stops the tools that validate the whole vault configuration.
    ("docs/releases/v0.31.0.md", "볼트 설정 전체를 검증하는 다른 도구도 같은 검사로 멈춘다."),
    ("docs/releases/v0.31.0.md", "`recall unavailable: invalid_config`와 종료 코드 2"),
    ("docs/releases/v0.31.0.md", "healthcheck와 staged 검사 밖의 오류 문구에는 인증서가 나오지 않는다."),
    ("docs/verification/v0.31.0.md",
     "Other tools that validate the whole vault configuration stop as well; re-run on `52325e3` the same day: "
     "`vault_recall.py` exit 2 with `recall unavailable: invalid_config`"),
    ("README.md",
     "Other tools that validate the whole vault configuration stop as well, for example `/vault-recall` "
     "reports `invalid_config` and `vault_ssot.py`, `check-support` included, reports "
     "`ssot_operation_unavailable`."),
    ("README.md",
     "볼트 설정 전체를 검증하는 다른 도구도 멈춘다(예: `/vault-recall`은 `invalid_config`, `check-support`를 "
     "포함한 `vault_ssot.py`는 `ssot_operation_unavailable`)."),
    ("docs/codex.md",
     "볼트 설정 전체를 검증하는 다른 명령도 멈춘다(예: `recall`은 `invalid_config`, `check-support`를 포함한 "
     "`vault_ssot.py`는 `ssot_operation_unavailable`)."),
    ("docs/hardening.md",
     "볼트 설정 전체를 검증하는 다른 도구도 멈춘다(예: `/vault-recall`은 `invalid_config`, 아래 "
     "`vault_ssot.py`는 `check-support`를 포함해 `ssot_operation_unavailable`)."),
    # Release-note precision: rows with a period, the last-day rule of 말, the session field and block.
    ("docs/releases/v0.31.0.md", "출처 노트와 비교한 행 가운데 값에 기간 표현이 있는 행마다 `normalized_match`"),
    ("docs/releases/v0.31.0.md",
     "`말`은 그 뒤에 글자·숫자가 오지 않거나(끝·공백·문장 부호) 문서의 조사 목록에 있는 말이 이어지면 그 기간의 "
     "마지막 하루로 계산하고"),
    ("docs/releases/v0.31.0.md",
     "`false`는 알아보지 못했다는 뜻이며 다른 훅이라는 뜻이 아니고, 기록에 명령 값이 없으면 `null`(텍스트 출력은 "
     "`none`)이다."),
    ("docs/releases/v0.31.0.md",
     "`session_start_hooks` 없이 여섯 키(`schema_version`·`status`·`reason_code`·`next_action`·"
     "`trust_boundary`·`complete`)만 있는 `unparsed`(`probe_failed`) 블록이 붙는다."),
    # Default output, --help and argparse usage lines; where the final-tree comparison is recorded.
    ("docs/releases/v0.31.0.md",
     "이 비교는 최종 트리에서 다시 실행해 `verification.json`에 남기며, "
     "[검증 기록](../verification/v0.31.0.md)은 이를 게시 전 점검 항목으로 둔다."),
    ("docs/releases/v0.31.0.md", "argparse usage 줄에도 새 옵션이 보인다. 종료 코드와 오류 문구는 같다."),
    # The Codex installation check covers the session-evidence reader and doctor.
    ("docs/releases/v0.31.0.md",
     "설치본의 `vault_period.py`·`vault_session_evidence.py`·`vault_doctor.py`도 저장소와 바이트로 대조한다."),
    ("docs/verification/v0.31.0.md",
     "installed resources verified, including `vault_period.py`, `vault_session_evidence.py` and "
     "`vault_doctor.py`"),
    # The run count, the commits after the reviewed head and where their checks are recorded.
    ("docs/releases/v0.31.0.md", "전체 시험 2,143개(통과 2,109·환경 제외 34·실패/오류 0)"),
    ("docs/releases/v0.31.0.md", "`169d8a6`부터 최종 소스까지의 커밋 목록은 `verification.json`에 남긴다."),
    ("docs/verification/v0.31.0.md",
     "The pre-publication `verification.json` lists every commit between `169d8a6` and the final release "
     "source with its checks, and records the final-tree re-runs and the independent review listed below."),
)
V0310_POST_REVIEW_COMMITS = ("`65555f1`", "`a6c4fc2`", "`3805d24`", "`c007b3b`", "`52325e3`")
FORBIDDEN_V0310_STALE_STATEMENTS = (
    ("README.md", "abstains on forms it does not read"),
    ("README.md", "읽지 못한 표현은 보류한다"),
    ("README.md", "an ast check keeps it that way"),
    ("README.md", "ast 검사가 이를 지킨다"),
    ("docs/releases/v0.31.0.md", "버전 표면을 바꾼 최종 릴리스 트리"),
    ("docs/releases/v0.31.0.md", "전체 시험 2,143개가 통과했다"),
    ("docs/releases/v0.31.0.md", "[검증 기록](../verification/v0.31.0.md)과 `verification.json`에 남긴다"),
    ("docs/verification/v0.31.0.md", "may follow"),
    (".codex-plugin/plugin.json", "update Claude Code and Codex together"),
)


def _folded(text: str) -> str:
    """Text with every whitespace run, wrapped Markdown line breaks included, folded to one space."""
    return " ".join(text.split())


def _published_text(tag: str, relative: str) -> str:
    """Read a released source without requiring an archived component in today's package."""
    tag_ref = f"refs/tags/{tag}"
    try:
        found = subprocess.run(["git", "rev-parse", "--verify", "--quiet", tag_ref],
                               cwd=REPO_ROOT, capture_output=True)
    except FileNotFoundError:
        raise unittest.SkipTest(f"cannot inspect {tag}: Git is unavailable") from None
    if found.returncode:
        raise unittest.SkipTest(f"release tag {tag} is not in the local checkout")
    return subprocess.check_output(["git", "show", f"{tag_ref}:{relative}"], cwd=REPO_ROOT).decode("utf-8")


def _module_constant(path: Path, name: str) -> object:
    """A literal module-level constant, read with ast so the module is not imported."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a literal module constant in {path.name}")


class ReleaseMetadataTests(unittest.TestCase):
    def test_v0310_release_note_covers_the_contract(self) -> None:
        self.assertTrue(RELEASE_NOTE_V0310.is_file())
        release = RELEASE_NOTE_V0310.read_text(encoding="utf-8")
        for section in REQUIRED_V0310_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V0310_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        # The note lists exactly the fields the reader reports, in its order.
        for label, constant in (("`entries`(훅마다 ", "HOOK_FIELDS"),
                                ("`failed_entries`(실패마다 ", "FAILURE_FIELDS")):
            fields = _module_constant(SESSION_EVIDENCE_SCRIPT, constant)
            with self.subTest(fields=constant):
                self.assertIn(label + "·".join(f"`{field}`" for field in fields) + ")", release)
        # Which engine copies a certificate must match, that rollback follows the same order, and
        # steps 1-4 as consecutive list lines. Leaving out the vault's copy blocks later commits.
        self.assertIn(V0310_UPGRADE_ORDER_LEAD, _folded(release))
        lines = release.splitlines()
        self.assertIn(V0310_UPGRADE_STEPS[0], lines)
        start = lines.index(V0310_UPGRADE_STEPS[0])
        self.assertEqual(tuple(lines[start:start + len(V0310_UPGRADE_STEPS)]), V0310_UPGRADE_STEPS)
        self.assertTrue((REPO_ROOT / "docs" / "verification" / "v0.31.0.md").is_file())
        for template in sorted(BROWSER_RULE.parent.glob("vault-*.md")):
            with self.subTest(rule=template.name):
                stamp = template.read_text(encoding="utf-8").splitlines()[0]
                self.assertIn("agentic-vault:rule engine=", stamp)
                self.assertNotIn("engine=0.31.0", stamp)

    def test_v0310_upgrade_and_rollback_order_is_stated(self) -> None:
        # The shorter statements of the release note's order keep the vault's engine copy,
        # rollback and promoting again; the note's own steps are pinned with its contract above.
        for rel_path, sentence in REQUIRED_V0310_UPGRADE_SENTENCES:
            with self.subTest(path=rel_path):
                self.assertIn(sentence, _folded((REPO_ROOT / rel_path).read_text(encoding="utf-8")))
        codex = json.loads((REPO_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertIn(V0310_CODEX_UPGRADE_SENTENCE, codex["interface"]["longDescription"])

    def test_v0310_reviewed_statements_stay_corrected(self) -> None:
        texts = {}
        for rel_path, statement in REQUIRED_V0310_REVIEWED_STATEMENTS:
            text = texts.setdefault(rel_path, _folded((REPO_ROOT / rel_path).read_text(encoding="utf-8")))
            with self.subTest(path=rel_path, statement=statement):
                self.assertIn(statement, text)
        for rel_path, stale in FORBIDDEN_V0310_STALE_STATEMENTS:
            text = texts.setdefault(rel_path, _folded((REPO_ROOT / rel_path).read_text(encoding="utf-8")))
            with self.subTest(path=rel_path, stale=stale):
                self.assertNotIn(stale, text)
        # Both records name the commits after the reviewed head instead of promising later cleanup.
        release = RELEASE_NOTE_V0310.read_text(encoding="utf-8")
        verification_section = release[release.index("## 검증"):release.index("## 알려진 경계")]
        record = (REPO_ROOT / "docs" / "verification" / "v0.31.0.md").read_text(encoding="utf-8")
        for commit in V0310_POST_REVIEW_COMMITS:
            with self.subTest(commit=commit):
                self.assertIn(commit, verification_section)
                self.assertIn(commit, record)

    # v0.55.0 intentionally transitions checker_sha256 after modifying vault_lint_extensions.py and vault_egress.py.
    def test_stated_source_checker_hash_matches_actual_certificate(self) -> None:
        # The expected value is declared, never derived from the implementation under test.
        # A child process reads the actual checkout beside the certificate CLI.
        script = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_warning_policy.py"
        result = subprocess.run([sys.executable, str(script), "--certificate"], capture_output=True,
                                text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["checker_sha256"],
                         CHECKER_SHA256_V0550)
        self.assertNotEqual(CHECKER_SHA256_V0550, CHECKER_SHA256_V0460)
        self.assertNotEqual(CHECKER_SHA256_V0460, CHECKER_SHA256_V0340)
        self.assertNotEqual(CHECKER_SHA256_V0340, CHECKER_SHA256_V0330)
        self.assertNotEqual(CHECKER_SHA256_V0330, CHECKER_SHA256_V0320)

    def test_v0550_checker_addenda_state_new_hash_and_coordination(self) -> None:
        # vault_lint_extensions.py and vault_egress.py changed in v0.55.0, so each addendum opens
        # above v0.46.0 with a section stating both fingerprints once and bidirectional coordination.
        for rel_path in SOURCE_CHECKER_ADDENDUM_PATHS:
            with self.subTest(path=rel_path):
                text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                self.assertEqual(text.count(V0550_CHECKER_SECTION + "\n"), 1)
                start = text.index(V0550_CHECKER_SECTION + "\n")
                self.assertLess(start, text.index(V0460_CHECKER_SECTION + "\n"))
                end = text.find("\n## ", start + len(V0550_CHECKER_SECTION))
                section = _folded(text[start:end if end != -1 else len(text)])
                self.assertIn(f"`{CHECKER_SHA256_V0550}`", section)
                self.assertIn(f"`{CHECKER_SHA256_V0460}`", section)
                for digest in (CHECKER_SHA256_V0550, CHECKER_SHA256_V0460):
                    self.assertEqual(section.count(digest[:8]), 1, digest[:8])
                self.assertIn("`38efaabde0ce1e1df4008276db9761ba1b88eff35ddd91cb8d4fc4899023d04a`", section)
                self.assertIn("`vault_lint_extensions.py`", section)
                self.assertIn("`vault_egress.py`", section)
                self.assertIn(SOURCE_CHECKER_COORDINATION, section)
                self.assertIn(V0550_RETURN_COORDINATION, section)
                for digest in (CHECKER_SHA256_V0340, CHECKER_SHA256_V0330, CHECKER_SHA256_V0320):
                    self.assertNotIn(digest[:8], section)

    def test_v0460_checker_addenda_state_new_hash_and_coordination(self) -> None:
        # vault_paths.py and vault_lint_extensions.py changed in v0.46.0, so each addendum opens
        # with a section that states both fingerprints once and the coordination in both directions.
        for rel_path in SOURCE_CHECKER_ADDENDUM_PATHS:
            with self.subTest(path=rel_path):
                text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                self.assertEqual(text.count(V0460_CHECKER_SECTION + "\n"), 1)
                start = text.index(V0460_CHECKER_SECTION + "\n")
                self.assertLess(start, text.index(CURRENT_CHECKER_SECTION))
                end = text.find("\n## ", start + len(V0460_CHECKER_SECTION))
                section = _folded(text[start:end if end != -1 else len(text)])
                self.assertIn(f"`{CHECKER_SHA256_V0460}`", section)
                self.assertIn(f"`{CHECKER_SHA256_V0340}`", section)
                for digest in (CHECKER_SHA256_V0460, CHECKER_SHA256_V0340):
                    self.assertEqual(section.count(digest[:8]), 1, digest[:8])
                self.assertIn("`38efaabde0ce1e1df4008276db9761ba1b88eff35ddd91cb8d4fc4899023d04a`", section)
                self.assertIn("`vault_paths.py`", section)
                self.assertIn("`vault_lint_extensions.py`", section)
                self.assertIn(SOURCE_CHECKER_COORDINATION, section)
                self.assertIn(V0460_RETURN_COORDINATION, section)
                for digest in (CHECKER_SHA256_V0330, CHECKER_SHA256_V0320):
                    self.assertNotIn(digest[:8], section)

    def test_current_checker_addenda_state_release_hash_and_coordination(self) -> None:
        # v0.35.1 retains the actual v0.34.0 checker/corpus bytes; a version-only
        # transition must not invent a certificate invalidation. Earlier migrations stay history.
        for rel_path in SOURCE_CHECKER_ADDENDUM_PATHS:
            with self.subTest(path=rel_path):
                text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                start = text.index(CURRENT_CHECKER_SECTION)
                end = text.index(V0340_CHECKER_SECTION)
                self.assertLess(start, end)
                section = _folded(text[start:end])
                self.assertIn(f"`{CHECKER_SHA256_V0340}`", section)
                self.assertEqual(section.count(CHECKER_SHA256_V0340[:8]), 1)
                self.assertNotIn(CHECKER_SHA256_V0330[:8], section)
                self.assertNotIn("## Development source after v0.33.1", text)
                self.assertNotIn("This development source is not a release.", _folded(text))
                self.assertNotIn("unreleased development source checkout", _folded(text))
                self.assertIn("version preparation does not certify them", section)
                self.assertIn("retains all seven certificate-bound checker files and the harmless corpus from v0.34.0", section)
                self.assertIn("does not invalidate an existing certificate or require promotion reset or certificate regeneration", section)
                self.assertIn("Older transitions that change bound bytes still require", section)
                self.assertIn("verification/v0.35.1.md)", section)

                historical = _folded(text[end:text.index(V0331_CHECKER_SECTION)])
                self.assertEqual(historical.count(CHECKER_SHA256_V0340[:8]), 1)
                self.assertIn(SOURCE_CHECKER_COORDINATION, historical)
                self.assertIn("This coordination also applies when returning to published v0.33.1.", historical)
                self.assertIn("verification/subject-boundary-20261030.md)", historical)
                self.assertIn("verification/v0.34.0.md)", historical)

    def test_v0331_checker_section_and_dated_notes_stay_as_history(self) -> None:
        # The published v0.33.1 section keeps its fingerprint, its coordination back to v0.32.0
        # and the link to its dated note.
        closing = "plugin or vault engine copy is updated by release preparation."
        for rel_path in SOURCE_CHECKER_ADDENDUM_PATHS:
            with self.subTest(path=rel_path):
                text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                start = text.index(V0331_CHECKER_SECTION)
                section = _folded(text[start:text.index(closing, start) + len(closing)])
                self.assertIn(f"`{CHECKER_SHA256_V0330}`", section)
                self.assertEqual(text.count(CHECKER_SHA256_V0330[:8]), 1)
                self.assertNotIn(CHECKER_SHA256_V0340[:8], section)
                self.assertIn("version preparation does not certify them", section)
                self.assertIn(SOURCE_CHECKER_COORDINATION, section)
                self.assertIn("This coordination also applies when returning to published v0.32.0.", section)
                self.assertIn("verification/memory-boundaries-20261028.md)", section)
                self.assertIn("verification/v0.33.1.md)", section)
        note = (REPO_ROOT / "docs/verification/memory-boundaries-20261028.md").read_text(encoding="utf-8")
        self.assertIn(f"`{CHECKER_SHA256_V0330}`", note)
        self.assertIn("e9d382a6877411c377ccb49c0d7f3fba604ba1f8", note)
        self.assertIn("Public metadata was v0.32.0 at that point", _folded(note))
        self.assertIn(f"`{CHECKER_SHA256_V0320}`", note)
        self.assertIn("vault_ssot.py", note)
        self.assertIn("LF", note)
        self.assertIn("[v0.34.0 verification record](v0.34.0.md)", note)
        # The subject-boundary note keeps its dated development statements and names the
        # implementation commit and the release candidate source.
        note = (REPO_ROOT / SOURCE_CHECKER_NOTE_20261030).read_text(encoding="utf-8")
        self.assertIn(f"`{CHECKER_SHA256_V0340}`", note)
        self.assertIn(f"`{CHECKER_SHA256_V0330}`", note)
        self.assertIn("Public metadata was v0.33.1 at that point", _folded(note))
        self.assertIn(SOURCE_CHECKER_COORDINATION, _folded(note))
        self.assertIn("vault_ssot.py", note)
        self.assertIn("LF", note)
        self.assertIn("`ac96d3eaeb7414080a01b1c40395dee3aae9f4c7`", note)
        self.assertIn("`67af284583c19b050ed93a1fc77a8cc7babb9086`", note)
        self.assertIn("[v0.34.0 verification record](v0.34.0.md)", note)
        self.assertNotIn("This is not a release", note)
        # Published records and the earlier dated note keep their own fingerprints.
        for rel_path in ("docs/releases/v0.32.0.md", "docs/verification/v0.32.0.md",
                         "docs/releases/v0.33.0.md", "docs/verification/v0.33.0.md",
                         "docs/releases/v0.33.1.md", "docs/verification/v0.33.1.md",
                         "docs/verification/memory-boundaries-20261028.md"):
            with self.subTest(published_record=rel_path):
                self.assertNotIn(CHECKER_SHA256_V0340[:8],
                                 (REPO_ROOT / rel_path).read_text(encoding="utf-8"))
        for rel_path in ("docs/releases/v0.32.0.md", "docs/verification/v0.32.0.md"):
            with self.subTest(published_record=rel_path):
                self.assertNotIn(CHECKER_SHA256_V0330,
                                 (REPO_ROOT / rel_path).read_text(encoding="utf-8"))

    def test_published_v0320_checker_hash_statements_and_history_stay_intact(self) -> None:
        texts = {rel_path: (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                 for rel_path in (*V0320_CHECKER_HASH_COUNTS, *CHECKER_HISTORY_RECORDS,
                                  ".codex-plugin/plugin.json", "SECURITY.md", "docs/README.md")}
        for rel_path in V0320_CHECKER_HASH_COUNTS:
            with self.subTest(path=rel_path):
                self.assertIn(CHECKER_SHA256_V0320, texts[rel_path])
        # README, the Codex guide and the hardening lead retain the published digest in full.
        for rel_path in ("README.md", "docs/codex.md", "docs/hardening.md"):
            with self.subTest(path=rel_path, form="full"):
                self.assertIn(f"`{CHECKER_SHA256_V0320}`", texts[rel_path])
        # Exact historical counts remain mandatory after adding a separate source fingerprint.
        for rel_path, expected in V0320_CHECKER_HASH_COUNTS.items():
            with self.subTest(path=rel_path, count=expected):
                self.assertEqual(texts[rel_path].count(CHECKER_SHA256_V0320[:8]), expected)
        for rel_path, text in texts.items():
            expected = 1 if rel_path == "docs/verification/v0.31.0.md" else 0
            with self.subTest(path=rel_path, digest_at_169d8a6=expected):
                self.assertEqual(text.count(CHECKER_SHA256_AT_169D8A6_PREFIX), expected)
        self.assertIn(CHECKER_HISTORY_AT_169D8A6, _folded(texts["docs/verification/v0.31.0.md"]))
        for rel_path, statement in (*REQUIRED_V0310_HASH_STATEMENTS, *REQUIRED_V0320_HASH_STATEMENTS):
            with self.subTest(path=rel_path, statement=statement):
                self.assertIn(statement, _folded(texts[rel_path]))
        for rel_path, stale in FORBIDDEN_V0320_STALE_STATEMENTS:
            with self.subTest(path=rel_path, stale=stale):
                self.assertNotIn(stale, _folded(texts[rel_path]))

    def test_v0230_release_note_covers_the_contract(self) -> None:
        self.assertTrue(RELEASE_NOTE_V0230.is_file())
        release = RELEASE_NOTE_V0230.read_text(encoding="utf-8")
        for section in REQUIRED_V0230_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V0230_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        self.assertTrue((REPO_ROOT / "docs" / "verification" / "v0.23.0.md").is_file())
        for template in sorted(BROWSER_RULE.parent.glob("vault-*.md")):
            with self.subTest(rule=template.name):
                stamp = template.read_text(encoding="utf-8").splitlines()[0]
                self.assertIn("agentic-vault:rule engine=", stamp)
                self.assertNotIn("engine=0.23.0", stamp)

    def test_v0171_context_delivery_fixes_are_wired(self) -> None:
        self.assertTrue(RELEASE_NOTE_V0171.is_file())
        release = RELEASE_NOTE_V0171.read_text(encoding="utf-8")
        for section in REQUIRED_V0171_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V0171_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        self.assertTrue((REPO_ROOT / "docs" / "verification" / "v0.17.1.md").is_file())
        for rel_path, literal in REQUIRED_V0171_WIRING:
            with self.subTest(path=rel_path, literal=literal):
                self.assertIn(literal, (REPO_ROOT / rel_path).read_text(encoding="utf-8"))
        for rel_path, literal in FORBIDDEN_V0171_STALE_LITERALS:
            with self.subTest(path=rel_path, stale=literal):
                self.assertNotIn(literal, (REPO_ROOT / rel_path).read_text(encoding="utf-8"))
        # 규칙 내용은 바뀌지 않았으므로 어느 규칙 템플릿도 0.17.1 스탬프를 달지 않는다.
        for template in sorted(BROWSER_RULE.parent.glob("vault-*.md")):
            with self.subTest(rule=template.name):
                stamp = template.read_text(encoding="utf-8").splitlines()[0]
                self.assertNotIn("engine=0.17.1", stamp)

    def test_release_note_records_v0120_contract(self) -> None:
        self.assertTrue(RELEASE_NOTE_V0120.is_file())
        release = RELEASE_NOTE_V0120.read_text(encoding="utf-8")
        for section in REQUIRED_V0120_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V0120_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        self.assertTrue((REPO_ROOT / "docs" / "verification" / "v0.12.0.md").is_file())

    def test_v0120_browser_rule_is_wired(self) -> None:
        self.assertTrue(BROWSER_RULE.is_file())
        text = BROWSER_RULE.read_text(encoding="utf-8")
        self.assertIn(REQUIRED_BROWSER_RULE_STAMP, text)
        for literal in REQUIRED_BROWSER_RULE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        body = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("<!--")]
        self.assertLessEqual(len(body), 120)  # rules_max_lines 기본값 — 상시 로드 파일은 얇게
        templates = sorted(p.name for p in BROWSER_RULE.parent.glob("vault-*.md"))
        self.assertEqual(len(templates), 6)
        for rel_path, literal in REQUIRED_SIX_RULES_WIRING:
            with self.subTest(path=rel_path, literal=literal):
                self.assertIn(literal, (REPO_ROOT / rel_path).read_text(encoding="utf-8"))
        for name in UNCHANGED_RULE_TEMPLATES:
            with self.subTest(template=name):
                stamp = (BROWSER_RULE.parent / name).read_text(encoding="utf-8").splitlines()[0]
                self.assertIn("agentic-vault:rule engine=", stamp)
                self.assertNotIn("engine=0.12.0", stamp)

    def test_v0160_run_discipline_is_wired(self) -> None:
        self.assertTrue(RELEASE_NOTE_V0160.is_file())
        release = RELEASE_NOTE_V0160.read_text(encoding="utf-8")
        for section in REQUIRED_V0160_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V0160_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        self.assertTrue((REPO_ROOT / "docs" / "verification" / "v0.16.0.md").is_file())
        collab = (REPO_ROOT / "assets/templates/rules/vault-collab.md").read_text(encoding="utf-8")
        self.assertIn(REQUIRED_COLLAB_RULE_STAMP, collab.splitlines()[0])
        for rel_path, literal in REQUIRED_V0160_WIRING:
            with self.subTest(path=rel_path, literal=literal):
                self.assertIn(literal, (REPO_ROOT / rel_path).read_text(encoding="utf-8"))
        for rel_path, literal in FORBIDDEN_V0160_STALE_LITERALS:
            with self.subTest(path=rel_path, stale=literal):
                self.assertNotIn(literal, (REPO_ROOT / rel_path).read_text(encoding="utf-8"))
        for name in ("vault-workflow.md", "vault-collab.md"):
            with self.subTest(rule=name):
                text = (BROWSER_RULE.parent / name).read_text(encoding="utf-8")
                body = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("<!--")]
                self.assertLessEqual(len(body), 120)  # rules_max_lines 기본값 — 상시 로드 파일은 얇게
        # 내용이 바뀌지 않은 규칙은 스탬프를 올리지 않는다(무의미한 전 볼트 교체 방지).
        for name in ("vault-architecture.md", "vault-linking.md", "vault-frontmatter.md"):
            with self.subTest(unchanged_rule=name):
                stamp = (BROWSER_RULE.parent / name).read_text(encoding="utf-8").splitlines()[0]
                self.assertIn("agentic-vault:rule engine=", stamp)
                self.assertNotIn("engine=0.16.0", stamp)


    def test_v0170_aside_adapter_is_wired(self) -> None:
        self.assertTrue(RELEASE_NOTE_V0170.is_file())
        release = RELEASE_NOTE_V0170.read_text(encoding="utf-8")
        for section in REQUIRED_V0170_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V0170_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        self.assertTrue((REPO_ROOT / "docs" / "verification" / "v0.17.0.md").is_file())
        for rel_path, literal in REQUIRED_V0170_WIRING:
            with self.subTest(path=rel_path, literal=literal):
                self.assertIn(literal, (REPO_ROOT / rel_path).read_text(encoding="utf-8"))
        for name in ("aside-up.ps1", "browser-clause.md", "operations-guide.md", "settings-hooks.json"):
            with self.subTest(asset=name):
                self.assertTrue((REPO_ROOT / "assets" / "adapters" / "aside" / name).is_file())
        self.assertTrue((REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_adapters.py").is_file())
        for name in UNCHANGED_RULE_TEMPLATES:
            with self.subTest(unchanged_rule=name):
                stamp = (BROWSER_RULE.parent / name).read_text(encoding="utf-8").splitlines()[0]
                self.assertNotIn("engine=0.17.0", stamp)

    def test_active_version_surfaces_match_release(self) -> None:
        plugin = json.loads(
            PLUGIN_MANIFEST.read_text(encoding="utf-8-sig")
        )
        market = json.loads(
            MARKETPLACE_MANIFEST.read_text(encoding="utf-8-sig")
        )
        readme = README.read_text(encoding="utf-8")
        readme_lines = readme.splitlines()

        self.assertEqual(plugin["version"], EXPECTED)
        self.assertEqual(market["plugins"][0]["version"], EXPECTED)
        codex = json.loads((REPO_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        codex_market = json.loads((REPO_ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["version"], EXPECTED)
        self.assertIn(f"v{EXPECTED}", codex["interface"]["longDescription"])
        self.assertEqual(codex["name"], plugin["name"])
        entry = codex_market["plugins"][0]
        self.assertEqual(entry["name"], codex["name"])
        self.assertEqual(entry["source"]["source"], "local")
        self.assertEqual((REPO_ROOT / entry["source"]["path"]).resolve(), REPO_ROOT)
        self.assertTrue((REPO_ROOT / codex["skills"] / "agentic-vault/SKILL.md").is_file())
        self.assertFalse((REPO_ROOT / codex["skills"] / "webapp-principles").exists())
        self.assertFalse((REPO_ROOT / "docs/webapp-principles.md").exists())
        self.assertTrue((REPO_ROOT / "docs/releases" / f"v{EXPECTED}.md").is_file())
        self.assertIn(EXPECTED_BADGE_LINE, readme_lines)
        self.assertIn(EXPECTED_TREE_LINE, readme_lines)
        self.assertIn(f"현재 버전은 **v{EXPECTED}**", readme)
        self.assertIn(f"releases/tag/v{EXPECTED_PUBLIC_REF}", readme)
        self.assertIn(f"[이번 변경·검증 범위](docs/releases/v{EXPECTED}.md)", readme)
        self.assertTrue((REPO_ROOT / "docs/verification" / f"v{EXPECTED}.md").is_file())
        for install_doc in (readme, (REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8")):
            refs = [line.strip() for line in install_doc.splitlines() if line.startswith("codex plugin marketplace add Technoetic/agentic-vault --ref ")]
            self.assertTrue(refs)
            self.assertEqual(set(refs), {f"codex plugin marketplace add Technoetic/agentic-vault --ref v{EXPECTED_PUBLIC_REF}"})
        for origin in EXPECTED_HISTORICAL_ORIGINS:
            with self.subTest(origin=origin):
                self.assertIn(origin, readme)

    def test_release_note_records_v082_contract(self) -> None:
        self.assertTrue(
            RELEASE_NOTE_V082.is_file(),
            f"missing release note: {RELEASE_NOTE_V082.relative_to(REPO_ROOT)}",
        )
        release = RELEASE_NOTE_V082.read_text(encoding="utf-8")
        release_lines = release.splitlines()

        self.assertIn("0.8.2", release)
        self.assertIn(EXPECTED_JARVIS_BOUNDARY_LINE, release_lines)
        for section in REQUIRED_V082_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V082_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        for line in EXPECTED_SCOPE_UPGRADE_LINES:
            with self.subTest(line=line):
                self.assertIn(line, release_lines)
        self.assertIn(EXPECTED_LEGACY_BRIEFING_UPGRADE_LINE, release_lines)

    def test_release_note_records_v083_contract(self) -> None:
        self.assertTrue(
            RELEASE_NOTE_V083.is_file(),
            f"missing release note: {RELEASE_NOTE_V083.relative_to(REPO_ROOT)}",
        )
        release = RELEASE_NOTE_V083.read_text(encoding="utf-8")

        self.assertIn("0.8.3", release)
        for section in REQUIRED_V083_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V083_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)

    def test_v083_loop_contract_is_wired(self) -> None:
        session_end = (REPO_ROOT / "commands" / "vault-session-end.md").read_text(
            encoding="utf-8"
        )
        lessons = (
            REPO_ROOT / "assets" / "templates" / "lessons.md"
        ).read_text(encoding="utf-8")
        workflow_rule = (
            REPO_ROOT / "assets" / "templates" / "rules" / "vault-workflow.md"
        ).read_text(encoding="utf-8")

        for literal in REQUIRED_SESSION_END_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, session_end)
        for literal in REQUIRED_LESSONS_TEMPLATE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, lessons)
        self.assertIn(REQUIRED_WORKFLOW_RULE_STAMP, workflow_rule)

    def test_workflow_rule_puts_prohibitions_before_fact_precedence(self) -> None:
        # The wording order is the measured property: docs/hardening.md explains why.
        text = (BROWSER_RULE.parent / "vault-workflow.md").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if "결과의 값을 쓰고" in ln)
        self.assertLess(line.index("어떤 문구로도 풀리지 않으므로"), line.index("결과의 값을 쓰고"))
        self.assertIn("해도 되는지는 그 규칙으로만 판단하라", line)
        self.assertIn("어긋난 곳을 하나씩 보고하라", line)

    def test_v083_index_spec_is_wired(self) -> None:
        for rel_path, literal in REQUIRED_INDEX_SPEC_WIRING:
            with self.subTest(path=rel_path):
                content = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                self.assertIn(literal, content)

    def test_release_note_records_v084_contract(self) -> None:
        self.assertTrue(
            RELEASE_NOTE_V084.is_file(),
            f"missing release note: {RELEASE_NOTE_V084.relative_to(REPO_ROOT)}",
        )
        release = RELEASE_NOTE_V084.read_text(encoding="utf-8")

        self.assertIn("0.8.4", release)
        for section in REQUIRED_V084_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V084_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        script = HEALTHCHECK_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(REQUIRED_HEALTHCHECK_STAMP, script)
        self.assertIn(f'ENGINE_VERSION = "{EXPECTED}"', script)

    def test_release_note_records_v090_contract(self) -> None:
        self.assertTrue(
            RELEASE_NOTE_V090.is_file(),
            f"missing release note: {RELEASE_NOTE_V090.relative_to(REPO_ROOT)}",
        )
        release = RELEASE_NOTE_V090.read_text(encoding="utf-8")

        self.assertIn("v0.9.0", release)
        for section in REQUIRED_V090_RELEASE_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in REQUIRED_V090_RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        # 사전 릴리스 식별자가 활성 표면(매니페스트·README 배지·트리·Codex ref)에 남아 있으면 안 된다
        for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".codex-plugin/plugin.json"):
            with self.subTest(path=rel):
                self.assertNotIn("local.2", (REPO_ROOT / rel).read_text(encoding="utf-8-sig"))
        readme = README.read_text(encoding="utf-8")
        self.assertNotIn("--ref v0.9.0-local.2", readme)
        self.assertNotIn("v0.9.0--local.2", readme)
        self.assertNotIn("releases/tag/v0.9.0-local.2", readme)
        self.assertIn("(docs/releases/v0.9.0.md)", readme)


# v0.32.0: linear-time heading, frontmatter, code, link and wiki-link parsing (results unchanged), the
# check-support body edges R10-R12 (the only default verdict changes), the opt-in period-normalization
# metamorphic suite, gold-removal twins and keyed-token row order, the real-vault sample audit and the
# opt-in deny-zone guard. Checker-bound files changed, so the note carries both hashes and the v0.31.0
# upgrade and rollback order. The published digest is CHECKER_SHA256_V0320.
class V0320ReleaseNoteTests(unittest.TestCase):
    RELEASE_NOTE = REPO_ROOT / "docs" / "releases" / "v0.32.0.md"
    VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.32.0.md"
    NEW_CHECKER_SHA256 = CHECKER_SHA256_V0320
    SECTIONS = ("## 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계")
    LITERALS = (
        # Linear-time parsing: the shared heading helper, the oracle tests and the removed module names.
        "`_atx_heading`", "`tests/test_linear_line_parsers.py`", "`wikilink_targets`",
        "`vault_healthcheck.WIKILINK_RE`", "`RULE_HEADING_RE`", "`vault_recall._TITLE_RE`", "`_HEADING_RE`",
        "`vault_ssot.CODE_SPAN_RE`", "`split_frontmatter`", "1,948,717", "`AGENTIC_VAULT_SLOW_TESTS=1`",
        "21.15초", "1.4 µs", "48.6초", "0.019초",
        # check-support body edges R10-R12 and their examples.
        "R10·R11·R12", "`ledger_table_only`", "`rows_supported_ledger_table_only`", "`--primary-text-only`",
        "`ssot-source-unsupported`", 'reason: "ledger_table_only"', "`ledger_tables_unseparated`",
        "`via_ledger_table_only`", "U+2028", "`utf-8-sig`", "NUL", "`기준 연도 2026년`",
        "`3분기 계획은 별도.`", "`title: 36개월`",
        # Period normalization: the metamorphic ratchet, the differential test, abstentions and recoveries.
        "`tests/test_vault_period_metamorphic.py`", "`KNOWN_FAILURES`", "`KNOWN_FAILURE_COUNTS`",
        "`PeriodDifferentialTests`", "`unittest.expectedFailure`", "`fetch-depth: 0`", "14,775",
        "561건 중 264건", "74건 중 16건", "1,764건 중 108건", "537·74·864건", "`초과`", "`late 2026Q3`",
        "`FY2026 & Q3`", "`period_convention`", "CheckList",
        # Gold-removal twins and keyed-token row order (scripts/evaluate_recall.py).
        "`--gold-removal-twins`", "`gold_removal_twins`", "`refused_empty_complete`", "`stale_only`",
        "`returned_candidates`", "`twin_error`", "`twin_error_reason`", "`retrieval_not_ok`",
        "`search_incomplete`", "`note_scan_incomplete`", "`possibly_still_answerable`", "`operating_point`",
        "`--redact-row-order token`", 'row_order: "sorted_by_keyed_id_token"', "`--redact-key-file`",
        "`--redact-ephemeral`", "`diagnostics.temporal`", "arXiv 2610.05348", "26/56", "30/56",
        # Real-vault sample audit (scripts/real_vault_audit.py, eval_stats).
        "`scripts/real_vault_audit.py`", "`draw`", "`bound`", "`eval_stats.hypergeometric_upper_bound`",
        "`sample_sha256`", "`population_sha256`", "`population_verified`", "`confidence_mismatch`",
        "`sample_mismatch`", "`--population`", "`--confidence`", "`--flagged-true-positives`",
        "`upper_misses`", "Systematic Reviews 9:273",
        # Opt-in deny-zone guard and its adapter report.
        "`assets/adapters/deny-guard/`", "`deny-guard.py`", "`ps-parse.ps1`", "`settings-hooks.json`",
        "`hooks/hooks.json`", "`.claude/settings.local.json`", "`adapters.deny_guard`",
        "`--offer-deny-guard`", "`offer_deny_guard`", "`offer_guard_update`", "`review_guard`",
        "`review_settings`", "`absent`", "`installed`", "`incomplete`", "`needs_review`",
        "`deny_guard.state`", '`"onFailure": "block"`', "v2.1.295", "fail-open", "`Move-Item`",
        "`Parser.ParseInput`", "`allow`", "4-2단계",
        # Cleanup and packaging.
        "`# v7.0.1`", "`# v7.0.0`", "`ref-version-mismatch`", "`tests/test_supply_chain.py`",
        "`text eol=lf`", "`skills/agentic-vault/scripts/resources/lint-benign-v1.json`", "`4b31a673…`",
        "`38efaabd…`", "`scripts/verify_codex_plugin.py`",
        # Command documents, upgrade, rollback and version surfaces.
        "`/vault-init`", "`/vault-doctor`", "`/vault-upgrade`", "`/vault-process-inbox`",
        "`/vault-session-end`", "checker_sha256", "vault_warning_policy.py --certificate",
        "`invalid_config`", "`ssot_operation_unavailable`", "`--g`·`--go`·`--gold`", "--ref v0.32.0",
        "engine=0.32.0", "`vault_ssot.py`·`vault_lint_extensions.py`·`vault_provenance.py`",
    )
    # Statements compared with whitespace runs folded, as for v0.31.0.
    RELEASE_STATEMENTS = (
        "경고 승격 인증서의 `checker_sha256`가 바뀐다. 경고를 `fatal`로 올린 볼트는 갱신하거나 되돌리기 전에 "
        "[아래 순서](#하위호환업그레이드)를 따른다.",
        "이 세 경우가 이번 버전에서 기본 판정이 바뀌는 유일한 곳이며",
        "플러그인 훅 정의(`hooks/hooks.json`)도 바꾸지 않는다",
        "두 방향 확인은 최종 트리에서 다시 실행해 `verification.json`에 남긴다.",
        "근거는 아래와 같으며 모든 입력에 대한 증명은 아니다",
        "로컬 전체 시험 2,297개(통과 2,258·환경 제외 38·실패 1)",
        "이번 릴리스는 이 도구로 실제 볼트를 재지 않았다.",
        "골드셋만 가진 사람이 위치로 행을 맞추는 일만 막을 뿐 익명화가 아니다.",
        "실제 Claude Code 세션 안에서 훅으로 실행해 확인하지 않았다",
        "수정 커밋은 수정한 쪽이 시험과 수정 전 커밋에서의 실패로 확인했으며 따로 다시 검토하지 않았다.",
    )
    VERIFICATION_STATEMENTS = (
        "Pending gates are not passes.",
        "2,297 tests, 1 failure, 38 skipped",
        "Not run as a hook inside a live Claude Code session",
        "installed resources verified, including `vault_adapters.py`, `deny-guard.py` and `ps-parse.ps1`",
    )

    def test_v0320_release_note_covers_the_contract(self) -> None:
        self.assertTrue(self.RELEASE_NOTE.is_file())
        self.assertTrue(self.VERIFICATION.is_file())
        release = self.RELEASE_NOTE.read_text(encoding="utf-8")
        for section in self.SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, release)
        for literal in self.LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        folded = _folded(release)
        for statement in self.RELEASE_STATEMENTS:
            with self.subTest(statement=statement):
                self.assertIn(statement, folded)

    def test_v0320_upgrade_and_rollback_order_is_the_v0310_order(self) -> None:
        # The same engine copies, the same rollback rule and the same four consecutive steps.
        release = self.RELEASE_NOTE.read_text(encoding="utf-8")
        self.assertIn(V0310_UPGRADE_ORDER_LEAD, _folded(release))
        lines = release.splitlines()
        self.assertIn(V0310_UPGRADE_STEPS[0], lines)
        start = lines.index(V0310_UPGRADE_STEPS[0])
        self.assertEqual(tuple(lines[start:start + len(V0310_UPGRADE_STEPS)]), V0310_UPGRADE_STEPS)

    def test_v0320_states_both_checker_hashes(self) -> None:
        release = self.RELEASE_NOTE.read_text(encoding="utf-8")
        record = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertIn(f"v0.31.0의 `{CHECKER_SHA256_V0310}`에서 `{self.NEW_CHECKER_SHA256}`로 바뀐다",
                      _folded(release))
        self.assertIn(f"`{CHECKER_SHA256_V0310}` in v0.31.0, `{self.NEW_CHECKER_SHA256}` in v0.32.0",
                      _folded(record))
        # One statement of the new hash in each document, so a stale second statement fails too.
        for name, text in (("release note", release), ("verification", record)):
            with self.subTest(document=name):
                self.assertEqual(text.count(self.NEW_CHECKER_SHA256), 1)
                self.assertIn(CHECKER_SHA256_V0310, text)

    def test_v0320_verification_record_separates_evidence_from_gates(self) -> None:
        record = self.VERIFICATION.read_text(encoding="utf-8")
        for heading in ("## Implementation evidence", "## Final release gates", "## Limits"):
            with self.subTest(heading=heading):
                self.assertIn(heading, record)
        folded = _folded(record)
        for statement in self.VERIFICATION_STATEMENTS:
            with self.subTest(statement=statement):
                self.assertIn(statement, folded)

    def test_v0320_documents_link_to_existing_files(self) -> None:
        import re
        from urllib.parse import unquote

        for document in (self.RELEASE_NOTE, self.VERIFICATION):
            text = document.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / unquote(target.split("#", 1)[0])).exists(), target)

    def test_v0320_rule_templates_keep_their_stamps(self) -> None:
        # Rule template content did not change, so no rule stamp moves to 0.32.0.
        for template in sorted(BROWSER_RULE.parent.glob("vault-*.md")):
            with self.subTest(rule=template.name):
                stamp = template.read_text(encoding="utf-8").splitlines()[0]
                self.assertIn("agentic-vault:rule engine=", stamp)
                self.assertNotIn("engine=0.32.0", stamp)


class V0390ReleaseMetadataTests(unittest.TestCase):
    def test_scope_and_evidence_boundaries_are_explicit(self) -> None:
        release = (REPO_ROOT / "docs/releases/v0.39.0.md").read_text(encoding="utf-8")
        for literal in ("scripts/evaluate_memory_stages.py",
                        "scripts/evaluate_model_korean_memory.py", "writing", "management", "retrieval",
                        "deterministic_experimental_intervention", "scripted-control",
                        "original fixtures", "not an official Inspect SDK integration",
                        "NoMIRACL dataset is not copied", "not actual-model performance evidence",
                        "--ref v0.39.0", CHECKER_SHA256_V0340, "harmless replay"):
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        record = (REPO_ROOT / "docs/verification/v0.39.0.md").read_text(encoding="utf-8")
        for literal in ("## Implementation evidence", "## Final release gates", "## Limits",
                        "pending, not passes", "verification.json", "post-publication.json",
                        "exact final source", "No actual-model inference", "seven certificate-bound",
                        "No old tag is rewritten", "Receipt hashes bind", "not authenticate"):
            with self.subTest(literal=literal):
                self.assertIn(literal, record)
        for old in (release, record):
            self.assertIn("unpublished candidate", old)
            self.assertIn("v0.39.1", old)
        patch = (REPO_ROOT / "docs/releases/v0.39.1.md").read_text(encoding="utf-8")
        verification = (REPO_ROOT / "docs/verification/v0.39.1.md").read_text(encoding="utf-8")
        for literal in ("unpublished v0.39.0", "41660095934", "macOS", "Python 3.10",
                        "request_timeout", "transport_error", "test-only", "--ref v0.39.1",
                        "No actual-model inference", CHECKER_SHA256_V0340):
            with self.subTest(patch_literal=literal):
                self.assertIn(literal, patch)
        for literal in ("## Implementation evidence", "## Final release gates", "## Limits",
                        "pending, not passes", "No old tag is rewritten", "verification.json",
                        "post-publication.json", "exact final source", "unpublished v0.39.0"):
            with self.subTest(patch_verification=literal):
                self.assertIn(literal, verification)
        for candidate in (patch, verification):
            for literal in ("unpublished candidate", "v0.39.2", "41664026652", "126014409284"):
                self.assertIn(literal, candidate)
        latest = (REPO_ROOT / "docs/releases/v0.39.2.md").read_text(encoding="utf-8")
        latest_record = (REPO_ROOT / "docs/verification/v0.39.2.md").read_text(encoding="utf-8")
        for literal in ("unpublished v0.39.1", "41664026652", "126014409284", "Windows",
                        "Python 3.12", "clock-independent", "request_timeout", "transport_error",
                        "attempt", "server acceptance", "No actual-model inference",
                        "--ref v0.39.2", CHECKER_SHA256_V0340):
            with self.subTest(latest_literal=literal):
                self.assertIn(literal, latest)
        for literal in ("## Implementation evidence", "## Final release gates", "## Limits",
                        "pending, not passes", "No old tag is rewritten", "verification.json",
                        "post-publication.json", "exact final source", "unpublished v0.39.1"):
            with self.subTest(latest_verification=literal):
                self.assertIn(literal, latest_record)
        for candidate in (latest, latest_record):
            for literal in ("unpublished candidate", "v0.39.3", "41671850228", "126042012584"):
                self.assertIn(literal, candidate)
        current = (REPO_ROOT / "docs/releases/v0.39.3.md").read_text(encoding="utf-8")
        current_record = (REPO_ROOT / "docs/verification/v0.39.3.md").read_text(encoding="utf-8")
        for literal in ("unpublished v0.39.2", "41671850228", "126042012584", "Ubuntu",
                        "Python 3.14", "test_supply_chain.py", "35", "45", "10",
                        "strict", "--ref v0.39.3", "No actual-model inference",
                        CHECKER_SHA256_V0340):
            with self.subTest(current_literal=literal):
                self.assertIn(literal, current)
        for literal in ("## Implementation evidence", "## Final release gates", "## Limits",
                        "pending, not passes", "No old tag is rewritten", "verification.json",
                        "post-publication.json", "exact final source", "unpublished v0.39.2"):
            with self.subTest(current_verification=literal):
                self.assertIn(literal, current_record)

    def test_ci_adds_only_offline_smokes_to_existing_matrix(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        unittest_job = workflow.split("  unittest:", 1)[1].split("\n  stateful:", 1)[0]
        for command in ("python scripts/evaluate_memory_stages.py",
                        "python scripts/evaluate_model_korean_memory.py"):
            with self.subTest(command=command):
                self.assertEqual(workflow.count("run: " + command + "\n"), 1)
                self.assertIn("run: " + command + "\n", unittest_job)
        self.assertIn("os: [ubuntu-latest, windows-latest, macos-latest]", unittest_job)
        self.assertIn("python: ['3.10', '3.12', '3.14']", unittest_job)
        self.assertNotIn("--allow-network", workflow)

    def test_current_guides_index_both_checkout_only_evaluators(self) -> None:
        readme = README.read_text(encoding="utf-8")
        codex = (REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8")
        index = (REPO_ROOT / "docs/README.md").read_text(encoding="utf-8")
        for text in (readme, codex, index):
            for guide in ("memory-stage-evaluation.md", "model-korean-memory.md"):
                with self.subTest(guide=guide):
                    self.assertIn(guide, text)
        self.assertIn("## v0.39.3", readme)
        self.assertIn("## v0.39.3", codex)
        security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn(f"| {EXPECTED} (latest) | Yes.", security)


class V0410ReleaseMetadataTests(unittest.TestCase):
    def test_scope_and_evidence_boundaries_are_explicit(self) -> None:
        release = _folded((REPO_ROOT / "docs/releases/v0.41.0.md").read_text(encoding="utf-8"))
        for literal in ("source-bound", "sidecar", "typed links", "metric definitions",
                        "exact", "structured plan", "ready_for_evidence", "read-only CLI",
                        "No NLP", "not an MCP server", "No new model-performance observation",
                        "--ref v0.41.0", CHECKER_SHA256_V0340, "harmless replay",
                        "2a24f5d34ef6b6e168a7f22d308504d3ab36a198", "NVIDIA",
                        "Reused", "new", "company vault"):
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        verification = _folded((REPO_ROOT / "docs/verification/v0.41.0.md").read_text(encoding="utf-8"))
        for literal in ("## Implementation evidence", "## Final release gates",
                        "pending, not passes", "exact final source", "master/tag CI",
                        "verification.json", "post-publication.json", "independent",
                        "No old tag is rewritten", CHECKER_SHA256_V0340):
            with self.subTest(verification=literal):
                self.assertIn(literal, verification)

    def test_ontology_resources_are_bundled_and_indexed(self) -> None:
        for relative in ("skills/agentic-vault/scripts/vault_ontology.py",
                         "skills/agentic-vault/scripts/vault_ontology_query.py",
                         "skills/agentic-vault/scripts/vault_ontology_cli.py",
                         "docs/ontology-contracts.md"):
            with self.subTest(resource=relative):
                self.assertTrue((REPO_ROOT / relative).is_file(), relative)
                for inventory in ("scripts/verify_codex_plugin.py", "tests/test_codex_verification.py"):
                    self.assertIn(relative, (REPO_ROOT / inventory).read_text(encoding="utf-8"))
        for relative in ("README.md", "docs/codex.md", "docs/README.md"):
            with self.subTest(index=relative):
                self.assertIn("ontology-contracts.md", (REPO_ROOT / relative).read_text(encoding="utf-8"))

    def test_previous_release_records_keep_canonical_bytes(self) -> None:
        for relative, expected in {
            "docs/releases/v0.40.1.md": "49c56adcf002e75bd14905c1a865edf5d01c580f7ad183c256917acc93d09882",
            "docs/verification/v0.40.1.md": "fafd9dd45dffea5164c410999665154056c4054470c188cd7d16b4632e0b55b7",
        }.items():
            with self.subTest(path=relative):
                canonical = (REPO_ROOT / relative).read_text(encoding="utf-8").encode("utf-8")
                self.assertEqual(hashlib.sha256(canonical).hexdigest(), expected)


class V0360ReleaseMetadataTests(unittest.TestCase):
    def test_release_scope_and_pending_gates(self) -> None:
        release = (REPO_ROOT / "docs/releases/v0.36.0.md").read_text(encoding="utf-8")
        verification = (REPO_ROOT / "docs/verification/v0.36.0.md").read_text(encoding="utf-8")
        for literal in ("64 KiB", "CRLF", "OKF v0.2", "frontmatter", "checkpoint",
                        "--ref v0.36.0", CHECKER_SHA256_V0340, "2,671"):
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        for literal in ("pending, not passes", "three module reruns", "not a fresh monolithic",
                        "verification.json", "post-publication.json", CHECKER_SHA256_V0340):
            with self.subTest(literal=literal):
                self.assertIn(literal, verification)


class V0330ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs/releases/v0.33.0.md"
    VERIFICATION = REPO_ROOT / "docs/verification/v0.33.0.md"
    # The published v0.33.1 patch records; v0.34.0 has its own class below.
    PATCH_RELEASE = REPO_ROOT / "docs/releases/v0.33.1.md"
    PATCH_VERIFICATION = REPO_ROOT / "docs/verification/v0.33.1.md"

    def test_v0330_feature_scope_and_migration_are_explicit(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        for literal in ("## 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계",
                        "scripts/evaluate_state_revisions.py", "scripts/evaluate_evidence_insufficiency.py",
                        "--row-references", "--bind-subject", "audit_population", "binding_warnings",
                        "파싱한 모든 사실 행", "같은 물리 줄", "모델 효능", "56건", "--ref v0.33.0",
                        CHECKER_SHA256_V0320, CHECKER_SHA256_V0330, "harmless replay"):
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        self.assertIn(V0310_UPGRADE_ORDER_LEAD, _folded(release))
        lines = release.splitlines()
        first = lines.index(V0310_UPGRADE_STEPS[0])
        self.assertEqual(lines[first:first + 2], list(V0310_UPGRADE_STEPS[:2]))
        self.assertTrue(lines[first + 2].startswith(V0310_UPGRADE_STEPS[2]))
        self.assertEqual(lines[first + 3], V0310_UPGRADE_STEPS[3])
        # Keep the unpublished candidate contract above, and separately constrain the patch.
        current = self.PATCH_RELEASE.read_text(encoding="utf-8")
        for literal in ("v0.33.0은 macOS CI의 시험 fixture 실패로 공개하지 않은 후보",
                        "공개 기준판은 v0.32.0", "/var", "/private/var", "Path(...).resolve()",
                        "엄격한 런타임 경로 가드는 유지", "제품 평가기·SSOT 로직은 바꾸지 않는다",
                        "--ref v0.33.1", "--row-references", "--bind-subject",
                        CHECKER_SHA256_V0320, CHECKER_SHA256_V0330,
                        "harmless replay", "verification.json", "post-publication.json"):
            with self.subTest(current_contract=literal):
                self.assertIn(literal, current)
        self.assertIn(V0310_UPGRADE_ORDER_LEAD, _folded(current))
        current_lines = current.splitlines()
        current_first = current_lines.index(V0310_UPGRADE_STEPS[0])
        self.assertEqual(current_lines[current_first:current_first + 2], list(V0310_UPGRADE_STEPS[:2]))
        self.assertTrue(current_lines[current_first + 2].startswith(V0310_UPGRADE_STEPS[2]))
        self.assertEqual(current_lines[current_first + 3], V0310_UPGRADE_STEPS[3])

    def test_v0330_verification_separates_implementation_from_release_gates(self) -> None:
        record = self.VERIFICATION.read_text(encoding="utf-8")
        for literal in ("## Implementation evidence", "## Final release gates", "## Limits",
                        "e9d382a6877411c377ccb49c0d7f3fba604ba1f8", "2,537", "1,407.344", "43 skips",
                        "Fifteen CLI checks", "56 flags-off", "not results for the final release source",
                        "healthcheck is outside", CHECKER_SHA256_V0320, CHECKER_SHA256_V0330,
                        "following gates are pending", "verification.json", "post-publication.json",
                        "zero model turns", "Master and tag CI", "L1 byte", "L2 Git-blob/member",
                        "Scripted controls are not actual-model efficacy evidence"):
            with self.subTest(literal=literal):
                self.assertIn(literal, _folded(record))
        current = self.PATCH_VERIFICATION.read_text(encoding="utf-8")
        for literal in ("## Candidate history and scoped fix", "## Evidence boundaries",
                        "## Final release gates", "## Limits", "was not published",
                        "published baseline remains v0.32.0", "old failed CI evidence",
                        "Path(...).resolve()", "strict runtime guards remain intact",
                        "No evaluator or SSOT runtime logic changes", "2,543 tests",
                        "not a final v0.33.1 CI result", "these gates are pending",
                        "verification.json", "post-publication.json", "No old tag is rewritten",
                        CHECKER_SHA256_V0320, CHECKER_SHA256_V0330):
            with self.subTest(current_evidence=literal):
                self.assertIn(literal, _folded(current))

    def test_v0330_document_links_resolve(self) -> None:
        import re
        from urllib.parse import unquote

        for document in (self.RELEASE, self.VERIFICATION, self.PATCH_RELEASE, self.PATCH_VERIFICATION):
            for target in re.findall(r"\]\(([^)\s]+)\)", document.read_text(encoding="utf-8")):
                if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / unquote(target.split("#", 1)[0])).exists())

    def test_historical_release_records_keep_canonical_bytes(self) -> None:
        # Text-mode reading canonicalizes platform checkout line endings to Git LF bytes.
        for path, expected in {
            "docs/releases/v0.32.0.md": "ff8b10c9bb5d51de7ccda0e0677dac962bc08310780bfe3eee850a6a2a2c7308",
            "docs/verification/v0.32.0.md": "b8f3b427190eb3b141c0d59d3585107e28bc076c5867bd1a73a71b73ba9d4e6b",
            "docs/releases/v0.33.0.md": "59f3a34a22be7f660f0bd96a1fd4739056fa5d68c5a005dd7afa82db18be0a1b",
            "docs/verification/v0.33.0.md": "85a04c40a5d9a7e9acbe669f06a43b45363a8ed1cd62b427693861b41c5b410a",
            # Published v0.33.1 records, equal to the blobs at tag v0.33.1.
            "docs/releases/v0.33.1.md": "9c3f3ceb727746969541c4e7338f94a35746e78d9635101d663eeb89182b337f",
            "docs/verification/v0.33.1.md": "e5c88bd26f939490f1e2825ad99d3eccbac8c3fcca36eeefb4682d70436abf05",
        }.items():
            with self.subTest(path=path):
                canonical = (REPO_ROOT / path).read_text(encoding="utf-8").encode("utf-8")
                self.assertEqual(hashlib.sha256(canonical).hexdigest(), expected)

    def test_v0330_keeps_unchanged_rule_and_adapter_stamps(self) -> None:
        for path in (*BROWSER_RULE.parent.glob("vault-*.md"),
                     REPO_ROOT / "assets/adapters/aside/aside-up.ps1"):
            with self.subTest(path=path.name):
                self.assertNotIn("engine=0.33.0", path.read_text(encoding="utf-8"))
                self.assertNotIn("engine=0.33.1", path.read_text(encoding="utf-8"))


# v0.34.0: five opt-in items on top of published v0.33.1 - the --bind-subject subject left edge,
# conformal abstention calibration in evaluate_recall.py, real_vault_audit.py plan, improvement
# --receipt-order and evaluate_tool_matrix.py --string-patterns. vault_ssot.py is the only bound
# file that changed, so the note states both fingerprints and the v0.31.0 upgrade and rollback order.
class V0340ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs/releases/v0.34.0.md"
    VERIFICATION = REPO_ROOT / "docs/verification/v0.34.0.md"
    TITLE = "# v0.34.0 — 다섯 가지 선택형 진단·평가 기능"
    RELEASE_LITERALS = (
        "## 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계",
        "4b213f3ed0a314595278adb7d70eb9a0c6fc61e5", "67af284583c19b050ed93a1fc77a8cc7babb9086",
        # The five items and their documented outputs.
        "`check-support --bind-subject`", "UAX #29 WB5", "U+119E", "`different_from`",
        "`evaluate_recall.py --abstention-calibration conformal`", "`--false-refusal-budget`",
        "`--abstention-signal`", "`abstention: host_decision`", "Learn then Test",
        "`scripts/real_vault_audit.py plan`", "`sample_size`", "`strict_ltpd_sample_size`", "`--oc`",
        "`vault_improvements.py inspect`·`compare --receipt-order`", "`plan_first`", "`results_first`",
        "`mixed`", "`evaluate_tool_matrix.py --string-patterns`",
        "`evaluate_tool_safety.lint_string_pattern`", "`re.fullmatch`",
        # Compatibility: help, abbreviations, printed source hashes and --bind-subject results.
        "`--f`", "`ambiguous option`", "`source_sha256`", "`code_sha256`", "`subject_absent`",
        "`ssot-subject-unbound`", "`unassessed`",
        # Version surfaces, certificates, local evidence and the external gates.
        "--ref v0.34.0", CHECKER_SHA256_V0330, CHECKER_SHA256_V0340, "harmless replay",
        "2,626", "1,578.457", "2,543", "verification.json", "post-publication.json",
    )
    # Statements compared with whitespace runs folded.
    RELEASE_STATEMENTS = (
        "새 옵션을 주지 않으면 기존 출력과 종료 코드는 v0.33.1과 같다. 다음은 예외다.",
        "`--fixture`를 줄여 쓴 `--f`는 `--false-refusal-budget`과도 맞아 `ambiguous option` 오류(종료 코드 2)가 된다.",
        "일곱 bound 파일 가운데 바뀐 것은 `vault_ssot.py`뿐이고 harmless corpus는 같다.",
        "Learn then Test는 어느 팔에서도 문턱을 내지 못한다.",
        "서버 동작은 실행으로 확인하지 않았고, 실제 모델로 A/B를 관측하지 않았다.",
        "이 PC에서는 Python 3.12만 실행했다.",
        "두 번째 검증의 지적은 `1228d4b`·`66a395f`·`67af284`로 반영했고 위 전체 시험만 다시 거쳤다.",
        "위 값은 공개 후보 시점의 관측이며 버전 문서를 바꾼 최종 릴리스 트리의 결과가 아니다.",
        "문서를 준비한 시점에는 이 최종 게이트가 대기 중이며 문서 준비만으로 완료를 주장하지 않는다.",
    )
    VERIFICATION_STATEMENTS = (
        "## Release content and scope", "## Evidence boundaries", "## Final release gates", "## Limits",
        "4b213f3ed0a314595278adb7d70eb9a0c6fc61e5", "67af284583c19b050ed93a1fc77a8cc7babb9086",
        "ac96d3eaeb7414080a01b1c40395dee3aae9f4c7",
        "The Codex verifier's installed-resource list is unchanged",
        "2,626 tests in 1,578.457 seconds with 43 skips and no failures or errors",
        "Three of the skips are Aside helper tests", "2,543 tests with 40 skips at v0.33.1",
        "`1228d4b` (tests)", "but no further independent review did",
        "not results for the final release source after metadata changes",
        "no actual-model `--string-patterns` run was made", "21 cases, zero false positives",
        "At documentation preparation time the following gates are pending.",
        "this document does not assert they passed", "verification.json", "post-publication.json",
        "zero model turns", "Master and tag CI", "L1 byte", "L2 Git-blob/member",
        "`agentic-vault-0.34.0.zip`", "No old tag is rewritten.",
    )

    def test_v0340_release_note_covers_scope_compatibility_and_gates(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(release.splitlines()[0], self.TITLE)
        for literal in self.RELEASE_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, release)
        folded = _folded(release)
        for statement in self.RELEASE_STATEMENTS:
            with self.subTest(statement=statement):
                self.assertIn(statement, folded)

    def test_v0340_upgrade_and_rollback_order_is_the_v0310_order(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        self.assertIn(V0310_UPGRADE_ORDER_LEAD, _folded(release))
        lines = release.splitlines()
        first = lines.index(V0310_UPGRADE_STEPS[0])
        self.assertEqual(lines[first:first + 2], list(V0310_UPGRADE_STEPS[:2]))
        self.assertTrue(lines[first + 2].startswith(V0310_UPGRADE_STEPS[2]))
        self.assertEqual(lines[first + 3], V0310_UPGRADE_STEPS[3])

    def test_v0340_states_both_checker_hashes_once(self) -> None:
        # One full statement of each fingerprint per document, so a stale second statement fails.
        for document in (self.RELEASE, self.VERIFICATION):
            text = document.read_text(encoding="utf-8")
            for digest in (CHECKER_SHA256_V0330, CHECKER_SHA256_V0340):
                with self.subTest(document=document.name, digest=digest[:8]):
                    self.assertEqual(text.count(digest), 1)
                    self.assertEqual(text.count(digest[:8]), 1)
        self.assertIn(f"v0.33.1의 `{CHECKER_SHA256_V0330}`(공개하지 않은 v0.33.0 후보와 같다)에서 "
                      f"`{CHECKER_SHA256_V0340}`로 바뀐다", _folded(self.RELEASE.read_text(encoding="utf-8")))
        record = _folded(self.VERIFICATION.read_text(encoding="utf-8"))
        self.assertIn(f"returned checker fingerprint `{CHECKER_SHA256_V0340}`", record)
        self.assertIn(f"differs from v0.33.1's `{CHECKER_SHA256_V0330}`, which the unpublished v0.33.0 "
                      "candidate also had", record)

    def test_v0340_verification_separates_candidate_evidence_from_release_gates(self) -> None:
        record = _folded(self.VERIFICATION.read_text(encoding="utf-8"))
        for statement in self.VERIFICATION_STATEMENTS:
            with self.subTest(statement=statement):
                self.assertIn(statement, record)

    def test_v0340_document_links_resolve(self) -> None:
        import re
        from urllib.parse import unquote

        for document in (self.RELEASE, self.VERIFICATION):
            for target in re.findall(r"\]\(([^)\s]+)\)", document.read_text(encoding="utf-8")):
                if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / unquote(target.split("#", 1)[0])).exists(), target)

    def test_v0340_keeps_unchanged_rule_and_adapter_stamps(self) -> None:
        # Rule template and Aside helper content did not change, so no stamp moves to 0.34.0.
        for path in (*BROWSER_RULE.parent.glob("vault-*.md"),
                     REPO_ROOT / "assets/adapters/aside/aside-up.ps1"):
            with self.subTest(path=path.name):
                self.assertNotIn("engine=0.34.0", path.read_text(encoding="utf-8"))


# v0.46.0: seven defect fixes found by an outside review of v0.45.1. The deny-zone fold changed
# vault_paths.py and vault_lint_extensions.py, two of the seven certificate-bound files, so the
# note states both fingerprints and the v0.31.0 upgrade and rollback order; the deny guard and
# its parser carry the fold and move to engine=0.46.0, unchanged rules and helpers keep theirs.
class V0460ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs/releases/v0.46.0.md"
    VERIFICATION = REPO_ROOT / "docs/verification/v0.46.0.md"
    TITLE = "# v0.46.0 — 금지 구역 정규화와 검사 빈틈 보강"
    SECTIONS = ("## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 참고와 기존 활용의 차이",
                "## 검증", "## 알려진 경계")
    INSTALL_REF = "현재 설치 ref는 `v0.46.0`이다."
    CERTIFICATE_STATEMENT = (
        f"`{CHECKER_SHA256_V0340}`에서 `{CHECKER_SHA256_V0460}`로 바뀐다. 일곱 bound 파일 가운데 바뀐 "
        "것은 `vault_paths.py`와 `vault_lint_extensions.py`이고 harmless corpus는 같다."
    )
    RECORD_OPENING = "This document specifies gates, not measured results."
    RECORD_SECTIONS = ("## Release content and scope", "## Required feature evidence",
                       "## Evidence boundaries", "## Final release gates", "## Limits")
    # Dated errata appended in v0.47.0 (item 11); V0460ErrataTests pins the published text above them.
    ERRATA = "## 정오표"
    RECORD_ERRATA = "## Errata"
    PUBLISHED_RELEASE_SHA256 = "ffc83b91bd48d96192720ef8c1b77b999a3d6cbcb29dd10eabccfe8289344d55"
    PUBLISHED_RECORD_SHA256 = "f2716b5a99f76d20bbce5e613966fc0bcf03a90f3c41987d690c8f1b508c238a"

    @staticmethod
    def published(text: str, heading: str) -> str:
        """The published text of a v0.46.0 document: everything before its errata heading."""
        return text[:text.index("\n" + heading + "\n")]

    def test_v0460_release_note_sections_and_install_ref(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        lines = release.splitlines()
        self.assertEqual(lines[0], self.TITLE)
        headings = [line for line in lines if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.SECTIONS + (self.ERRATA,))
        # The closing paragraph after the last published section states the install ref and the stamps.
        published = self.published(release, self.ERRATA)
        closing = _folded(published[published.index("\n" + self.SECTIONS[-1] + "\n"):])
        self.assertIn(self.INSTALL_REF, closing)
        self.assertIn("engine=0.46.0", closing)

    def test_v0460_upgrade_and_rollback_order_is_the_v0310_order(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        start = release.index("\n## 하위호환·업그레이드\n")
        section = release[start:release.index("\n## ", start + 1)]
        self.assertIn(V0310_UPGRADE_ORDER_LEAD, _folded(section))
        lines = section.splitlines()
        first = lines.index(V0310_UPGRADE_STEPS[0])
        self.assertEqual(tuple(lines[first:first + len(V0310_UPGRADE_STEPS)]), V0310_UPGRADE_STEPS)

    def test_v0460_states_both_checker_hashes_once(self) -> None:
        for document in (self.RELEASE, self.VERIFICATION):
            text = document.read_text(encoding="utf-8")
            for digest in (CHECKER_SHA256_V0340, CHECKER_SHA256_V0460):
                with self.subTest(document=document.name, digest=digest[:8]):
                    self.assertEqual(text.count(digest), 1)
                    self.assertEqual(text.count(digest[:8]), 1)
            for digest in (CHECKER_SHA256_V0330, CHECKER_SHA256_V0320):
                with self.subTest(document=document.name, absent=digest[:8]):
                    self.assertNotIn(digest[:8], text)
        self.assertIn(self.CERTIFICATE_STATEMENT, _folded(self.RELEASE.read_text(encoding="utf-8")))
        record = _folded(self.VERIFICATION.read_text(encoding="utf-8"))
        self.assertIn(f"returned checker fingerprint `{CHECKER_SHA256_V0460}`", record)
        self.assertIn(f"differs from v0.45.1's `{CHECKER_SHA256_V0340}`", record)
        self.assertIn("corpus `38efaabde0ce1e1df4008276db9761ba1b88eff35ddd91cb8d4fc4899023d04a` is unchanged",
                      record)

    def test_v0460_record_specifies_gates_not_results(self) -> None:
        record = self.VERIFICATION.read_text(encoding="utf-8")
        lines = record.splitlines()
        self.assertEqual(lines[0], "# v0.46.0 verification contract")
        self.assertTrue(_folded(record).startswith(
            "# v0.46.0 verification contract " + self.RECORD_OPENING))
        headings = [line for line in lines if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.RECORD_SECTIONS + (self.RECORD_ERRATA,))
        folded = _folded(record)
        for literal in ("verification.json", "129", "claude plugin validate --strict",
                        "Reference Atlas", "both directions", "flags-off"):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_v0460_document_links_resolve(self) -> None:
        import re
        from urllib.parse import unquote

        for document in (self.RELEASE, self.VERIFICATION):
            for target in re.findall(r"\]\(([^)\s]+)\)", document.read_text(encoding="utf-8")):
                if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                    continue
                if document == self.RELEASE and target == "../../commands/vault-jarvis-setup.md":
                    errata = _section(document.read_text(encoding="utf-8"), self.ERRATA)
                    self.assertIn("](https://github.com/Technoetic/agentic-vault/blob/v0.46.0/"
                                  "commands/vault-jarvis-setup.md)", errata)
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / unquote(target.split("#", 1)[0])).exists(), target)

    def test_v0460_moves_only_the_guard_stamps(self) -> None:
        # At v0.46.0 the deny guard carried the fold and its parser matched its stamp; rule
        # templates, the Aside helper, the lesson cue hook and the git hooks did not change.
        for relative in ("assets/adapters/deny-guard/deny-guard.py",
                         "assets/adapters/deny-guard/ps-parse.ps1"):
            with self.subTest(stamped=relative):
                first = _published_text("v0.46.0", relative).splitlines()[0]
                self.assertTrue(first.endswith(" engine=0.46.0"), first)
        unchanged = (*BROWSER_RULE.parent.glob("vault-*.md"),
                     REPO_ROOT / "assets/adapters/aside/aside-up.ps1",
                     REPO_ROOT / "assets/adapters/lesson-cues/lesson-cues.py",
                     *(REPO_ROOT / "assets/git-hooks").iterdir())
        self.assertGreaterEqual(len(unchanged), 10)
        for path in unchanged:
            with self.subTest(unchanged=path.name):
                self.assertNotIn("engine=0.46.0", path.read_text(encoding="utf-8-sig"))


def _section(text: str, heading: str) -> str:
    """The text from a heading line to the next heading of the same or a higher level, or to the end."""
    start = text.index("\n" + heading + "\n") + 1
    level = heading.split(" ", 1)[0]
    ends = [text.find("\n" + "#" * size + " ", start + len(heading)) for size in range(1, len(level) + 1)]
    ends = [end for end in ends if end != -1]
    return text[start:min(ends) if ends else len(text)]


# v0.47.0 item 11: dated errata at the end of the two v0.46.0 documents. The published text above
# each errata heading keeps its bytes, and each erratum says what was wrong and what holds instead.
class V0460ErrataTests(unittest.TestCase):
    def test_published_text_is_kept_above_the_errata(self) -> None:
        cls = V0460ReleaseMetadataTests
        for document, heading, digest in (
                (cls.RELEASE, cls.ERRATA, cls.PUBLISHED_RELEASE_SHA256),
                (cls.VERIFICATION, cls.RECORD_ERRATA, cls.PUBLISHED_RECORD_SHA256)):
            with self.subTest(document=document.name):
                # read_text folds CRLF, so a Windows checkout hashes like the Git blob.
                text = document.read_text(encoding="utf-8")
                self.assertEqual(text.count("\n" + heading + "\n"), 1)
                headings = [line for line in text.splitlines() if line.startswith("## ")]
                self.assertEqual(headings[-1], heading)
                published = cls.published(text, heading)
                self.assertEqual(hashlib.sha256(published.encode("utf-8")).hexdigest(), digest)

    def test_errata_state_what_was_wrong_and_what_is_right(self) -> None:
        import re

        cls = V0460ReleaseMetadataTests
        release = cls.RELEASE.read_text(encoding="utf-8")
        release_errata = release[release.index("\n" + cls.ERRATA + "\n"):]
        for literal in ("2026-11-13", "\"영문자·숫자·`_` 밖의 글자가 든 키 뒤\"", "`\\w`", "단어 문자", "see-also"):
            with self.subTest(release_literal=literal):
                self.assertIn(literal, _folded(release_errata))
        self.assertEqual(re.findall(r"(?m)^(\d+)\. ", release_errata), ["1"])
        record = cls.VERIFICATION.read_text(encoding="utf-8")
        record_errata = record[record.index("\n" + cls.RECORD_ERRATA + "\n"):]
        for literal in ("2026-11-13", "\"on the annotated v0.46.0 tag before master is pushed\"",
                        "pushed together", "candidate-branch run",
                        "\"The vault folder name itself is not normalized\"", "deny guard on macOS",
                        "\"built from the local tag before anything is pushed\"",
                        "Candidate branches had been pushed", "before the tag and master were pushed"):
            with self.subTest(record_literal=literal):
                self.assertIn(literal, _folded(record_errata))
        self.assertEqual(re.findall(r"(?m)^(\d+)\. ", record_errata), ["1", "2", "3"])
        # The errata correct sentences only; neither restates a checker digest.
        for errata in (release_errata, record_errata):
            for digest in (CHECKER_SHA256_V0460, CHECKER_SHA256_V0340):
                with self.subTest(digest=digest[:8]):
                    self.assertNotIn(digest[:8], errata)


# v0.47.0: eleven items. Items 1-8 come from a review that compared the plugin with a vendor
# prompting guide, item 9 (the /aside-repl procedure command) is a request handled in the same
# review, and items 10 and 11 (the seed repository race, the v0.46.0 errata) are carried over from
# the v0.46.0 release. Item 2 was tried and not shipped, no certificate-bound file changed (the v0.46.0
# fingerprint stays), and only the health check, the Aside clause and the new command asset move to
# engine=0.47.0. The note fixes the publication order; the record specifies gates, not results.
class V0470ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs/releases/v0.47.0.md"
    VERIFICATION = REPO_ROOT / "docs/verification/v0.47.0.md"
    TITLE = "# v0.47.0 — 안내서 대조 검토 반영과 aside-repl 절차 명령"
    SECTIONS = ("## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 참고와 기존 활용의 차이",
                "## v0.46.0 문서 정정", "## 검증", "## 알려진 경계")
    INSTALL_REF = "현재 설치 ref는 `v0.47.0`이다."
    CERTIFICATE_SENTENCE = (
        f"일곱 bound 파일은 v0.46.0과 같으므로 checker_sha256는 `{CHECKER_SHA256_V0460}` 그대로다. "
        "v0.46.0에서 만든 인증서는 실제 설치 바이트가 같으면 그대로 맞는다. v0.45.1 이하에서 올 때는 "
        "[v0.46.0의 순서](v0.46.0.md#하위호환업그레이드)를 따른다."
    )
    CORPUS_SHA256 = "38efaabde0ce1e1df4008276db9761ba1b88eff35ddd91cb8d4fc4899023d04a"
    RESULT_SHA256 = "2efeda6adfcbfd1b677159fa9ce59cb61fa011179168740f99bf9d20655dac59"
    DEFAULT_CHANGES_LEAD = (
        "플러그인을 갱신하면 명령·스킬·브리지 코드는 바로 바뀐다. 볼트에 설치된 사본(규칙·healthcheck·어댑터)은 "
        "/vault-upgrade(Codex는 upgrade)를 실행하기 전까지 예전대로 동작하고, 이전 플러그인 폴더에서 시작한 "
        "Telegram 브리지는 다시 시작하기 전까지 예전대로 동작한다."
    )
    DEFAULT_CHANGES = 20
    ORDER_SENTENCE = (
        "후보 브랜치 CI(최종 커밋) → 로컬 태그·ZIP·재빌드 확인·Codex 설치 확인·인증서 → 태그 push → "
        "태그 CI 11/11 → 참고 대장 갱신과 독립 확인 → 독립 릴리스 검토 → master push → master CI 11/11 → "
        "검증 기록(verification.json) → 업로드 → 초안 점검 → 게시 → 게시 후 확인"
    )
    RECORD_TITLE = "# v0.47.0 verification contract"
    RECORD_OPENING = "This document specifies gates, not measured results."
    RECORD_SECTIONS = ("## Release content and scope", "## Required feature evidence",
                       "## Evidence boundaries", "## Final release gates", "## Limits")
    GATE_LABELS = (
        "Local gates on the integrated tree and on the final commit.",
        "Candidate-branch CI on the final commit.",
        "Local tag, release ZIP, rebuild check, native Codex check and certificate.",
        "Tag push.",
        "Tag CI.",
        "Reference catalog update and its independent check.",
        "Independent release review.",
        "Master push.",
        "Master CI.",
        "Receipt.",
        "Upload.",
        "Draft inspection.",
        "Publication.",
        "Post-publication checks.",
    )
    # The local steps that the release steps repeat before every changed candidate and on the
    # final commit: full suite, compile, whitespace, smokes, manifest validation, flags-off
    # comparison with probes, independent source review and the real /aside-repl run.
    GATE_ONE_STEPS = ("unittest", "compile", "whitespace", "smoke", "claude plugin validate --strict",
                      "flags-off", "independent source review", "/aside-repl")
    # Fixed boundary texts (what ran, on what, and what did not run).
    BOUNDARY_TEXTS = (
        "기여자 안내(AGENTS.md와 이를 불러오는 .claude/CLAUDE.md)가 다른 폴더의 세션에 읽히지 않는 것은 Claude Code "
        "2.1.252 출력 모드에서 --plugin-dir로 플러그인을 불러 한 번 확인했다. 마켓플레이스 설치본, IDE 확장, 플러그인 "
        "파일을 읽는 턴은 시험하지 않았고 Codex는 실행하지 않았다(호스트 문서의 탐색 규칙을 따른다). 플러그인 체크아웃 "
        "폴더에서 세션을 시작하면(볼트 안에 둔 체크아웃 포함) Claude Code가 그 .claude/CLAUDE.md를 읽는다. 플러그인 "
        "루트에는 CLAUDE.md를 두지 않는다: claude plugin validate --strict가 루트의 CLAUDE.md를 경고로 막는다(2.1.252).",
        "엔진 사본 소유를 밝히는 계약 한 줄은 계획해 규칙 문구 시험을 했으나 통과하지 못해 넣지 않았다: 시험한 세 "
        "문구가 모두, 사용자가 명시적으로 요청한 볼트 사본 수정을 거절하게 만들었다(줄 없이 4건 중 3건 수행, 줄이 "
        "있으면 0건; 로컬 모델 하나, 문구마다 1회, 지어낸 사례 — "
        "[hardening.md](../hardening.md#engine-copy-ownership-line-tried-not-shipped)). 그래서 vault-architecture.md와 "
        "그 스탬프는 v0.46.0과 같다.",
        "브리지는 모델 이름이 CLI에서 쓸 수 있는지 검사하지 않는다. --effort가 없는 이전 claude CLI는 값을 지정하면 "
        "실행이 실패할 수 있다. 설치된 claude CLI 2.1.252에서 브리지 인자 배열에 두 옵션을 넣어 확인했다(2026-11-13, "
        "경우마다 1회): --bare에서는 옵션 해석까지만 확인했고(이 기기에는 API 키가 없어 응답 생성은 못 했다), --bare "
        "없이 로그인한 상태에서는 haiku·low로 응답했다. CLI가 받지 않는 모델 이름 하나는 --bare 없이 0이 아닌 종료 "
        "코드로 끝났다. --bare에서 인증된 응답 생성과 노력 수준 적용은 확인하지 않았다. 모델·노력 수준을 바꿔 응답 "
        "시간이나 품질이 어떻게 달라지는지는 재지 않았다.",
        "브리지는 claude CLI의 텍스트 출력과 종료 코드만 읽어 모델 제공자의 요청 거절과 장애·빈 응답을 구별하지 "
        "못한다. --output-format json은 쓰지 않는다. 실제 거절의 출력 형태는 확인하지 않았다. --bare에서 API 키가 "
        "없을 때 CLI는 로그인하지 않았다는 줄을 표준 출력에 내고 종료 코드 1로 끝났다(1회). 브리지는 0이 아닌 종료 "
        "코드를 생성 실패로 처리하고 그 출력을 전달하지 않는다.",
        "주입 명령이 훅을 거치지 않는다는 관찰은 Claude Code 2.1.252 출력 모드에서 경우마다 1회 본 것이며, 훅 기록은 "
        "두 번의 실행에서 확인했다(시험한 명령은 `allowed-tools`로 주입 명령을 미리 허용했다). repl 전달 방식은 Windows 11, Aside CLI 1.26.916.1741에서 2026-11-12~13에 "
        "실측했다: Git Bash에서 인자로 넘기기와 Python에서 셸 없이 넘기기는 스크립트가 그대로 전달됐고, Windows "
        "PowerShell 5.1에서 인자로 넘기면 큰따옴표가 사라져 실패했다(1회). 같은 PowerShell 5.1에서 Python 한 줄 "
        "경로로 넘기면 그대로 전달됐다(1회). PowerShell 7, macOS, Linux와 Codex 세션의 실행은 시험하지 않았다. /aside-repl은 최종 파일로 Claude Code 2.1.252 출력 모드에서 공개 페이지 하나로 한 번 "
        "실행했다(기동 도우미와 aside repl 줄은 에이전트 자신의 도구 호출로 실행됐다). IDE 확장, 슬래시 명령 없이 "
        "말로 한 요청, Codex에서는 실행하지 않았다. 절차 글은 동작을 강제하지 못한다.",
    )
    RECORD_LIMITS = (
        "The contributor guide (`AGENTS.md` and `.claude/CLAUDE.md`, which imports it) was checked on Claude Code "
        "2.1.252 in print mode with `--plugin-dir`, one run each: a session in another folder did not see it and a "
        "session inside the checkout did. A marketplace installation, the IDE extension and a turn that reads a "
        "plugin file were not tested, and Codex was not run. No `CLAUDE.md` is placed at the plugin root, because "
        "`claude plugin validate --strict` rejects one.",
        "The two bridge options were checked on Claude Code 2.1.252, one run per case: with `--bare` only up "
        "to option parsing (the release machine has no API key), and without `--bare`, logged in, with an "
        "answer. Generation with authentication in bare mode and whether bare mode applies the effort level "
        "were not run.",
    )

    def test_v0470_release_note_sections_and_install_ref(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        lines = release.splitlines()
        self.assertEqual(lines[0], self.TITLE)
        headings = [line for line in lines if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.SECTIONS + ("## 정오표",))
        closing = _folded(_section(release, self.SECTIONS[-1]))
        self.assertIn(self.INSTALL_REF, closing)
        self.assertIn("engine=0.47.0", closing)
        self.assertIn("--ref v0.47.0", closing)

    def test_v0470_states_the_unchanged_checker_once(self) -> None:
        for document in (self.RELEASE, self.VERIFICATION):
            text = document.read_text(encoding="utf-8")
            with self.subTest(document=document.name):
                self.assertEqual(text.count(CHECKER_SHA256_V0460), 1)
                self.assertEqual(text.count(CHECKER_SHA256_V0460[:8]), 1)
            for digest in (CHECKER_SHA256_V0340, CHECKER_SHA256_V0330, CHECKER_SHA256_V0320):
                with self.subTest(document=document.name, absent=digest[:8]):
                    self.assertNotIn(digest[:8], text)
        release = self.RELEASE.read_text(encoding="utf-8")
        self.assertIn(self.CERTIFICATE_SENTENCE, _folded(_section(release, "## 하위호환·업그레이드")))
        record = _folded(self.VERIFICATION.read_text(encoding="utf-8"))
        self.assertIn(f"checker fingerprint `{CHECKER_SHA256_V0460}`", record)
        self.assertIn(f"`{self.CORPUS_SHA256}`", record)
        self.assertIn(f"`{self.RESULT_SHA256}`", record)

    def test_v0470_publication_order(self) -> None:
        import re

        record = self.VERIFICATION.read_text(encoding="utf-8")
        gates = _section(record, "## Final release gates")
        self.assertEqual(re.findall(r"(?m)^(\d+)\. \*\*", gates), [str(n) for n in range(1, 15)])
        folded = _folded(gates)
        position = -1
        for number, label in enumerate(self.GATE_LABELS, 1):
            with self.subTest(gate=number):
                found = folded.find(f"{number}. **{label}**")
                self.assertGreater(found, position)
                position = found
        release = self.RELEASE.read_text(encoding="utf-8")
        self.assertIn(self.ORDER_SENTENCE, _section(release, "## 검증").splitlines())
        self.assertEqual(_folded(release).count(self.ORDER_SENTENCE), 1)

    def test_v0470_record_specifies_gates_not_results(self) -> None:
        record = self.VERIFICATION.read_text(encoding="utf-8")
        lines = record.splitlines()
        self.assertEqual(lines[0], self.RECORD_TITLE)
        self.assertTrue(_folded(record).startswith(self.RECORD_TITLE + " " + self.RECORD_OPENING))
        headings = [line for line in lines if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.RECORD_SECTIONS)
        folded = _folded(record)
        for literal in ("verification.json", "130", "claude plugin validate --strict", "Reference Atlas",
                        "flags-off", "this document does not assert they passed"):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_v0470_errata_section_links_both_errata(self) -> None:
        import re

        section = _section(self.RELEASE.read_text(encoding="utf-8"), "## v0.46.0 문서 정정")
        self.assertIn("틀린 문장 넷", section)
        self.assertNotIn("틀린 문장 셋", section)
        bullets = [line for line in section.splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), 4)
        self.assertEqual(section.count("](v0.46.0.md#정오표)"), 1)
        self.assertTrue(bullets[0].startswith("- [릴리스 노트의 정오표](v0.46.0.md#정오표): "))
        self.assertEqual(re.findall(r"\]\(\.\./verification/v0\.46\.0\.md#errata\) (\d):", section),
                         ["1", "2", "3"])
        self.assertEqual(section.count("](../verification/v0.46.0.md#errata)"), 3)

    def test_v0470_document_links_resolve(self) -> None:
        import re
        from urllib.parse import unquote

        for document in (self.RELEASE, self.VERIFICATION):
            text = document.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)\s]+)\)", text):
                if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                    continue
                # These release-note links refer to commands present in the published tag.
                if document == self.RELEASE and target in (
                        "../../assets/adapters/aside/aside-repl-command.md", "../../commands/vault-jarvis-setup.md"):
                    errata = _section(text, "## 정오표")
                    self.assertIn("2026-11-24", errata)
                    pinned = "https://github.com/Technoetic/agentic-vault/blob/v0.47.0/"
                    self.assertIn(f"]({pinned}{target.removeprefix('../../')})", errata)
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / unquote(target.split("#", 1)[0])).exists(), target)

    def test_no_current_file_still_has_the_v0470_stamp(self) -> None:
        stamped = set()
        for top in ("assets", "hooks", "skills"):
            for path in (REPO_ROOT / top).rglob("*"):
                if not path.is_file():
                    continue
                try:
                    head = path.read_text(encoding="utf-8-sig").splitlines()[:5]
                except UnicodeDecodeError:
                    continue
                if any("engine=0.47.0" in line for line in head):
                    stamped.add(path.relative_to(REPO_ROOT).as_posix())
        # The health check and browser clause now have newer stamps. The bundled command was retired.
        self.assertEqual(stamped, set())

    def test_v0470_default_changes_are_numbered(self) -> None:
        import re

        section = _section(self.RELEASE.read_text(encoding="utf-8"), "## 기본 결과 변경")
        self.assertIn(self.DEFAULT_CHANGES_LEAD, section.splitlines())
        self.assertEqual(re.findall(r"(?m)^(\d+)\. ", section),
                         [str(n) for n in range(1, self.DEFAULT_CHANGES + 1)])
        # Item 2 was not shipped, so no entry concerns a change of the architecture rule.
        self.assertIn("상시 로드 규칙 `vault-architecture.md`는 그대로다", _folded(section))
        # Entry 18 states the row count of the guide skeleton's 2026-11-12~13 table as it ships.
        guide = (REPO_ROOT / "assets/adapters/aside/operations-guide.md").read_text(encoding="utf-8")
        table = _section(guide, "## 엔진 개발 실측: repl 호출 (Windows 11, CLI 1.26.916.1741, 2026-11-12~13)")
        rows = [line for line in table.splitlines() if line.startswith("|")][2:]
        entry = _folded(section[section.index("\n18. "):section.index("\n19. ")])
        self.assertIn(f"2026-11-12~13의 표({len(rows)}행)", entry)

    def _default_change_entry(self, number: int) -> str:
        section = _section(self.RELEASE.read_text(encoding="utf-8"), "## 기본 결과 변경")
        start = section.index(f"\n{number}. ")
        end = section.find(f"\n{number + 1}. ", start)
        return _folded(section[start:end if end != -1 else len(section)])

    def test_v0470_entry_19_names_recall_by_mechanism(self) -> None:
        # Measured on 2e22557 by the reconcile step and two sceptics (findings TS-1 and RD-1): with the
        # command file installed, recall reads it as one more candidate file and its diagnostics rise.
        # Recall ranks the matching files and returns at most `--limit` of them, and it stops at its file,
        # entry and read-byte limits, where the counters no longer rise; a vault with six matching notes
        # left the command file out of the five results (final check F1). The entry states both bounds.
        entry = self._default_change_entry(19)
        sentence = (
            "recall도 이 파일을 검색 대상 파일로 읽는다: 질의가 이 파일의 낱말과 맞으면 다른 파일과 함께 순위가 매겨져 "
            "결과(`--limit`, 기본 5개)에 나올 수 있고, 검색 상한(파일 수·항목 수·읽기 바이트) 안에서는 진단의 "
            "files_considered·files_read가 1씩 늘고 bytes_read가 늘며, 폴더 항목도 세는 entries_scanned는 이 파일과 "
            "새로 생긴 폴더 수만큼 는다(.claude/commands가 이미 있으면 1, 그 폴더만 새로 생기면 2)."
        )
        self.assertIn(sentence + " 대상: 명령을 설치한 볼트.", entry)
        self.assertIn("나올 수 있고", entry)
        self.assertIn("검색 상한", entry)
        self.assertNotIn("결과에 이 파일이 나오고", entry)
        # The default of `--limit` that the entry names, read from the source (the module is not imported).
        recall = ast.parse((REPO_ROOT / "skills/agentic-vault/scripts/vault_recall.py").read_text(encoding="utf-8"))
        defaults = [keyword.value for node in ast.walk(recall)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument" and node.args
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--limit"
                    for keyword in node.keywords if keyword.arg == "default"]
        self.assertEqual([ast.literal_eval(value) for value in defaults], [5])

    WORDING_BULLET_START = (
        '- 문구 정리: 새 볼트의 hot·handoff 템플릿 제목은 "500단어 이내" 대신 "500단어 권고"라고 하고, hot 템플릿 '
        '본문은 세션 주입 예산(`hot_max_tokens`) 안에 들도록 압축하라고 한다. SKILL.md는 hot을 "500단어 스냅숏" 대신 '
        '"짧은 현재 상태 스냅숏"으로 부르고, 종료 때 "500단어 이내로 재작성" 대신 핵심 결정·다음 행동을 앞에 두라고 '
        '한다. 상시 로드 규칙 `vault-architecture.md`의 문구는 바꾸지 않았다.'
    )
    ENTRY_7_START = (
        '7. 새 볼트의 hot·handoff 템플릿 제목은 "500단어 이내" 대신 "500단어 권고"라고 하고, hot 템플릿 본문은 세션 '
        '주입 예산(`hot_max_tokens`) 안에 들도록 압축하라고 한다. SKILL.md는 hot을 "짧은 현재 상태 스냅숏"으로 '
        '부르고, 종료 때 `hot_note`를 핵심 결정·다음 행동이 앞에 오게 다시 쓰라고 한다. 상시 로드 규칙 '
        '`vault-architecture.md`는 그대로다'
    )

    def test_v0470_500_word_passages_state_what_each_file_changed(self) -> None:
        # Only the hot template names the injection budget; the handoff template changed its title only,
        # and SKILL.md changed two phrases (final check of the documents, F3).
        changes = _section(self.RELEASE.read_text(encoding="utf-8"), "## 변경")
        start = changes.index("- 문구 정리:")
        bullet = _folded(changes[start:changes.index("\n- ", start)])
        entry = self._default_change_entry(7)
        self.assertTrue(bullet.startswith(self.WORDING_BULLET_START), bullet)
        self.assertTrue(entry.startswith(self.ENTRY_7_START), entry)
        for name, passage in (("bullet", bullet), ("entry 7", entry)):
            with self.subTest(passage=name):
                self.assertIn("hot 템플릿 본문은 세션 주입 예산(`hot_max_tokens`) 안에", passage)
                self.assertNotIn('"500단어 권고"와 세션 주입', passage)
        # The phrases the passages state, in the files they describe.
        hot = (REPO_ROOT / "assets/templates/hot.md").read_text(encoding="utf-8")
        handoff = (REPO_ROOT / "assets/templates/handoff.md").read_text(encoding="utf-8")
        skill = (REPO_ROOT / "skills/agentic-vault/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("`hot_max_tokens`", hot)
        self.assertNotIn("hot_max_tokens", handoff)
        for name, template in (("hot", hot), ("handoff", handoff)):
            title = next(line for line in template.splitlines() if line.startswith("# "))
            with self.subTest(title=name):
                self.assertIn("500단어 권고", title)
                self.assertNotIn("500단어 이내", title)
        self.assertIn("hot(짧은 현재 상태 스냅숏)", skill)
        self.assertNotIn("500단어 스냅숏", skill)
        self.assertIn("`hot_note`를 핵심 결정·다음 행동이 앞에 오게 다시 쓰고", skill)
        self.assertNotIn("500단어 이내로 재작성", skill)

    def test_v0470_entry_14_names_the_output_that_gains_the_lines(self) -> None:
        # /vault-doctor runs vault_adapters.py with `--format json`; the two new lines are in that script's
        # own text output, its default format (final check of the documents, F4).
        entry = self._default_change_entry(14)
        self.assertIn(
            "`vault_adapters.py`의 텍스트 출력(기본 `--format text`)에 두 줄이 더 붙으며 `/vault-doctor`가 읽는 "
            "JSON에는 `adapters.aside_command`가 더해진다. 다른 항목은 그대로다.", entry)
        self.assertNotIn("doctor 텍스트에 두 줄", entry)
        doctor = (REPO_ROOT / "commands/vault-doctor.md").read_text(encoding="utf-8")
        self.assertIn('vault_adapters.py" --vault "<현재 볼트 절대 경로>" --format json', doctor)
        adapters = (REPO_ROOT / "skills/agentic-vault/scripts/vault_adapters.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--format", choices=("text", "json"), default="text")', adapters)

    def test_v0470_note_states_the_shared_briefing_backoff(self) -> None:
        # The retry backoff is shared by all pending slots and returns to its base only when `pending` is
        # empty (finding BR-1 in the published release).
        changes = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 변경"))
        self.assertIn(
            "최대 1시간)을 따른다. 이 간격은 회차마다 따로 두지 않고 `pending`이 빌 때만 60초로 돌아가므로, 두 회차 "
            "이상이 함께 `pending`에 있으면 뒤 회차는 앞 회차의 실패로 늘어난 간격에서 이어 시도하고 건너뛰기 안내도 "
            "그만큼 늦게 온다.", changes)

    # The Aside start hook acts only on Bash and PowerShell commands that begin with `aside`
    # (assets/adapters/aside/settings-hooks.json); the Python route begins with `python` (finding LA-2).
    PYTHON_ROUTE_HOOK_SENTENCE = (
        "다만 Python 경로의 호출은 `python`으로 시작하므로, `aside`로 시작하는 Bash·PowerShell 명령에만 건 Aside 자동 "
        "기동 훅은 동작하지 않는다. 그래서 절차는 그 경로에서 훅이 있어도 기동 도우미를 직접 실행하게 한다(훅의 조건은 "
        "설정 파일을 읽어 확인했고 훅을 켠 채 실행해 보지는 않았다)."
    )
    ENTRY_17_START_LINE = (
        "`**기동(Windows):**` 줄은 Python 경로로 `aside repl`을 부를 때 자동 기동 훅이 있어도 기동 도우미를 먼저 "
        "실행하게 한다."
    )

    def test_v0470_note_states_that_the_start_hook_misses_the_python_route(self) -> None:
        changes = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 변경"))
        self.assertIn("권한 확인과 훅을 거친다. " + self.PYTHON_ROUTE_HOOK_SENTENCE, changes)
        self.assertIn(self.ENTRY_17_START_LINE + " 기존 조항은 바뀌지 않는다. 대상: 새로 Aside를 들이는 볼트.",
                      self._default_change_entry(17))

    def test_aside_start_clause_accounts_for_the_python_route(self) -> None:
        hooks = (REPO_ROOT / "assets/adapters/aside/settings-hooks.json").read_text(encoding="utf-8")
        self.assertIn('"if": "Bash(aside *)"', hooks)
        self.assertIn('"if": "PowerShell(aside *)"', hooks)
        clause = (REPO_ROOT / "assets/adapters/aside/browser-clause.md").read_text(encoding="utf-8")
        start = next(line for line in clause.splitlines() if line.startswith("- **기동(Windows):**"))
        self.assertIn("Python으로 `aside repl`을 부를 때는 훅이 있어도 먼저 아래 도우미를 실행한다.", start)

    def test_v0470_entry_3_names_the_retire_condition_of_session_end(self) -> None:
        # /vault-session-end now proposes RETIRE only on evidence seen in the session (finding RD-7).
        condition = "은퇴는 그 교훈이 기대던 원인이 사라졌다는 근거를 이번 세션에서 확인했을 때만 제안"
        session_end = _folded((REPO_ROOT / "commands/vault-session-end.md").read_text(encoding="utf-8"))
        self.assertIn(condition, session_end)
        self.assertIn(condition + "한다. 대상: 모든 사용자의 새 승격과 은퇴 제안.", self._default_change_entry(3))

    PLACEHOLDER_SENTENCE ="내용 없는 자리표시 파일(`.gitkeep` 등)은 대기열 파일로 세지 않는다."

    def test_v0470_placeholder_files_are_not_counted_as_queue_files(self) -> None:
        # Every /vault-init vault has empty `.gitkeep` files in the inbox folders (finding TS-2).
        changes = _section(self.RELEASE.read_text(encoding="utf-8"), "## 변경")
        start = changes.index("- [`/vault-process-inbox`]")
        bullet = _folded(changes[start:changes.index("\n- ", start)])
        self.assertIn("보류돼야 끝난다. " + self.PLACEHOLDER_SENTENCE, bullet)
        self.assertIn(self.PLACEHOLDER_SENTENCE + " 대상: 플러그인을 갱신한 모든 사용자(Claude Code와 Codex).",
                      self._default_change_entry(1))

    def test_v0470_placeholder_statement_matches_the_inbox_command(self) -> None:
        command = (REPO_ROOT / "commands/vault-process-inbox.md").read_text(encoding="utf-8")
        self.assertIn("- 내용 없는 자리표시 파일(`.gitkeep` 등)은 대기열 파일이 아니다 — 처리·이동·보고하지 않는다.",
                      command.splitlines())

    def test_v0470_no_placeholder_left(self) -> None:
        import re

        for document in (self.RELEASE, self.VERIFICATION):
            text = document.read_text(encoding="utf-8")
            with self.subTest(document=document.name):
                self.assertIsNone(re.search(r"<[^<>\n]*>", text))
                self.assertNotIn("TBD", text)
                self.assertNotIn("TODO", text)

    def test_v0470_record_describes_probe_e1_and_the_baseline_as_they_run(self) -> None:
        # The feature-evidence probes take a clean checkout of tag v0.46.0 as their baseline, and probe E1
        # compares the files of the two trees; it does not run native Codex discovery, which gate 3 runs on
        # the v0.47.0 tag and ZIP (final check of the documents, F1).
        record = self.VERIFICATION.read_text(encoding="utf-8")
        evidence = _folded(_section(record, "## Required feature evidence"))
        self.assertTrue(evidence.startswith(
            "## Required feature evidence Each probe runs on the v0.47.0 source and, as its baseline, on a clean "
            "checkout of tag v0.46.0 in a separate folder. "), evidence[:200])
        self.assertIn(
            "Probe E1: the v0.46.0 tree has neither file; the v0.47.0 tree (the tagged commit and the extracted "
            "ZIP) has root `AGENTS.md` and `.claude/CLAUDE.md` and no root `CLAUDE.md`, no manifest names them, "
            "and the skill files, the hook files and the hook handlers per event equal those of v0.46.0 (no new "
            "skill or hook). Native Codex discovery of the v0.47.0 tag and ZIP is part of gate 3.", evidence)
        folded = _folded(record)
        self.assertIn("a clean checkout of tag v0.46.0", folded)
        self.assertIn("the skill files, the hook files and the hook handlers per event", folded)
        for old in ("the v0.46.0 ZIP has neither file", "native Codex discovery is identical",
                    "git show v0.46.0:PATH"):
            with self.subTest(absent=old):
                self.assertNotIn(old, folded)

    def test_v0470_record_conditions_probe_e10_on_the_installed_git(self) -> None:
        # Probe E10 requires no automatic maintenance child under the v0.47.0 settings; under the v0.46.0
        # settings it records whether git started one, which not every git does (final check of the
        # documents, F2).
        evidence = _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"), "## Required feature evidence"))
        self.assertIn(
            "Probe E10: in a fresh repository with the seed's settings, the Trace2 record of a commit shows no "
            "automatic maintenance child under the v0.47.0 settings; under the v0.46.0 settings it shows one where "
            "the installed git starts one at all, and the receipt records which of the two was observed.", evidence)
        self.assertIn("where the installed git starts one at all", evidence)
        self.assertNotIn("must show one automatic maintenance child", evidence)

    def test_v0470_record_never_claims_nothing_was_pushed(self) -> None:
        record = _folded(self.VERIFICATION.read_text(encoding="utf-8"))
        self.assertNotIn("Nothing is pushed", record)
        self.assertNotIn("before anything is pushed", record)
        self.assertIn("only candidate branches have been pushed", record)

    def test_v0470_gate_one_names_every_repeated_local_step(self) -> None:
        gates = _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"), "## Final release gates"))
        gate_one = gates[gates.index("1. **" + self.GATE_LABELS[0]):gates.index("2. **" + self.GATE_LABELS[1])]
        for step in self.GATE_ONE_STEPS:
            with self.subTest(step=step):
                self.assertIn(step, gate_one)
        self.assertIn("Before the first candidate-branch push", gate_one)
        self.assertIn("again on the final commit", gate_one)
        # The release note names the same repetitions as gate 1 (finding RD-5).
        check = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 검증"))
        self.assertIn("전체 로컬 시험은 통합한 트리에서 첫 후보 브랜치 push 전에, 그 뒤 트리가 바뀐 후보 push 전마다, "
                      "그리고 최종 커밋에서 다시 실행한다.", check)
        self.assertNotIn("최종 커밋에서 다시 한 번 실행한다", check)

    def test_v0470_note_counts_the_deliberate_copies(self) -> None:
        import re

        # The rule's source file for each marker; SHARED ZONE RULE has no marked source.
        sources = {"SHARED PATH RULE": "vault_paths.py", "SHARED WORKTREE RULE": "vault_worktrees.py",
                   "ATX HEADING": "vault_ssot.py"}
        unmarked = "SHARED ZONE RULE"
        files = [*(REPO_ROOT / "skills/agentic-vault/scripts").glob("*.py"),
                 *(REPO_ROOT / "assets/adapters").glob("*/*.py"),
                 *(REPO_ROOT / "hooks").glob("*.py")]
        names, copies = set(), []
        for path in files:
            text = path.read_text(encoding="utf-8")
            for name in re.findall(r"(?m)^# --- BEGIN (.+?)(?: \(| ---)", text):
                names.add(name)
                if sources.get(name) != path.name:
                    copies.append((name, path.name))
            outside = re.sub(r"(?ms)^# --- BEGIN " + unmarked + r".*?^# --- END " + unmarked + " ---$", "", text)
            if path.name != "vault_paths.py" and "def fold_path_part" in outside:
                copies.append(("fold_path_part", path.name))
        self.assertEqual(names - set(sources) - {unmarked}, set())
        rules = len(names | {"fold_path_part"})
        korean = {4: "네", 5: "다섯", 6: "여섯", 7: "일곱", 8: "여덟", 9: "아홉"}
        self.assertEqual((rules, len(copies)), (5, 7), copies)
        changes = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 변경"))
        self.assertIn(f"{korean[rules]} 규칙의 사본({korean[len(copies)]} 곳)", changes)
        self.assertNotIn("다섯 쌍", changes)

    def test_v0470_boundaries_state_what_ran_and_what_did_not(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        boundaries = _section(release, "## 알려진 경계")
        for text in self.BOUNDARY_TEXTS:
            with self.subTest(boundary=text[:30]):
                self.assertIn(text, boundaries)
        # PowerShell 5.1 appears twice, each time in one measured sentence with its run count: the
        # direct argument failed, the command's one-line Python route passed the script intact.
        self.assertEqual(boundaries.count("PowerShell 5.1"), 2)
        position = 0
        for ending in ("실패했다(1회).", "그대로 전달됐다(1회)."):
            with self.subTest(powershell_sentence=ending):
                start = boundaries.index("PowerShell 5.1", position)
                sentence = boundaries[start:boundaries.index(ending, start)]
                self.assertNotIn("다. ", sentence)
                self.assertNotIn("\n", sentence)
                position = start + len(sentence)
        self.assertNotIn("영수증", release)
        self.assertNotIn("호스트 문서와 영수증에", release)
        limits = _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"), "## Limits"))
        for statement in self.RECORD_LIMITS:
            with self.subTest(limit=statement[:30]):
                self.assertIn(statement, limits)
        self.assertIn("Codex was not run", limits)
        self.assertIn("were not run", limits)
        # The hook observation names its condition wherever it is stated (finding LA-4).
        self.assertIn("with the hook log checked in two runs (the probe command pre-approved its injected command "
                      "with `allowed-tools`).", limits)

    # Fix round 3: a timing-dependent test that failed again in the local gates. The test moves the
    # rewritten file's modification time on explicitly (runtime code unchanged), and both documents
    # name the limit of the final source sweep that lies behind it. Fix round 4: the test was already
    # listed as unstable in the v0.23.0 verification document and the v0.25.0 and v0.26.0 release
    # notes, so neither document presents it as a first discovery; and the limit is stated as equal
    # file times (one tick, or on Windows a rewrite whose modification time is put back, because
    # `st_ctime_ns` is the creation time there), not as a matter of speed only.
    TIMING_FIX_OPENING = (
        "그 밖에 이전 릴리스 문서에 불안정한 시험으로 적혀 있던 시험 하나가 이번 릴리스 게이트에서 다시 떨어져 "
        "그 시간 의존 결함을 고쳤다(런타임 코드는 그대로다)."
    )
    EARLIER_LISTINGS = (
        "이 시험은 [v0.23.0의 검증 문서](../verification/v0.23.0.md)와 [v0.25.0](v0.25.0.md)·[v0.26.0](v0.26.0.md) "
        "릴리스 노트에 불안정한 시험으로 적혀 있었고, v0.26.0에서는 다루지 않은 채 남았다."
    )
    TIMING_FIX_BULLET = (
        "- 시험: [tests/test_vault_retrieval.py](../../tests/test_vault_retrieval.py)의 "
        "`test_later_source_verification_cannot_leave_earlier_source_stale`은 같은 크기로 다시 쓴 출처를 마지막 "
        "재확인이 알아채는지 보는데, 두 번의 쓰기가 같은 파일 시각 눈금에 들어가면 떨어졌다. " + EARLIER_LISTINGS +
        " 이 릴리스의 첫 후보 "
        "커밋에서 전체 로컬 시험을 두 번 돌린 가운데 한 번 떨어졌고, 그 시험만 한 프로세스에서 300번 반복하면 그 "
        "커밋에서 97번, v0.46.0에서 86번 떨어졌다(Windows 11, Python 3.12). 시험이 다시 쓴 파일의 수정 시각을 "
        "명시적으로 뒤로 옮기게 고쳤다. 런타임 코드는 바뀌지 않는다."
    )
    SWEEP_LIMIT_BULLET = (
        "- 검색의 마지막 출처 재확인(`vault_retrieval.py`)은 파일의 장치·inode·크기와 파일 시각"
        "(`st_mtime_ns`·`st_ctime_ns`)을 처음 읽기 전의 값과 견준다. 출처의 내용 해시를 확인한 뒤 그 파일이 같은 "
        "크기로 제자리에서 다시 쓰이고 이 값들이 그대로 남으면 이 재확인은 알아채지 못한다. 두 쓰기가 같은 파일 시각 "
        "눈금에 들어갈 때 그렇고, Windows에서는 `st_ctime_ns`가 만든 시각이어서 수정 시각을 되돌린 다시 쓰기도 "
        "그렇다(Windows 11, Python 3.12에서 20번 가운데 20번 옛 내용이 나왔다). v0.46.0에도 있던 한계이며 이번 "
        "릴리스에서 고치지 않았다(위 시험 수정은 시험만 바꾼다)."
    )
    RECORD_TIMING_SCOPE = (
        "It also fixes one timing-dependent test (`tests/test_vault_retrieval.py`) that earlier release documents "
        "had listed as unstable and that failed again in the local gates of this release; runtime code is unchanged "
        "by that fix."
    )
    RECORD_TIMING_EVIDENCE = (
        "- Test fix (a test listed as unstable since v0.23.0 that failed again in this release's local gates): "
        "`tests/test_vault_retrieval.py` "
        "`AdvancedRetrievalTests.test_later_source_verification_cannot_leave_earlier_source_stale` moves the "
        "rewritten file's modification time on explicitly. The receipt names the record of that test repeated 300 "
        "times in one process on the v0.46.0 tree and on the final commit."
    )
    RECORD_SWEEP_LIMIT = (
        "The final source sweep of retrieval compares file identity, size and file times with the values taken "
        "before the first read; a same-size in-place rewrite that leaves those equal after the source's content hash "
        "was verified is not noticed (two writes inside one file-time tick, or on Windows a rewrite whose "
        "modification time is put back, because `st_ctime_ns` is the creation time there). This limit is in v0.46.0 "
        "as well and is not fixed in this release."
    )
    TIMING_TEST = "test_later_source_verification_cannot_leave_earlier_source_stale"

    def test_v0470_note_states_the_timing_test_fix_and_the_sweep_limit(self) -> None:
        import re

        release = self.RELEASE.read_text(encoding="utf-8")
        opening = _folded(release[:release.index("\n## 변경\n")])
        self.assertIn("통과하지 못해 넣지 않았다. " + self.TIMING_FIX_OPENING + " 새 옵션 없이도", opening)
        # The new bullet of `## 변경` follows the test-fixture bullet; it holds the sentence about the
        # modification time and says that runtime code does not change.
        changes = [line for line in _section(release, "## 변경").splitlines() if line.startswith("- ")]
        fixture = next(index for index, line in enumerate(changes) if line.startswith("- 시험 fixture:"))
        self.assertEqual(changes[fixture + 1], self.TIMING_FIX_BULLET)
        self.assertIn("수정 시각을 명시적으로 뒤로 옮기게 고쳤다", changes[fixture + 1])
        self.assertIn("런타임 코드는 바뀌지 않는다", changes[fixture + 1])
        # The sentence about the earlier listings links three earlier documents, and each one names the test.
        links = re.findall(r"\]\(([^)]+)\)", self.EARLIER_LISTINGS)
        self.assertEqual(links, ["../verification/v0.23.0.md", "v0.25.0.md", "v0.26.0.md"])
        for link in links:
            target = (self.RELEASE.parent / link).resolve()
            self.assertTrue(target.is_file(), link)
            self.assertIn(self.TIMING_TEST, target.read_text(encoding="utf-8"), link)
        # The release note no longer reads like a first discovery of the test.
        self.assertNotIn("릴리스 게이트가 찾은", release)
        # The limit stands directly before the last boundary (injected commands and the repl).
        boundaries = [line for line in _section(release, "## 알려진 경계").splitlines() if line.startswith("- ")]
        self.assertTrue(boundaries[-1].startswith("- 주입 명령이 훅을 거치지 않는다는 관찰은"), boundaries[-1][:40])
        self.assertEqual(boundaries[-2], self.SWEEP_LIMIT_BULLET)
        self.assertIn("이 재확인은 알아채지 못한다", boundaries[-2])
        self.assertIn("v0.46.0에도 있던 한계", boundaries[-2])
        # The condition is equal file times compared with the stamp taken before the first read, and a
        # rewrite on Windows whose modification time is put back is named; speed is not the only case.
        self.assertIn("처음 읽기 전의 값과 견준다", boundaries[-2])
        self.assertIn("수정 시각을 되돌린 다시 쓰기도 그렇다", boundaries[-2])
        self.assertNotIn("구별되지 않을 만큼 빨리", release)

    def test_v0470_record_states_the_timing_test_fix_and_the_sweep_limit(self) -> None:
        import re

        record = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertIn(self.RECORD_TIMING_SCOPE, _folded(_section(record, "## Release content and scope")))
        self.assertNotIn("that the local gates of this release found", _folded(record))
        # The evidence bullet of the test fix follows the bullet of item 11.
        evidence = _section(record, "## Required feature evidence")
        bullets = [_folded(part) for part in re.split(r"\n(?=- )", evidence) if part.startswith("- ")]
        item_11 = next(index for index, bullet in enumerate(bullets) if bullet.startswith("- Item 11:"))
        self.assertEqual(bullets[item_11 + 1], self.RECORD_TIMING_EVIDENCE)
        self.assertIn(self.RECORD_SWEEP_LIMIT, _folded(_section(record, "## Limits")))

    def test_v0470_timing_test_moves_the_modification_time_after_the_rewrite(self) -> None:
        source = (REPO_ROOT / "tests/test_vault_retrieval.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn("os", {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names})
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef) and node.name == self.TIMING_TEST)
        segment = ast.get_source_segment(source, function)
        rewrite = segment.index('first.write_text("rollback FIRST_NEW')
        self.assertIn("os.utime(", segment[rewrite:])

    def test_v0470_sweep_limit_matches_the_stamp_fields(self) -> None:
        # The boundary sentence names the fields of `_stamp`; it stays true only while `_stamp` returns
        # exactly these five and the final sweep compares that stamp.
        source = (REPO_ROOT / "skills/agentic-vault/scripts/vault_retrieval.py").read_text(encoding="utf-8")
        stamp = next(node for node in ast.parse(source).body
                     if isinstance(node, ast.FunctionDef) and node.name == "_stamp")
        returns = [node for node in ast.walk(stamp) if isinstance(node, ast.Return)]
        self.assertEqual(len(returns), 1)
        self.assertIsInstance(returns[0].value, ast.Tuple)
        fields = [element.attr if isinstance(element, ast.Attribute) else None for element in returns[0].value.elts]
        self.assertEqual(fields, ["st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"])
        sweep = next(line for line in source.splitlines() if '"final source sweep"' in line)
        self.assertIn('_stamp(path) != documents[rel]["stamp"]', sweep)
        # "처음 읽기 전의 값" / "the values taken before the first read": the stamp kept for each source is
        # the one taken before the source is first read.
        lines = source.splitlines()
        kept = next(index for index, line in enumerate(lines)
                    if line.lstrip().startswith("documents[rel] = {") and '"stamp": before' in line)
        taken = max(index for index in range(kept) if lines[index].strip() == "before = _stamp(path)")
        read = max(index for index in range(kept) if "_legacy._read_markdown(path" in lines[index])
        self.assertLess(taken, read)


# v0.47.0 shared surfaces: README, SECURITY.md, docs/codex.md, docs/hardening.md, docs/README.md and
# commands/vault-upgrade.md carry the parts of items 1-5 and 9 that other phases leave to the release.
class V0470SharedSurfaceTests(unittest.TestCase):
    JARVIS_BRIDGE = "skills/agentic-vault/scripts/jarvis_bridge.py"
    JARVIS_SETUP = "commands/vault-jarvis-setup.md"
    LESSON_DELTA = REPO_ROOT / "skills/agentic-vault/scripts/vault_lesson_delta.py"
    HARDENING = REPO_ROOT / "docs/hardening.md"
    SECURITY = REPO_ROOT / "SECURITY.md"
    OWNERSHIP_HEADING = "### Engine copy ownership line (tried, not shipped)"
    OWNERSHIP_ROWS = (
        ("None (v0.46.0)", "15/16", "8/8", "3/4", "0/4"),
        ("Line 6 wording only", "15/16", "8/8", "3/4", "0/4"),
        ("W1, last line", "14/16", "8/8", "0/4", "0/4"),
        ("W1, after the `00-meta/` entry", "16/16", "8/8", "0/4", "0/4"),
        ("W2, last line", "15/16", "8/8", "0/4", "0/4"),
        ("W3, last line", "15/16", "8/8", "0/4", "0/4"),
    )
    # Where the eleven items come from, as each of the seven places states it: items 1-8 from the
    # review that compared the plugin with the vendor guide, item 9 a request handled in the same
    # review, items 10 and 11 carried over from the v0.46.0 release. Since v0.48.0 the seventh place,
    # the Codex long description, describes the current release, so SOURCE_CODEX_LONG is the opening
    # of its text in tag v0.47.0 and is no longer read from the tree.
    SOURCE_RELEASE_OPENING = "견준 대조 검토가 고칠 곳을 찾았다. 이번 릴리스는 열한 항목을 다룬다."
    SOURCE_RELEASE_SPLIT = (
        "이 가운데 `/aside-repl` 절차 명령은 같은 검토에서 함께 다룬 요청이고, 시험 fixture의 경합과 "
        "v0.46.0 문서 정정은 v0.46.0 릴리스에서 넘어온 것이며, 나머지 여덟(넣지 않은 계약 한 줄 포함)은 "
        "그 검토가 찾은 것이다."
    )
    SOURCE_RECORD = (
        "v0.47.0 builds on published v0.46.0 at `e47d0a2cca599538bbe4351eba908e5fa4290fda` and takes up "
        "eleven items ([release note](../releases/v0.47.0.md)). Items 1 to 8 come from a review that "
        "compared the plugin with a vendor prompting guide, item 9 is a request handled in the same review, "
        "and items 10 and 11 are carried over from the v0.46.0 release:"
    )
    SOURCE_README_KOREAN = (
        "열한 항목을 다룬다. 여덟은 이 플러그인을 공급사의 프롬프트 작성 안내서와 견준 대조 검토가 찾은 "
        "것이고, `/aside-repl` 절차 명령은 같은 검토에서 함께 다룬 요청이며, 시험 fixture의 경합과 v0.46.0 "
        "문서 정정은 v0.46.0 릴리스에서 넘어온 것이다."
    )
    SOURCE_README_ENGLISH = (
        "This release takes up eleven items: eight from a review that compared the plugin with a vendor "
        "prompting guide, one request handled in the same review (the `/aside-repl` procedure command) and "
        "two carried over from the v0.46.0 release (a test fixture race and errata for its documents)."
    )
    SOURCE_README_CURRENT = (
        ")을 다뤘다. 여덟은 대조 검토가 찾은 것이고, `/aside-repl` 절차 명령은 같은 검토에서 함께 다룬 "
        "요청이며, 마지막 둘은 v0.46.0 릴리스에서 넘어온 것이다. 엔진 사본 소유 줄은 시험 뒤 넣지 않았다."
    )
    SOURCE_CODEX_DOC = (
        "v0.47.0 takes up eleven items: eight from a review that compared the plugin with a vendor prompting "
        "guide, one request handled in the same review (the `/aside-repl` procedure command) and two carried "
        "over from the v0.46.0 release."
    )
    SOURCE_CODEX_LONG = (
        "Release v0.47.0 takes up eleven items, eight of them from a review that compared the plugin with a "
        "vendor prompting guide, one a request handled in the same review and two carried over from v0.46.0:"
    )
    SOURCE_STALE = ("그 가운데 열한 항목", "대조 검토가 찾은 열한 항목", "대조 검토에서 나온 열한 항목",
                    "eleven items from a review", "eleven items of a review")

    def test_readme_tree_lists_the_contributor_guide(self) -> None:
        readme = README.read_text(encoding="utf-8")
        tree = readme[readme.index("agentic-vault/\n├── .claude-plugin/"):]
        tree = tree[:tree.index("```")]
        # Root AGENTS.md and .claude/CLAUDE.md, each on its own top-level line; no root CLAUDE.md.
        entries = [line.split("←")[0].strip() for line in tree.splitlines() if line.startswith("├── ")]
        self.assertIn("├── AGENTS.md", entries)
        self.assertIn("├── .claude/CLAUDE.md", entries)
        self.assertNotIn("AGENTS.md · CLAUDE.md", tree)
        self.assertIn("← Claude Code가 위 안내를 불러오는 한 줄 (@../AGENTS.md)", tree)
        docs_index = (REPO_ROOT / "docs/README.md").read_text(encoding="utf-8")
        self.assertIn("](../AGENTS.md)", docs_index)

    def test_v0470_texts_call_the_review_a_comparison(self) -> None:
        # The comparison with the vendor guide was made for the maintainer by read-only agents, and
        # a second set of agents checked their findings; no third party took part. The v0.47.0
        # texts say so and never call it an outside review (the published v0.46.0 texts keep theirs).
        release = V0470ReleaseMetadataTests.RELEASE.read_text(encoding="utf-8")
        opening = release[:release.index("\n## ")]
        self.assertIn("대조 검토", opening)
        self.assertNotIn("외부 검토", release)
        readme = README.read_text(encoding="utf-8")
        korean = _section(readme, "## v0.47.0 — 안내서 대조 검토 반영과 aside-repl 절차 명령")
        self.assertIn(self.SOURCE_README_KOREAN, _folded(korean))
        self.assertNotIn("외부", korean)
        # Since v0.48.0 the current-version line keeps the v0.47.0 summary as retained history.
        current = next(line for line in readme.splitlines() if line.startswith("현재 버전은 **v"))
        self.assertIn(self.SOURCE_README_CURRENT, current)
        self.assertNotIn("외부 검토", current)
        english = _section(readme, "## v0.47.0 — guide-comparison fixes and the aside-repl procedure command")
        codex_doc = (REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8")
        codex_section = _section(codex_doc, "## v0.47.0 — guide-comparison fixes and the aside-repl procedure command")
        # The Codex long description describes the current release (v0.48.0 since then); its v0.47.0 text
        # is kept in tag v0.47.0.
        texts = {"readme": english, "docs_codex": codex_section,
                 "record": V0470ReleaseMetadataTests.VERIFICATION.read_text(encoding="utf-8")}
        for name, text in texts.items():
            with self.subTest(text=name):
                folded = _folded(text)
                self.assertIn("a review that compared the plugin with a vendor prompting guide", folded)
                self.assertNotIn("outside", folded.lower())

    def test_v0470_texts_say_where_the_eleven_items_come_from(self) -> None:
        # Items 1-8 are findings of the comparison review (item 2 included, tried and not shipped),
        # item 9 is a request handled in the same review, and items 10 and 11 are carried over from
        # the v0.46.0 release; none of the seven places may say that all eleven come from the review.
        import re
        release = V0470ReleaseMetadataTests.RELEASE.read_text(encoding="utf-8")
        record = V0470ReleaseMetadataTests.VERIFICATION.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        codex_doc = (REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8")
        codex_long = json.loads(
            (REPO_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["interface"]["longDescription"]
        opening = _folded(release[:release.index("\n## ")])
        scope = _section(record, "## Release content and scope")
        korean = _folded(_section(readme, "## v0.47.0 — 안내서 대조 검토 반영과 aside-repl 절차 명령"))
        english = _folded(_section(readme, "## v0.47.0 — guide-comparison fixes and the aside-repl procedure command"))
        # The current-version line keeps the v0.47.0 summary as history since v0.48.0.
        current = next(line for line in readme.splitlines() if line.startswith("현재 버전은 **v"))
        codex_section = _folded(_section(codex_doc,
                                         "## v0.47.0 — guide-comparison fixes and the aside-repl procedure command"))
        expected = (
            ("release opening", opening,
             "안내서(아래 `참고와 기존 활용의 차이`)와 " + self.SOURCE_RELEASE_OPENING + " 저장소 기여자 안내,"),
            ("release opening", opening,
             "v0.46.0 문서 정정이다. " + self.SOURCE_RELEASE_SPLIT + " 엔진 사본 소유를 밝히는 계약 한 줄은"),
            ("record scope", _folded(scope), self.SOURCE_RECORD),
            ("readme korean", korean, self.SOURCE_README_KOREAN),
            ("readme english", english, self.SOURCE_README_ENGLISH),
            ("readme current", current, "안내서 대조 검토 반영과 aside-repl 절차 명령: 열한 항목(기여자 안내,"),
            ("readme current", current, "v0.46.0 문서 정정" + self.SOURCE_README_CURRENT),
            ("docs codex", codex_section, self.SOURCE_CODEX_DOC),
        )
        for name, text, sentence in expected:
            with self.subTest(text=name, sentence=sentence[:40]):
                self.assertIn(sentence, text)
        self.assertTrue(korean.startswith("## v0.47.0 — 안내서 대조 검토 반영과 aside-repl 절차 명령 "
                                          + self.SOURCE_README_KOREAN))
        self.assertTrue(english.startswith("## v0.47.0 — guide-comparison fixes and the aside-repl procedure "
                                           "command " + self.SOURCE_README_ENGLISH))
        self.assertTrue(codex_section.startswith("## v0.47.0 — guide-comparison fixes and the aside-repl "
                                                 "procedure command " + self.SOURCE_CODEX_DOC))
        # The Codex long description moved on to v0.48.0; SOURCE_CODEX_LONG opens its text in tag v0.47.0, and
        # the stale phrasings below stay forbidden in the current one.
        # The numbers the record's sentence names exist in its item list.
        self.assertEqual(re.findall(r"^- Item (\d+)\b", scope, re.MULTILINE), [str(n) for n in range(1, 12)])
        texts = {"release": release, "record": record, "readme": readme, "docs_codex": codex_doc,
                 "codex_long": codex_long}
        for name, text in texts.items():
            for stale in self.SOURCE_STALE:
                with self.subTest(text=name, stale=stale):
                    self.assertNotIn(stale, _folded(text))

    def test_v0470_note_describes_the_skip_notice_as_the_bridge_sends_it(self) -> None:
        # The notice names the slot as well as its date, time and attempt count, and guesses no cause
        # (jarvis_bridge._briefing_skip_notice); the note must not say it holds only date, time and count.
        bridge = _published_text("v0.47.0", self.JARVIS_BRIDGE)
        notice = bridge[bridge.index("def _briefing_skip_notice"):bridge.index("def _briefing_prompt")]
        self.assertIn("{name}", notice)
        changes = _folded(_section(V0470ReleaseMetadataTests.RELEASE.read_text(encoding="utf-8"), "## 변경"))
        self.assertIn("소유자에게 날짜·시각·회차 이름과 시도 횟수를 적고 원인은 짐작하지 않는 안내를 한 번 보낸다.", changes)
        self.assertNotIn("시도 횟수만 담은 안내", changes)

    def test_codex_long_description_conditions_the_checker_sentence(self) -> None:
        # Preserve the published v0.47.0 description where checker_sha256 did not change
        # from v0.46.0 (finding RD-3); v0.55.0 intentionally transitions checker_sha256.
        codex = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.47.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        long_description = codex["interface"]["longDescription"]
        self.assertIn("For transitions from v0.45.1 or earlier, where " + V0310_CODEX_UPGRADE_SENTENCE,
                      long_description)
        self.assertEqual(long_description.count(V0310_CODEX_UPGRADE_SENTENCE), 1)

    def test_readme_korean_section_conditions_the_certificate_sentence(self) -> None:
        # The certificate stays valid only for a vault that comes from v0.46.0 (finding RD-4).
        korean = _folded(_section(README.read_text(encoding="utf-8"),
                                  "## v0.47.0 — 안내서 대조 검토 반영과 aside-repl 절차 명령"))
        for sentence in ("v0.46.0에서 올 때는 인증서가 묶는 일곱 파일이 같아 경고 승격 인증서를 다시 만들 필요가 없다"
                         "(실제 설치 바이트가 같을 때).",
                         "v0.45.1 이하에서 올 때는 [v0.46.0 업그레이드 안내](docs/releases/v0.46.0.md#하위호환업그레이드)를 "
                         "따른다."):
            with self.subTest(sentence=sentence[:20]):
                self.assertIn(sentence, korean)
        self.assertNotIn("인증서가 묶는 일곱 파일은 바뀌지 않아", korean)
        self.assertIn("\n## 하위호환·업그레이드\n",
                      (REPO_ROOT / "docs/releases/v0.46.0.md").read_text(encoding="utf-8"))

    def test_contributor_guide_layout_is_stated_as_shipped(self) -> None:
        # The guide ships as root AGENTS.md plus .claude/CLAUDE.md; no text may name a root CLAUDE.md.
        release = V0470ReleaseMetadataTests.RELEASE.read_text(encoding="utf-8")
        changes = _section(release, "## 변경")
        first = next(line for line in changes.splitlines() if line.startswith("- "))
        self.assertTrue(first.startswith(
            "- 저장소 루트에 기여자·에이전트 안내 [AGENTS.md](../../AGENTS.md)를 두고, Claude Code가 이를 불러오도록 "
            ".claude/CLAUDE.md(한 줄: @../AGENTS.md)를 둔다. 유지관리자가 설계와 릴리스를 정하고, "), first)
        self.assertIn("릴리스 ZIP과 설치본에 AGENTS.md와 .claude/CLAUDE.md가 늘어난다. 플러그인 동작은 바뀌지 않는다.",
                      _section(release, "## 하위호환·업그레이드").splitlines())
        record = _folded(V0470ReleaseMetadataTests.VERIFICATION.read_text(encoding="utf-8"))
        self.assertIn("root `AGENTS.md` and `.claude/CLAUDE.md`", record)
        readme = README.read_text(encoding="utf-8")
        korean = _section(readme, "## v0.47.0 — 안내서 대조 검토 반영과 aside-repl 절차 명령")
        english = _section(readme, "## v0.47.0 — guide-comparison fixes and the aside-repl procedure command")
        self.assertIn(".claude/CLAUDE.md", korean)
        self.assertIn("`.claude/CLAUDE.md`", english)
        codex = json.loads((REPO_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        texts = {"release": release, "record": record, "readme": readme, "codex_long": codex["interface"]["longDescription"],
                 "docs_codex": (REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8"),
                 "docs_index": (REPO_ROOT / "docs/README.md").read_text(encoding="utf-8")}
        for name, text in texts.items():
            folded = _folded(text)
            for stale in ("AGENTS.md·CLAUDE.md", "AGENTS.md · CLAUDE.md", "이를 가져오는 CLAUDE.md",
                          "imported by `CLAUDE.md`", "and `CLAUDE.md` (an import", "그 CLAUDE.md를",
                          "the `CLAUDE.md` of a plugin checkout", "neither root file", "has both at its top level"):
                with self.subTest(text=name, stale=stale):
                    self.assertNotIn(stale, folded)

    def test_hardening_ownership_subsection_states_the_dropped_result(self) -> None:
        text = self.HARDENING.read_text(encoding="utf-8")
        self.assertEqual(text.count(self.OWNERSHIP_HEADING + "\n"), 1)
        start = text.index(self.OWNERSHIP_HEADING + "\n")
        end = text.index("\n## ", start)
        # It closes the rule-facts section, directly before the sync-conflict section.
        self.assertLess(text.index("\n## Rule facts and tool results\n"), start)
        self.assertEqual(end, text.index("\n## 동기화 충돌 사본과 checkpoint 이후 변경 경고 (선택)\n"))
        section = text[start:end]
        rows = [tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
                for line in section.splitlines()
                if line.startswith("| ") and not line.startswith("| Wording") and not line.startswith("| ---")]
        self.assertEqual(tuple(rows), self.OWNERSHIP_ROWS)
        folded = _folded(section)
        for literal in ("The line was not shipped because", "0/4", "Qwen3.5-9B-Q8_0.gguf", "b10729-458681e1d",
                        "harness is not published"):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)
        self.assertNotIn("<", section)
        self.assertNotIn("x/", section)

    def test_readme_describes_optional_model_and_effort(self) -> None:
        readme = _published_text("v0.47.0", "README.md")
        for literal in ("`jarvis.model`·`jarvis.effort`", "`low`·`medium`·`high`·`xhigh`·`max`",
                        "비어 있으면 브리지는 `--model`·`--effort`를 넘기지 않아",
                        "`--bare`에서 인증된 응답 생성과 노력 수준 적용은 확인하지 않았다"):
            with self.subTest(literal=literal):
                self.assertIn(literal, readme)
        self.assertIn("(선택 키 `model`·`effort` 포함, 기본 빈 값)", readme)
        limits = _section(_published_text("v0.47.0", "SECURITY.md"), "## Known limitations")
        bullets = [_folded(bullet) for bullet in ("\n" + limits).split("\n- ")[1:]]
        named = [bullet for bullet in bullets if "`jarvis.model`" in bullet]
        self.assertEqual(len(named), 1)
        self.assertIn("were not run", named[0])

    def test_readme_states_the_briefing_attempt_limit_and_refusal_limit(self) -> None:
        attempts = 8
        self.assertIn(f"BRIEFING_MAX_ATTEMPTS = {attempts}", _published_text("v0.47.0", self.JARVIS_BRIDGE).splitlines())
        readme = _published_text("v0.47.0", "README.md")
        for literal in (f"같은 회차가 {attempts}번 연속 실패하면",
                        "이 안내가 전달되지 않아도 다시 보내지 않고 로그에만 남긴다.",
                        "날이 바뀔 때 전날의 `pending` 회차는 한 번만 더 시도하고",
                        "시도 횟수는 브리지 메모리에만 두므로 브리지를 다시 시작하면 0부터 센다.",
                        "모델 제공자의 요청 거절과 장애·빈 응답을 구별하지 못하며"):
            with self.subTest(literal=literal):
                self.assertIn(literal, readme)

        def block(text: str) -> str:
            start = text.index("이 보장에도 조건이 붙는다. 같은 회차가")
            end = text.index("0부터 센다.", start) + len("0부터 센다.")
            return text[start:end]

        self.assertEqual(block(readme), block(_published_text("v0.47.0", self.JARVIS_SETUP)))
        self.assertIn(f"since v0.47.0 a scheduled briefing slot that fails {attempts} times in a row is skipped",
                      readme)

    def test_skip_notice_wording_names_the_slot_in_every_text(self) -> None:
        # The notice also names the slot and guesses no cause (jarvis_bridge._briefing_skip_notice), so no
        # text may say it holds only the date, time and attempt count (host result HR-7, finding BR-4).
        phrase = "회차의 날짜·시각·이름과 시도 횟수를 적고 원인은 짐작하지 않는 안내를 한 번 보낸다"
        readme = _published_text("v0.47.0", "README.md")
        setup = _published_text("v0.47.0", self.JARVIS_SETUP)
        release = V0470ReleaseMetadataTests.RELEASE.read_text(encoding="utf-8")
        for name, text in (("readme", readme), ("setup", setup)):
            with self.subTest(text=name):
                self.assertEqual(_folded(text).count(phrase), 1)
        for name, text in (("readme", readme), ("setup", setup), ("release", release)):
            for stale in ("날짜·시각과 시도 횟수만 담은 안내", "날짜·시각과 시도 횟수가 든 안내"):
                with self.subTest(text=name, stale=stale):
                    self.assertNotIn(stale, _folded(text))
        entries = _folded(_section(release, "## 기본 결과 변경"))
        self.assertIn("소유자는 날짜·시각·회차 이름과 시도 횟수가 든 안내를 한 번 받는다.", entries)

    def test_constraints_guidance_asks_for_scope_reason_and_removal(self) -> None:
        section = _folded(_section(self.HARDENING.read_text(encoding="utf-8"),
                                   "## Compact constraints note (`constraints_note`)"))
        for literal in ("scope and its reason", "Remove an item when its reason no longer holds",
                        "only the first 120 characters"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)
        row = next(line for line in README.read_text(encoding="utf-8").splitlines()
                   if line.startswith("| `constraints_note` |"))
        self.assertIn("적용 범위와 이유", row)
        self.assertIn("이유가 사라진 항목은 지운다", row)

    def test_retire_reason_section_names_the_real_error_codes(self) -> None:
        text = self.HARDENING.read_text(encoding="utf-8")
        self.assertEqual(text.count("\n## 교훈 은퇴 사유\n"), 1)
        section = _section(text, "## 교훈 은퇴 사유")
        literals = {node.value for node in ast.walk(ast.parse(self.LESSON_DELTA.read_text(encoding="utf-8")))
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        for code in ("invalid_retire_reason", "retire_reason_exists", "retire_reason_unsupported"):
            with self.subTest(code=code):
                self.assertIn(code, section)
                self.assertIn(code, literals)

    def test_reasonless_retire_notes_state_the_kept_reason_cell_exception(self) -> None:
        # A row whose status cell already reads `상태: retired(…, YYYY-MM-DD)` keeps that cell under v0.47.0,
        # so a reasonless RETIRE proposal that v0.46.0 prepared for it is refused when applied (finding LA-1).
        literals = {node.value for node in ast.walk(ast.parse(self.LESSON_DELTA.read_text(encoding="utf-8")))
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertIn("proposal_changed_or_stale", literals)
        release = _folded(_section(V0470ReleaseMetadataTests.RELEASE.read_text(encoding="utf-8"),
                                   "## 하위호환·업그레이드"))
        self.assertIn(
            "상태 칸이 이미 `상태: retired(…, YYYY-MM-DD)`인 줄을 빼면 사유 없는 옛 RETIRE 제안은 같은 바이트로 다시 "
            "준비되고, 제안의 `version`은 1 그대로다. 그런 줄에서는 v0.47.0이 그 칸을 그대로 두므로(기본 결과 변경 "
            "5번) v0.46.0이 준비한 제안은 `proposal_changed_or_stale`로 거부되며 다시 준비해야 한다.", release)
        self.assertEqual(release.count("사유 없는 옛 RETIRE 제안은 같은 바이트로"), 1)
        hardening = _folded(_section(self.HARDENING.read_text(encoding="utf-8"), "## 교훈 은퇴 사유"))
        self.assertIn(
            "- `reason`이 없으면 상태 칸은 예전처럼 `상태: retired`이고, 사유 없는 옛 제안은 같은 바이트로 다시 계산되어 "
            "적용된다. 다만 상태 칸이 이미 `상태: retired(…, YYYY-MM-DD)`인 줄은 그 칸을 그대로 두므로, 그런 줄에 "
            "v0.46.0이 준비한 사유 없는 제안은 `proposal_changed_or_stale`로 거부되고 다시 준비해야 한다.", hardening)

    def test_security_policy_states_the_injection_observation(self) -> None:
        security = self.SECURITY.read_text(encoding="utf-8")
        limits = _folded(_section(security, "## Known limitations"))
        for literal in ("**Commands that a command or skill body injects bypass PreToolUse hooks (observed).**",
                        "2.1.252", "2026-11-12", "disableSkillShellExecution", "allowed-tools",
                        "two runs whose hook log was checked", "deny and ask rules still override",
                        "in the two runs whose hook log was checked (the probe command pre-approved its injected "
                        "command with `allowed-tools`), the three commands"):
            with self.subTest(limits=literal):
                self.assertIn(literal, limits)
        for stale in ("four injected commands", "states neither behaviour"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, limits)
        summary = _folded(_section(security, "## 한국어 요약"))
        for literal in ("disableSkillShellExecution", "2.1.252", "훅 기록을 확인한 두 번의 실행",
                        "훅 기록을 확인한 두 번의 실행(시험한 명령은 `allowed-tools`로 주입 명령을 미리 허용했다) 동안"):
            with self.subTest(summary=literal):
                self.assertIn(literal, summary)
        self.assertNotIn("문서상 권한 규칙만 거친다", summary)


# v0.48.0: six items that add a generated Antigravity CLI package (antigravity/) beside the unchanged
# Claude Code and Codex plugin root: the Antigravity mode of the session-start hook, the Stop checkpoint,
# the generator and its committed package, the isolated install verifier, host-neutral shared texts and
# the documents. No certificate-bound file changed (the v0.46.0 fingerprint stays), only the health check
# moves to engine=0.48.0, and no real agy conversation was run. The note fixes the publication order; the
# record specifies gates, not results.
class V0480ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs/releases/v0.48.0.md"
    VERIFICATION = REPO_ROOT / "docs/verification/v0.48.0.md"
    TITLE = "# v0.48.0 — Antigravity CLI 겸용 패키지"
    SECTIONS = ("## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 참고와 기존 활용의 차이",
                "## 검증", "## 알려진 경계")
    INSTALL_REF = "현재 설치 ref는 `v0.48.0`이다."
    AGY_URL = "https://github.com/Technoetic/agentic-vault/tree/v0.48.0/antigravity"
    CHANGES = 6
    CERTIFICATE_SENTENCE = (
        f"일곱 bound 파일은 v0.46.0·v0.47.0과 같으므로 checker_sha256는 `{CHECKER_SHA256_V0460}` 그대로다. "
        "v0.46.0이나 v0.47.0에서 만든 인증서는 실제 설치 바이트가 같으면 그대로 맞는다. v0.45.1 이하에서 올 때는 "
        "[v0.46.0의 순서](v0.46.0.md#하위호환업그레이드)를 따른다."
    )
    DEFAULT_CHANGES_LEAD = (
        "플러그인을 갱신하면 명령·스킬·훅 코드는 바로 바뀐다. 볼트에 설치된 사본(규칙·healthcheck·생성 "
        "AGENTS.md)은 /vault-upgrade(Codex는 upgrade)를 실행하기 전까지 예전대로다."
    )
    # The list is drafted from the specification; the flags-off comparison of the integrated tree
    # confirms it (every difference maps to an entry) before the count is final.
    DEFAULT_CHANGES = 10
    ORDER_SENTENCE = (
        "후보 브랜치 CI(최종 커밋) → 로컬 태그·ZIP·재빌드 확인·Codex 설치 확인·Antigravity 패키지 확인과 격리 "
        "설치·인증서 → 태그 push → 태그 CI 11/11 → 참고 대장 갱신과 독립 확인 → 독립 릴리스 검토 → master push → "
        "master CI 11/11 → 검증 기록(verification.json) → 업로드 → 초안 점검 → 게시 → 게시 후 확인"
    )
    LOCAL_RUNS = ("전체 로컬 시험은 통합한 트리에서 첫 후보 브랜치 push 전에, 그 뒤 트리가 바뀐 후보 push 전마다, "
                  "그리고 최종 커밋에서 다시 실행한다.")
    NO_CONVERSATION = "실제 agy 대화 세션은 하지 않았다."
    RECORD_TITLE = "# v0.48.0 verification contract"
    RECORD_OPENING = V0470ReleaseMetadataTests.RECORD_OPENING
    RECORD_SECTIONS = V0470ReleaseMetadataTests.RECORD_SECTIONS
    GATE_LABELS = V0470ReleaseMetadataTests.GATE_LABELS
    # The local steps that the release repeats before every changed candidate and on the final commit;
    # the three Antigravity steps replace the /aside-repl run of v0.47.0 (that command did not change).
    GATE_ONE_STEPS = ("unittest", "compile", "whitespace", "smoke", "claude plugin validate --strict",
                      "build_antigravity_package.py check", "verify_antigravity_plugin.py",
                      "agy plugin validate --strict", "flags-off", "independent source review")
    GATE_THREE_ANTIGRAVITY = "the Antigravity package check and the isolated install from the extracted ZIP"
    # The two placeholders of the generated command skills and the slash-command form of the official
    # skills page are named in code spans; nothing else in the documents is a fill-in marker.
    NAMED_PLACEHOLDERS = ("`<PLUGIN_ROOT>`", "`<ARGUMENTS>`", "`/<스킬 이름>`")
    BOUNDARY_LITERALS = (
        "실제 agy 대화 세션(모델 호출)은 실행하지 않았다.",
        "제3자 소프트웨어로 Antigravity 로그인에 접근하면 약관 위반",
        "Windows에서 훅 명령을 실행하는 셸은 문서에 없고 확인하지 않았다.",
        "실행기가 없는 처리기는 실패하고 agy가 그 오류를 기록한다",
        # The Plugins and Build plugins pages call a plugin skill /<skill-name>; Customization discovery
        # names plugin skills <plugin>:<skill>. Which form agy shows and how the text after the command
        # reaches the skill are what is open.
        "Plugins·Build plugins 문서는 플러그인 스킬을 `/<스킬 이름>`으로 부른다고 적고(`/vault-day`),",
        "Customization discovery 문서는 플러그인 스킬의 이름이 `agentic-vault:vault-day`처럼 플러그인 이름으로 "
        "시작한다고 적는다.",
        "실제 agy가 어느 표기를 쓰는지와, 뒤에 적은 텍스트가 스킬에 어떻게 전달되는지는 확인하지 않았다.",
        "`injectSteps`의 크기 상한은 확인하지 않았다.",
        "압축 뒤에는 인계를 다시 넣지 않",
        # A claim marker lasts 120 seconds: how agy numbers executionNum and whether a reopened
        # conversation starts a new SessionStart are not documented.
        "표지는 120초 동안만 유효하다.",
        "`executionNum`의 범위",
        "SessionStart가 다시 오는지는 문서에 없고 확인하지 않았다.",
        "deny zone 가드·교훈 단서 훅·Aside 자동 기동 훅",
        "Jarvis 브리지는 Claude CLI로만 응답을 만들며",
        "`vault_doctor.py`는 agy 설치와 Antigravity 플러그인 상태를 진단하지 않는다.",
        "상호작용 데이터 수집",
        "설정에서 끌 수 있",
        "비즈니스 계정에서는 이 수집이 늘 꺼져 있다.",
        "실행 권한을 보존하는지",
    )
    RECORD_LIMIT_LITERALS = (
        "No real agy conversation was run",
        "were not run",
        "the shell that runs hook commands on Windows",
        "three handlers",
        "are not ported",
        "interaction data",
        "business accounts",
        "`executionNum`",
        "120 seconds",
    )
    # Sentences that the independent review found false or wider than their evidence (first and second
    # pass); none may come back in the release note or the guide.
    RETRACTED = (
        "`/vault-day` 또는 `/agentic-vault:vault-day`",
        "가장 최근 체크포인트는 에이전트가 마지막으로 완전히 멈춘 시점의 해시다",
        "실행 번호(`executionNum`)마다 한 번만 쓴다",
        "`lastUserInput`·`transcriptPath`와 경로는 저장하지 않고",
        "훅이나 `--event Stop`으로만 생기므로",
        "URL이 아니라,",
        "같은 볼트를 세 호스트에서 쓸 수 있다.",
        "(둘 이상이면 표준 오류에",
        "원본 격리 같은 절차가 실패한다.",
        "이름공간이 붙는지와, 뒤에 적은 텍스트가 스킬에 어떻게 전달되는지는 문서에 없고",
    )
    # Official Antigravity pages read on 2026-11-13 that the release note and the guide cite
    # (https://antigravity.google/docs/<page>).
    OFFICIAL_PAGES = ("build-plugins", "hooks", "customization-discovery", "plugins", "rules", "permissions",
                      "cli/commands/permissions", "settings", "cli/reference", "skills", "faq")
    # Probe IDs of the release specification and of the feature evidence tool: the item each probe covers
    # and literals of what it measures, its baseline included.
    PROBES = (
        ("E1", "- Item 3:", ("`antigravity/plugin.json`", "`agy plugin validate --strict --json`", "17 skills",
                            "1 hook group", "no plugin.json found")),
        ("E2", "- Item 4:", ("`agy plugin install`", "`agy plugin list --json`", "source `antigravity`",
                            "byte-identical", "`real_home_unchanged`", "`claude-code`", "`agentic-vault-cmd-*`")),
        ("E3", "- Item 1:", ("`workspacePaths`", "`injectSteps`", "the default-mode startup output",
                            "several-vaults line", "0 bytes")),
        ("E4", "- Item 2:", ("`fullyIdle`", "SHA-256 of the `conversationId`", "`lastUserInput`",
                            "`transcriptPath`", "`--host antigravity`", "`--event Stop`")),
        ("E5", "- Probe E5", ("`startup`", "`compact`", "`PreCompact`", "`SessionEnd`", "minus their time field",
                             "byte-identical", "`hooks/hooks.json`", "`assets/templates/settings-permissions.json`")),
    )
    # Files under assets/, hooks/ and skills/ that carry the active release engine stamp in their first five lines.
    DECLARED_STAMPS = {
        "skills/agentic-vault/scripts/vault_healthcheck.py",
    }

    def test_v0480_release_note_sections_and_install_ref(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        lines = release.splitlines()
        self.assertEqual(lines[0], self.TITLE)
        headings = [line for line in lines if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.SECTIONS)
        # The closing paragraph after the last section states the install refs and the stamp.
        closing = _folded(release[release.index("\n" + self.SECTIONS[-1] + "\n"):])
        for literal in (self.INSTALL_REF, "engine=0.48.0", "--ref v0.48.0", f"`{self.AGY_URL}`"):
            with self.subTest(literal=literal):
                self.assertIn(literal, closing)

    def test_v0480_changes_are_the_six_items(self) -> None:
        changes = _section(self.RELEASE.read_text(encoding="utf-8"), "## 변경")
        bullets = [line for line in changes.splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), self.CHANGES)
        # Items 1 to 6 of the specification, in order.
        for bullet, start in zip(bullets, ("- [세션 시작 훅](../../hooks/session_start.py)",
                                           "- [체크포인트 훅](../../hooks/session_checkpoint.py)",
                                           "- [패키지 생성기](../../scripts/build_antigravity_package.py)",
                                           "- [격리 설치 확인 도구](../../scripts/verify_antigravity_plugin.py)",
                                           "- 공용 문구:", "- 문서:")):
            with self.subTest(item=start):
                self.assertTrue(bullet.startswith(start), bullet[:60])
        self.assertIn("v0.47.0과 같다", _folded(changes[:changes.index("\n- [체크포인트 훅]")]))

    def test_v0480_states_the_unchanged_checker_once(self) -> None:
        for document in (self.RELEASE, self.VERIFICATION):
            text = document.read_text(encoding="utf-8")
            with self.subTest(document=document.name):
                self.assertEqual(text.count(CHECKER_SHA256_V0460), 1)
                self.assertEqual(text.count(CHECKER_SHA256_V0460[:8]), 1)
            for digest in (CHECKER_SHA256_V0340, CHECKER_SHA256_V0330, CHECKER_SHA256_V0320):
                with self.subTest(document=document.name, absent=digest[:8]):
                    self.assertNotIn(digest[:8], text)
        release = self.RELEASE.read_text(encoding="utf-8")
        self.assertIn(self.CERTIFICATE_SENTENCE, _folded(_section(release, "## 하위호환·업그레이드")))
        record = _folded(self.VERIFICATION.read_text(encoding="utf-8"))
        self.assertIn(f"checker fingerprint `{CHECKER_SHA256_V0460}`", record)
        self.assertIn(f"`{V0470ReleaseMetadataTests.CORPUS_SHA256}`", record)
        self.assertIn(f"`{V0470ReleaseMetadataTests.RESULT_SHA256}`", record)

    def test_v0480_publication_order(self) -> None:
        import re

        record = self.VERIFICATION.read_text(encoding="utf-8")
        gates = _section(record, "## Final release gates")
        self.assertEqual(re.findall(r"(?m)^(\d+)\. \*\*", gates), [str(n) for n in range(1, 15)])
        folded = _folded(gates)
        position = -1
        for number, label in enumerate(self.GATE_LABELS, 1):
            with self.subTest(gate=number):
                found = folded.find(f"{number}. **{label}**")
                self.assertGreater(found, position)
                position = found
        release = self.RELEASE.read_text(encoding="utf-8")
        check = _section(release, "## 검증")
        self.assertIn(self.ORDER_SENTENCE, check.splitlines())
        self.assertEqual(_folded(release).count(self.ORDER_SENTENCE), 1)
        self.assertIn("](../verification/v0.48.0.md)", check)
        self.assertIn(self.NO_CONVERSATION, _folded(check))

    def test_v0480_record_specifies_gates_not_results(self) -> None:
        record = self.VERIFICATION.read_text(encoding="utf-8")
        lines = record.splitlines()
        self.assertEqual(lines[0], self.RECORD_TITLE)
        self.assertTrue(_folded(record).startswith(self.RECORD_TITLE + " " + self.RECORD_OPENING))
        headings = [line for line in lines if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.RECORD_SECTIONS)
        folded = _folded(record)
        for literal in ("verification.json", "claude plugin validate --strict", "agy plugin validate --strict",
                        "Reference Atlas", "flags-off", "this document does not assert they passed",
                        "measured at gate 3"):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)
        # The number of resources the native Codex check compares is measured at gate 3, not carried over.
        self.assertNotIn("130", folded)

    def test_v0480_gate_one_and_three_name_the_antigravity_checks(self) -> None:
        gates = _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"), "## Final release gates"))
        gate_one = gates[gates.index("1. **" + self.GATE_LABELS[0]):gates.index("2. **" + self.GATE_LABELS[1])]
        for step in self.GATE_ONE_STEPS:
            with self.subTest(step=step):
                self.assertIn(step, gate_one)
        self.assertIn("Before the first candidate-branch push", gate_one)
        self.assertIn("again on the final commit", gate_one)
        gate_three = gates[gates.index("3. **" + self.GATE_LABELS[2]):gates.index("4. **" + self.GATE_LABELS[3])]
        self.assertIn(self.GATE_THREE_ANTIGRAVITY, gate_three)
        self.assertIn("only candidate branches have been pushed", gate_three)
        self.assertNotIn("Nothing is pushed", gates)
        # The release note names the same repetitions and the same Antigravity checks as gate 1.
        check = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 검증"))
        self.assertIn(self.LOCAL_RUNS, check)
        for literal in ("`build_antigravity_package.py check`", "`agy plugin validate --strict`",
                        "`verify_antigravity_plugin.py`"):
            with self.subTest(note=literal):
                self.assertIn(literal, check)

    def test_v0480_moves_only_the_declared_stamps(self) -> None:
        stamped = set()
        for top in ("assets", "hooks", "skills"):
            for path in (REPO_ROOT / top).rglob("*"):
                if not path.is_file():
                    continue
                try:
                    head = path.read_text(encoding="utf-8-sig").splitlines()[:5]
                except UnicodeDecodeError:
                    continue
                if any(f"engine={EXPECTED}" in line for line in head):
                    stamped.add(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(stamped, self.DECLARED_STAMPS)

    def test_v0480_default_changes_are_numbered(self) -> None:
        import re

        section = _section(self.RELEASE.read_text(encoding="utf-8"), "## 기본 결과 변경")
        self.assertIn(self.DEFAULT_CHANGES_LEAD, _folded(section))
        self.assertEqual(re.findall(r"(?m)^(\d+)\. ", section),
                         [str(n) for n in range(1, self.DEFAULT_CHANGES + 1)])
        # Every entry names whom it concerns.
        entries = re.split(r"(?m)^\d+\. ", section)[1:]
        for number, entry in enumerate(entries, 1):
            with self.subTest(entry=number):
                self.assertIn("대상:", entry)

    def test_v0480_no_placeholder_left(self) -> None:
        import re

        for document in (self.RELEASE, self.VERIFICATION):
            text = document.read_text(encoding="utf-8")
            for placeholder in self.NAMED_PLACEHOLDERS:
                text = text.replace(placeholder, "")
            with self.subTest(document=document.name):
                self.assertIsNone(re.search(r"<[^<>\n]*>", text))
                self.assertNotIn("TBD", text)
                self.assertNotIn("TODO", text)

    def test_v0480_document_links_resolve(self) -> None:
        import re
        from urllib.parse import unquote

        for document in (self.RELEASE, self.VERIFICATION):
            for target in re.findall(r"\]\(([^)\s]+)\)", document.read_text(encoding="utf-8")):
                if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / unquote(target.split("#", 1)[0])).exists(), target)

    def test_v0480_boundaries_state_what_was_not_run(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        boundaries = _folded(_section(release, "## 알려진 경계"))
        for literal in self.BOUNDARY_LITERALS:
            with self.subTest(boundary=literal[:30]):
                self.assertIn(literal, boundaries)
        self.assertIn("](../antigravity.md#직접-해-보는-확인)", boundaries)
        limits = _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"), "## Limits"))
        for literal in self.RECORD_LIMIT_LITERALS:
            with self.subTest(limit=literal):
                self.assertIn(literal, limits)

    def test_v0480_reference_table_names_the_sources_read(self) -> None:
        section = _section(self.RELEASE.read_text(encoding="utf-8"), "## 참고와 기존 활용의 차이")
        rows = [line for line in section.splitlines() if line.startswith("| [")]
        self.assertEqual(len(rows), 4)
        for page in self.OFFICIAL_PAGES:
            with self.subTest(page=page):
                self.assertIn(f"](https://antigravity.google/docs/{page})", rows[0])
        self.assertIn("2026-11-13", rows[0])
        self.assertIn("](https://github.com/google-antigravity/antigravity-cli)", rows[1])
        self.assertIn("`87bd973`", rows[1])
        self.assertIn("](https://github.com/tw93/Waza)", rows[2])
        self.assertIn("](https://github.com/obra/superpowers)", rows[3])
        never_cited = "이 저장소의 이전 문서는 이 자료를 인용하지 않았다."
        for row in rows[:3]:
            with self.subTest(row=row[:40]):
                self.assertTrue(row.rstrip().endswith(never_cited + " |"), row[-80:])
        # The internal plans did name skills of the superpowers workflow, so that row says what they named
        # instead of claiming that nothing earlier mentioned it.
        self.assertNotIn(never_cited, rows[3])
        self.assertIn("`superpowers:subagent-driven-development`", rows[3])
        plans = REPO_ROOT / "docs/superpowers/plans"
        self.assertTrue(any("superpowers:subagent-driven-development" in path.read_text(encoding="utf-8")
                            for path in plans.glob("*.md")))

    def test_v0480_default_changes_five_six_and_nine(self) -> None:
        import re

        section = _section(self.RELEASE.read_text(encoding="utf-8"), "## 기본 결과 변경")
        entries = [_folded(entry) for entry in re.split(r"(?m)^\d+\. ", section)[1:]]
        host, stop, version = entries[4], entries[5], entries[8]
        # 5: the new --host option. Its abbreviation --h of --help now matches --help and --host.
        for literal in ("`--host {claude,antigravity}`", "`--help`를 `--h`로 줄여 쓰면",
                        "`--help`·`--host` 둘에 맞아 인자 오류(종료 코드 2)로 끝난다(v0.47.0은 도움말을 냈다).",
                        "줄임 옵션 `--h`를 빼면"):
            with self.subTest(entry=5, literal=literal):
                self.assertIn(literal, host)
        # 6: Stop wherever it is written or read, the stdin Stop that the default path still refuses, and
        # the helper bundle copies that the upgrade plan reports as differing.
        for literal in ("`--event`가 `Stop`도 받는다", "`--event` 없이 표준 입력으로 온 `Stop`은 v0.47.0처럼 거부",
                        "`checkpoint_drift_check`", "`--host antigravity`", "helper bundle",
                        "`vault_state.py`·`vault_healthcheck.py`", "`differs`"):
            with self.subTest(entry=6, literal=literal):
                self.assertIn(literal, stop)
        self.assertNotIn("{claude,antigravity}", stop)
        # 9: the health check stamp and ENGINE_VERSION.
        for literal in ("healthcheck 스탬프와 `ENGINE_VERSION`", "`00-meta/scripts/vault_healthcheck.py`"):
            with self.subTest(entry=9, literal=literal):
                self.assertIn(literal, version)

    def test_v0480_default_path_is_unchanged_except_the_abbreviation(self) -> None:
        release = self.RELEASE.read_text(encoding="utf-8")
        compatibility = _folded(_section(release, "## 하위호환·업그레이드"))
        self.assertIn("줄임 옵션 `--h`를 빼면 두 훅의 기본 경로(`--host` 없음)는 v0.47.0과 같은 출력을 낸다",
                      compatibility)
        changes = _folded(_section(release, "## 변경"))
        self.assertIn("줄임 옵션 `--h`(아래 `기본 결과 변경` 5번)를 빼면 플래그 없는 기본 경로", changes)
        record = _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"), "## Release content and scope"))
        self.assertIn("apart from its help text and the abbreviation `--h`", record)

    def test_v0480_checkpoint_item_scopes_what_is_not_stored(self) -> None:
        changes = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 변경"))
        checkpoint = changes[changes.index("- [체크포인트 훅]"):changes.index("- [패키지 생성기]")]
        for literal in ("훅 입력의 `lastUserInput`·`transcriptPath`·작업 공간 경로는 저장하지 않고",
                        "노트의 볼트 기준 상대 경로·크기·해시는 Claude Code 체크포인트처럼 남는다",
                        "120초 안에 다시 오면"):
            with self.subTest(literal=literal):
                self.assertIn(literal, checkpoint)
        start = changes[:changes.index("- [체크포인트 훅]")]
        self.assertIn("표지는 120초 동안만 유효하다", start)

    def test_v0480_retracted_sentences_stay_out(self) -> None:
        texts = {"release": self.RELEASE, "guide": REPO_ROOT / "docs/antigravity.md"}
        for name, path in texts.items():
            folded = _folded(path.read_text(encoding="utf-8"))
            for sentence in self.RETRACTED:
                with self.subTest(document=name, sentence=sentence[:30]):
                    self.assertNotIn(sentence, folded)
        check = _folded(self.RELEASE.read_text(encoding="utf-8"))
        self.assertIn("공개 문서가 아니라", check)

    def test_v0480_probes_follow_the_specification(self) -> None:
        import re

        record = self.VERIFICATION.read_text(encoding="utf-8")
        section = _section(record, "## Required feature evidence")
        self.assertEqual(sorted(re.findall(r"\bProbe (E\d)\b", section)), ["E1", "E2", "E3", "E4", "E5"])
        bullets = [_folded(part) for part in re.split(r"(?m)^(?=- )", section) if part.startswith("- ")]
        lead = _folded(section[:section.index("\n- ")])
        self.assertIn("E1 package, E2 install, E3 SessionStart, E4 Stop and E5 unchanged defaults", lead)
        for probe, item, literals in self.PROBES:
            holders = [bullet for bullet in bullets if f"Probe {probe}" in bullet]
            with self.subTest(probe=probe):
                self.assertEqual(len(holders), 1)
                self.assertTrue(holders[0].startswith(item), holders[0][:40])
            for literal in literals:
                with self.subTest(probe=probe, literal=literal):
                    self.assertIn(literal, holders[0])
        # The merged default-bytes bullet is probe E5 now.
        self.assertNotIn("Default hook and permission bytes", record)

    def test_v0480_record_states_the_real_home_scope(self) -> None:
        record = _folded(self.VERIFICATION.read_text(encoding="utf-8"))
        boundaries = _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"), "## Evidence boundaries"))
        self.assertIn("The verifier's real-home check covers `~/.gemini/config` only", boundaries)
        self.assertIn("`~/.gemini/antigravity-cli`", boundaries)
        self.assertIn("the unchanged real `~/.gemini/config`", record)


# v0.48.0 shared surfaces: README, SECURITY.md, docs/antigravity.md, docs/codex.md, docs/README.md,
# docs/hardening.md, docs/release-integrity.md and the Codex long description.
class V0480SharedSurfaceTests(unittest.TestCase):
    GUIDE = REPO_ROOT / "docs/antigravity.md"
    PACKAGE = REPO_ROOT / "antigravity"
    KOREAN = "## v0.48.0 — Antigravity CLI 겸용 패키지"
    ENGLISH = "## v0.48.0 — Antigravity CLI package"
    GUIDE_SECTIONS = ("## 설치", "## 갱신", "## 무엇이 들어 있나", "## 훅 동작", "## 볼트에서 읽는 규칙",
                      "## 직접 해 보는 확인", "## 하지 않는 것", "## 권한", "## 데이터", "## 약관 메모",
                      "## 확인하지 않은 것", "## agy 판")

    @staticmethod
    def agy_url() -> str:
        return f"https://github.com/Technoetic/agentic-vault/tree/v{EXPECTED_PUBLIC_REF}/antigravity"

    def test_readme_sections_link_the_v0480_documents(self) -> None:
        readme = README.read_text(encoding="utf-8")
        self.assertLess(readme.index("\n" + self.KOREAN + "\n"),
                        readme.index("\n## v0.47.0 — 안내서 대조 검토 반영과 aside-repl 절차 명령\n"))
        self.assertLess(readme.index("\n" + self.ENGLISH + "\n"),
                        readme.index("\n## v0.47.0 — guide-comparison fixes and the aside-repl procedure command\n"))
        korean = _folded(_section(readme, self.KOREAN))
        english = _folded(_section(readme, self.ENGLISH))
        for name, text in (("korean", korean), ("english", english)):
            for link in ("](docs/releases/v0.48.0.md)", "](docs/verification/v0.48.0.md)", "](docs/antigravity.md)"):
                with self.subTest(section=name, link=link):
                    self.assertIn(link, text)
        self.assertIn("인증서가 묶는 일곱 파일은 v0.46.0·v0.47.0과 같아", korean)
        self.assertIn("실제 agy 대화 세션으로는 확인하지 않았다", korean)
        self.assertIn("unchanged from v0.46.0 and v0.47.0", english)
        self.assertIn("No real agy conversation was run", english)
        current = next(line for line in readme.splitlines() if line.startswith("현재 버전은 **v"))
        self.assertTrue(current.startswith(f"현재 버전은 **v{EXPECTED}**이며 **Claude Code, Codex 겸용**이고"), current[:60])
        self.assertIn("](docs/antigravity.md)", current)

    def test_readme_tree_lists_the_antigravity_package(self) -> None:
        readme = README.read_text(encoding="utf-8")
        tree = readme[readme.index("agentic-vault/\n├── .claude-plugin/"):]
        tree = tree[:tree.index("```")]
        lines = {line.split("←")[0].strip(): line for line in tree.splitlines()}
        column = len(EXPECTED_TREE_LINE.split("←")[0])
        for entry in ("├── antigravity/", "│   ├── antigravity_hook.py", "│   │   ├── antigravity.md"):
            with self.subTest(entry=entry):
                self.assertIn(entry, lines)
                # The new lines put their arrow in the column of the manifest line.
                self.assertEqual(len(lines[entry].split("←")[0]), column)

    def test_agy_install_lines_pin_the_current_tag(self) -> None:
        import re

        texts = {"readme": README.read_text(encoding="utf-8"),
                 "guide": self.GUIDE.read_text(encoding="utf-8"),
                 "release": V0480ReleaseMetadataTests.RELEASE.read_text(encoding="utf-8")}
        for name, text in texts.items():
            urls = re.findall(r"agy plugin install (https://[^\s`]+)", text)
            with self.subTest(text=name):
                self.assertTrue(urls or name == "release")
                self.assertEqual(set(urls) - ({V0480ReleaseMetadataTests.AGY_URL} if name == "release" else {self.agy_url()}), set())
        for name in ("readme", "guide"):
            with self.subTest(own_line=name):
                self.assertIn("agy plugin install " + self.agy_url(), texts[name].splitlines())
        # The repository root is never offered as an agy install source (the automatic conversion
        # injects no handoff).
        for name, text in texts.items():
            with self.subTest(root=name):
                self.assertNotRegex(text, r"agy plugin install https://github\.com/Technoetic/agentic-vault/?(?:\s|`|$)")

    def test_codex_long_description_states_the_v0480_package(self) -> None:
        # This method preserves the published v0.48.0 description; the current description is tested by the active release.
        source = subprocess.run(["git", "-C", str(REPO_ROOT), "show", "v0.48.0:.codex-plugin/plugin.json"],
                                check=True, capture_output=True).stdout
        codex = json.loads(source)
        long_description = codex["interface"]["longDescription"]
        for literal in ("Release v0.48.0", "antigravity/", "No real agy conversation was run",
                        "unchanged from v0.46.0 and v0.47.0", "Some default results change"):
            with self.subTest(literal=literal):
                self.assertIn(literal, long_description)
        for digest in (CHECKER_SHA256_V0460, CHECKER_SHA256_V0340):
            self.assertNotIn(digest[:8], long_description)

    def test_codex_doc_opens_with_the_v0480_section(self) -> None:
        codex = (REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertIn(self.ENGLISH, headings)
        self.assertIn("## v0.49.0 — source-aware task workflows", headings)
        self.assertIn("## v0.50.0 — superseded pull-request CI runs and linked-worktree HEADs", headings)
        self.assertIn("## v0.51.0 — independent-evaluation fixes and opt-in BizRouter Decisions adapter", headings)
        self.assertIn(V0520ReleaseMetadataTests.ENGLISH, headings)
        self.assertEqual(headings[:11], [V0590ReleaseMetadataTests.CODEX_EN,
                                         V0580ReleaseMetadataTests.CODEX_EN,
                                         V0570ReleaseMetadataTests.CODEX_EN,
                                         V0560ReleaseMetadataTests.CODEX_EN, V0550ReleaseMetadataTests.CODEX_EN,
                                         V0541ReleaseMetadataTests.CODEX_EN, V0540ReleaseMetadataTests.CODEX_EN,
                                         V0532ReleaseMetadataTests.CODEX_EN, V0530ReleaseMetadataTests.ENGLISH,
                                         V0521ReleaseMetadataTests.ENGLISH, V0520ReleaseMetadataTests.ENGLISH])
        section = _folded(_section(codex, self.ENGLISH))
        for literal in ("](antigravity.md)", "](releases/v0.48.0.md)", "](verification/v0.48.0.md)",
                        "unchanged from v0.46.0 and v0.47.0"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)
        self.assertIn(f"설치 ref·검증 기록 안내와 [설치](#설치) 절은 v{EXPECTED} 기준이다.", codex)
        self.assertIn("](antigravity.md)", _section(codex, "## 설치"))

    def test_docs_index_names_the_latest_release_and_the_guide(self) -> None:
        index = (REPO_ROOT / "docs/README.md").read_text(encoding="utf-8")
        row = next(line for line in index.splitlines() if line.startswith("| [releases/](releases/) |"))
        self.assertIn(f"최신 소스는 [v{EXPECTED}](releases/v{EXPECTED}.md); [검증 기록](verification/v{EXPECTED}.md).", row)
        self.assertIn("이전 [v0.58.0](releases/v0.58.0.md) / [v0.57.0](releases/v0.57.0.md) / [v0.56.0](releases/v0.56.0.md) / [v0.55.0](releases/v0.55.0.md) / [v0.54.1](releases/v0.54.1.md) / [v0.54.0](releases/v0.54.0.md) / [v0.53.2](releases/v0.53.2.md) / [v0.53.0](releases/v0.53.0.md) / [v0.52.1](releases/v0.52.1.md) / [v0.51.0](releases/v0.51.0.md) / [v0.50.0](releases/v0.50.0.md) / [v0.49.0](releases/v0.49.0.md) / [v0.48.0](releases/v0.48.0.md) / [v0.47.0](releases/v0.47.0.md)", row)
        guide = next(line for line in index.splitlines() if line.startswith("| [antigravity.md](antigravity.md) |"))
        self.assertIn("(v0.48.0)", guide)

    def test_release_integrity_and_hardening_name_the_current_release(self) -> None:
        integrity = _folded((REPO_ROOT / "docs/release-integrity.md").read_text(encoding="utf-8"))
        self.assertIn(f"현재 v{EXPECTED}의 최종 소스·CI·ZIP/blob 대조와 재빌드 명세는 [릴리스 검증](verification/v{EXPECTED}.md)",
                      integrity)
        self.assertIn("`antigravity/`", integrity)
        hardening = _folded((REPO_ROOT / "docs/hardening.md").read_text(encoding="utf-8"))
        self.assertIn("v0.47.0과 v0.48.0은 인증서가 묶는 일곱 파일을 바꾸지 않아 v0.46.0과 같은 인증서를 쓰며, "
                      "인증서 전환은 맨 위 v0.46.0 절을 따른다.", hardening)

    def test_hardening_names_every_checkpoint_event(self) -> None:
        import re

        # The drift section lists the last events a checkpoint can carry; they are the health check's own
        # set (PreCompact and SessionEnd from Claude Code and Codex, Stop from the Antigravity package).
        source = HEALTHCHECK_SCRIPT.read_text(encoding="utf-8")
        assign = next(node for node in ast.parse(source).body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "_CHECKPOINT_EVENTS"
                              for target in node.targets))
        events = set(ast.literal_eval(assign.value.args[0]))
        section = _section((REPO_ROOT / "docs/hardening.md").read_text(encoding="utf-8"),
                           "### checkpoint 이후 바뀐 엔진 노트")
        named = re.search(r"마지막 사건\(([^)]*)\)", section)
        self.assertIsNotNone(named)
        self.assertEqual(set(re.findall(r"`(\w+)`", named.group(1))), events)
        self.assertIn("Stop", events)

    def test_security_states_the_antigravity_limits(self) -> None:
        security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn(f"| 0.58.0 and earlier | No. Upgrade to {EXPECTED}.", security)
        scope = _section(security, "## Scope")
        self.assertIn("- **Antigravity CLI package** (`antigravity/`", scope)
        self.assertIn("vulnerabilities in Claude Code, Codex, Antigravity CLI", _folded(scope))
        limits = _section(security, "## Known limitations")
        bullets = [_folded(bullet) for bullet in ("\n" + limits).split("\n- ")[1:]]
        named = [bullet for bullet in bullets if bullet.startswith("**Antigravity CLI")]
        self.assertEqual(len(named), 1)
        # No per-hook approval is documented, the CLI asks whether to trust a workspace, and when plugin
        # hooks start to run was not observed; data collection is the personal-account default.
        for literal in ("no per-hook approval step", "trust a workspace folder",
                        "was not checked in a real session", "non-zero", "fail open", "three handlers",
                        "deny-zone guard", "`/permissions`", "opts out", "business accounts", "Google",
                        "violates its terms", "No real agy conversation was run"):
            with self.subTest(limit=literal):
                self.assertIn(literal, named[0])
        summary = _folded(_section(security, "## 한국어 요약"))
        for literal in (f"최신 {EXPECTED} 소스", "Antigravity CLI", "훅마다 승인하는 단계는 문서에 없고",
                        "작업 공간 신뢰 대화상자", "실제 세션에서 확인하지 않았다", "fail-open", "비즈니스 계정",
                        "약관 위반"):
            with self.subTest(summary=literal):
                self.assertIn(literal, summary)
        for retracted in ("without a separate trust step", "별도 신뢰 단계 없이"):
            with self.subTest(retracted=retracted):
                self.assertNotIn(retracted, _folded(security))

    def test_antigravity_guide_covers_the_contract(self) -> None:
        guide = self.GUIDE.read_text(encoding="utf-8")
        headings = [line for line in guide.splitlines() if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.GUIDE_SECTIONS)
        folded = _folded(guide)
        for literal in ("`fullyIdle`", "`injectSteps`", "`/hooks`", "`/skills`", "`read_file`", "(opt-out)",
                        "1.5.6 이상", "1.5.7", "`workspacePaths`", "`cwd`", "agentic-vault-hook-claims",
                        "에이전트가 agy를 감싸 실행하지 않는다", "Google로 전송된다",
                        "`<PLUGIN_ROOT>`", "`<ARGUMENTS>`"):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_antigravity_guide_matches_the_generated_package(self) -> None:
        # The handler commands, the skill count and the version that the guide states, read from the
        # committed generated package.
        guide = self.GUIDE.read_text(encoding="utf-8")
        hooks = json.loads((self.PACKAGE / "hooks.json").read_text(encoding="utf-8"))
        commands = [handler["command"] for events in hooks.values() for handlers in events.values()
                    for handler in handlers]
        self.assertEqual(len(commands), 6)
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, guide.splitlines())
        skills = sorted(path.parent.name for path in (self.PACKAGE / "skills").glob("*/SKILL.md"))
        self.assertIn(f"스킬 {len(skills)}개", guide)
        manifest = json.loads((self.PACKAGE / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], EXPECTED)

    def test_antigravity_guide_states_the_reviewed_limits(self) -> None:
        guide = _folded(self.GUIDE.read_text(encoding="utf-8"))
        for literal in (
            # The several-vaults line comes from the SessionStart hook only.
            "(SessionStart 훅은 둘 이상일 때 표준 오류에 `agentic-vault: several vault candidates; nothing "
            "injected` 한 줄을 남긴다)",
            # A claim marker lasts 120 seconds; the reasons are what agy does not document.
            "표지는 120초 동안만 유효하다.", "`executionNum`의 범위",
            "SessionStart가 다시 오는지는 문서에 없고 확인하지 않았다.",
            # What a Stop checkpoint keeps, and what of the hook input it leaves out.
            "노트의 볼트 기준 상대 경로·크기·해시",
            "`transcriptPath`(대화 기록 경로)·작업 공간 경로는 저장하지 않는다.",
            # The manual check: an answer counts only when no tool ran before it; where agy shows a hook
            # error is not known.
            "`grep_search` 등)를 하나도 부르지 않았고", "도구를 불렀다면",
            "주입은 실패한 것으로 보고 훅 오류를 확인한다.",
            "`/hooks`", "`--log-file", "실제로 남는지는 확인하지 않았다.",
            # A read_file deny can make a move fail; a shell move was not tried.
            "절차가 실패할 수 있다(셸 명령으로 옮기는 경우도 막히는지는 확인하지 않았다).",
            # Collection is the default of a personal Google account and always off for business accounts.
            "개인 Google 계정", "비즈니스 계정에서는 수집이 늘 꺼져 있다.",
            # agy may not keep the executable bit of the vault git hooks.
            "실행 권한을 보존하는지", "`chmod +x`",
            # The skills page documents one slash command per skill.
            "`/<스킬 이름>`", "같은 볼트를 세 호스트(Claude Code·Codex·Antigravity CLI)에서 쓰도록 만든 패키지다.",
        ):
            with self.subTest(literal=literal[:40]):
                self.assertIn(literal, guide)
        text = self.GUIDE.read_text(encoding="utf-8")
        cited = text[text.index("참고한 공식 자료:"):]
        for page in V0480ReleaseMetadataTests.OFFICIAL_PAGES:
            with self.subTest(page=page):
                self.assertIn(f"](https://antigravity.google/docs/{page})", cited)

    def test_readme_v0480_sections_except_the_abbreviation(self) -> None:
        readme = README.read_text(encoding="utf-8")
        korean = _folded(_section(readme, self.KOREAN))
        english = _folded(_section(readme, self.ENGLISH))
        self.assertIn("도움말과 줄임 옵션 `--h`를 빼면 플래그 없는 기본 경로는 v0.47.0과 같다.", korean)
        self.assertIn("apart from the help text and the abbreviation `--h`, which now matches both `--help` and "
                      "`--host`", english)
        # The verifier's real-home check is named in the Korean list of what it does.
        self.assertIn("실제 `~/.gemini/config`가 그대로인지", korean)

    def test_vault_lint_not_a_vault_line_matches_the_vault_argument(self) -> None:
        lint = (REPO_ROOT / "commands/vault-lint.md").read_text(encoding="utf-8")
        premise = _section(lint, "## 0. 전제")
        # The step 1 command always passes --vault, so the not-a-vault answer has no flag condition.
        self.assertIn('vault_healthcheck.py" --vault "<볼트 루트>"', _section(lint, "## 1. 검증 엔진 실행"))
        self.assertNotIn("`--vault` 없이 실행한", premise)
        self.assertIn('- 검증 엔진이 "볼트 아님"을 출력하면(exit 0), 여기가 볼트가 아니라는 사실을', premise)


class V0500ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs/releases/v0.50.0.md"
    VERIFICATION = REPO_ROOT / "docs/verification/v0.50.0.md"
    TITLE = "# v0.50.0 — 낡은 PR CI 실행 취소와 연결 작업 트리 HEAD 확인"
    SECTIONS = ("## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증",
                "## 연구 출처와 기존 기능의 차이", "## 알려진 경계")
    INSTALL_REF = "현재 설치 ref는 `v0.50.0`이다."
    CERTIFICATE_SENTENCE = (
        "인증서가 묶는 일곱 파일은 v0.49.0과 같아 v0.46.0 이후 인증서는 실제 바이트가 같은 경우 계속 유효하다. "
        "v0.45.1 이하와 오갈 때는 [v0.46.0 업그레이드 안내](v0.46.0.md#하위호환업그레이드)를 따른다."
    )
    RECORD_TITLE = "# v0.50.0 verification contract"
    RECORD_SECTIONS = ("## Scope", "## Feature evidence", "## Default-result comparison",
                       "## Required release gates", "## Boundaries")
    SOURCES = (
        "https://www.youtube.com/watch?v=iysCcCRKZA4",
        "https://www.anthropic.com/research/redesigning-source-control",
        "https://openai.com/index/scaling-git-at-openai/",
        "https://github.blog/engineering/infrastructure/inside-githubs-new-git-storage-architecture/",
    )
    DECLARED_STAMPS = {
        "skills/agentic-vault/scripts/vault_healthcheck.py",
    }

    def test_release_note_title_and_sections_are_in_order(self) -> None:
        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], self.TITLE)
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.SECTIONS)

    def test_release_note_states_the_install_ref_and_the_unchanged_certificate(self) -> None:
        text = self.RELEASE.read_text(encoding="utf-8")
        upgrade = _section(text, "## 하위호환·업그레이드")
        self.assertIn(self.INSTALL_REF, upgrade)
        self.assertIn(self.CERTIFICATE_SENTENCE, _folded(upgrade))
        self.assertIn("healthcheck만 `engine=0.50.0`으로 이동하며 기존 규칙·어댑터 스탬프는 유지한다.", upgrade)
        self.assertIn(f"`{CHECKER_SHA256_V0460}` 그대로다.", upgrade)

    def test_release_note_names_the_default_changes(self) -> None:
        defaults = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 기본 결과 변경"))
        for literal in ("`--worktree-heads` 줄", "설치한 플러그인의 동작이 아니다",
                        "그 밖에는 새 플래그 없이 바뀌는 결과가 없다."):
            with self.subTest(literal=literal):
                self.assertIn(literal, defaults)

    def test_release_note_cites_the_four_sources(self) -> None:
        sources = _section(self.RELEASE.read_text(encoding="utf-8"), "## 연구 출처와 기존 기능의 차이")
        for url in self.SOURCES:
            with self.subTest(url=url):
                self.assertIn(f"]({url})", sources)
        self.assertIn("Scion·Pando", sources)
        self.assertIn("이전 저장소 문서는 이 네 출처를 인용하지 않았다", _folded(sources))

    def test_verification_title_sections_and_fourteen_gates(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], self.RECORD_TITLE)
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.RECORD_SECTIONS)
        gates = _section(text, "## Required release gates")
        numbers = [int(line.split(".", 1)[0]) for line in gates.splitlines() if line[:1].isdigit()]
        self.assertEqual(numbers, list(range(1, 15)))
        for literal in ("nine OS/Python unit and two stateful required CI successes", "push only the tag",
                        "Update Reference Atlas", "Publish immutable Latest", "Never rewrite a public tag."):
            with self.subTest(literal=literal):
                self.assertIn(literal, gates)
        self.assertIn(CHECKER_SHA256_V0460, _section(text, "## Default-result comparison"))

    def test_v0500_moves_only_the_healthcheck_stamp(self) -> None:
        stamped = set()
        for top in ("assets", "hooks", "skills"):
            for path in (REPO_ROOT / top).rglob("*"):
                if not path.is_file():
                    continue
                try:
                    head = path.read_text(encoding="utf-8-sig").splitlines()[:5]
                except UnicodeDecodeError:
                    continue
                if any(f"engine={EXPECTED}" in line for line in head):
                    stamped.add(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(stamped, self.DECLARED_STAMPS)

    # First independent source review of v0.50.0 (findings B1, F2, F4 and M1).
    def test_the_patch_file_limit_names_only_linked_trees_in_the_vault_folder(self) -> None:
        # vault_commit.py checks the location of a patch file only when it lies in the vault, so a
        # linked tree elsewhere is not detected (finding B1).
        guide = _folded(_section((REPO_ROOT / "docs/concurrent-commits.md").read_text(encoding="utf-8"),
                                 "## Landing work from a linked worktree"))
        changes = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 변경"))
        for name, text, present, stale in (
                ("guide", guide,
                 ("Paths inside a linked tree that sits in the vault folder (such as `.claude/worktrees/<name>`) "
                  "are refused, the patch file too; a linked tree elsewhere is not detected.",
                  "keep the patch file in the main tree or a temporary folder"),
                 "paths inside a linked tree are refused, the patch file too"),
                ("release", changes,
                 ("볼트 폴더 안에 있는 연결 작업 트리(예: `.claude/worktrees/<이름>`) 안의 경로는 패치 파일까지 "
                  "거부되지만, 다른 곳에 있는 연결 작업 트리는 감지되지 않는다.",
                  "패치 파일은 본 트리나 임시 폴더에 둔다"),
                 "(연결 작업 트리 안의 경로는 패치 파일까지 거부된다)")):
            for literal in present:
                with self.subTest(text=name, literal=literal[:30]):
                    self.assertIn(literal, text)
            with self.subTest(text=name, stale=stale[:30]):
                self.assertNotIn(stale, text)

    def test_no_text_says_help_gains_only_one_line(self) -> None:
        # argparse adds the option to the usage block and one option line and realigns the six
        # existing option lines (finding F2). Preserve the published v0.50.0 marketplace text.
        readme = README.read_text(encoding="utf-8")
        codex = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.50.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        places = (
            ("record", _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"),
                                        "## Default-result comparison")), "gains one line"),
            ("release", _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 기본 결과 변경")),
             "줄이 하나 늘어난다"),
            ("readme_ko", _folded(_section(readme, "## v0.50.0 — 낡은 PR CI 실행 취소와 연결 작업 트리 HEAD 확인")),
             "`--help` 한 줄"),
            ("readme_en", _folded(_section(readme,
                                           "## v0.50.0 — superseded pull-request CI runs and linked-worktree HEADs")),
             "one `--help` line"),
            ("codex_long", codex["interface"]["longDescription"], "one help line"),
        )
        for name, text, stale in places:
            with self.subTest(text=name):
                self.assertNotIn(stale, text)
                self.assertTrue("realign" in text or "다시 정렬" in text, name)

    def test_codex_long_description_carries_the_checker_range_to_v0500(self) -> None:
        # Preserve the historical v0.50.0 checker-range claim; v0.55.0 intentionally transitions checker_sha256.
        codex = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.50.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        long_description = codex["interface"]["longDescription"]
        self.assertIn("unchanged from v0.46.0 through v0.50.0.", long_description)
        self.assertNotIn("through v0.49.0", long_description)
        self.assertNotIn("through v0.51.0", long_description)
        self.assertNotIn("through v0.52.0", long_description)

    def test_release_note_states_that_remote_tracking_refs_are_not_consulted(self) -> None:
        boundaries = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 알려진 경계"))
        self.assertIn("원격 추적 참조(`refs/remotes/*`)는 보지 않으므로", boundaries)
        record = _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"), "## Boundaries"))
        self.assertIn("remote-tracking refs (`refs/remotes/*`) are not consulted", record)

    def test_verification_record_names_the_engine_stamp_not_a_version_output(self) -> None:
        # The healthcheck prints no version; only its line-2 engine stamp moves (fix1 check, minor 1).
        record = _folded(_section(self.VERIFICATION.read_text(encoding="utf-8"), "## Default-result comparison"))
        self.assertNotIn("healthcheck version/help information", record)
        self.assertIn("healthcheck engine stamp (line 2", record)

    def test_readme_and_codex_description_name_only_local_branches_and_tags(self) -> None:
        # The README summaries and the published v0.50.0 Codex description carry the same local-only
        # boundary (final-candidate check, minor): section 14c consults refs/heads and refs/tags only.
        readme = _folded((REPO_ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertNotIn("detached HEAD no branch or tag contains", readme)
        self.assertIn("detached HEAD no local branch or tag contains", readme)
        self.assertNotIn("어느 분기·태그에도", readme)
        self.assertEqual(readme.count("어느 로컬 분기·태그에도"), 2)
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.50.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"] if "interface" in manifest else manifest["longDescription"]
        self.assertNotIn("no branch or tag contains", description)
        self.assertIn("no local branch or tag contains", description)

    def test_security_and_codex_name_only_local_branches_and_tags(self) -> None:
        # Section 14c consults refs/heads and refs/tags only (fix1 check, minor 2).
        security = _folded((REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8"))
        codex = _folded((REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8"))
        for name, text in (("SECURITY.md", security), ("docs/codex.md", codex)):
            with self.subTest(file=name):
                self.assertNotIn("detached HEAD no branch or tag contains", text)
                self.assertIn("detached HEAD no local branch or tag contains", text)


class V0510ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs/releases/v0.51.0.md"
    VERIFICATION = REPO_ROOT / "docs/verification/v0.51.0.md"

    def test_english_overview_is_unbroken_and_precedes_folded_history(self) -> None:
        readme = README.read_text(encoding="utf-8")
        overview = readme[readme.index("## 🌐 English overview"):readme.index("<details>", readme.index("## 🌐 English overview"))]
        self.assertIn("**Core commands** (all 14 are in [commands/](commands/)):\n\n- `/vault-init <name>`", overview)
        self.assertNotIn("**v0.32.0:**", overview)
        self.assertLess(readme.index("## 🌐 English overview"), readme.index("📜 이전 버전 변경 이력"))

    def test_active_install_and_stamp_lines_have_no_stale_versions(self) -> None:
        readme = README.read_text(encoding="utf-8")
        active_readme = readme[:readme.index("📜 이전 버전 변경 이력")]
        self.assertIn(f"git 볼트의 healthcheck 스탬프는 `engine={EXPECTED}`이다.", active_readme)
        self.assertNotIn("engine=0.48.0", active_readme)
        self.assertNotIn("releases/tag/v0.48.0", active_readme)
        self.assertNotIn("docs/verification/v0.48.0.md", active_readme)
        antigravity_doc = (REPO_ROOT / "docs/antigravity.md").read_text(encoding="utf-8")
        self.assertIn(f"`version`이 `{EXPECTED}`이고", antigravity_doc)
        self.assertNotIn("`version`이 `0.48.0`이고", antigravity_doc)

    def test_release_note_uses_absolute_links_for_github_release_paste(self) -> None:
        import re
        text = self.RELEASE.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            self.assertTrue(
                target.startswith(("https://", "http://", "#")),
                f"relative link would break on GitHub Release web page: {target}",
            )
        self.assertIn(CHECKER_SHA256_V0460, text)
        self.assertIn(CHECKER_SHA256_V0460, self.VERIFICATION.read_text(encoding="utf-8"))


# v0.52.0 is an unpublished candidate: its tag is kept, it has no GitHub release, and v0.52.1 carries all of
# its changes. The note and the contract keep their tagged text under a status statement, and the README and
# the Codex guide keep the candidate's sections behind the v0.52.1 sections.
class V0520ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs/releases/v0.52.0.md"
    VERIFICATION = REPO_ROOT / "docs/verification/v0.52.0.md"
    TITLE = "# v0.52.0 — 미공개 후보"
    SECTIONS = ("## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증",
                "## 연구 출처와 기존 기능의 차이", "## 알려진 경계")
    INSTALL_REF = "현재 설치 ref는 `v0.52.0`이다."
    CERTIFICATE_SENTENCE = (
        "인증서가 묶는 일곱 파일(`vault_lint_extensions.py`·`vault_warning_policy.py`·`vault_provenance.py`·"
        "`vault_egress.py`·`vault_ssot.py`·`jev_client.py`·`vault_paths.py`)은 v0.46.0부터 v0.52.0까지 바이트 "
        "단위로 같아, 실제 설치 바이트가 같은 경우 기존 경고 승격 인증서가 그대로 유효하다."
    )
    RECORD_TITLE = "# v0.52.0 verification contract"
    RECORD_SECTIONS = ("## Scope", "## Feature evidence", "## Default-result comparison",
                       "## Required release gates", "## Boundaries")
    KOREAN = "## v0.52.0 — 같은 번호 보고, 가짜 머리글 줄 무력화, 판단 올리기 원답 보존"
    ENGLISH = "## v0.52.0 — same-number report, defused header lines and kept Jev answers on escalation"
    PREVIOUS = (("## v0.51.0 — 독립 평가 결함 수정과 선택형 BizRouter Decisions 어댑터",
                 "## v0.50.0 — 낡은 PR CI 실행 취소와 연결 작업 트리 HEAD 확인"),
                ("## v0.51.0 — independent-evaluation fixes and opt-in BizRouter Decisions adapter",
                 "## v0.50.0 — superseded pull-request CI runs and linked-worktree HEADs"))
    # Each source is cited by the address that answered on 2026-11-16 (the GitHub pages after redirects).
    SOURCES = (
        "https://arxiv.org/abs/2403.14720",
        "https://arxiv.org/abs/2510.09023",
        "https://docs.jj-vcs.dev/latest/guides/divergence/",
        "https://arxiv.org/abs/2607.10139",
        "https://arxiv.org/abs/2407.02348",
        "https://slsa.dev/spec/v1.2/source-requirements",
        "https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/"
        "configure-vulnerability-reporting/configure-for-a-repository",
        "https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/"
        "managing-rulesets/about-rulesets",
        "https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/"
        "managing-rulesets/available-rules-for-rulesets",
    )
    REJECTED_MODELS = ("typesafe/jev-1.13.0", "typesafe/jev-latest")
    # The status statements that the two documents open with.
    NOTE_STATUS = (
        "이 후보는 릴리스로 게시하지 않았다.",
        "`v0.52.0` 태그는 그대로 두고 다시 쓰지 않으며, 이 태그의 GitHub 릴리스는 없다.",
        "[v0.52.1](v0.52.1.md)로 대체한다.",
    )
    RECORD_STATUS = (
        "Status: **unpublished candidate**, superseded by [v0.52.1](v0.52.1.md).",
        "No GitHub release was published for v0.52.0.",
        "The `v0.52.0` tag stays as it is and is never rewritten.",
    )
    # The text from these lines to the end is the text of the blobs at tag v0.52.0 from the same lines on
    # (SHA-256 of the LF text, as test_historical_release_records_keep_canonical_bytes computes it).
    KEPT_TEXT = (
        ("docs/releases/v0.52.0.md", "개선 조사가 먼저 볼 항목으로 고른 세 가지를 구현하고 저장소 설정 둘을 켠다.",
         "aaa56bb0fc70ce768a3400cfdee1632b0e3b4f9b71699498bc18b7a8a6427d68"),
        ("docs/verification/v0.52.0.md", "This specifies required gates, not measured pass results.",
         "6fd507a0c5721bbcafed43150626ce75e26176ccee3fe8db75a3d7683787a4f3"),
    )

    def test_release_note_title_and_sections_are_in_order(self) -> None:
        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], self.TITLE)
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.SECTIONS)

    def test_note_and_contract_open_with_the_candidate_statement(self) -> None:
        # The statement is the first paragraph below the title: not published and why, the tag kept and
        # never rewritten, no GitHub release, and the release that replaces the candidate.
        for path, status in ((self.RELEASE, self.NOTE_STATUS), (self.VERIFICATION, self.RECORD_STATUS)):
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[1], "")
            self.assertEqual(lines[3], "")
            for literal in status:
                with self.subTest(document=path.name, literal=literal):
                    self.assertIn(literal, lines[2])
            with self.subTest(document=path.name, reason="TEMP"):
                self.assertIn("TEMP", lines[2])
            self.assertTrue((path.parent / "v0.52.1.md").is_file())

    def test_note_and_contract_keep_their_tagged_text(self) -> None:
        for relative, start, expected in self.KEPT_TEXT:
            with self.subTest(path=relative):
                text = (REPO_ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(text.count(start), 1)
                # Nothing but the title and the status statement stands above the kept text.
                self.assertEqual(len(text[:text.index(start)].splitlines()), 4)
                kept = text[text.index(start):].encode("utf-8")
                self.assertEqual(hashlib.sha256(kept).hexdigest(), expected)

    def test_release_note_states_the_install_ref_the_certificate_and_the_rejected_models(self) -> None:
        # The candidate's own statements, kept as they were at the tag.
        upgrade = _section(self.RELEASE.read_text(encoding="utf-8"), "## 하위호환·업그레이드")
        self.assertIn(self.INSTALL_REF, upgrade.splitlines())
        folded = _folded(upgrade)
        self.assertIn(self.CERTIFICATE_SENTENCE, folded)
        self.assertIn("healthcheck만 `engine=0.52.0`으로 이동하며 기존 규칙·어댑터 스탬프는 유지한다.", folded)
        self.assertIn(f"`checker_sha256`는 `{CHECKER_SHA256_V0460}` 그대로다.", folded)
        # The two TypeSafe Jev names that v0.51.0 sent to BizRouter are now refused with the old error.
        for model in self.REJECTED_MODELS:
            with self.subTest(model=model):
                self.assertIn(f"`{model}`", folded)
        self.assertIn("`invalid_arguments`", folded)

    def test_release_note_names_the_default_changes(self) -> None:
        defaults = _folded(_section(self.RELEASE.read_text(encoding="utf-8"), "## 기본 결과 변경"))
        # The version metadata, the three conditions of decision D1 and the BizRouter model narrowing.
        for literal in ("`engine=0.52.0`", "`ENGINE_VERSION`", "섹션 14d", "`duplicate_entry_ids`",
                        "`[note line, not a hook header] `", "`--escalate-bizrouter`", "`primary`", "`agreement`",
                        "`disagreed_ids`", "`provider_disagreement`", "`invalid_arguments`",
                        "v0.51.0과 바이트 단위로 같다"):
            with self.subTest(literal=literal):
                self.assertIn(literal, defaults)
        bullets = [line for line in _section(self.RELEASE.read_text(encoding="utf-8"), "## 기본 결과 변경")
                   .splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), 5)

    def test_release_note_cites_each_source_once_and_claims_no_numbers(self) -> None:
        sources = _section(self.RELEASE.read_text(encoding="utf-8"), "## 연구 출처와 기존 기능의 차이")
        for url in self.SOURCES:
            with self.subTest(url=url):
                self.assertEqual(sources.count(f"]({url})"), 1)
        folded = _folded(sources)
        for literal in ("부록 I", "효과 수치는 이 저장소의 효과로 주장하지 않는다",
                        "이전 저장소 문서는 이 출처들을 인용하지 않았다"):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_release_note_links_name_the_candidate_tag_and_the_replacing_note(self) -> None:
        import re
        text = self.RELEASE.read_text(encoding="utf-8")
        targets = re.findall(r"\]\(([^)\s]+)\)", text)
        # The one relative link is the status statement's link to the note that replaces the candidate.
        self.assertEqual([target for target in targets if not target.startswith(("https://", "#"))],
                         ["v0.52.1.md"])
        self.assertIn("](v0.52.1.md)", text.splitlines()[2])
        # Links to repository files still name the candidate's tag, which exists and is not rewritten.
        refs = re.findall(r"\]\(https://github\.com/Technoetic/agentic-vault/(?:blob|tree)/([^/)\s]+)/", text)
        self.assertTrue(refs)
        self.assertEqual(set(refs), {"v0.52.0"})
        self.assertIn(CHECKER_SHA256_V0460, text)

    def test_verification_title_sections_and_fourteen_gates(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], self.RECORD_TITLE)
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.RECORD_SECTIONS)
        gates = _section(text, "## Required release gates")
        numbered = [line for line in gates.splitlines() if line[:1].isdigit()]
        self.assertEqual([int(line.split(".", 1)[0]) for line in numbered], list(range(1, 15)))
        for literal in ("nine OS/Python unit and two stateful required CI successes", "push only the tag",
                        "Update Reference Atlas", "Publish immutable Latest", "Never rewrite a public tag."):
            with self.subTest(literal=literal):
                self.assertIn(literal, gates)
        # The two repository settings are applied just before the tag push and read back there.
        for literal in ("`v*` tag ruleset", "private vulnerability reporting", "read back"):
            with self.subTest(gate4=literal):
                self.assertIn(literal, numbered[3])
        evidence = _folded(_section(text, "## Feature evidence"))
        for literal in ("section 14d", "`[note line, not a hook header] `", "`primary`", "`agreement`",
                        "`disagreed_ids`", "`provider_disagreement`", "`google/gemini-decision-1`",
                        "tag ruleset", "private vulnerability reporting", "`V0520ReleaseMetadataTests`"):
            with self.subTest(evidence=literal):
                self.assertIn(literal, evidence)
        defaults = _folded(_section(text, "## Default-result comparison"))
        self.assertIn(f"`checker_sha256` stays `{CHECKER_SHA256_V0460}`", defaults)
        self.assertIn("byte-identical to v0.51.0", defaults)

    def test_no_engine_stamp_stays_at_the_candidate_version(self) -> None:
        # The health check stamp follows the plugin version in a patch release too.
        stamped = set()
        for top in ("assets", "hooks", "skills"):
            for path in (REPO_ROOT / top).rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                try:
                    text = path.read_text(encoding="utf-8-sig")
                except UnicodeDecodeError:
                    continue
                if "engine=0.52.0" in text:
                    stamped.add(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(stamped, set())

    def test_readme_keeps_the_candidate_sections_first_in_the_folded_history(self) -> None:
        readme = README.read_text(encoding="utf-8")
        history = readme.index("📜 이전 버전 변경 이력")
        self.assertIn("📜 이전 버전 변경 이력 (v0.9.0 ~ v0.52.0 / Historical release notes)", readme)
        for heading, (previous, older) in zip((self.KOREAN, self.ENGLISH), self.PREVIOUS):
            with self.subTest(heading=heading):
                self.assertEqual(readme.count("\n" + heading + "\n"), 1)
                self.assertLess(history, readme.index("\n" + heading + "\n"))
                self.assertLess(readme.index("\n" + heading + "\n"), readme.index("\n" + previous + "\n"))
                self.assertEqual(readme.count("\n" + previous + "\n"), 1)
                self.assertLess(readme.index("\n" + previous + "\n"), readme.index("\n" + older + "\n"))
        korean = _folded(_section(readme, self.KOREAN))
        english = _folded(_section(readme, self.ENGLISH))
        # Each section opens by saying that the candidate was not published, why, and where its changes ship.
        self.assertTrue(korean.startswith(self.KOREAN + " 공개하지 않은 후보다. "), korean[:120])
        self.assertTrue(english.startswith(self.ENGLISH + " Unpublished candidate: "), english[:150])
        self.assertIn("태그는 그대로 있고 GitHub 릴리스는 없으며, 아래 변경은 [v0.52.1](docs/releases/v0.52.1.md)로 공개한다.",
                      korean)
        self.assertIn("The tag is kept and has no GitHub release, and the changes below ship as "
                      "[v0.52.1](docs/releases/v0.52.1.md).", english)
        for name, text in (("korean", korean), ("english", english)):
            with self.subTest(section=name, reason="TEMP"):
                self.assertIn("TEMP", text)
        for name, text in (("korean", korean), ("english", english)):
            for link in ("](docs/releases/v0.52.1.md)", "](docs/releases/v0.52.0.md)",
                         "](docs/verification/v0.52.0.md)"):
                with self.subTest(section=name, link=link):
                    self.assertIn(link, text)
        self.assertIn("v0.46.0부터 v0.52.0까지", korean)
        self.assertIn("unchanged from v0.46.0 through v0.52.0", english)
        # No text offers the candidate as a release, an install ref or a package address.
        for stale in ("releases/tag/v0.52.0", "--ref v0.52.0", "tree/v0.52.0/", "engine=0.52.0"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, readme)

    def test_codex_guide_keeps_the_candidate_section_behind_the_release(self) -> None:
        codex = (REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[10], self.ENGLISH)
        section = _folded(_section(codex, self.ENGLISH))
        for literal in ("The v0.52.0 candidate, which was not published (see v0.52.1 above), adds",
                        "](releases/v0.52.0.md)", "](verification/v0.52.0.md)",
                        "unchanged from v0.46.0 through v0.52.0"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)
        self.assertNotIn("Release v0.52.0", codex)
        self.assertNotIn("--ref v0.52.0", codex)

    def test_security_states_the_reporting_form_and_the_session_start_hygiene_limit(self) -> None:
        security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        reporting = _folded(_section(security, "## Reporting a vulnerability"))
        self.assertIn("Private vulnerability reporting has been enabled for this repository since 2026-11-16",
                      reporting)
        self.assertIn("Security contact request", reporting)
        self.assertNotIn("Its current state could not be verified from the source tree.", reporting)
        limits = _folded(_section(security, "## Known limitations"))
        for literal in ("`[note line, not a hook header] `", "not a guarantee against adaptive prompt injection",
                        "](https://arxiv.org/abs/2510.09023)", "deny zones"):
            with self.subTest(limit=literal):
                self.assertIn(literal, limits)
        summary = _folded(_section(security, "## 한국어 요약"))
        for literal in ("2026-11-16", "`[note line, not a hook header] `", "적응형 프롬프트 주입", "결정론적 통제"):
            with self.subTest(summary=literal):
                self.assertIn(literal, summary)

    def test_release_integrity_records_the_tag_ruleset_and_the_source_track(self) -> None:
        text = (REPO_ROOT / "docs/release-integrity.md").read_text(encoding="utf-8")
        immutable = _section(text, "## v0.23.0부터: 불변 릴리스")
        lines = immutable.splitlines()
        marker = "- **v0.52.0(2026-11-16):**"
        starts = [index for index, line in enumerate(lines) if line.startswith(marker)]
        self.assertEqual(len(starts), 1)
        bullet = [lines[starts[0]]]
        for line in lines[starts[0] + 1:]:
            if not line.startswith("  "):
                break
            bullet.append(line)
        bullet = _folded("\n".join(bullet))
        for literal in ("`refs/tags/v*`", "우회 주체", "태그 70개 가운데 41개", "릴리스 23개", "초안만 있는 태그 4개",
                        "릴리스가 없는 태그 14개", "비공개 취약점 제보",
                        # The candidate's own tag is one more tag without a release; v0.52.1 reads the two
                        # settings back.
                        "v0.52.0은 공개하지 않은 후보다.", "릴리스가 없는 태그로 남아",
                        "[검증 계약](verification/v0.52.1.md)"):
            with self.subTest(bullet=literal):
                self.assertIn(literal, bullet)
        proofs = _section(text, "## 증명하는 것과 증명하지 않는 것")
        rows = [line for line in proofs.splitlines() if line.startswith("| `v*` 태그 규칙 집합")]
        self.assertEqual(len(rows), 1)
        cells = [cell.strip() for cell in rows[0].strip().strip("|").split("|")]
        self.assertEqual(len(cells), 3)
        self.assertIn("옮기거나 지울 수 없다", cells[1])
        for literal in ("누가 태그를 만들었는지", "꺼져 있던 기간", "관리자"):
            with self.subTest(not_proved=literal):
                self.assertIn(literal, cells[2])
        after = _folded(proofs[proofs.index(rows[0]) + len(rows[0]):])
        for literal in ("SLSA", "Source VSA", "L4", "두 사람", "Source 수준은 주장하지 않는다"):
            with self.subTest(source_track=literal):
                self.assertIn(literal, after)


# v0.52.1 publishes, for the first time, everything the v0.52.0 candidate added and fixes the long Windows
# TEMP path run that the candidate's release review found wanting. Only the heading, the usage line and the
# tag statement of the new release-note section are pinned: its body is revised after the fixes are in.
class V0521ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs/releases/v0.52.1.md"
    VERIFICATION = REPO_ROOT / "docs/verification/v0.52.1.md"
    TITLE = "# v0.52.1 — 같은 번호 보고, 가짜 머리글 줄 무력화, 판단 올리기 원답 보존"
    LONG_TEMP_SECTION = "## v0.52.0 후보에서 고친 긴 TEMP 경로 시험"
    SECTIONS = (LONG_TEMP_SECTION, "## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증",
                "## 연구 출처와 기존 기능의 차이", "## 알려진 경계")
    INSTALL_REF = "현재 설치 ref는 `v0.52.1`이다."
    CERTIFICATE_SENTENCE = (
        "인증서가 묶는 일곱 파일(`vault_lint_extensions.py`·`vault_warning_policy.py`·`vault_provenance.py`·"
        "`vault_egress.py`·`vault_ssot.py`·`jev_client.py`·`vault_paths.py`)은 v0.46.0부터 v0.52.1까지 바이트 "
        "단위로 같아, 실제 설치 바이트가 같은 경우 기존 경고 승격 인증서가 그대로 유효하다."
    )
    TAG_STATEMENT = "v0.52.0 태그는 다시 쓰지 않았고 그 태그에는 GitHub 릴리스가 없다."
    USAGE = "python -B scripts/long_temp_suite.py [--length N] [--parent DIR] [--keep] [--summary OUT] [-- ARG ...]"
    RECORD_TITLE = "# v0.52.1 verification contract"
    RECORD_SECTIONS = ("## Scope", "## Feature evidence", "## Default-result comparison",
                       "## Required release gates", "## Boundaries")
    GATE_1 = (
        "1. Commit integrated source, focused tests, a full fresh local suite, a second full run of the suite on "
        "Windows through `scripts/long_temp_suite.py` (TEMP and TMP set to a 150-character folder that "
        "`tempfile.gettempdir()` returns, with the `LongPathsEnabled` value recorded), required offline smoke "
        "commands, compilation, whole-tree whitespace, package generation/check, and independent read-only "
        "verification."
    )
    KOREAN = "## v0.52.1 — 같은 번호 보고, 가짜 머리글 줄 무력화, 판단 올리기 원답 보존"
    ENGLISH = "## v0.52.1 — same-number report, defused header lines and kept Jev answers on escalation"
    DECLARED_STAMPS = {
        "skills/agentic-vault/scripts/vault_healthcheck.py",
    }

    def test_release_note_title_and_sections_are_in_order(self) -> None:
        text = self.RELEASE.read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertEqual(lines[0], self.TITLE)
        headings = [line for line in lines if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.SECTIONS)
        # The long TEMP section comes right after the opening paragraph.
        self.assertEqual(lines[1], "")
        self.assertTrue(lines[2].startswith("개선 조사가 먼저 볼 항목으로 고른 세 가지를 구현하고"), lines[2][:40])
        self.assertEqual(lines[3:5], ["", self.LONG_TEMP_SECTION])
        # The opening paragraph says where the changes were first carried and names the public baseline.
        for literal in ("공개하지 않은 v0.52.0 후보", "v0.52.1은 그 변경을 모두 담아 처음 공개하고",
                        "`22168b7ddd10796e8ad0a894fe2c79a704bd7da2`"):
            with self.subTest(opening=literal):
                self.assertIn(literal, lines[2])

    def test_long_temp_section_states_the_usage_line_and_the_kept_tag(self) -> None:
        # Only these literals of the section are pinned; its body is not.
        section = _section(self.RELEASE.read_text(encoding="utf-8"), self.LONG_TEMP_SECTION)
        lines = section.splitlines()
        self.assertEqual(lines.count(self.USAGE), 1)
        usage = lines.index(self.USAGE)
        self.assertEqual((lines[usage - 1], lines[usage + 1]), ("```text", "```"))
        self.assertIn(self.TAG_STATEMENT, lines)

    def test_release_note_states_the_install_ref_the_certificate_and_the_rejected_models(self) -> None:
        upgrade = _section(self.RELEASE.read_text(encoding="utf-8"), "## 하위호환·업그레이드")
        self.assertIn(self.INSTALL_REF, upgrade.splitlines())
        folded = _folded(upgrade)
        self.assertIn(self.CERTIFICATE_SENTENCE, folded)
        self.assertIn("healthcheck만 `engine=0.52.1`로 이동하며 기존 규칙·어댑터 스탬프는 유지한다.", folded)
        self.assertIn(f"`checker_sha256`는 `{CHECKER_SHA256_V0460}` 그대로다.", folded)
        for model in V0520ReleaseMetadataTests.REJECTED_MODELS:
            with self.subTest(model=model):
                self.assertIn(f"`{model}`", folded)
        self.assertIn("`invalid_arguments`", folded)
        self.assertNotIn("v0.52.0까지", folded)

    def test_release_note_names_the_default_changes(self) -> None:
        text = self.RELEASE.read_text(encoding="utf-8")
        defaults = _folded(_section(text, "## 기본 결과 변경"))
        for literal in ("`engine=0.52.1`", "`ENGINE_VERSION`", "섹션 14d", "`duplicate_entry_ids`",
                        "`[note line, not a hook header] `", "`--escalate-bizrouter`", "`primary`", "`agreement`",
                        "`disagreed_ids`", "`provider_disagreement`", "`invalid_arguments`",
                        "v0.51.0과 바이트 단위로 같다"):
            with self.subTest(literal=literal):
                self.assertIn(literal, defaults)
        self.assertNotIn("engine=0.52.0", defaults)
        # The five items of the candidate come first, in its order, with the version line moved; the four
        # feature items are the candidate's lines.
        bullets = [line for line in _section(text, "## 기본 결과 변경").splitlines() if line.startswith("- ")]
        candidate = [line for line in _section(V0520ReleaseMetadataTests.RELEASE.read_text(encoding="utf-8"),
                                               "## 기본 결과 변경").splitlines() if line.startswith("- ")]
        self.assertGreaterEqual(len(bullets), 5)
        self.assertTrue(bullets[0].startswith("- 판 메타데이터:"), bullets[0][:30])
        self.assertEqual(bullets[1:5], candidate[1:5])

    def test_release_note_carries_the_changes_and_sources_of_the_candidate(self) -> None:
        text = self.RELEASE.read_text(encoding="utf-8")
        changes = _section(text, "## 변경")
        numbered = [line for line in changes.splitlines() if line[:1].isdigit()]
        for index, lead in enumerate(("1. 같은 번호의 결정·실수 항목 보고(healthcheck 섹션 14d",
                                      "2. `SessionStart`의 가짜 머리글 줄 무력화(`hooks/session_start.py`).",
                                      "3. 판단 올리기의 Jev 원답 보존과 BizRouter 모델 축소(",
                                      "4. 저장소 설정(유지관리자가 적용, 코드 변경 없음).",
                                      "5. 판 표면.")):
            with self.subTest(item=index + 1):
                self.assertTrue(numbered[index].startswith(lead), numbered[index][:50])
        sources = _section(text, "## 연구 출처와 기존 기능의 차이")
        for url in V0520ReleaseMetadataTests.SOURCES:
            with self.subTest(url=url):
                self.assertEqual(sources.count(f"]({url})"), 1)
        folded = _folded(sources)
        for literal in ("부록 I", "효과 수치는 이 저장소의 효과로 주장하지 않는다",
                        "이전 저장소 문서는 이 출처들을 인용하지 않았다"):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_release_note_uses_absolute_links_for_github_release_paste(self) -> None:
        import re
        text = self.RELEASE.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(target=target):
                self.assertTrue(target.startswith(("https://", "#")), target)
        # Links to repository files name this release's tag, not the candidate's.
        refs = re.findall(r"\]\(https://github\.com/Technoetic/agentic-vault/(?:blob|tree)/([^/)\s]+)/", text)
        self.assertTrue(refs)
        self.assertEqual(set(refs), {"v0.52.1"})
        self.assertIn(CHECKER_SHA256_V0460, text)

    def test_release_note_boundaries_name_the_long_temp_limits(self) -> None:
        # The run that was made, the executables that were not tried and the adapter that was not changed
        # are named; the sentences themselves are not pinned.
        boundaries = _section(self.RELEASE.read_text(encoding="utf-8"), "## 알려진 경계")
        for token in ("`LongPathsEnabled`", "150자", "`codex`", "`agy`",
                      "`assets/adapters/lesson-cues/lesson-cues.py`"):
            with self.subTest(token=token):
                self.assertIn(token, boundaries)

    def test_verification_title_sections_and_fourteen_gates(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], self.RECORD_TITLE)
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(tuple(headings), self.RECORD_SECTIONS)
        gates = _section(text, "## Required release gates")
        numbered = [line for line in gates.splitlines() if line[:1].isdigit()]
        self.assertEqual([int(line.split(".", 1)[0]) for line in numbered], list(range(1, 15)))
        # Gate 1 names the second full run through the long TEMP runner, word for word.
        self.assertEqual(numbered[0], self.GATE_1)
        # Gate 4 reads back the two settings applied for the candidate; they must still be active, and
        # the candidate's tag stays as it is.
        for literal in ("read back", "applied for v0.52.0", "`v*` tag ruleset", "private vulnerability reporting",
                        "still active", "unchanged remote `v0.52.0` tag", "push only the tag"):
            with self.subTest(gate4=literal):
                self.assertIn(literal, numbered[3])
        self.assertNotIn("apply the", numbered[3])
        # The other twelve gates are the candidate's gates.
        candidate = [line for line in _section(V0520ReleaseMetadataTests.VERIFICATION.read_text(encoding="utf-8"),
                                               "## Required release gates").splitlines() if line[:1].isdigit()]
        for index in (1, 2, *range(4, 14)):
            with self.subTest(gate=index + 1):
                self.assertEqual(numbered[index], candidate[index])
        self.assertIn("Never rewrite a public tag.", numbered[13])

    def test_verification_scope_evidence_and_unchanged_checker(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        scope = _folded(_section(text, "## Scope"))
        for literal in ("unpublished v0.52.0 candidate", "`LongPathsEnabled` 0", "150-character",
                        "`scripts/long_temp_suite.py`", "`tests/_winfs.py`", "applied for v0.52.0 on 2026-11-16",
                        "seven certificate-bound checker files", "is not rewritten"):
            with self.subTest(scope=literal):
                self.assertIn(literal, scope)
        evidence = _section(text, "## Feature evidence")
        bullets = [line for line in evidence.splitlines() if line.startswith("- ")]
        candidate = [line for line in _section(V0520ReleaseMetadataTests.VERIFICATION.read_text(encoding="utf-8"),
                                               "## Feature evidence").splitlines() if line.startswith("- ")]
        # The three feature items of the candidate are unchanged.
        self.assertEqual(bullets[:3], candidate[:3])
        leads = [line.split(":", 1)[0] for line in bullets]
        self.assertEqual(leads, ["- Same-number decision and mistake headings", "- Header-line defusing",
                                 "- Kept Jev answer and BizRouter models", "- Repository settings",
                                 "- Long TEMP run", "- Long TEMP guard", "- Long-path fixes",
                                 "- Release documents and surfaces"])
        folded = _folded(evidence)
        for literal in ("`scripts/long_temp_suite.py`", "`AGENTIC_VAULT_REQUIRE_LONG_TEMP`",
                        "`DeclaredLongTempTests.test_tempfile_uses_the_declared_long_temp`",
                        "`tests/test_000_environment.py`", "`tests/test_long_temp_suite.py`",
                        "It is never skipped.", "`V0521ReleaseMetadataTests`", "`V0520ReleaseMetadataTests`",
                        "`engine=0.52.1`"):
            with self.subTest(evidence=literal):
                self.assertIn(literal, folded)
        defaults = _folded(_section(text, "## Default-result comparison"))
        self.assertIn("Seven certificate-bound checker files remain byte-identical from v0.46.0 through v0.52.1, "
                      f"so `checker_sha256` stays `{CHECKER_SHA256_V0460}`.", defaults)
        self.assertIn("byte-identical to v0.51.0", defaults)
        self.assertIn("`ENGINE_VERSION` move to 0.52.1.", defaults)
        boundaries = _folded(_section(text, "## Boundaries"))
        for token in ("`LongPathsEnabled` 0", "150-character", "`codex`", "`agy`", "lesson cue adapter"):
            with self.subTest(boundary=token):
                self.assertIn(token, boundaries)

    def test_only_the_healthcheck_carries_the_active_release_stamp(self) -> None:
        stamped = set()
        for top in ("assets", "hooks", "skills"):
            for path in (REPO_ROOT / top).rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                try:
                    text = path.read_text(encoding="utf-8-sig")
                except UnicodeDecodeError:
                    continue
                if f"engine={EXPECTED}" in text:
                    stamped.add(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(stamped, self.DECLARED_STAMPS)
        lines = HEALTHCHECK_SCRIPT.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[1], f"# agentic-vault:healthcheck engine={EXPECTED}")
        self.assertEqual(sum(f"engine={EXPECTED}" in line for line in lines), 1)
        self.assertEqual(_module_constant(HEALTHCHECK_SCRIPT, "ENGINE_VERSION"), EXPECTED)

    def test_readme_sections_link_the_v0521_documents_and_name_the_candidate(self) -> None:
        readme = README.read_text(encoding="utf-8")
        overview = readme.index("## 🌐 English overview")
        history = readme.index("📜 이전 버전 변경 이력")
        for heading in (self.KOREAN, self.ENGLISH):
            with self.subTest(heading=heading):
                self.assertEqual(readme.count("\n" + heading + "\n"), 1)
                self.assertLess(overview, readme.index("\n" + heading + "\n"))
                self.assertLess(readme.index("\n" + heading + "\n"), history)
        self.assertLess(readme.index("\n" + self.KOREAN + "\n"), readme.index("\n" + self.ENGLISH + "\n"))
        korean = _folded(_section(readme, self.KOREAN))
        english = _folded(_section(readme, self.ENGLISH))
        for name, text in (("korean", korean), ("english", english)):
            for literal in ("](docs/releases/v0.52.1.md)", "](docs/verification/v0.52.1.md)",
                            "`scripts/long_temp_suite.py`", "`LongPathsEnabled`"):
                with self.subTest(section=name, literal=literal):
                    self.assertIn(literal, text)
        self.assertIn("](https://github.com/Technoetic/agentic-vault/releases/tag/v0.52.1)", korean)
        self.assertIn("v0.46.0부터 v0.52.1까지", korean)
        self.assertIn("unchanged from v0.46.0 through v0.52.1", english)
        # The candidate is named as unpublished, in the wording used for v0.45.0.
        self.assertIn("v0.52.0은 공개하지 않은 후보다.", korean)
        self.assertIn("unpublished v0.52.0 candidate", english)
        self.assertIn("was never rewritten and has no GitHub release", english)
        # The part above the folded history offers no document, ref or stamp of the candidate.
        active = readme[:history]
        for stale in ("releases/tag/v0.52.0", "docs/releases/v0.52.0.md", "docs/verification/v0.52.0.md",
                      "engine=0.52.0", "--ref v0.52.0", "tree/v0.52.0/", "v0.52.0까지", "through v0.52.0"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, active)
        current = next(line for line in readme.splitlines() if line.startswith("현재 버전은 **v"))
        self.assertIn("공개하지 않은 v0.52.0 후보", current)

    def test_codex_guide_and_description_name_the_release(self) -> None:
        codex = (REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[9], self.ENGLISH)
        section = _folded(_section(codex, self.ENGLISH))
        for literal in ("v0.52.1 supersedes the unpublished v0.52.0 candidate", "](releases/v0.52.1.md)",
                        "](verification/v0.52.1.md)", "unchanged from v0.46.0 through v0.52.1",
                        "`scripts/long_temp_suite.py`"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)
        # Preserve the exact marketplace description shipped by the historical tag.
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.52.1:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.52.1", "section 14d", "google/gemini-decision-1", "primary",
                        "an unpublished candidate that v0.52.1 supersedes",
                        "unchanged from v0.46.0 through v0.52.1."):
            with self.subTest(description=literal):
                self.assertIn(literal, description)
        self.assertNotIn("Release v0.52.0", description)

    def test_release_integrity_names_the_release_and_the_read_back(self) -> None:
        integrity = _folded((REPO_ROOT / "docs/release-integrity.md").read_text(encoding="utf-8"))
        self.assertIn("현재 v0.59.0의 최종 소스·CI·ZIP/blob 대조와 재빌드 명세는 [릴리스 검증](verification/v0.59.0.md)",
                      integrity)
        # The two settings applied for the candidate are read back for this release.
        self.assertIn("v0.52.1은 태그를 push하기 직전에 두 설정을 다시 읽어 그대로 켜져 있는지 확인한다"
                      "([검증 계약](verification/v0.52.1.md)).", integrity)
        self.assertNotIn("verification/v0.52.0.md", integrity)

    def test_security_and_docs_index_name_the_release_and_the_candidate(self) -> None:
        security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        versions = _section(security, "## Supported versions")
        self.assertIn("| 0.59.0 (latest) | Yes. Security fixes target the latest source version. |",
                      versions.splitlines())
        self.assertIn("| 0.58.0 and earlier | No. Upgrade to 0.59.0. From 0.55.0 to 0.59.0 the seven "
                      "certificate-bound checker files are unchanged", versions)
        self.assertIn("The unpublished v0.53.1, v0.52.0, v0.40.0,", versions)
        summary = next(line for line in security.splitlines() if line.startswith("- 보안 수정 대상은 최신"))
        for literal in ("최신 0.59.0 소스다.", "0.58.0 이하는 0.59.0으로 갱신한다.", "0.55.0부터 0.59.0 사이는",
                        "0.52.0은", "게시하지 않은 후보다.", "태그는 그대로 있고 GitHub 릴리스는 없으며"):
            with self.subTest(summary=literal):
                self.assertIn(literal, summary)
        index = (REPO_ROOT / "docs/README.md").read_text(encoding="utf-8")
        row = next(line for line in index.splitlines() if line.startswith("| [releases/](releases/) |"))
        self.assertIn("최신 소스는 [v0.59.0](releases/v0.59.0.md); [검증 기록](verification/v0.59.0.md). "
                      "[v0.52.0](releases/v0.52.0.md)은 긴 Windows TEMP 경로 시험 문제로 대체한 미공개 후보이며 "
                      "태그와 당시 기록을 보존한다.", row)


class V0530ReleaseMetadataTests(unittest.TestCase):
    ENGLISH = "## v0.53.0 — Order-sensitive recall and first handoff"
    README_KO = "## v0.53.0 — 작업 순서를 구별하는 검색과 첫 인계"
    README_EN = "## v0.53.0 — Order-sensitive recall and your first handoff"

    def test_reader_can_find_each_feature_and_its_verification(self) -> None:
        readme = README.read_text(encoding="utf-8")
        guide = (REPO_ROOT / "docs/codex.md").read_text(encoding="utf-8")
        for document, heading, prefix in ((readme, self.README_KO, "docs/"),
                                          (readme, self.README_EN, "docs/"),
                                          (guide, self.ENGLISH, "")):
            section = _section(document, heading)
            for target in ("ko-order-recall.md", "first-handoff.md", "handoff-resume.md",
                           "releases/v0.53.0.md", "verification/v0.53.0.md"):
                with self.subTest(heading=heading, target=target):
                    self.assertIn(f"]({prefix}{target})", section)

    def test_historical_codex_manifest_names_the_release_and_its_scope(self) -> None:
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.53.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.53.0", "break recall ties", "first-handoff tutorial",
                        "scripted controls and participant scoring", "No new runtime dependencies"):
            with self.subTest(literal=literal):
                self.assertIn(literal, description)


class V0532ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs" / "releases" / "v0.53.2.md"
    VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.53.2.md"
    CANDIDATE_RELEASE = REPO_ROOT / "docs" / "releases" / "v0.53.1.md"
    CANDIDATE_VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.53.1.md"
    README_KO = "## v0.53.2 — 외부 평가 개선, CI 병렬 실행, 보안·문서 정합성 복구 및 시험 경합 해소"
    README_EN = "## v0.53.2 — External-review fixes, parallel CI runner, documentation sync and race fixes"
    CODEX_EN = "## v0.53.2 — External-review fixes, parallel CI runner, documentation sync and race fixes"

    def test_release_note_uses_only_absolute_links_and_pins_unchanged_checker(self) -> None:
        import re

        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.53.2 — 외부 평가 개선, CI 병렬 실행, 보안·문서 정합성 복구 및 시험 경합 해소")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계"])
        self.assertIn("현재 설치 ref는 `v0.53.2`이다.", text)
        self.assertIn(CHECKER_SHA256_V0460, text)
        self.assertIn("미공개 `v0.53.1` 후보 태그", text)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue(
                    target.startswith("https://"),
                    f"v0.53.2 release note must not contain relative links for GitHub Release rendering: {target}",
                )

    def test_v0531_candidate_documents_are_marked_unpublished(self) -> None:
        self.assertIn("(미공개 후보)", self.CANDIDATE_RELEASE.read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("(unpublished candidate)", self.CANDIDATE_VERIFICATION.read_text(encoding="utf-8").splitlines()[0])

    def test_verification_contract_requires_master_ci_before_publication(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.53.2 verification contract")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Scope", "## Feature evidence", "## Default-result comparison",
                                    "## Required release gates", "## Boundaries"])
        folded = _folded(text)
        for literal in ("scripts/run_unittest_parallel.py", "bizrouter_client.py",
                        "BEFORE publishing the GitHub Release", CHECKER_SHA256_V0460,
                        "unpublished `v0.53.1` candidate"):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_security_and_antigravity_and_jarvis_fixes_stay_pinned(self) -> None:
        security = _folded((REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8"))
        for literal in ("bizrouter_client.py", "https://api.bizrouter.ai/v1/decisions",
                        "BIZROUTER_API_KEY", "google/gemini-decision-1", "since 0.52.1"):
            with self.subTest(security=literal):
                self.assertIn(literal, security)
        self.assertNotIn("(since 0.52.0)", security)
        self.assertNotIn("or a Stop event no integer", security)
        antigravity = (REPO_ROOT / "docs" / "antigravity.md").read_text(encoding="utf-8")
        self.assertNotIn("Stop에 정수 `executionNum`이", antigravity)
        self.assertIn("원시 이벤트 SHA-256", antigravity)
        self.assertIn("tree/v0.59.0/antigravity", antigravity)
        jarvis = _published_text("v0.53.2", "skills/agentic-vault/scripts/jarvis_bridge.py")
        self.assertNotIn("D:/NS", jarvis)

    def test_readme_and_codex_guide_and_manifest_name_v0532(self) -> None:
        readme = README.read_text(encoding="utf-8")
        self.assertIn("Antigravity CLI", readme.splitlines()[4])
        self.assertIn("Antigravity_CLI-Package", readme)
        for heading in (self.README_KO, self.README_EN):
            section = _section(readme, heading)
            self.assertIn("](docs/releases/v0.53.2.md)", section)
            self.assertIn("](docs/verification/v0.53.2.md)", section)
        codex = (REPO_ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[7], self.CODEX_EN)
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.53.2:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.53.2", "bizrouter_client.py", "run_unittest_parallel.py",
                        "v0.46.0 through v0.53.2", "unpublished v0.53.1 candidate"):
            with self.subTest(description=literal):
                self.assertIn(literal, description)


class V0540ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs" / "releases" / "v0.54.0.md"
    VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.54.0.md"
    README_KO = "## v0.54.0 — 불가시 유니코드 차단, 훅 배선·설치 상태 진단, CI 정적 위생 및 릴리스 주장 자동 검증"
    README_EN = "## v0.54.0 — Invisible Unicode defense, hook-wiring & install-state diagnostics, static CI hygiene and release claim verification"
    CODEX_EN = "## v0.54.0 — Invisible Unicode defense, hook-wiring & install-state diagnostics, static CI hygiene and release claim verification"

    def test_release_note_uses_only_absolute_links_and_cites_both_sources(self) -> None:
        import re

        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.54.0 — 불가시 유니코드 차단, 훅 배선·설치 상태 진단, CI 정적 위생 및 릴리스 주장 자동 검증")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계"])
        for literal in ("현재 설치 ref는 `v0.54.0`이다.", CHECKER_SHA256_V0460,
                        "https://github.com/affaan-m/ECC", "https://doi.org/10.5281/zenodo.24361690",
                        "vault_hook_wiring.py", "vault_install_state.py", "verify_release_claims.py"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue(
                    target.startswith("https://"),
                    f"v0.54.0 release note must not contain relative links: {target}",
                )

    def test_verification_contract_pins_gates_and_verify_release_claims(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.54.0 verification contract")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Scope", "## Feature evidence", "## Default-result comparison",
                                    "## Required release gates", "## Boundaries"])
        folded = _folded(text)
        for literal in ("_strip_invisible_smuggling", "vault_hook_wiring.py", "vault_install_state.py",
                        "scripts/verify_release_claims.py", "BEFORE publishing the GitHub Release",
                        CHECKER_SHA256_V0460):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_readme_codex_and_manifest_name_v0540(self) -> None:
        readme = README.read_text(encoding="utf-8")
        for heading in (self.README_KO, self.README_EN):
            section = _section(readme, heading)
            self.assertIn("](docs/releases/v0.54.0.md)", section)
            self.assertIn("](docs/verification/v0.54.0.md)", section)
        codex = (REPO_ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[6], self.CODEX_EN)
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.54.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.54.0", "vault_hook_wiring.py", "vault_install_state.py",
                        "scripts/verify_release_claims.py", "v0.46.0 through v0.54.0"):
            with self.subTest(description=literal):
                self.assertIn(literal, description)


class V0541ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs" / "releases" / "v0.54.1.md"
    VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.54.1.md"
    README_KO = "## v0.54.1 — 불가시 유니코드 7종 보강, UTF-8 BOM 정리·차단, 백업 POSIX 디렉터리 fsync 및 CI actionlint"
    README_EN = "## v0.54.1 — Expanded invisible Unicode hardening, UTF-8 BOM cleanup & guard, POSIX backup directory fsync and CI actionlint"
    CODEX_EN = "## v0.54.1 — Expanded invisible Unicode hardening, UTF-8 BOM cleanup & guard, POSIX backup directory fsync and CI actionlint"

    def test_release_note_uses_only_absolute_links_and_cites_sources(self) -> None:
        import re

        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.54.1 — 불가시 유니코드 7종 보강, UTF-8 BOM 정리·차단, 백업 POSIX 디렉터리 fsync 및 CI actionlint")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계"])
        for literal in ("현재 설치 ref는 `v0.54.1`이다.", CHECKER_SHA256_V0460,
                        "https://www.usenix.org/conference/usenixsecurity23/presentation/boucher",
                        "https://www.unicode.org/reports/tr39/",
                        "https://arxiv.org/abs/2106.09898",
                        "https://github.com/rhysd/actionlint",
                        "U+00AD", "U+034F", "_sync_dir", "actionlint"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue(
                    target.startswith("https://"),
                    f"v0.54.1 release note must not contain relative links: {target}",
                )

    def test_verification_contract_pins_gates_and_v0541_fixes(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.54.1 verification contract")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Scope", "## Feature evidence", "## Default-result comparison",
                                    "## Required release gates", "## Boundaries"])
        folded = _folded(text)
        for literal in ("U+00AD", "U+034F", "_sync_dir", "rhysd/actionlint",
                        "scripts/verify_release_claims.py", "BEFORE publishing the GitHub Release",
                        CHECKER_SHA256_V0460):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_readme_codex_and_manifest_name_v0541(self) -> None:
        readme = README.read_text(encoding="utf-8")
        for heading in (self.README_KO, self.README_EN):
            section = _section(readme, heading)
            self.assertIn("](docs/releases/v0.54.1.md)", section)
            self.assertIn("](docs/verification/v0.54.1.md)", section)
        codex = (REPO_ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[5], self.CODEX_EN)
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.54.1:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.54.1", "U+034F", "backup_vault.py", "actionlint", "v0.46.0 through v0.54.1"):
            with self.subTest(description=literal):
                self.assertIn(literal, description)


class V0550ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs" / "releases" / "v0.55.0.md"
    VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.55.0.md"
    README_KO = "## v0.55.0 — 검증일 노화·별칭 충돌 린트, URL 쿼리 유출 검사, 클라우드 동기화 경고 및 첫 실행 기능 요약"
    README_EN = "## v0.55.0 — Verification-date staleness, alias-collision lint, URL-query egress guard, cloud-sync awareness and capability summary"
    CODEX_EN = "## v0.55.0 — Verification-date staleness, alias-collision lint, URL-query egress guard, cloud-sync awareness and capability summary"

    def test_release_note_uses_only_absolute_links_and_pins_certificate_transition(self) -> None:
        import re

        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.55.0 — 검증일 노화·별칭 충돌 린트, URL 쿼리 유출 검사, 클라우드 동기화 경고 및 첫 실행 기능 요약")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계"])
        for literal in ("현재 설치 ref는 `v0.55.0`이다.", CHECKER_SHA256_V0550, CHECKER_SHA256_V0460,
                        "https://github.com/seulee26/stock-team-harness",
                        "checked-at-stale", "review-by-passed", "alias-collision", "egress-url-secret",
                        "vault_cloud_sync.py", "--cloud-sync", "--summary"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue(
                    target.startswith("https://"),
                    f"v0.55.0 release note must not contain relative links: {target}",
                )

    def test_verification_contract_pins_gates_and_v0550_features(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.55.0 verification contract")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Scope", "## Feature evidence", "## Default-result comparison",
                                    "## Required release gates", "## Boundaries"])
        folded = _folded(text)
        for literal in ("checked-at-stale", "review-by-passed", "alias-collision", "egress-url-secret",
                        "vault_cloud_sync.py", "scripts/verify_release_claims.py",
                        "BEFORE publishing the GitHub Release", CHECKER_SHA256_V0550, CHECKER_SHA256_V0460):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_readme_codex_and_manifest_name_v0550(self) -> None:
        readme = README.read_text(encoding="utf-8")
        for heading in (self.README_KO, self.README_EN):
            section = _section(readme, heading)
            self.assertIn("](docs/releases/v0.55.0.md)", section)
            self.assertIn("](docs/verification/v0.55.0.md)", section)
        codex = (REPO_ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[4], self.CODEX_EN)
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.55.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.55.0", "stock-team-harness", "checked-at-stale", "alias-collision",
                        "egress-url-secret", "vault_cloud_sync.py", V0310_CODEX_UPGRADE_SENTENCE):
            with self.subTest(description=literal):
                self.assertIn(literal, description)
        self.assertNotIn("unchanged from v0.46.0 through v0.55.0", description)


class V0560ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs" / "releases" / "v0.56.0.md"
    VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.56.0.md"
    README_KO = "## v0.56.0 — 사전 등록 평가 계획·사후 개정 이중 판정, 설치 상태 자체 다이제스트·기능 커버리지 표 및 사일런트 노옵 방지 감사"
    README_EN = "## v0.56.0 — Pre-registered evaluation plans with dual-verdict amendments, install-state self-digest & coverage matrix, and silent no-op activation audits"
    CODEX_EN = "## v0.56.0 — Pre-registered evaluation plans with dual-verdict amendments, install-state self-digest & coverage matrix, and silent no-op activation audits"

    def test_release_note_uses_only_absolute_links_and_pins_unchanged_checker(self) -> None:
        import re

        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.56.0 — 사전 등록 평가 계획·사후 개정 이중 판정, 설치 상태 자체 다이제스트·기능 커버리지 표 및 사일런트 노옵 방지 감사")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계"])
        for literal in ("현재 설치 ref는 `v0.56.0`이다.", CHECKER_SHA256_V0550,
                        "https://github.com/ai3inc/foldboost",
                        "verify_eval_plan.py", "frozen_core_sha256", "manifest_sha256",
                        "vault_coverage_matrix.py", "--coverage", "eval_activation.py", "--check-activation"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue(
                    target.startswith("https://"),
                    f"v0.56.0 release note must not contain relative links: {target}",
                )

    def test_verification_contract_pins_gates_and_v0560_features(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.56.0 verification contract")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Scope", "## Feature evidence", "## Default-result comparison",
                                    "## Required release gates", "## Boundaries"])
        folded = _folded(text)
        for literal in ("verify_eval_plan.py", "vault_coverage_matrix.py", "eval_activation.py",
                        "manifest_sha256", "INACTIVE_EXIT = 70", "scripts/verify_release_claims.py",
                        "BEFORE publishing the GitHub Release", CHECKER_SHA256_V0550):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_readme_codex_and_manifest_name_v0560(self) -> None:
        readme = README.read_text(encoding="utf-8")
        for heading in (self.README_KO, self.README_EN):
            section = _section(readme, heading)
            self.assertIn("](docs/releases/v0.56.0.md)", section)
            self.assertIn("](docs/verification/v0.56.0.md)", section)
        codex = (REPO_ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[3], self.CODEX_EN)
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.56.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.56.0", "ai3inc/foldboost", "verify_eval_plan.py",
                        "vault_coverage_matrix.py", "eval_activation.py",
                        "byte-identical from v0.55.0 through v0.56.0", V0310_CODEX_UPGRADE_SENTENCE):
            with self.subTest(description=literal):
                self.assertIn(literal, description)


class V0570ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs" / "releases" / "v0.57.0.md"
    VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.57.0.md"
    README_KO = "## v0.57.0 — 짝 부트스트랩·2-폴드 교차 적합 통계, 질의 누수·레이아웃 불변 위생, 결과 표 동기화, 선택형 결정 API 재정렬 및 `webapp-principles` 분리 제거"
    README_EN = "## v0.57.0 — Paired bootstrap & cross-fitting statistics, recall leakage & layout-invariance hygiene, result table sync, opt-in decision-API reranking, and webapp-principles removal"
    CODEX_EN = "## v0.57.0 — Paired bootstrap & cross-fitting statistics, recall leakage & layout-invariance hygiene, result table sync, opt-in decision-API reranking, and webapp-principles removal"

    def test_release_note_uses_only_absolute_links_and_pins_unchanged_checker(self) -> None:
        import re

        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.57.0 — 짝 부트스트랩·2-폴드 교차 적합 통계, 질의 누수·레이아웃 불변 위생, 결과 표 동기화, 선택형 결정 API 재정렬 및 `webapp-principles` 분리 제거")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계"])
        for literal in ("현재 설치 ref는 `v0.57.0`이다.", CHECKER_SHA256_V0550,
                        "https://github.com/ai3inc/opentoolsearch",
                        "https://github.com/Technoetic/webapp6",
                        "paired_bootstrap_ci", "cross_fit_two_fold", "eval_recall_hygiene.py",
                        "sync_result_tables.py", "rerank_candidates.py", "webapp-principles"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue(
                    target.startswith("https://"),
                    f"v0.57.0 release note must not contain relative links: {target}",
                )

    def test_verification_contract_pins_gates_and_v0570_features(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.57.0 verification contract")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Scope", "## Feature evidence", "## Default-result comparison",
                                    "## Required release gates", "## Boundaries"])
        folded = _folded(text)
        for literal in ("paired_bootstrap_ci", "cross_fit_two_fold", "eval_recall_hygiene.py",
                        "sync_result_tables.py", "rerank_candidates.py", "Technoetic/webapp6",
                        "scripts/verify_release_claims.py", "BEFORE publishing the GitHub Release",
                        CHECKER_SHA256_V0550):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_webapp_principles_removed_from_source_and_generated_antigravity_package(self) -> None:
        self.assertFalse((REPO_ROOT / "skills" / "webapp-principles").exists())
        self.assertFalse((REPO_ROOT / "docs" / "webapp-principles.md").exists())
        self.assertFalse((REPO_ROOT / "antigravity" / "skills" / "webapp-principles").exists())
        self.assertFalse((REPO_ROOT / "antigravity" / "docs" / "webapp-principles.md").exists())
        antigravity_skills = sorted(p.parent.name for p in (REPO_ROOT / "antigravity" / "skills").glob("*/SKILL.md"))
        self.assertIn("17개에서 16개", self.RELEASE.read_text(encoding="utf-8"))
        self.assertNotIn("webapp-principles", antigravity_skills)

    def test_readme_codex_and_manifest_name_v0570(self) -> None:
        readme = README.read_text(encoding="utf-8")
        for heading in (self.README_KO, self.README_EN):
            section = _section(readme, heading)
            self.assertIn("](docs/releases/v0.57.0.md)", section)
            self.assertIn("](docs/verification/v0.57.0.md)", section)
        codex = (REPO_ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[2], self.CODEX_EN)
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.57.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.57.0", "ai3inc/opentoolsearch", "eval_recall_hygiene.py",
                        "sync_result_tables.py", "rerank_candidates.py", "Technoetic/webapp6",
                        "byte-identical from v0.55.0 through v0.57.0", V0310_CODEX_UPGRADE_SENTENCE):
            with self.subTest(description=literal):
                self.assertIn(literal, description)


class V0580ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs" / "releases" / "v0.58.0.md"
    VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.58.0.md"
    README_KO = "## v0.58.0 — SessionStart 숨김 주석·태그 주입 차단, Holm·PPI++ 평가 통계, 결정론적 거부 복구 안내 및 `/aside-repl`·Jarvis Telegram 제거"
    README_EN = "## v0.58.0 — Hidden markup injection sanitization, Holm & PPI++ evaluation statistics, deterministic refusal recovery guidance, and `/aside-repl` & Jarvis Telegram removal"
    CODEX_EN = "## v0.58.0 — Hidden markup injection sanitization, Holm & PPI++ evaluation statistics, deterministic refusal recovery guidance, and `/aside-repl` & Jarvis Telegram removal"

    def test_release_note_uses_only_absolute_links_and_pins_unchanged_checker(self) -> None:
        import re

        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.58.0 — SessionStart 숨김 주석·태그 주입 차단, Holm·PPI++ 평가 통계, 결정론적 거부 복구 안내 및 `/aside-repl`·Jarvis Telegram 제거")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계"])
        for literal in ("현재 설치 ref는 `v0.58.0`이다.", CHECKER_SHA256_V0550,
                        "https://arxiv.org/abs/2611.28763",
                        "https://doi.org/10.2307/4615733",
                        "https://doi.org/10.1126/science.adi6000",
                        "https://arxiv.org/abs/2311.01453",
                        "https://arxiv.org/abs/2601.18777",
                        "https://arxiv.org/abs/2611.24185",
                        "_strip_hidden_markup", "holm_bonferroni_adjust", "ppi_pp_mean_ci",
                        "Next legal steps:", "next_legal_steps", "engine=0.58.0"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue(
                    target.startswith("https://"),
                    f"v0.58.0 release note must not contain relative links: {target}",
                )

    def test_verification_contract_pins_gates_and_v0580_features(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.58.0 verification contract")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Scope", "## Feature evidence", "## Default-result comparison",
                                    "## Required release gates", "## Boundaries"])
        folded = _folded(text)
        for literal in ("_strip_hidden_markup", "holm_bonferroni_adjust", "ppi_pp_mean_ci",
                        "Next legal steps:", "next_legal_steps", "engine=0.58.0",
                        "scripts/verify_release_claims.py", "BEFORE publishing the GitHub Release",
                        CHECKER_SHA256_V0550):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_v0580_moves_guard_stamps_and_retires_unreleased_note(self) -> None:
        self.assertFalse((REPO_ROOT / "docs" / "releases" / "unreleased.md").exists())
        for relative in ("assets/adapters/deny-guard/deny-guard.py",
                         "assets/adapters/deny-guard/ps-parse.ps1"):
            with self.subTest(stamped=relative):
                first = (REPO_ROOT / relative).read_text(encoding="utf-8-sig").splitlines()[0]
                self.assertTrue(first.endswith(" engine=0.58.0"), first)
        antigravity_skills = sorted(p.parent.name for p in (REPO_ROOT / "antigravity" / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(antigravity_skills), 15)

    def test_readme_codex_and_manifest_name_v0580(self) -> None:
        readme = README.read_text(encoding="utf-8")
        for heading in (self.README_KO, self.README_EN):
            section = _section(readme, heading)
            self.assertIn("](docs/releases/v0.58.0.md)", section)
            self.assertIn("](docs/verification/v0.58.0.md)", section)
        codex = (REPO_ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[1], self.CODEX_EN)
        manifest = json.loads(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", "v0.58.0:.codex-plugin/plugin.json"],
            check=True, capture_output=True).stdout)
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.58.0", "arXiv:2611.28763", "holm_bonferroni_adjust",
                        "ppi_pp_mean_ci", "arXiv:2611.24185", "engine=0.58.0",
                        "byte-identical from v0.55.0 through v0.58.0", V0310_CODEX_UPGRADE_SENTENCE):
            with self.subTest(description=literal):
                self.assertIn(literal, description)


class V0590ReleaseMetadataTests(unittest.TestCase):
    RELEASE = REPO_ROOT / "docs" / "releases" / "v0.59.0.md"
    VERIFICATION = REPO_ROOT / "docs" / "verification" / "v0.59.0.md"
    README_KO = "## v0.59.0 — 노트 철회·대체 수명주기 감사, 검증 번들 정확 트리 소속·0건 음성 증거 보존 및 커버리지 명세·검토 분리"
    README_EN = "## v0.59.0 — Note withdrawal & supersession lifecycle audit, exact verification-bundle tree membership with zero-count negative evidence, and coverage spec/review separation"
    CODEX_EN = "## v0.59.0 — Note withdrawal & supersession lifecycle audit, exact verification-bundle tree membership with zero-count negative evidence, and coverage spec/review separation"

    def test_release_note_uses_only_absolute_links_and_pins_unchanged_checker(self) -> None:
        import re

        text = self.RELEASE.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.59.0 — 노트 철회·대체 수명주기 감사, 검증 번들 정확 트리 소속·0건 음성 증거 보존 및 커버리지 명세·검토 분리")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## 변경", "## 기본 결과 변경", "## 하위호환·업그레이드", "## 검증", "## 알려진 경계"])
        for literal in ("현재 설치 ref는 `v0.59.0`이다.", CHECKER_SHA256_V0550,
                        "https://github.com/openai/math", "3344da59",
                        "vault_lifecycle.py", "proof_or_evidence_only",
                        "check_exact_tree_membership", "review_status",
                        "spec_files", "permitted_gaps", "engine=0.59.0"):
            with self.subTest(literal=literal):
                self.assertIn(literal, text)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue(
                    target.startswith("https://"),
                    f"v0.59.0 release note must not contain relative links: {target}",
                )

    def test_verification_contract_pins_gates_and_v0590_features(self) -> None:
        text = self.VERIFICATION.read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "# v0.59.0 verification contract")
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Scope", "## Feature evidence", "## Default-result comparison",
                                    "## Required release gates", "## Boundaries"])
        folded = _folded(text)
        for literal in ("vault_lifecycle.py", "proof_or_evidence_only",
                        "check_exact_tree_membership", "review_status",
                        "spec_files", "permitted_gaps", "engine=0.59.0",
                        "scripts/verify_release_claims.py", "BEFORE publishing the GitHub Release",
                        CHECKER_SHA256_V0550):
            with self.subTest(literal=literal):
                self.assertIn(literal, folded)

    def test_v0590_antigravity_package_includes_lifecycle_and_preserves_15_skills(self) -> None:
        self.assertTrue((REPO_ROOT / "antigravity" / "skills" / "agentic-vault" / "scripts" / "vault_lifecycle.py").is_file())
        self.assertTrue((REPO_ROOT / "antigravity" / "docs" / "note-lifecycle.md").is_file())
        antigravity_skills = sorted(p.parent.name for p in (REPO_ROOT / "antigravity" / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(antigravity_skills), 15)

    def test_readme_codex_and_manifest_name_v0590(self) -> None:
        readme = README.read_text(encoding="utf-8")
        for heading in (self.README_KO, self.README_EN):
            section = _section(readme, heading)
            self.assertIn("](docs/releases/v0.59.0.md)", section)
            self.assertIn("](docs/verification/v0.59.0.md)", section)
        codex = (REPO_ROOT / "docs" / "codex.md").read_text(encoding="utf-8")
        headings = [line for line in codex.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[0], self.CODEX_EN)
        manifest = json.loads((REPO_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        description = manifest["interface"]["longDescription"]
        for literal in ("Release v0.59.0", "openai/math", "vault_lifecycle.py",
                        "check_exact_tree_membership", "review_status", "spec_files",
                        "permitted_gaps", "byte-identical from v0.55.0 through v0.59.0",
                        V0310_CODEX_UPGRADE_SENTENCE):
            with self.subTest(description=literal):
                self.assertIn(literal, description)
