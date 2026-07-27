<!-- agentic-vault:begin — /vault-init이 추가한 볼트 행동 계약(스텁, engine=0.6.0). 마커 쌍은 유지하라(중복 append 방지·업그레이드 앵커). -->

# {{VAULT_NAME}} — 에이전틱 지식 볼트

이 디렉토리는 코드 리포지토리가 아니라 **옵시디언 기반의 상호 연결된 지식 베이스(LLM Wiki 패턴)** 이자
에이전트 세션을 횡단하는 **영구 상태 계층(Persistent State Layer)** 이다.
너(Claude Code)는 이 볼트에서 정보를 섭취(Ingest)하고, 위키링크로 연결하며, 지식을 복리로 축적하는 자율적 지식 관리자다.

- **상세 행동 규칙은 `.claude/rules/vault-*.md` 5개 파일에 있고 자동 로드된다** (architecture·linking·frontmatter·workflow·collab). 이 파일들은 **엔진 소유**라 `/vault-upgrade`가 통째로 교체한다 — 볼트 고유 규칙은 rules가 아니라 이 CLAUDE.md에 쓰라.
- 볼트 기계 설정(deny zone·프런트매터 필수 키·Enum·로그 태그)의 단일 출처는 `00-meta/vault-config.json`이다.
- 타 에이전트(Codex 등)용 규약은 루트 `AGENTS.md`다 — rules에서 생성된 산출물이니 직접 편집하지 말고 `/vault-upgrade`로 재생성하라.

<!-- agentic-vault:end -->
