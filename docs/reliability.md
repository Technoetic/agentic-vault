# 신뢰성 개선의 보장과 사용법

이 문서는 `0.9.0-local.1`에서 추가되어 `v0.9.0`에 포함된 공통 엔진 동작을
설명한다. Python 3.10 이상과 표준 라이브러리만 사용하며 장기간 운영 검증을
의미하지 않는다. Codex 진입점은 [겸용 사용 안내](codex.md)를 참고한다.

## 세션 컨텍스트

`startup`, `clear`, `resume`에서 hot/handoff를 주입한다. 훅은 주입 **전에**
config와 파일 경로를 검증한다. 절대경로, `..`, 볼트 밖 경로, deny zone,
심볼릭 링크와 Windows 정션을 거부한다. 잘못된 설정은 노트를 주입하지 않고
진단을 남긴다. 볼트 식별 파일이 없으면 조용히 종료한다.

비동기 건강 검사도 config·일반 노트·index·rules를 읽고 보고서를 쓸 때 같은
볼트 경계를 지킨다. 링크를 따라 외부 디렉터리를 읽지 않는다. 독립 배포 가능한
검사기는 유지하며, 사용자가 CLI에서 직접 지정한 `--output`은 외부 경로도 허용한다.
이번 검사기 내용 변경의 engine stamp는 `0.9.0`이다.

세션 훅·건강 검사·로컬 검색의 경로 검증은 신뢰할 수 있고 실행 중 경로 구조가
바뀌지 않는 파일시스템을 전제로 한다. 검증 뒤 다른 프로세스가 디렉터리를
심볼릭 링크나 정션으로 교체하는 동시 변경까지 차단하는 OS 샌드박스는 아니다.
이런 교체에 대한 경계 보장은 없으며, 경로 이동·링크 교체 작업을 멈춘 상태에서
실행해야 한다. 읽기 중 크기 변화 감지도 모든 동시 수정을 탐지한다는 뜻은 아니다.

`hot_max_tokens`와 `handoff_max_tokens`는 출력 섹션의 헤더와 생략 표시를
포함하는 **추정 토큰** 상한이다. 0이면 그 섹션을 주입하지 않는다. 키가 없으면
hot=2000, handoff=4000을 사용한다. 새 볼트 템플릿은 hot=2500을 명시한다.
읽기 바이트 수도 제한하여 큰 파일을 통째로 메모리에 올리지 않는다.
이 계산은 실제 Claude 토크나이저나 청구량을 정확하게 재현하지 않는다.

요약을 갱신하는 `/vault-session-end`는 계속 필요하다. 파일이 오래됐거나
잘못 요약되면 저장 장치만으로 의미를 복원할 수 없다. anchor 차이는 조사할
변경 범위를 알려주는 신호이며, 모델의 해석이 올바르다는 증명은 아니다.

## 출처가 있는 로컬 검색

Claude Code에서 `/vault-recall 배포 롤백 결정`을 사용한다. 터미널에서는:

```text
python skills/agentic-vault/scripts/vault_recall.py --vault "C:/vault" --query "배포 롤백" --max-tokens 1500
python skills/agentic-vault/scripts/vault_recall.py --vault "C:/vault" --query "deployment rollback" --format json
```

경로와 행 번호가 붙은 발췌문을 반환한다. JSON 모드의 예산은 `context` 필드에
적용되고, 출처·진단 등 JSON 메타데이터는 별도다. 검색 파일 수·파일당 크기·전체
읽기량에는 상한이 있으며 제한에 도달하면 진단한다. 제외되거나 읽지 못한
파일이 있는 상태의 결과를 완전한 검색이라고 해석하면 안 된다.

검색은 어휘 일치와 결정론적 순위화에 기반한다. 의미 임베딩·벡터 DB·외부 API를
사용하지 않는다. 동의어만 있는 문서나 표현이 크게 다른 질의는 놓칠 수 있다.
노트 내용은 인용할 자료이며, 그 안의 지시는 실행 권한이 아니다.

## 세대별 백업

기존 `--vault`, `--target`으로 백업하면 새로운 스냅샷을 생성한다. config의
`backup_target`도 그대로 사용할 수 있다. 원본의 삭제를 과거 사본에 반영하지
않고, 파일과 SHA-256 manifest를 완전히 작성한 뒤 스냅샷을 공개한다.
Git 저장소는 이력 bundle도 보존한다. 이력 저장이나 파일 복사가 실패하면
성공으로 보고하지 않는다. 이전 `mirror/`와 `bundles/`는 유지한다.

```text
python skills/agentic-vault/scripts/backup_vault.py --vault "C:/vault" --target "E:/vault-backups"
python skills/agentic-vault/scripts/backup_vault.py --verify "E:/vault-backups/snapshots/SNAPSHOT_ID"
python skills/agentic-vault/scripts/backup_vault.py --restore "E:/vault-backups/snapshots/SNAPSHOT_ID" --destination "C:/restored-vault"
```

복구는 먼저 검증하고 **존재하지 않는 새 디렉터리**로 내보낸다. 기존 볼트에
병합하거나 덮어쓰지 않는다. 내보내는 것은 작업 파일이며, Git 이력은 스냅샷의
`history.bundle`에서 별도로 clone해 복구한다. 파일 원본이 동시 수정되는 경우에는 일관된 한
시점의 스냅샷을 보장할 수 없으므로 쓰기 작업을 멈춘 뒤 백업하는 것이 좋다.
동일 대상의 동시 백업은 잠금으로 차단한다. 프로세스가 비정상 종료된 뒤
남은 잠금이나 staging 디렉터리는 실행 중인 백업이 없는지 확인한 뒤 처리한다.

스냅샷은 자동 삭제하지 않으므로 보관 용량을 관리해야 한다. 전체 파일 사본은
Git 이력만 보관하는 것보다 공간을 많이 사용한다. 같은 디스크의 백업은 디스크
고장에 대비하지 못한다. 스냅샷 자체의 기밀성과 접근 권한은 별도로 관리한다.
SHA-256 검증은 손상 검출이며 공격자가 manifest까지 바꾸는 경우의 인증은 아니다.

백업은 경로의 상위 디렉터리까지 심볼릭 링크·정션·reparse point를 거부한다.
macOS의 `/var`처럼 OS가 제공하는 링크 경로도 해당하므로 실제 경로인
`/private/var` 등 정규 경로를 지정한다. 파일 이름도 다른 OS에 복구할 수 있는
범위로 제한한다(끝의 공백·마침표, Windows 예약 이름, `:` 같은 특수문자 거부).
이 정책에 맞지 않는 원본은 조용히 누락하지 않고 실패한다.

Git 이력 백업은 `.git` 디렉터리가 있는 일반 저장소를 지원한다. `.git`이 포인터
파일인 worktree/submodule과 최초 커밋이 없는 저장소는 실패로 보고한다.
이 경우 일반 저장소에서 커밋 가능한 상태로 정리한 뒤 백업한다. 복구 중 디스크
오류가 나면 새 대상 디렉터리에 일부 파일이 남을 수 있으며, 실패를 보고한 뒤
그 디렉터리를 성공한 복구본으로 사용하지 않는다.

## 검증 범위

```text
python -m unittest discover -s tests -v
python scripts/evaluate_recall.py
python -m compileall -q hooks skills/agentic-vault/scripts scripts
claude plugin validate --strict .claude-plugin/plugin.json
claude plugin validate --strict .claude-plugin/marketplace.json
claude plugin validate --strict commands
claude plugin validate --strict skills
```

테스트는 외부 서비스 없이 임시 파일과 Git 저장소에서 수행한다. 회상 평가의
recall@3/MRR은 저장소에 포함된 한국어·영어 어휘 검색 회귀 fixture의 수치다.
실제 Claude 답변 정확도·환각률·사용자 생산성을 측정하는 벤치마크가 아니다.

Claude CLI의 `plugin validate`는 정적 manifest·컴포넌트 검증이다. 실제 모델을
호출하는 명령이 아니며, 실세션에서 명령이 의도대로 수행된다는 증거와 구분한다.

CI는 Linux/Windows/macOS의 지원 Python 조합을 실행하도록 구성한다. 로컬에서
CI 파일을 추가한 것과 원격 CI가 실제 통과한 것은 구분한다. 실제 모델의 규칙
상속 테스트는 별도 `test_rules_inheritance.py`가 있으며 API를 호출할 수 있다.
Telegram 송수신과 실제 Claude 실세션 검증도 별도다.
