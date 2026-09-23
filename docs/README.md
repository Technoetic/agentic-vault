# 문서 색인 / Documentation index

이 폴더에는 **사용자 문서**와 **내부 작업 기록**이 섞여 있다. 플러그인을 설치·운영하려면
사용자 문서만 읽으면 된다. 내부 작업 기록은 개발 과정의 계획·설계·검증 기록이며
당시 시점의 서술이라 현재 동작과 다를 수 있다. 현재 동작의 기준은 루트
[README](../README.md), 명령 문서([commands/](../commands/)), 아래 사용자 문서다.

This folder mixes **user documentation** and **internal work records**. To install and
run the plugin you only need the user documentation. Internal records are
point-in-time plans, designs and verification logs and may not match current behavior.

## 사용자 문서 / User documentation

| 문서 | 내용 |
|---|---|
| [codex.md](codex.md) | Claude Code · Codex 겸용 설치, 명령 대응, 훅 신뢰, 기존 설치 갱신 |
| [reliability.md](reliability.md) | 세션 주입 예산, 기억 주입 진단, 출처가 있는 로컬 검색, 세대별 백업의 보장 범위와 사용법 |
| [evidence.md](evidence.md) | 검증 근거·인계 CLI (v0.11.0+) |
| [lesson-proposals.md](lesson-proposals.md) | 교훈 수정안 기록·검토·적용 (v0.10.0+) |
| [jev-judgments.md](jev-judgments.md) | Jev-first 직접 질문과 파일 근거 판단의 입력·전송·결과 해석 |
| [releases/](releases/) | 릴리스 노트. 최신은 [v0.15.1](releases/v0.15.1.md) |
| [../SECURITY.md](../SECURITY.md) | 지원 버전, 비공개 취약점 제보, 범위, 알려진 한계 |

## 내부 작업 기록 / Internal work records

| 위치 | 내용 |
|---|---|
| [verification/](verification/) | 릴리스·기능별 검증 기록(실행한 명령과 결과, 미해결 항목) |
| [validation.md](validation.md) | `0.9.0-local.1` 시점의 로컬 개선판 검증 기록 (역사 기록) |
| [jev-first-contract.md](jev-first-contract.md) | Jev-first 직접 질문 구현 계약 (2026-09-20 설계 메모) |
| [plans/](plans/) | 구현 계획 |
| [superpowers/](superpowers/) | 에이전트 작업용 구현 계획([plans/](superpowers/plans/))과 설계 명세([specs/](superpowers/specs/)) |
| [screenshots/](screenshots/) | README 이미지 자산 |

내부 작업 기록을 별도 폴더로 옮기는 작업은 링크와 테스트 참조가 넓어 아직 하지 않았다.
Moving internal records into a separate folder is deferred because many links and tests
reference their current paths.
