---
title: "Aside CLI 운영 가이드"
type: tool
status: draft
ai_priority: high
tags: [browser, automation, aside, cli]
created: {{DATE}}
updated: {{DATE}}
---

# Aside CLI 운영 가이드

이 볼트의 브라우저 도구는 Aside CLI다(CLAUDE.md의 「브라우저 도구: Aside CLI」 조항). 이 노트는 **이 볼트의 기기에서 실행해 본 결과**만 기록한다. 아래 「엔진 개발 실측」 표는 agentic-vault 개발 기기의 기록이다. 이 기기에서 다시 실행해 결과를 「이 볼트의 실측」 표로 옮기기 전까지는 참고로만 쓴다. 옮기고 나면 `status`를 `active`로 바꾼다.

## 명령과 데이터 경로

| 명령 | 실행 주체 | 페이지 내용이 가는 곳 | 쓰는 곳 |
|---|---|---|---|
| `aside "작업"` = `aside exec` | Aside 에이전트(설정된 클라우드 모델) | Aside가 쓰는 클라우드 모델 제공자 | 공개 웹 조사 |
| `aside repl "<JS>"` | Aside 안에서 도는 Playwright식 JS | Aside 쪽 모델로는 가지 않는다(엔진 개발 기기 부분 실측, 데몬의 외부 통신은 미확인). 단 `console.log()` 출력은 호출한 에이전트(Claude Code·Codex)의 모델 제공자로 간다 | 외부 전송 금지 자료 — 원문 대신 파일 저장 + 건수·합계·해시만 출력 |

## 엔진 개발 실측 (Windows 11, Aside 앱 1.0.928.2, CLI 1.26.916.1741, 2026-09-29)

| 확인한 것 | 결과 | 대응 |
|---|---|---|
| CLI와 앱의 관계 | CLI는 `aside-daemon.exe`와 통신한다. 브라우저가 데몬을 띄우고, 브라우저가 종료되면 데몬을 강제 종료한다 | 작업이 끝나도 창을 닫지 않고 탭만 `closeTab` |
| 실행 파일 이름 | CLI 실행 파일도 `aside.exe`다. 파일 속성 ProductName은 브라우저 `Aside`, CLI `Aside CLI` | 도우미는 ProductName으로 브라우저를 가려낸다 |
| `--no-startup-window`로 기동 | 창이 없어 브라우저가 곧바로 종료되고 데몬이 뜨지 않는다 | 쓰지 않는다 |
| `start /min`으로 기동 | 최소화 지시를 무시하고 보통 창으로 떠서 약 2초간 포커스를 가져간다 | `00-meta/scripts/aside-up.ps1`: 창이 보이자마자 최소화하고 이전 창에 포커스를 돌려준다 |
| 최소화 상태에서 작업 | youtube 스킬, `openTab`·`evaluate`(1440×900, `visibilityState` visible)·`screenshot`·`closeTab`이 동작하고 창은 최소화 상태로 남는다. 스크린샷은 6.4초로 느렸다 | 최소화한 채로 작업한다 |
| 기동 도우미 콜드 스타트 2회 | 기동 후 530·579ms에 첫 창이 보였고 같은 50ms 감시 주기 안에 최소화됐다. 두 번 모두 포커스가 이전 창으로 돌아왔고 CLI는 4.9·5.0초 뒤 응답했다. 그 사이 새 창이 잠깐 포커스를 가질 수 있다 | — |

## 이 볼트의 실측

| 날짜·버전 | 확인한 것 | 결과 | 대응 |
|---|---|---|---|
| | | | |

## 관련

- 브라우저 경계 규칙: `.claude/rules/vault-browser.md`
- 결정 기록: 이 도구를 채택한 결정은 프로젝트 decisions 노트에 ADR로 남긴다.
- 볼트 지도: [[index]]
