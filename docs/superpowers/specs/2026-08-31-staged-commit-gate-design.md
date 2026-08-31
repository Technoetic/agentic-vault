# Staged Commit Gate v0.8.2 보강 설계

날짜: 2026-08-31 / 상태: 사용자 검토 대기

## 1. 목적

현재 “fail-closed”라고 문서화된 pre-commit을 실제 Git index 기준의 결정론적 게이트로 교체한다. 한글 경로, rename, staged config, 삭제 백링크를 빠짐없이 처리하고 셸 훅은 Python 검사기의 종료 코드를 전달하는 얇은 래퍼로만 남긴다.

## 2. 배포 구조

- 범용 검사 엔진 `skills/agentic-vault/scripts/vault_healthcheck.py`에 `--staged` 모드를 추가한다.
- 같은 엔진 파일을 볼트의 `00-meta/scripts/vault_healthcheck.py`에 엔진 소유 사본으로 설치한다.
- `assets/git-hooks/pre-commit`은 Python 실행기와 설치된 검사기 존재를 fail-closed로 확인한 뒤 `python .../vault_healthcheck.py --vault . --staged`만 실행한다.
- 엔진 사본과 pre-commit에는 `engine=0.8.2` 스탬프를 둔다. `/vault-init`은 둘을 설치하고 `/vault-upgrade`는 낮은 스탬프의 엔진 소유 파일만 교체한다. 로컬 수정이 있으면 교체 전 사용자에게 diff를 보여준다.
- pre-push 정책은 이번 범위에서 동작을 바꾸지 않되 설치 스탬프와 셸 문법 회귀 테스트에 포함한다.

## 3. Git index가 진실의 원천

- 설정은 워킹트리가 아니라 `git show :00-meta/vault-config.json`에서 BOM-safe JSON으로 읽는다.
- 변경 목록은 `git diff --cached --name-status -z --find-renames --diff-filter=ACMRD`로 읽어 공백·한글·rename을 손실 없이 파싱한다. unborn HEAD와 Git 명령 실패도 명시적으로 처리한다.
- 노트 내용은 `git show :<path>`에서 읽는다. unstaged 작업 트리 내용은 판정에 영향을 주지 않는다.
- 검사 대상과 참조 검색은 config의 `deny_zones`·`exclude_dirs`를 Git pathspec exclusion으로 적용한다. deny zone 파일명이나 본문을 검사 목적으로 순회하지 않는다.

## 4. 차단 규칙

- 추가·수정·rename된 Markdown 중 `frontmatter_roots` 아래이며 `frontmatter_exempt_paths` 밖인 파일은 프런트매터와 필수 키·Enum·따옴표 없는 위키링크 규칙을 통과해야 한다.
- 기본 `frontmatter_roots`는 `00-meta`, `20-knowledge`, `30-journal`, `40-people`, `50-projects`; 기본 면제는 `00-meta/scratch`, `00-meta/scripts`, `10-inbox`다. 기존 config에 키가 없어도 동일하게 동작한다.
- 삭제 또는 rename 전 경로의 노트 stem을 가리키는 백링크가 결과 index에 남으면 차단한다. 같은 커밋에서 링크를 수정하면 통과한다.
- log와 health report처럼 append-only·generated 파일은 삭제 백링크 참조자에서 제외하되 경로는 staged config에서 가져온다.
- config 자체가 잘못된 JSON, 절대경로, 드라이브·UNC 경로, `..` 경로, 잘못된 타입을 포함하면 침묵 통과하지 않고 차단한다. 단 사용자가 CLI로 직접 지정한 `--output`은 기존처럼 볼트 밖 임시 경로를 허용하고 config 내부 경로만 제한한다.
- 훅이 활성인 상태에서 vault marker인 `00-meta/vault-config.json`을 삭제하는 커밋은 차단한다. 볼트 해제는 훅 비활성화·엔진 파일 제거를 포함한 명시적 uninstall 절차로만 수행한다.
- copy(`C`)는 새 경로의 스키마만 검사하고 원본 stem 삭제로 취급하지 않는다. rename(`R`)은 새 경로를 검사하고, stem이 바뀐 경우에만 이전 stem의 삭제 백링크를 검사한다.
- 같은 stem의 다른 Markdown 노트가 결과 index에 남아 있으면 위키링크 대상이 계속 존재하므로 삭제 백링크 차단을 적용하지 않는다.
- Markdown 변경이 없는 커밋도 config 변경과 훅에 필요한 기본 진단은 실행한다.

## 5. 전체 검사와의 관계

`--staged`는 커밋 직전 변경 표면만 차단하며 health report를 쓰지 않는다. 인덱스 전체의 기존 관리성 부채는 기존 full mode가 리포트한다. 동일한 config 검증기와 프런트매터 파서를 공유해 두 모드의 규칙 의미가 갈라지지 않게 한다.

기존 config의 `fm_exempt_zones`는 `frontmatter_exempt_paths`의 호환 alias로 읽는다. `frontmatter_roots`가 없는 legacy config에서 full mode는 기존처럼 활성 노트 전체에 스키마를 적용하고, staged mode만 기존 pre-commit과 같은 기본 5개 root를 사용한다. 새 템플릿은 명시적 roots를 포함하므로 새 볼트의 두 모드는 같은 roots를 사용한다. `/vault-upgrade`는 legacy config의 기존 full mode 범위와 alias를 보존하며, 두 모드의 범위를 맞추는 마이그레이션은 사용자가 별도로 명시적으로 승인한 경우에만 수행한다.

## 6. 테스트

`unittest` 임시 Git 저장소로 다음을 RED→GREEN으로 검증한다.

- staged 내용과 unstaged 내용이 다를 때 index만 판정
- 한글·공백 경로와 rename 파싱
- rename된 이전 stem의 잔존 백링크 차단
- 같은 커밋의 백링크 수정 통과
- staged config 사용과 작업 트리 config 무시
- deny/exclude 경로 미열거와 pathspec 제외
- 잘못된 config·검사기 부재·Python 부재 시 fail-closed
- wrapper가 검사기 종료 코드를 그대로 반환
- pre-push 셸 문법과 설치 스탬프

## 7. 비목표

- Git 훅을 우회 불가능한 보안 경계라고 주장하지 않는다. `--no-verify`와 `core.hooksPath` 변경은 가능한 로컬 관리자 우회다.
- 기존 볼트 전체의 관리성 이슈를 커밋 시 일괄 차단하지 않는다.
- hosted CI나 네트워크 원격 push를 전제로 하지 않는다.
- 백업·복구 기능은 변경하지 않는다.
