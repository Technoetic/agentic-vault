# -*- coding: utf-8 -*-
"""agentic-vault SessionStart 훅 — 세션 인계(handoff)·핫 컨텍스트(hot) 주입.

동작:
  1. 환경변수 CLAUDE_PROJECT_DIR에서 프로젝트 루트를 얻는다.
  2. 루트에 00-meta/vault-config.json이 없으면 볼트가 아니므로 조용히 종료(exit 0).
  3. 볼트면 config의 handoff_note / hot_note를 읽어 stdout으로 주입한다.
     - handoff_note: 빈 문자열이면 기능 생략(우아한 성능 저하).
     - 각 파일은 존재할 때만 헤더와 함께 출력한다.

stdout은 SessionStart 훅 규약에 따라 Claude 컨텍스트에 주입된다.
어떤 실패 상황에서도 에러 스팸 없이 exit 0 한다(세션 시작을 막지 않는다).
"""
import json
import os
import sys
from pathlib import Path

CONFIG_REL = "00-meta/vault-config.json"
# 비정상적으로 큰 파일이 컨텍스트를 폭파하지 않도록 파일당 출력 상한(문자 수)
MAX_CHARS_PER_FILE = 100_000


def _utf8_stdout() -> None:
    """Windows cp949 콘솔에서도 한국어 출력이 깨지지 않도록 stdout을 UTF-8로 재설정."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _read_note(vault: Path, rel_path: str) -> str | None:
    """볼트 상대 경로의 노트를 읽어 반환. 미설정·부재·오류 시 None."""
    if not rel_path or not isinstance(rel_path, str):
        return None
    try:
        note = (vault / rel_path).resolve()
        if not note.is_file():
            return None
        text = note.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    if len(text) > MAX_CHARS_PER_FILE:
        text = text[:MAX_CHARS_PER_FILE] + "\n\n[... 파일이 너무 커서 이하 생략 ...]"
    return text.strip()


def main() -> int:
    _utf8_stdout()

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if not project_dir:
        return 0
    vault = Path(project_dir)
    config_path = vault / CONFIG_REL
    if not config_path.is_file():
        return 0  # 볼트가 아님 — 조용히 무동작

    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict):
            return 0
    except (OSError, ValueError):
        return 0  # 설정 파손 시에도 세션 시작을 막지 않는다

    sections: list[str] = []

    handoff = _read_note(vault, config.get("handoff_note", ""))
    if handoff:
        sections.append("=== SESSION HANDOFF (직전 세션 인계) ===\n" + handoff)

    hot = _read_note(vault, config.get("hot_note", ""))
    if hot:
        sections.append("=== HOT CONTEXT ===\n" + hot)

    if sections:
        print("\n\n".join(sections))
    return 0


if __name__ == "__main__":
    sys.exit(main())
