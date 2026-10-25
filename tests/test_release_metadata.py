"""Release metadata consistency and historical contract regressions."""
from __future__ import annotations

import ast
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

EXPECTED = "0.31.0"
EXPECTED_PUBLIC_REF = "0.31.0"
EXPECTED_BADGE_LINE = (
    "[![Version](https://img.shields.io/badge/v0.31.0-10B981?style=for-the-badge)]"
    "(docs/releases/v0.31.0.md)"
)
EXPECTED_TREE_LINE = (
    "├── .claude-plugin/                    "
    "← plugin.json · marketplace.json (v0.31.0 · MIT)"
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
REQUIRED_README_LITERALS = (
    '`chat.type == "private"`',
    "`chat.id == from.id`",
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
# 조항이 추가돼 0.16.0으로 올라갔다.
REQUIRED_WORKFLOW_RULE_STAMP = "agentic-vault:rule engine=0.16.0"
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
    ("commands/vault-jarvis-setup.md", "텍스트가 아닌 메시지는 캡처·Q&A 대상이 아니며"),
    ("docs/reliability.md", "`hot_max_tokens`·`handoff_max_tokens`를 잠시 0으로"),
    # Keep the approval-first briefing contract and hook disablement wired to
    # current behavior; the old ambient-settings warning was superseded by bare mode.
    ("skills/agentic-vault/scripts/jarvis_bridge.py", "내 결정·승인 대기를 먼저"),
    ("SECURITY.md", "--settings '{\"disableAllHooks\":true}'"),
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
    ("commands/vault-upgrade.md", "6. **도구 어댑터(선택)**"),
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
# checker_sha256 of the seven bound files as shipped now. A change to any of them invalidates
# promoted vaults' certificates: update this pin together with a release note upgrade section.
CURRENT_CHECKER_SHA256 = CHECKER_SHA256_V0310
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
# Each file states the current checker hash this many times (README in English and Korean), so a
# stale second statement fails as well as a missing one. The 169d8a6 digest is history and may
# appear only in the verification record's phrase below.
CURRENT_CHECKER_HASH_COUNTS = {
    "README.md": 2, "docs/codex.md": 1, "docs/hardening.md": 1,
    "docs/releases/v0.31.0.md": 1, "docs/verification/v0.31.0.md": 1,
}
CHECKER_SHA256_V0240_TO_V0250_PREFIX = "2ef4b9d4"
CHECKER_SHA256_AT_169D8A6_PREFIX = "832a86f9"
CHECKER_HISTORY_AT_169D8A6 = (
    f"(it was `{CHECKER_SHA256_AT_169D8A6_PREFIX}…` at `169d8a6`, "
    "before the pre-release cleanup edited `vault_ssot.py` again)"
)
_OLD_HASH = f"`{CHECKER_SHA256_V0260_TO_V0300[:8]}…`"
_NEW_HASH = f"`{CHECKER_SHA256_V0310[:8]}…`"
_V025_HASH = f"`{CHECKER_SHA256_V0240_TO_V0250_PREFIX}…`"
# Old-to-new hash statements. Prose is compared with whitespace runs folded to one space, so
# rewrapping a Markdown paragraph does not matter but a changed hash or version range does.
REQUIRED_V0310_HASH_STATEMENTS = (
    ("docs/releases/v0.31.0.md",
     f"v0.26.0~v0.30.0의 `{CHECKER_SHA256_V0260_TO_V0300}`에서 `{CHECKER_SHA256_V0310}`로 바뀐다"),
    ("docs/verification/v0.31.0.md",
     f"`{CHECKER_SHA256_V0260_TO_V0300}` at v0.26.0 to v0.30.0, `{CHECKER_SHA256_V0310}` in v0.31.0 "
     + CHECKER_HISTORY_AT_169D8A6),
    ("README.md", f"binds ({_OLD_HASH} in v0.26.0 to v0.30.0, now {_NEW_HASH})"),
    ("README.md", f"`checker_sha256`가 v0.26.0~v0.30.0의 {_OLD_HASH}에서 {_NEW_HASH}로 바뀐다"),
    ("docs/codex.md", f"`vault_ssot.py`를 바꿨다({_OLD_HASH}에서 {_NEW_HASH}로"),
    ("docs/codex.md", f"`vault_provenance.py`를 바꿨다({_V025_HASH}에서 {_OLD_HASH}로"),
    ("docs/hardening.md",
     f"v0.24.0·v0.25.0의 `checker_sha256`는 {_V025_HASH}, v0.26.0~v0.30.0은 {_OLD_HASH}, v0.31.0은 {_NEW_HASH}."),
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

    def test_stated_checker_hash_is_the_shipped_checker_hash(self) -> None:
        # Certificates bind these bytes; a stale stated hash misleads every promoted vault.
        # A child process computes it from the files next to the shipped script.
        script = REPO_ROOT / "skills" / "agentic-vault" / "scripts" / "vault_warning_policy.py"
        result = subprocess.run([sys.executable, str(script), "--certificate"], capture_output=True,
                                text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["checker_sha256"], CURRENT_CHECKER_SHA256)
        texts = {rel_path: (REPO_ROOT / rel_path).read_text(encoding="utf-8")
                 for rel_path in (*CURRENT_CHECKER_HASH_COUNTS, ".codex-plugin/plugin.json",
                                  "SECURITY.md", "docs/README.md")}
        for rel_path in ("docs/releases/v0.31.0.md", "docs/verification/v0.31.0.md"):
            with self.subTest(path=rel_path):
                self.assertIn(CURRENT_CHECKER_SHA256, texts[rel_path])
        for rel_path in ("README.md", "docs/codex.md", "docs/hardening.md"):
            with self.subTest(path=rel_path):
                self.assertIn(f"`{CURRENT_CHECKER_SHA256[:8]}…`", texts[rel_path])
        # Exact counts: every statement of the hash is current, not only one of them.
        for rel_path, expected in CURRENT_CHECKER_HASH_COUNTS.items():
            with self.subTest(path=rel_path, count=expected):
                self.assertEqual(texts[rel_path].count(CURRENT_CHECKER_SHA256[:8]), expected)
        for rel_path, text in texts.items():
            expected = 1 if rel_path == "docs/verification/v0.31.0.md" else 0
            with self.subTest(path=rel_path, digest_at_169d8a6=expected):
                self.assertEqual(text.count(CHECKER_SHA256_AT_169D8A6_PREFIX), expected)
        self.assertIn(CHECKER_HISTORY_AT_169D8A6, _folded(texts["docs/verification/v0.31.0.md"]))
        for rel_path, statement in REQUIRED_V0310_HASH_STATEMENTS:
            with self.subTest(path=rel_path, statement=statement):
                self.assertIn(statement, _folded(texts[rel_path]))

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
        for literal in REQUIRED_README_LITERALS:
            with self.subTest(literal=literal):
                self.assertIn(literal, readme)

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
