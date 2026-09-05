# Claude Code · Codex 겸용 사용

`0.9.0-local.2`는 같은 Markdown 볼트, Python 엔진, 명령 문서를 두 클라이언트에서
사용한다. Codex CLI `0.150.1`에서 로컬 설치와 스킬·훅 검색을 검증했다.
Python 3.10+와 Git이 필요하며, Windows의 공통 훅 실행에는 Git Bash가 필요하다.
개발 중 사용한 `local.2` 버전 식별자를 유지한 공개 사전 릴리스다.

## 설치

터미널에서 GitHub의 해당 릴리스 태그를 등록한다:

```text
codex plugin marketplace add Technoetic/agentic-vault --ref v0.9.0-local.2
codex plugin add agentic-vault@agentic-vault-local
```

`agentic-vault-local`은 기존 marketplace 식별자이며 GitHub 설치에서도 동일하다.
Windows PowerShell에서 `codex.ps1` 실행 정책 오류가 나면 `codex` 대신 `codex.cmd`를
사용한다. 실행 정책을 변경할 필요는 없다.

로컬 설치도 가능하다. Release ZIP을 풀거나 저장소를 clone한 뒤
`.codex-plugin/plugin.json`과 README가 있는 디렉터리를 지정한다:

```text
codex plugin marketplace add "C:/path/to/agentic-vault"
codex plugin add agentic-vault@agentic-vault-local
```

macOS/Linux도 같은 명령에 해당 OS의 절대경로를 쓴다. 설치 후 볼트 디렉터리에서
새 Codex 세션을 연다. `/skills`에서 `agentic-vault:agentic-vault`를 확인한다.
이 이름은 전체 배포본의 플러그인 설치 기준이다. 스킬 폴더만 떼어 복사하면 공통
명령 문서·훅·템플릿을 찾을 수 없으므로 위 방식으로 전체 배포본을 설치한다.

자동 복원을 사용하려면 `/hooks`에서 이 플러그인의 SessionStart 정의를 검토하고
신뢰한다. 설치만으로 훅을 신뢰하지 않는다. 정의가 바뀌면 다시 검토해야 한다.
주입 훅은 hot/handoff를 읽고, 비동기 건강 검사 훅은 설정된 보고서 파일을 쓴다.
신뢰 전에도 아래의 명시적 `session-start` 요청으로 복원할 수 있다.
[Codex 훅 공식 문서](https://learn.chatgpt.com/docs/hooks)

Claude Code 설치는 [README의 설치 안내](../README.md#-설치)를
따른다. 두 클라이언트를 별도로 설치해도 볼트 데이터는 한 벌이다.

## 사용

Codex 대화 입력란에서 다음처럼 요청한다. 셸 명령이 아니다.

| 작업 | Claude Code | Codex |
|---|---|---|
| 새 볼트 | `/vault-init 연구볼트` | `$agentic-vault:agentic-vault init 연구볼트` |
| 기존 볼트 갱신 | `/vault-upgrade` | `$agentic-vault:agentic-vault upgrade` |
| 세션 복원 | `/vault-session-start` | `$agentic-vault:agentic-vault session-start` |
| 출처 검색 | `/vault-recall 배포 롤백` | `$agentic-vault:agentic-vault recall 배포 롤백` |
| 인계 저장 | `/vault-session-end` | `$agentic-vault:agentic-vault session-end` |
| 건강 검사 | `/vault-lint` | `$agentic-vault:agentic-vault lint` |

Codex는 `day`, `ingest`, `process-inbox`, `trace`도 같은 이름으로 호출한다.
`backup`, `verify <스냅샷 경로>`, `restore <스냅샷 경로> <새 복구 경로>`는
기존 백업 CLI로 연결한다. [전체 연결 규약](../skills/agentic-vault/references/codex.md),
[공통 검색·백업 사용법](reliability.md)을 참고한다.

기존 볼트는 먼저 `upgrade`를 요청한다. 엔진 규칙 다섯 개와 전용 스텁을 합친
`AGENTS.md`가 생성된다. 수제 AGENTS와 사용자 수정은 자동으로 덮어쓰지 않는다.
볼트 고유 규칙은 `CLAUDE.md`의 관리 마커 밖에 보존하고 Codex에도 읽도록 안내한다.
`CLAUDE.md`와 `.claude/rules/`는 Claude Code용 계약으로 계속 유지된다.

Claude에서 인계를 저장한 뒤 Codex에서 복원하거나 그 반대로 사용할 수 있다.
동시에 같은 노트·handoff를 편집하지 않고 세션을 번갈아 마감하는 방식을 권장한다.
스냅샷 생성 중에도 볼트 쓰기를 멈춘다. 전체 볼트를 잠그는 동시 편집 조정 기능은 없다.

## 실행과 검증 범위

세션 스크립트의 볼트 선택 순서는 `--vault` → 비어 있지 않은
`CLAUDE_PROJECT_DIR` → 작업 디렉터리다. Codex의 수동 진입점은 `--vault`를
명시하므로 남아 있는 Claude 환경변수보다 사용자가 지정한 볼트가 우선한다.
자동 훅은 `CLAUDE_PROJECT_DIR`가 없거나 비어 있을 때 세션 작업 디렉터리를 사용한다.
다른 볼트의 Claude 환경변수가 남아 있으면 제거하고 Codex를 시작하거나,
사용할 볼트를 지정해 스킬의 수동 `session-start`를 요청한다.

경로·deny zone 검증과 섹션별 토큰 예산은 두 클라이언트에 동일하게 적용한다.
예산 0은 해당 섹션 주입을 끈다. 토큰 수는 추정치이며 Codex가 큰 훅 출력을
추가로 줄일 수 있다. 생략된 내용을 원본 재읽기로 다시 주입하지 않는다.
Claude의 `.claude/settings.json` 권한을 Codex 권한으로 자동 변환하지 않는다.
이 접근 검증은 OS나 Codex의 권한 샌드박스를 대체하지 않는다.
[공식 플러그인 구조](https://developers.openai.com/plugins/build/plugins),
[스킬](https://learn.chatgpt.com/docs/build-skills),
[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

사용자 설정을 건드리지 않는 설치 검증은 저장소 루트에서 실행한다:

```text
python -X utf8 scripts/verify_codex_plugin.py
```

CLI가 PATH에 없으면 `--codex <실행 파일 경로>`를 추가한다. Windows에서는 일반 npm
런처의 네이티브 실행 파일을 찾아 사용한다. 사용자 제작 `.cmd`·`.bat` 런처처럼
해석할 수 없는 경우에는 `--codex`에 네이티브 `codex.exe` 경로를 지정한다. 검증기는 임시
`CODEX_HOME`에 이 배포본을 복사·설치하고 app-server의 스킬·훅 목록만 조회한 뒤
임시 디렉터리를 정리한다. 모델 턴을 만들거나 훅을 신뢰·실행하지 않는다.
볼트 엔진의 실제 읽기·검색·복구 동작은 별도 Python 테스트에서 검증한다:

```text
python -m unittest discover -s tests -v
python scripts/evaluate_recall.py
```

실제 모델이 초기화·마감을 수행하는 대화, 신뢰 후 자동 훅 실행, Codex의 출력
축약 동작은 통합 실세션으로 검증하지 않았다. Telegram Jarvis는 계속 Claude CLI를
호출하며 Codex 기반 Telegram 실행기는 포함하지 않는다.
