---
title: "{{PROJECT_NAME}} decisions"
type: decision
status: active
ai_priority: high
tags: [adr, decisions]
related:
  - "[[{{PROJECT_NAME}} context]]"
project: {{PROJECT_NAME}}
created: {{DATE}}
updated: {{DATE}}
---

# 아키텍처·전략 결정 기록 (ADR 누적)

새 결정은 아래 형식으로 **최상단에** 추가한다(번호는 증가, 최신이 위).
과거 결정을 번복할 때는 **삭제하지 말고** 옛 ADR의 상태를 `대체됨 → ADR-XXX`로 표시한다 — 결정의 역사가 곧 맥락이다.
다른 노트에서 ADR을 참조할 땐 앵커형 위키링크를 써라: `[[{{PROJECT_NAME}} decisions#ADR-001]]` (ADR은 독립 노트가 아니라 이 파일의 헤딩이므로, 앵커 없는 `[[ADR-001]]`은 데드 링크가 된다).

---

## ADR-000 | {{DATE}} | (형식 예시 — 첫 실제 결정을 기록할 때 이 블록을 대체하라)

- **상태:** 채택 | 보류 | 대체됨 → ADR-XXX
- **맥락:** (이 결정을 요구한 상황·문제 — 왜 지금 결정해야 했는가)
- **결정:** (무엇을 어떻게 하기로 했는가 — 검토한 대안과 기각 사유를 함께 남기면 번복 시 재검토 비용이 준다)
- **결과:** (이 결정이 만든 기준·후속 작업·트레이드오프)
