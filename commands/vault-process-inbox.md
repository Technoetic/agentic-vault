---
description: 10-inbox 수집 대기열을 정제해 영구 지식(20-knowledge)으로 병합하고 원본을 _processed로 격리
---

인박스 정제 파이프라인을 수행하라.

## 0. 사전 가드 (다른 어떤 단계보다 먼저)

1. **볼트 감지**: 현재 프로젝트 루트에 `00-meta/vault-config.json`이 존재하는지 확인하라.
   없으면 여기는 agentic-vault 볼트가 아니다 — "이 디렉토리는 agentic-vault 볼트가 아닙니다(`00-meta/vault-config.json` 없음). 아무 작업도 수행하지 않았습니다."라고 한 줄만 정중히 보고하고 즉시 종료하라. 에러를 반복 출력하거나 파일을 생성하지 마라.
2. **설정 로드**: `00-meta/vault-config.json`을 읽어 다음 키를 이 명령의 동작 기준으로 삼아라:
   `deny_zones`, `exclude_dirs`, `required_keys`, `enums`, `frontmatter_max_lines`, `index_note`, `log_note`, `log_tags`.
3. **deny zone 가드**: `deny_zones`·`exclude_dirs`에 해당하는 경로는 읽기·grep·glob 등 어떤 접근에도 포함하지 마라.

## 1. 대기열 확인

- `10-inbox/` 바로 아래의 파일과 하위 폴더(예: quick/, voice/, web/ — 볼트에 실제 존재하는 폴더 기준)를 스캔하라.
- 언더스코어(`_`)로 시작하는 폴더 — 특히 `10-inbox/_processed/` — 와 `deny_zones` 경로는 **절대 스캔 금지**.
- 처리할 파일이 없으면 "인박스 비어 있음"을 한 줄로 보고하고 종료하라.

## 2. 파일별 정제 (각 파일에 대해 a→d 순서 엄수)

a. 내용을 읽고 핵심 엔티티·개념을 추출하라.
b. YAML 프런트매터를 갖춘 정제 노트를 `20-knowledge/` 하위의 적절한 카테고리 폴더(볼트에 실제 존재하는 폴더 중 선택)에 생성하라.
   - `required_keys`의 모든 키를 채우고, `enums`에 정의된 값만 사용하며, `frontmatter_max_lines`를 넘기지 마라.
   - **프런트매터 안의 위키링크 값은 반드시 이중 따옴표로 감싸라**: `related: ["[[노트 이름]]"]`.
   - 볼트에 `00-meta/schemas/frontmatter.md`가 존재하면 세부 규칙은 그 스키마 문서를 우선 따르라.
c. 기존 노트와의 연관성을 분석해 본문과 `related`에 위키링크를 주입하라. 고립 노드를 만들지 마라.
d. **원본 격리 — deny zone 이동 규칙 (순서 엄수)**:
   1. 원본 파일에 프런트매터 갱신이 필요하면(예: `status: archive` 표기) **반드시 이동 전에 끝내라**.
      `_processed/`는 deny zone이라 **이동 후에는 그 파일을 다시 읽거나 수정할 수 없다.**
   2. 원본을 **셸 move 명령**(POSIX `mv` / Windows `Move-Item`)으로 `10-inbox/_processed/YYYY-MM/`로 이동하라(대상 폴더가 없으면 먼저 생성). Write/Edit 도구로 복사 후 삭제하는 방식은 금지.
   3. **이동 시 파일명은 절대 바꾸지 마라** — 위키링크는 파일명 기반이므로 이름만 유지하면 폴더 간 이동은 안전하다.

## 3. 기록

- `index_note`(기본 `00-meta/index.md`)에 새 노트를 등록하라.
- `log_note`(기본 `00-meta/log.md`) 최상단에 `[ingest]` 태그(`log_tags`에 정의된 태그만 사용)로 처리 요약 1줄을 추가하라.
  log 파일 헤더에 형식 정의가 있으면 그 형식을 따르고, 없으면 `- YYYY-MM-DD HH:MM | 행위자 | [ingest] 요약` 형식을 사용하라.

## 4. 보고

- 처리 건수, 생성된 노트, 이동된 원본을 표로 보고하라.
