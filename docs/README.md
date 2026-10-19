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
| [reliability.md](reliability.md) | 세션 주입 예산, 기억 주입 진단, Claude Code 권장 버전(2.1.295 이상)·선택형 호스트 버전 확인·SessionStart 실측 절차(미실행), 출처가 있는 로컬 검색, 세대별 백업의 보장 범위와 사용법 |
| [evidence.md](evidence.md) | 검증 근거·인계 CLI (v0.11.0+) |
| [lesson-proposals.md](lesson-proposals.md) | 교훈 수정안 기록·검토·적용 (v0.10.0+) |
| [moai-diagnostics.md](moai-diagnostics.md) | 전체 지침량·원문 보존 인덱스 정리 미리보기·요구별 검증 누락 진단 |
| [tower-reliability.md](tower-reliability.md) | 교훈 반복 비교·실행 조건 기록·충돌 복구·외부 작업 영수증·단계별 연결 진단 (선택형 소스 추가) |
| [link-proposals.md](link-proposals.md) | 관련 노트 후보·해시·검토용 diff를 반환하는 읽기 전용 연결 제안 |
| [memory-patterns.md](memory-patterns.md) | 아홉 논문/공식 코드의 적용 범위, 시간·연결·BM25/RRF·qmd·기억 단위·교훈 관측·연속 작업 평가, 선택형 한글 음절 bigram BM25 |
| [memory-evaluation.md](memory-evaluation.md) | 한국어 근거·구조화 답변 분리 평가, 생성된 상태 변경 순서, 선택형 합성 도구 호출 평가와 실패 진단, 시간 유효성 render-matched 대조군, 선택형 소표본 정확 부호 검정·비율 상한, 선택형 실제 볼트 골드셋 제자리 평가·dev/held-out 분할 |
| [evaluation-stability.md](evaluation-stability.md) | v0.24.0의 선택형 언어·문자열 보존 매트릭스, 공개 fixture Kiwi/qmd 순위 비교, Windows 편집 보강과 실제 관측의 한계, v0.25.0의 선택형 측정 조건 기록(프롬프트 캐시·슬롯 부하·반복 일치), 간헐 실패 테스트의 실패 분류와 반복 측정(x/N·정확 상한) 규칙 |
| [hardening.md](hardening.md) | 출처·공유 상태·시간 대장·교훈 델타·정제 품질·전송 경고·명시적 토큰 보정·선택형 날조 후보/출처 뒷받침 경고·선택형 동기화 충돌 사본/checkpoint 이후 변경 경고 |
| [jev-judgments.md](jev-judgments.md) | Jev-first 직접 질문과 파일 근거 판단의 입력·전송·결과 해석 |
| [release-integrity.md](release-integrity.md) | 공개 자산(ZIP·SHA256SUMS·verification.json), v0.23.0부터의 불변 릴리스 게시 절차, 선택형 사용자 확인의 보장 범위, 경고 전용 CI 공급망 점검과 해시 고정 pip-audit 설치·갱신 절차 |
| [releases/](releases/) | 릴리스 노트. 최신은 [v0.25.0](releases/v0.25.0.md); [검증 기록](verification/v0.25.0.md) |
| [../SECURITY.md](../SECURITY.md) | 지원 버전, 비공개 취약점 제보, 범위, 알려진 한계 |

## 내부 작업 기록 / Internal work records

| 위치 | 내용 |
|---|---|
| [verification/](verification/) | 릴리스·기능별 검증 기록(실행한 명령과 결과, 미해결 항목) |
| [security/](security/) | 제공된 OWASP 2026 문서를 기준으로 한 보강 계획·위험별 대응·검증 기록 |
| [validation.md](validation.md) | `0.9.0-local.1` 시점의 로컬 개선판 검증 기록 (역사 기록) |
| [jev-first-contract.md](jev-first-contract.md) | Jev-first 직접 질문 구현 계약 (2026-09-20 설계 메모) |
| [plans/](plans/) | 구현 계획 |
| [superpowers/](superpowers/) | 에이전트 작업용 구현 계획([plans/](superpowers/plans/))과 설계 명세([specs/](superpowers/specs/)) |
| [screenshots/](screenshots/) | README 이미지 자산 |

내부 작업 기록을 별도 폴더로 옮기는 작업은 링크와 테스트 참조가 넓어 아직 하지 않았다.
Moving internal records into a separate folder is deferred because many links and tests
reference their current paths.
