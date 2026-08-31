# agentic-vault v0.8.2 릴리스 통합 설계

날짜: 2026-08-31 / 상태: 사용자 검토 대기

## 1. 목적과 의존성

v0.8.2는 [Jarvis 보강 설계](2026-08-31-jarvis-v082-hardening-design.md)와 [staged commit gate 설계](2026-08-31-staged-commit-gate-design.md)가 각각 독립 검증을 통과한 뒤에만 조립한다. 두 하위 프로젝트가 공개 계약을 바꾸므로 한쪽만 반영된 버전 표시는 금지한다.

## 2. 릴리스 표면

- `.claude-plugin/plugin.json`과 `.claude-plugin/marketplace.json` 버전을 모두 `0.8.2`로 맞춘다.
- README 버전 배지와 트리 설명을 `0.8.2`로 맞추고, 복수 브리핑·private-chat 강제·내구성 있는 offset·index-only commit gate를 실제 동작 수준으로 설명한다.
- `docs/releases/v0.8.2.md`에 변경점, 하위호환, 업그레이드 단계, 보안 경계, 검증 명령을 기록한다.
- `/vault-upgrade`는 일반 새 설정 키를 기존 값 비파괴 방식으로 보충하되, legacy config의 기존 full mode 범위와 alias는 보존한다. 검사 범위 마이그레이션은 사용자가 별도로 명시적으로 승인한 경우에만 수행하고, 엔진 소유 검사기·훅은 스탬프 기준으로 갱신한다.

## 3. 호환성

- Python 3.10+ 및 표준 라이브러리 전용을 유지한다.
- 기존 `jarvis.briefing_time`만 있는 볼트는 설정 변경 없이 동작한다.
- 기존 pre-commit 설치는 `/vault-upgrade` 시 사용자 확인 후 새 엔진 소유 파일로 교체한다.
- 기존 custom hook 수정은 자동 삭제하지 않고 diff를 제시해 보존·이관 여부를 결정하게 한다.
- 플러그인 미사용 일반 Markdown 볼트와 config가 없는 디렉터리에서는 기존처럼 조용히 무동작한다.

## 4. 릴리스 게이트

- 전체 `unittest` 0 failures
- Python 파일 AST/compile 성공, JSON 파싱 성공, 셸 훅 `sh -n` 성공, `git diff --check` 성공
- 임시 볼트에서 init/upgrade 산출물의 staged gate 통과·차단 시나리오 성공
- Jarvis `--self-test`에서 토큰 미설정 경고를 제외한 FAIL 0
- manifest·marketplace·README·release note 버전 문자열 일치
- 작업 트리 clean 및 origin 대비 커밋 목록을 보고

## 5. 외부 변경 경계

로컬 커밋과 태그 후보 준비까지만 자동 수행한다. GitHub push, tag push, GitHub Release 발행은 공유 원격 상태를 바꾸므로 사용자 명시 승인 전 실행하지 않는다.

## 6. 후속 v0.9.0

healthcheck/session hook 전체 경로 containment, hook doctor, backup sentinel·bundle verify·restore drill, CI 도입은 v0.8.2를 막지 않는 별도 설계로 남긴다.
