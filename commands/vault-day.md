---
description: 오늘의 데일리 노트 생성/추가 — 입력 텍스트를 위키링크와 함께 30-journal에 기록
argument-hint: [기록할 내용 (생략 시 빈 노트 생성)]
---

오늘 날짜(YYYY-MM-DD)를 확인하고 데일리 노트 워크플로우를 수행하라.

## 0. 볼트 감지 (필수 게이트)

현재 작업 디렉토리 루트에 `00-meta/vault-config.json`이 존재하는지 확인하라.
- **없으면**: "이 디렉토리는 agentic-vault 볼트가 아니므로 /vault-day를 생략합니다." 한 줄만 정중히 안내하고 즉시 종료하라.
- **있으면**: 설정을 읽어라. `required_keys`, `enums`, `language` 키를 사용한다.

## 1. 데일리 노트 확보

대상 경로: `30-journal/YYYY/MM/YYYY-MM-DD.md` (연/월 하위 폴더가 없으면 생성).

파일이 아직 없으면 생성하라:

1. `00-meta/templates/daily-template.md`가 볼트에 존재하면 그것을 복제하고 `{{date}}` 플레이스홀더를 실제 날짜로 치환하라.
2. 템플릿이 없으면 아래 기본 골격으로 생성하라. 프런트매터는 설정의 `required_keys`를 모두 채우고 `enums`에 정의된 값만 사용하라 (`type: journal`, `status: active`, `ai_priority: medium` 권장). 섹션 제목은 설정의 `language`에 맞춰 작성하라 (아래는 ko 예시):

```markdown
---
title: "YYYY-MM-DD"
type: journal
status: active
ai_priority: medium
tags: [daily]
created: "YYYY-MM-DD"
updated: "YYYY-MM-DD"
---

# YYYY-MM-DD 일일 로그

## 오늘의 초점 (Top 3)

- [ ]

## 타임라인 / 작업 기록

-

## 발견 · 배운 것

-

## 내일로 넘길 것

-
```

## 2. 입력 기록 (사용자 입력이 있는 경우)

사용자 입력: $ARGUMENTS

- 입력 속 프로젝트명·인명·조직명·핵심 개념에 **공격적으로 위키링크** `[[노트 이름]]`을 씌워 "타임라인" 섹션에 추가하라. 대상 노트가 아직 없어도 주저 없이 링크하라 (고아 링크는 나중에 노트가 된다).
- 상대/절대 경로 마크다운 링크는 쓰지 마라 — 오직 `[[노트 이름]]` 형태만.
- 미팅 내용이면: 볼트에 `40-people/interactions/` 폴더가 존재할 때만 그곳에 별도 미팅 노트 생성을 제안하라 (미팅 템플릿이 `00-meta/templates/`에 있으면 활용).
- 프런트매터 `updated`를 오늘 날짜로 갱신하라.

## 3. 할 일 반영

입력에서 할 일이 식별되면 데일리 노트의 "오늘의 초점" 체크박스에 추가하고, 볼트에 프로젝트 tasks 노트 관행이 있으면(예: `index_note`에서 확인) 그쪽 반영도 제안하라.

## 4. 보고

노트 경로와 추가된 내용을 간결히 보고하라.
