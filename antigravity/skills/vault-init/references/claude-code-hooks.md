# vault-init: Claude Code 전용 선택 훅(4-2·4-3)

> 원본은 저장소 `commands/vault-init.md`의 4-2·4-3절이고, Claude Code 전용 선택 설치라 `scripts/build_antigravity_package.py`가 이 파일로 옮겼다. 이 파일을 직접 고치지 않는다. `<PLUGIN_ROOT>`는 이 플러그인 설치 폴더의 절대 경로로, 이 파일이 있는 폴더의 세 단계 위 폴더다.

## 4-2. deny zone 가드 (선택 — Claude Code 전용, 따로 승인한 경우에만)

선택 기능이다. Read·Grep·Glob·Edit·Write·NotebookEdit·Bash·PowerShell·Monitor 호출이 deny zone을 가리키면 PreToolUse 훅이 `deny`로 막거나 `ask`로 사용자 확인을 받게 하는 가드 템플릿을 설치한다. `allow`는 내지 않으므로 권한을 넓히지 않는다. 가드이지 샌드박스가 아니며 파일 시스템 권한을 바꾸지 않는다. 기본으로는 설치하지 않는다.

- **Codex에서는 이 단계를 생략한다.** Claude Code 전용 템플릿이다(Codex는 훅 모델이 다르다).
- `python "<PLUGIN_ROOT>/skills/agentic-vault/scripts/vault_adapters.py" --vault . --format json --offer-deny-guard`를 실행해 `adapters.deny_guard`를 읽는다. 읽기 전용 진단이며 아무것도 설치하지 않는다. `--offer-deny-guard`는 이 단계에서만 붙인다(`/vault-doctor`·`/vault-upgrade`는 가드를 제안하지 않는다).
- `actions`에 `offer_deny_guard`가 없으면 묻지 않는다. `state`가 `absent`가 아니면 이미 일부가 있으므로 상태만 한 줄로 보고하고, `hook.unreadable`에 `.claude/settings.local.json`이 있으면(읽을 수 없거나 링크·정션을 거침) 병합할 수 없다고 보고한다.
- 있으면 4-1의 Aside 질문과 **따로** 묻는다: "deny zone 가드를 켤까요? Read·Grep·Glob·Edit·Write·NotebookEdit·Bash·PowerShell·Monitor 호출이 deny zone을 가리키면 막거나(deny) 확인을 받습니다(ask). 가드 스크립트 두 개를 `00-meta/scripts/`에 복사하고 이 기기의 `.claude/settings.local.json`에 훅을 병합합니다. 대상 도구를 부를 때마다 Python 시작 시간(약 0.5초)이 더 들고, 샌드박스가 아니어서 실행된 프로그램이 스스로 여는 파일 같은 경로는 막지 못합니다." 거부하거나 답이 없으면 아무것도 복사·병합하지 않는다.
- 승인하면 아래 순서로 진행한다. 원본은 `<PLUGIN_ROOT>/assets/adapters/deny-guard/`에 있고, 판정표·한계·끄는 법은 같은 폴더의 `guide.md`에 있다.
  1. **python 확인:** 훅은 PATH의 `python`을 셸 없이 실행한다. `python -c "import sys; print(sys.version)"`이 3.10 이상을 출력하는지 확인한다. 실패하거나 낮으면 병합하지 말고 `guide.md` 「켜는 법」 1의 대안(python.exe 절대 경로, `py -3`)을 보여 주고 어느 쪽으로 바꿀지 다시 묻는다. Claude Code v2.1.295 이상에서는 훅이 `python`을 시작하지 못하면 대상 도구 호출이 모두 막힌다.
  2. **스크립트 복사:** `deny-guard.py`와 `ps-parse.ps1`을 `00-meta/scripts/deny-guard.py`·`00-meta/scripts/ps-parse.ps1`로 바이트 그대로 함께 복사한다(폴더가 없으면 만든다). 첫 줄의 `agentic-vault:adapter deny-guard engine=`·`agentic-vault:adapter ps-parse engine=` 스탬프를 지우지 않는다.
  3. **훅 병합:** `settings-hooks.json`을 보여 주고 JSON을 파싱해 `.claude/settings.local.json`의 `hooks.PreToolUse` 배열에 이 그룹을 추가한다(파일이 없으면 이 내용으로 만든다). 기존 키와 훅은 모두 보존하고, `deny-guard.py`를 부르는 훅이 이미 있으면 건너뛴다. 공유 `.claude/settings.json`에는 넣지 않는다(python 경로가 기기마다 다르다). `matcher`·`"onFailure": "block"`·`${CLAUDE_PROJECT_DIR}` 경로를 고치지 말고 `if` 필터를 더하지 마라. 1에서 실행기를 바꾸기로 했으면 그것만 `command`·`args`에 반영한다. 폴더 동기화를 쓰는 볼트는 local 파일도 다른 기기로 퍼진다는 점을 알린다.
  4. **확인:** 같은 진단을 `--offer-deny-guard` 없이 다시 실행해 `deny_guard.state=installed`, `guard.state`·`parser.state`=`current`, `hook.state=configured`를 확인한다. 끄는 법(`.claude/settings.local.json`에서 `deny-guard.py`를 부르는 그룹을 지운다)과, Claude Code v2.1.295 미만에서는 가드가 충돌하거나 시간을 넘기면 호출이 그대로 진행된다는 점(fail-open)을 한 줄씩 알린다. 훅 반영을 위해 세션 재시작을 권한다(9단계).
- 이 세션에 훅이 곧바로 적용되면 이후 단계(5단계 권한 병합, 6단계 초기 커밋에서 구역 폴더의 `.gitkeep`을 이름으로 스테이징하는 명령)가 `ask`·`deny`를 받을 수 있다. 우회하지 말고 결과를 그대로 보고한다.
- 가드를 켜면 엔진 작업 흐름 세 곳이 달라진다는 점을 한 줄로 알린다: `/vault-process-inbox`의 격리 이동과 `/vault-lint`의 노화 문서 아카이브 이동은 PowerShell `Move-Item`이 막히므로 Bash 도구의 `mv -n`(이미 추적 중인 파일은 `git mv`)으로 하고, 구역으로 옮긴 파일을 이름으로 스테이징하는 `git add`(`/vault-session-end` 7단계)는 `deny`이므로 `git mv`로 옮기거나 구역 밖 상위 폴더(`git add 10-inbox`)를 스테이징한다. 자세한 것은 `guide.md` 「한계」에 있다.

## 4-3. 교훈 단서 훅 (선택 — Claude Code 전용, 따로 승인한 경우에만)

선택 기능이다. 교훈 대장의 표지에 적은 단서(`cues`)가 도구 호출과 맞으면 PreToolUse 훅이 hint는 사실문으로 `additionalContext`에 넣고, guard는 세션당 1회 `deny`로 낸다. `allow`는 내지 않는다. `onFailure`를 쓰지 않으므로 훅이 실패하면 호출은 그대로 진행된다. 권고이며 차단 보장이 아니다. 기본으로는 설치하지 않는다.

- **Codex에서는 이 단계를 생략한다.** Claude Code 전용 템플릿이다.
- `python "<PLUGIN_ROOT>/skills/agentic-vault/scripts/vault_adapters.py" --vault . --format json --offer-lesson-cues`를 실행해 `adapters.lesson_cues`를 읽는다. 읽기 전용 진단이며 아무것도 설치하지 않는다. `--offer-lesson-cues`는 이 단계에서만 붙인다.
- `actions`에 `offer_lesson_cues`가 없으면 묻지 않는다. 새 볼트의 대장에는 아직 단서가 없으므로 지금 켜도 효과가 없다는 점을 함께 말한다.
- 있으면 앞 단계들과 **따로** 묻는다: "교훈 단서 훅을 켤까요? 대장에 적은 단서가 도구 호출과 맞으면 그 교훈을 호출 옆에 사실문으로 붙이고, guard로 표시한 단서는 세션당 한 번 호출을 막습니다. 훅 스크립트 하나를 `00-meta/scripts/`에 복사하고 이 기기의 `.claude/settings.local.json`에 훅을 병합합니다. 대상 도구를 부를 때마다 Python 시작 시간이 더 들고, 힌트는 이미 낸 호출을 바꾸지 못하며 효과는 측정되지 않았습니다." 거부하거나 답이 없으면 아무것도 복사·병합하지 않는다.
- 승인하면 아래 순서로 진행한다. 원본은 `<PLUGIN_ROOT>/assets/adapters/lesson-cues/`에 있고, 단서 형식·한계·끄는 법은 같은 폴더의 `guide.md`에 있다.
  1. **스크립트 복사:** `lesson-cues.py`를 `00-meta/scripts/lesson-cues.py`로 바이트 그대로 복사한다. 첫 줄의 `agentic-vault:adapter lesson-cues engine=` 스탬프를 지우지 않는다.
  2. **훅 병합:** `settings-hooks.json`을 보여 주고 JSON을 파싱해 `.claude/settings.local.json`의 `hooks.PreToolUse` 배열에 이 그룹을 추가한다. 기존 키와 훅은 모두 보존하고, `lesson-cues.py`를 부르는 훅이 이미 있으면 건너뛴다. `"onFailure": "block"`을 더하지 마라.
  3. **목록 컴파일:** 대장에 단서가 있으면 `python "<PLUGIN_ROOT>/skills/agentic-vault/scripts/vault_lesson_cues.py" --vault . build --write`로 `00-meta/lesson-cues.json`을 만든다. 대장을 고친 뒤에는 다시 실행해야 훅에 반영된다.
  4. **확인:** 같은 진단을 `--offer-lesson-cues` 없이 다시 실행해 `lesson_cues.state=installed`, `script.state=current`, `hook.state=configured`를 확인한다. 끄는 법(`.claude/settings.local.json`에서 `lesson-cues.py`를 부르는 그룹을 지운다)을 한 줄로 알린다.
