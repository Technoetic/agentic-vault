---
title: "frontmatter — 볼트 표준 프런트매터 스키마"
type: reference
status: active
ai_priority: high
tags: [schema, frontmatter, meta]
created: {{DATE}}
updated: {{DATE}}
---

# 볼트 표준 프런트매터 스키마

볼트에 생성되는 **모든 .md 파일**은 최상단에 아래 구조의 YAML 블록을 가져야 한다.
줄 수 예산: **필수 키 기준 12줄 내외를 지향하고, 선택 키(aliases·related 다중 링크 등) 포함 시 최대 16줄**까지 허용한다.
AI는 노트 생성 전 반드시 이 스키마를 따른다. 본문 인라인 필드(`[key:: value]`)는 금지 — 메타데이터는 오직 프런트매터에만 둔다.

기계 검증 기준값(필수 키·Enum·줄 상한·deny zone)의 단일 출처는 `00-meta/vault-config.json`이다.
이 문서와 config가 어긋나면 **config가 우선**한다(이 문서는 사람이 읽는 해설).

```yaml
---
title: "고유한 식별자이자 명확한 제목 (String, 필수)"
type: concept | guide | reference | tool | pattern | journal | person | organization | meeting | decision | project   # Enum, 필수
status: active | draft | archive           # 문서 생명주기, 필수
ai_priority: high | medium | low | archive # AI 스캔 우선순위, 필수
tags: [최소 1개 이상의 소문자 태그]        # 필수
domain: "상위 폴더와 일치하는 도메인명"     # 선택 (예: meta, 지식 도메인명)
related:
  - "[[연관 노트 1]]"                      # 반드시 이중 따옴표! 고립 노드 방지
  - "[[연관 노트 2]]"
project: 프로젝트명                        # 프로젝트 종속 문서일 경우만
attendees: ["[[인물]]"]                    # type: meeting 일 경우만
confidence: 50~100                         # AI가 자율 캡처한 지식일 경우만 (신뢰도 자가 평가)
aliases: ["다른 이름"]                     # 자동완성 제안 전용 — 원시 위키링크는 해석 안 됨! 링크 대상은 파일명과 일치시킬 것
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

## 강제 규칙

1. **위키링크 값은 반드시 이중 따옴표로 감싼다** — `related: [[X]]` (금지) → `related: ["[[X]]"]` (허용).
   따옴표 없는 대괄호는 YAML 중첩 배열로 오파싱되어 Dataview 등 프런트매터 소비자 전체가 붕괴한다.
2. 다중 링크는 하이픈 리스트 + 항목별 이중 따옴표를 권장한다.
3. `type`, `status`, `ai_priority`는 위에 열거된 Enum 값만 사용한다. 임의의 값 발명 금지.
4. `updated`는 본문을 수정할 때마다 오늘 날짜로 갱신한다.
5. 날짜는 ISO 8601(`YYYY-MM-DD`)만 사용한다.

## Dataview 활용 예시

```dataview
TABLE status, updated, project
FROM "50-projects"
WHERE type = "decision"
SORT updated DESC
```
