<!-- agentic-vault:rule engine=0.6.0 — 엔진 소유 파일. /vault-upgrade가 신버전으로 통째 교체한다.
     볼트 고유 규칙은 여기가 아니라 CLAUDE.md에 쓰라 — 이 파일의 로컬 편집은 업그레이드 때 사라진다. -->

# Hard Rules: YAML 프런트매터 (위반 시 메타데이터 계층 붕괴)

- 볼트에 생성되는 **모든 .md 파일 최상단에 YAML 프런트매터**를 작성하라(필수 키 기준 12줄 내외, 선택 키 포함 최대 16줄). 스키마: `00-meta/schemas/frontmatter.md`
- 프런트매터가 없는 노트를 새로 만들지 마라.
- **CRITICAL:** 프런트매터 안에서 위키링크를 값으로 쓸 때는 반드시 이중 따옴표로 감싸라: `related: ["[[노트 이름]]"]` 또는 하이픈 리스트 + 항목별 따옴표.
  따옴표 없는 `[[ ]]`는 YAML 중첩 배열로 오파싱되어 메타데이터 소비 시스템 전체가 붕괴한다.
- `type`/`status`/`ai_priority`는 `vault-config.json`의 Enum 값만 사용한다. 인라인 필드(`[key:: value]`)는 사용 금지.
