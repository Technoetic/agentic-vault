---
title: "index — 볼트 내비게이션 허브"
type: reference
domain: meta
status: active
ai_priority: high
tags: [index, navigation]
created: {{DATE}}
updated: {{DATE}}
---

# 볼트 내비게이션 허브

**새 노트를 생성할 때마다 이 인덱스의 해당 카테고리에 위키링크를 추가해 지도를 갱신한다.**
등록 누락 = 고아 노드의 시작이다. 링크 형식: `- [[노트 이름]] — 1줄 설명` (반드시 **한 물리적 줄** — 여러 줄로 나누면 이 지도의 grep 계약이 깨진다).

**서술 규격 — 에이전트는 설명만 읽고 노트를 열지 말지 결정한다.** 제목을 다시 쓴 설명은 그 판단을 못 돕는다(제목 재진술 금지). 1~2문장으로:
- 실패·교훈형 노트(패턴·실수·회고 계열): `문제 + 근본 원인 + 처방`.
- 그 외: `핵심 결론 + 다루는 범위 + 언제 열어볼지`.

카테고리가 커지면 하위 소제목으로 분할하되, 이 파일이 항상 볼트의 유일한 전역 지도여야 한다.

## 시스템 (00-meta)

- [[frontmatter]] — 표준 프런트매터 스키마 (00-meta/schemas)

## 프로젝트 (50-projects)

(활성 프로젝트의 core 노트 — context·tasks·handoff·decisions·mistakes — 를 여기 등록)

## 개념 (20-knowledge/concepts)

(원자적 개념 노트)

## 도메인 (20-knowledge/domains)

(도메인별 지식 노트)

## 패턴 (20-knowledge/patterns)

(반복 검증된 작업 패턴·방법론)

## 레퍼런스 (20-knowledge/references)

(스키마·규격·조견표)

## 도구 (20-knowledge/tools)

(검증된 도구 사용법·운영 가이드)

## 소스 (20-knowledge/sources)

(원전·긴 보고서 — 소화 전 원본 보존용)

## 인물·조직 (40-people)

(인물·조직·미팅 기록)

## 저널·회고 (30-journal)

(일일 로그는 날짜 파일로 자동 축적 — 주간·분기 회고만 여기 등록)
