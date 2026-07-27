<!-- agentic-vault:rule engine=0.6.0 — 엔진 소유 파일. /vault-upgrade가 신버전으로 통째 교체한다.
     볼트 고유 규칙은 여기가 아니라 CLAUDE.md에 쓰라 — 이 파일의 로컬 편집은 업그레이드 때 사라진다. -->

# 다중 에이전트 협업

- 새 노트 생성 전 `00-meta/index.md`와 대상 폴더를 grep해 중복 노트 생성을 금지하라. 생성 후 index 등록 + log 1줄 기록은 의무다.
- tasks·handoff 노트는 전체 덮어쓰기(Write) 금지 — 부분 수정(Edit)으로 자기 항목만 추가·갱신하라.
- 서브에이전트를 스폰할 때는 이 rules가 상속됨을 전제하되, deny zone·산출물 경로 같은 안전 제약은 프롬프트에도 명시하라(이중 방어).
