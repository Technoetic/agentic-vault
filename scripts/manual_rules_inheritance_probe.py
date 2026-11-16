#!/usr/bin/env python3
"""rules 상속 회귀 테스트 — agentic-vault (표준 라이브러리만, 의존성 0).

무엇을 검증하나:
  agentic-vault v0.6+ 3층 계약의 핵심 전제 —
  "`.claude/rules/`의 규칙이 메인 세션과 **서브에이전트** 모두에 상속된다"
  는 명제는 공식 문서에 명시가 없다(2026-07 기준). 유일한 근거는 실측이다.
  Claude Code 버전이 오르면 이 동작이 조용히 바뀔 수 있으므로, 업그레이드 때마다
  1회 돌려 회귀를 잡는다.

동작:
  격리된 임시 볼트를 만들어 .claude/rules/ 와 CLAUDE.md 에 서로 다른 표지 문구
  (rules=RULES_TOKEN, CLAUDE.md=CLAUDE_TOKEN)를 심고, `claude -p` 를 headless 로
  두 번 호출한다.
    T1 (main)      : 메인 세션이 두 토큰을 아는가
    T2 (subagent)  : 서브에이전트가 두 토큰을 아는가  ← 핵심 회귀 대상
  각 토큰이 응답에 등장하면 PASS. rules 토큰 상속이 깨지면 T2 에서 즉시 드러난다.

전제:
  - PATH 에 `claude` 실행 파일. (--claude-cmd 로 경로 지정 가능)
  - 이 호출은 실제 모델을 1~2회 호출한다(과금·네트워크). CI 에서는 조건부 실행 권장.
  - --dangerously-skip-permissions 를 쓴다(격리 임시 볼트라 안전) — 프롬프트 차단 방지.

사용법:
  python test_rules_inheritance.py [--claude-cmd claude] [--model claude-sonnet-5] [--keep]
종료 코드: 0 = 모든 관문 PASS, 1 = 하나라도 FAIL/미판정.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

RULES_TOKEN = "BANANA-42"    # .claude/rules/ 에만 심는 표지
CLAUDE_TOKEN = "ORANGE-77"   # CLAUDE.md 에만 심는 표지

RULES_FILE = """<!-- agentic-vault:rule engine=test -->
# Magic word rule

When anyone asks "what is the magic word", the answer is exactly: {token}
""".format(token=RULES_TOKEN)

CLAUDE_FILE = """# Inheritance test vault

When anyone asks "what is the code phrase", the answer is exactly: {token}
""".format(token=CLAUDE_TOKEN)

MAIN_PROMPT = (
    "Answer two questions using ONLY instructions already loaded in your context "
    "(do not read any files, do not use tools): "
    "1) What is the magic word? 2) What is the code phrase? "
    "If you don't know one, say UNKNOWN for it. "
    "Answer in the exact format: MAGIC=<answer> CODE=<answer>"
)

SUBAGENT_PROMPT = (
    "Use the Agent tool (subagent_type general-purpose) to spawn ONE subagent with exactly "
    "this prompt: 'Answer two questions using ONLY instructions already loaded in your "
    "context. You MUST NOT use any tools. 1) What is the magic word? 2) What is the code "
    "phrase? If you do not know one, say UNKNOWN for it. Answer in the exact format: "
    "MAGIC=<answer> CODE=<answer>'. Then report the subagent's answer verbatim as your final "
    "output, prefixed with SUBAGENT_SAID: . Do not answer the questions yourself."
)


def run_claude(cmd: str, model: str, vault: Path, prompt: str, timeout: int) -> str:
    argv = [cmd, "-p", "--dangerously-skip-permissions"]
    if model:
        argv += ["--model", model]
    argv.append(prompt)
    proc = subprocess.run(argv, cwd=str(vault), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return (proc.stdout or "") + (proc.stderr or "")


def judge(output: str, subagent_only: bool = False) -> tuple[bool, bool]:
    """(rules 토큰 상속됨?, CLAUDE.md 토큰 상속됨?).
    subagent_only=True 면 'SUBAGENT_SAID:' 이후 텍스트에서만 판정한다 — 오케스트레이터
    메인 세션(같은 rules를 상속함)의 로그에 토큰이 새어 T2가 거짓 PASS 하는 것을 막는다.
    접두사가 없으면(위임 실패·형식 미준수) 상속 판정 불가로 (False, False) 처리한다."""
    scope = output
    if subagent_only:
        idx = output.find("SUBAGENT_SAID:")
        if idx < 0:
            return False, False
        scope = output[idx:]
    return RULES_TOKEN in scope, CLAUDE_TOKEN in scope


def main() -> int:
    ap = argparse.ArgumentParser(description="rules 상속 회귀 테스트 (agentic-vault)")
    ap.add_argument("--claude-cmd", default="claude", help="claude 실행 파일 (기본: PATH의 claude)")
    ap.add_argument("--model", default="", help="모델 강제 지정 (예: claude-sonnet-5). 비우면 기본 모델")
    ap.add_argument("--timeout", type=int, default=300, help="각 호출 타임아웃 초 (기본 300)")
    ap.add_argument("--keep", action="store_true", help="임시 볼트를 지우지 않고 경로를 출력")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="av-rules-test-"))
    try:
        (tmp / ".claude" / "rules").mkdir(parents=True)
        (tmp / ".claude" / "rules" / "magic.md").write_text(RULES_FILE, encoding="utf-8")
        (tmp / "CLAUDE.md").write_text(CLAUDE_FILE, encoding="utf-8")

        print(f"[rules-test] 임시 볼트: {tmp}")
        print(f"[rules-test] claude: {args.claude_cmd}  model: {args.model or '(기본)'}")

        results: list[tuple[str, bool, bool]] = []
        for label, prompt, sub_only in [("T1 main", MAIN_PROMPT, False),
                                        ("T2 subagent", SUBAGENT_PROMPT, True)]:
            try:
                out = run_claude(args.claude_cmd, args.model, tmp, prompt, args.timeout)
            except FileNotFoundError:
                print(f"[rules-test] 오류: '{args.claude_cmd}' 실행 파일을 찾지 못함 "
                      f"(--claude-cmd 로 경로 지정).", file=sys.stderr)
                return 1
            except subprocess.TimeoutExpired:
                print(f"[rules-test] {label}: 타임아웃 → FAIL", file=sys.stderr)
                results.append((label, False, False))
                continue
            rules_ok, claude_ok = judge(out, subagent_only=sub_only)
            results.append((label, rules_ok, claude_ok))
            print(f"[rules-test] {label}: rules({RULES_TOKEN})={'OK' if rules_ok else 'MISS'}  "
                  f"CLAUDE.md({CLAUDE_TOKEN})={'OK' if claude_ok else 'MISS'}")

        if args.keep:
            print(f"[rules-test] 임시 볼트 보존: {tmp}")
    finally:
        if not args.keep:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    # 판정: 4개 관문(T1·T2 × rules·CLAUDE.md) 전부 OK 여야 PASS.
    # 핵심 회귀는 T2 rules — 여기가 MISS 면 서브에이전트 rules 상속이 깨진 것이다.
    all_ok = all(r and c for _, r, c in results) and len(results) == 2
    print(f"\n[rules-test] 판정: {'PASS ✅' if all_ok else 'FAIL ❌'}")
    if not all_ok:
        for label, r, c in results:
            if not (r and c):
                miss = []
                if not r:
                    miss.append(f"rules 토큰({RULES_TOKEN}) — 서브에이전트 상속 회귀 의심"
                                if "subagent" in label else f"rules 토큰({RULES_TOKEN})")
                if not c:
                    miss.append(f"CLAUDE.md 토큰({CLAUDE_TOKEN})")
                print(f"  - {label}: {', '.join(miss)} 미검출")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
