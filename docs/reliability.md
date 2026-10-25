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
볼트 경계를 지킨다. 링크를 따라 외부 디렉터리를 읽지 않는다. 따라가지 않은 링크·정션은
내용 없이 볼트 상대 경로만 보고서 §14b에 남는다(deny zone·exclude_dirs 안은 제외). 독립 배포 가능한
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

추정 토큰 예산은 비용 예산이고, 호스트 전송 상한(Claude Code 훅 출력 10,000자 —
엔진은 9,500자로 묶는다)은 별도다. Claude Code 2.1.252는 훅 출력이 10,000자(UTF-16
단위)를 넘으면 전체를 파일로 저장하고, 세션에는 그 파일 경로를 알리는 안내와 앞 2,000자
미리보기만 넘긴다. 나머지는 세션 문맥에 들어가지 않는다(저장된 전체 출력을 읽어 생략을
우회하지 않는다). 두 섹션을 합친 출력이 9,500자를 넘으면 각 섹션에 헤더와 짧은 표식 몫을
먼저 주고 남은 글자를 토큰 예산 비율로 나눈다(짧은 섹션이 남긴 몫은 다른 섹션으로 넘긴다).
자르는 위치는 맞는 앞부분의 4분의 3 이상을 남기는 줄 경계, 아니면 단어 경계, 아니면 글자
위치다. 본문을 잃은 제목이 마지막 줄로 남으면, 생략된 제목을 싣는 표시일 때 같은 4분의 3 안에서
한 번만 그 제목을 생략 표시 쪽으로 넘긴다. 두 헤더는 남는다.
노트의 줄바꿈은 LF로 통일해 출력하므로 측정한 길이가 호스트가 받는 길이와 같다.
잘린 섹션 끝의 생략 표시에는 예산·몫에 들어가면 볼트 상대 경로, 생략된 제목(최대 5개),
주제 검색용 `/vault-recall` 안내가 붙고, 들어가지 않으면 짧은 `[... truncated ...]`만 남는다. 이 안내는 주제별 조회용이며 생략분을 다시 읽어 예산을
우회하라는 뜻이 아니다. `/vault-doctor`는 같은 계산으로 `estimated_emitted_chars`와
`host_char_cap`을 보고하고, 이 상한 때문에 더 잘린 섹션을 `host_char_cap` 경고로 표시한다
(노트가 읽기 한도도 넘었으면 `source_byte_limit`이 먼저다). 상한은 고정값이라 예산을 올려도
풀리지 않으므로, 이 경고의 다음 조치는 노트를 줄이는 것이다.

요약을 갱신하는 `/vault-session-end`는 계속 필요하다. 파일이 오래됐거나
잘못 요약되면 저장 장치만으로 의미를 복원할 수 없다. anchor 차이는 조사할
변경 범위를 알려주는 신호이며, 모델의 해석이 올바르다는 증명은 아니다.

Claude Code에서 안전 장치가 메시지를 표시하면 세션이 이전 모델로 전환될 수 있다.
2026-09-22 Anthropic 안내는 Claude 앱에서 이 판정이 파일·검색 결과를 포함한 대화 내용을
본다고 설명한다(Claude Code에서도 같은지는 확인하지 않았다). 이 훅이 주입한 handoff/hot도 대화의 일부이고 `startup`·`clear`·`resume`마다 다시
들어가므로, 표시된 작업을 `/vault-session-end`가 handoff/hot에 요약했다면 새 세션이나
`/clear`로 시작해도 전환이 반복될 수 있다(실제 판정 동작은 확인하지 않았다). 관련 없는
작업에서도 세션마다 전환이 반복되면 `hot_max_tokens`·`handoff_max_tokens`를 잠시 0으로
두고 새 세션을 연 뒤, 첫 메시지에서 `/vault-session-start`와 볼트 노트 읽기를 하지 말라고
지시하고 확인한다(스킬은 주입이 없으면 이 명령으로 복원하며, 이 명령은 index와, hot/handoff가
비면 log 상단도 읽는다). 원인이 주입 내용이면 끝난 작업의 서술은 주입되지
않는 노트로 옮기고 hot/handoff에는 위키링크만 남긴 뒤 예산을 되돌린다. 플러그인은 어떤
문구가 표시되는지 판정하지 않는다.

## 기억 주입 진단 (v0.10.0)

`/vault-doctor` 또는 Codex의 `$agentic-vault:agentic-vault doctor`는 설정·경로·
handoff/hot의 실제 주입 예정 상태를 읽기 전용으로 검사한다. v0.10.0부터 제공한다.

```text
python skills/agentic-vault/scripts/vault_doctor.py --vault "C:/vault" --format json
```

설정 오류, 예산 0, 파일 누락, 빈 파일, 읽기 실패, 안전하지 않은 경로, 잘림,
헤더조차 넣을 수 없는 작은 예산을 구별한다. 원문과 임의의 설정값을 출력하지 않고,
상태·주입 예정량·다음 조치만 반환한다. 한 섹션의 치명 오류 때문에 기존 훅이
전체 출력을 중단한다면 그 사실도 함께 표시한다. 진단은 보고서나 캐시를 쓰지 않는다.

종료 코드 0은 사용 가능하거나 의도적으로 꺼진 상태, 1은 일부 기억이 빠지는 경고,
2는 잘못되거나 사용할 수 없는 설정이다. 비볼트 여부도 JSON 상태로 확인한다.
이 결과는 **파일과 설정의 진단**이다. 호스트가 훅을 신뢰했는지·실제로 실행했는지,
모델이 기억을 제대로 사용했는지까지 검증하지 않는다. 실행 기록은 아래의 선택형
`--session-evidence`(세션 기록으로 SessionStart 실행 확인)로 따로 본다. 정상적인 기존 세션 훅의
출력과 fail-soft 종료 정책은 유지한다.

기존 사용자 소유 문서의 교훈 적용 기록은 [교훈 제안 절차](lesson-proposals.md)를 따른다.

## 호스트 버전과 SessionStart 실측

Claude Code는 2.1.295 이상을 권장한다. 이 플러그인은 `startup|clear|resume`에 동기 훅
`session_start.py`(handoff/hot 주입)와 비동기 훅 `vault_healthcheck.py`(stdout에 한 줄 요약)를
등록하고, `compact`에는 `session_start.py`만 등록한다. [Claude Code 공식 변경 기록](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)의
2.1.295 항목은 "Fixed an async SessionStart hook's unchanged context being added to the
conversation again on every resume"이다(2026-10-19 원문 확인). 그 이전 버전에서는 건강 검사
요약이 resume마다 다시 붙을 수 있으며, 이 저장소에서는 재현하지 않았다. `hooks/hooks.json`은
바꾸지 않았고, 훅 실패 시 동작을 막는 `onFailure: "block"`(같은 버전 추가)도 채택하지 않았다.

### 설치된 버전 확인 (선택)

```text
python skills/agentic-vault/scripts/vault_doctor.py --vault "C:/vault" --host-version --format json
```

`--host-version`을 줄 때만 PATH에서 `claude`를 찾아 `claude --version`을 한 번 실행한다. 인자는
고정이고 셸을 거치지 않는다. 4초 안에 끝나지 않으면 종료시키고 정리에 최대 1초를 더 기다린다.
결과는 `host_version`에 `host`(`claude-code`), `status`(`ok`·`below_recommended`·`unavailable`·`unparsed`), `version`,
`recommended_min`(2.1.295), `reason_code`, `next_action`, `trust_boundary`로 붙는다. 옵션이 없으면 출력은 이전과
바이트 단위로 같고, 옵션이 있어도 종료 코드는 볼트 진단의 값 그대로다.

- `unavailable`: PATH에 없음(`executable_not_found`), 실행 실패·시간 초과·0이 아닌 종료 코드,
  현재 폴더에서만 찾은 실행 파일(`current_directory_executable_refused`), Windows에서 `.exe`가 아닌
  런처(npm의 `.cmd` 등, `windows_non_exe_launcher_not_run`), 그 밖의 예상하지 못한 실행 오류(`probe_failed`)다. 실행하지 않은 경우는 터미널에서
  `claude --version`을 직접 실행한다.
- `unparsed`: 출력 첫 줄이 `x.y.z`(뒤에 공백과 설명 허용)가 아니다. 출력 원문은 보고하지 않는다.
- 결과는 PATH에서 찾은 실행 파일의 버전이다. 지금 훅을 실행하는 호스트(IDE 확장, 다른 설치본)와
  같다는 보장은 없다(`trust_boundary`). Codex 버전은 확인하지 않는다.

### 세션 기록으로 SessionStart 실행 확인 (선택)

v0.31.0의 선택형 기능이다. 지정한 Claude Code 세션 기록 파일 하나에서 SessionStart 훅이 실행·잘림·실패로
기록됐는지 본다.

```text
python skills/agentic-vault/scripts/vault_doctor.py --vault "C:/vault" --session-evidence "<세션 기록 .jsonl 파일>" --format json
```

Claude Code는 세션 기록을 보통 `~/.claude/projects/<프로젝트 경로로 만든 폴더 이름>/<세션 ID>.jsonl`에 쓴다.
폴더 이름은 프로젝트 폴더 이름이 아니라 전체 경로에서 영문자·숫자가 아닌 글자를 모두 `-`로 바꾼 것이다(예:
`C:\work\vault` → `C--work-vault`, 2.1.309 실행 파일 기준). 바꾼 이름이 200자를 넘으면 앞 200자 뒤에 `-`와
해시가 붙으므로, 그때는 `~/.claude/projects`의 폴더 가운데 바꾼 이름의 앞 200자와 `-`로 시작하는 폴더를 고른다.
이 형식은 호스트 내부 형식이고 문서화되지 않았다(`trust_boundary`: `host_internal_format_undocumented`). 판마다 바뀔 수
있으므로 관측한 기록 모양만 해석하고, 모양이 다른 기록은 추측하지 않고 센다.

`--session-evidence`를 줄 때만 지정한 세션 기록 파일 **하나**를 읽는다. 경로가 `~`로 시작하면 셸이 바꾸지 않고
넘겨도 홈 폴더(Windows는 `USERPROFILE`)로 바꿔 읽으므로 위 위치를 그대로 쓸 수 있다. 그 밖에 읽는 파일은 이
플러그인 설치본의 `hooks/hooks.json` 하나뿐이다(상태 문구 확인용, 64 KiB까지, 아래 **훅 명령 값**). 폴더를 훑어
기록을 고르지 않고, 아무것도 쓰지 않고, 네트워크·모델을 부르지 않는다. 결과는 `session_evidence`에 붙고 종료 코드는 볼트
진단의 값 그대로다. 옵션이 없으면 읽기 모듈(`vault_session_evidence.py`)을 불러오지도 않으며 출력은 이전과 같다.

- **읽는 범위:** 한 줄씩 읽고 한 줄 16 MiB, 파일 512 MiB까지 본다. 종류 표지(`"type":"attachment"`·
  `"assistant"`·`"system"`)가 있는 줄만 JSON으로 풀고, 사용자 입력·요청 원문·파일 이력 줄은 풀지 않는다(그 안의
  객체에 같은 표지가 있으면 풀었다가 최상위 종류를 보고 버린다).
  한 줄 상한을 넘은 줄은 세기만 한다(`lines.oversized`). 그 줄에 종류 표지가 있었거나, 깨진 줄이 있거나, 파일
  상한에서 멈추면 `complete`는 `false`다. 진행 중인 세션의 마지막 줄은 아직 쓰는 중일 수 있다.
- **남기는 값(허용 목록):**
  - SessionStart `hook_success` 기록마다 `source`(`startup`·`resume`·`clear`·`compact`·`fork`·`other`),
    `exit_code`, `duration_ms`, `emitted_chars`(훅 stdout 길이), `injected_chars`(호스트가 대화에 넣은
    `content` 길이), `truncated`, `command_mentions_session_start_py`(아래 **훅 명령 값**). 길이는
    `estimated_emitted_chars`와 같은 UTF-16 단위다. 목록(`entries`)은 50건까지 싣고 수(`records`)는 모두 센다.
  - 성공하지 못한 SessionStart 훅 기록(`hook_non_blocking_error`·`hook_blocking_error`·`hook_cancelled`·
    `hook_error_during_execution`)마다 `source`, `outcome`(`non_blocking_error`·`blocking_error`·`cancelled`·
    `error_during_execution` 가운데 하나), `exit_code`, `duration_ms`, `command_mentions_session_start_py`만
    남긴다. 마지막 값은 성공 기록과 같은 방식으로 정한다(`hook_blocking_error`는 `blockingError` 안의 명령을 본다).
    다른 훅의 실패도 같은 목록에 온다. stdout·stderr·명령·내용은 출력하지 않는다. 수는 `failed`, 목록은
    `failed_entries`(50건까지)다. 2.1.309 실행 파일에서는 0이 아닌 종료 코드가 `hook_non_blocking_error`이고(종료
    코드 2의 차단 오류도 이 모양으로 바뀐다), 시간 초과·취소가 `hook_cancelled`(종료 코드 없음), 훅을 실행하기
    전이나 실행하는 중의 호스트 오류가 `hook_error_during_execution`이다. 이 모양은 실행 파일에서 확인했고 실제
    세션 기록에서 관측한 것은 아니다.
  - 지침 파일 첨부(`instructions`)마다 파일 수, 빠진 파일 수(`removed_files`), `reason`(`session_start`·
    `compaction`·`other`, 없으면 `none`), `changed`. 목록(`entries`)은 50건까지 싣고 수(`records`)는 모두
    센다. 경로 필드는 아래 **개인정보**에 있다.
  - `first_turn_usage`: 파일의 첫 API 응답의 `input_tokens`·`cache_read_input_tokens`·
    `cache_creation_input_tokens`와 그 합(`prompt_tokens_total`). 시스템 프롬프트·도구·지침·훅 출력을 모두 더한
    프롬프트 전체라 훅의 몫이 아니다(`label`: `whole_prompt_not_attributable_to_hooks`). 한 응답이 내용
    블록마다 한 줄씩 기록되므로 (`message.id`, `requestId`)가 같은 줄은 한 번만 센다(`duplicate_lines`).
    서브에이전트·합성 메시지·오류 메시지 줄은 건너뛴다.
  - `api_errors`: `api_error` 기록 수와 HTTP 상태 코드별 수. `host_versions`: 기록의 `version` 최솟값·최댓값.
- **훅 명령 값(`command_mentions_session_start_py`):** 기록된 `command`가 `session_start.py`라는 파일 이름을
  가리키거나 이 플러그인의 SessionStart 상태 문구(`statusMessage`) 가운데 하나와 같으면 `true`, 그렇지 않으면
  `false`, 명령 문자열이 없으면 `none`이다. Claude Code 2.1.309 실행 파일(2.1.310도 같다)은 command 훅의
  `hook_success`·`hook_non_blocking_error`·`hook_cancelled`·`hook_error_during_execution` 기록의 `command`에 명령
  대신 훅의 표시 문구를 남긴다. 표시 문구는 `statusMessage`가 있으면 그 문구이고, 없을 때만 명령이다. 차단
  오류(종료 코드 2)의 기록만 `${CLAUDE_PLUGIN_ROOT}`를 펼친 명령을 남긴다(SessionStart에서는
  `hook_non_blocking_error`로 바꿔 쓴 기록). 이 플러그인의 SessionStart 훅에는 v0.1.0 이후 모든 릴리스에서 상태
  문구가 있으므로(지금은 `session_start.py` 둘과 비동기 `vault_healthcheck.py`), 파일 이름만 보면 이 플러그인의
  훅 기록도 대부분 `false`가 된다. 그래서 설치본 `hooks/hooks.json`의 SessionStart 상태 문구와
  v0.1.0~v0.30.0 릴리스의 문구(코드에 고정한 세 가지)를, 호스트가 표시 문구를 만들 때처럼 제어 문자를 바꾼 뒤
  비교한다. `hooks/hooks.json`이 없거나 형식이 다르거나 64 KiB를 넘으면 그 파일의 문구는 쓰지 않고 고정한 문구와
  파일 이름만 본다. 바꾸는 방식이 앞뒤 글자나 Unicode 판에 따라 달라지는 문자(서식 문자 등)가 든 문구도 쓰지 않는다.
  문구는 출력하지 않는다. `true`는 보통 이 플러그인의 훅이지만, 같은 이름의 다른 파일이나 같은 문구를 쓰는 다른 훅도
  `true`다. `false`는 이 플러그인의 훅으로 **알아보지 못했다**는 뜻이다. 다른 훅일 수도 있고 이 읽기 모듈이 모르는
  기록 모양일 수도 있으므로, `false`만으로 다른 훅이라고 단정하지 않는다.
- **상태(`status`):** 기록이 섞이면 `observed_error`, `observed_truncated`, `observed_ran` 순으로 앞선 것 하나를
  보고하고, 수는 `failed`·`truncated`·`records`에 모두 센다. 괄호 안은 그 상태의 `reason_code`와 `next_action`이다.
  - `observed_error`(`session_start_hook_failed`, `check_the_failed_session_start_hook`): 성공하지 못한
    SessionStart 훅 기록(위 네 종류)이 하나 이상 있다. 호스트가 그 훅을 실행하려 했다는 기록이며 "실행되지
    않음"도 "기록 없음"도 아니다. 다음 조치는 실패한 훅을 확인하는 것이다. 이 플러그인의 훅이 실행됐어도 다른
    플러그인이나 볼트 설정의 훅이 실패하면 이 상태다. 실패 기록의 `command_mentions_session_start_py`는 `true`면 보통
    이 플러그인의 훅, `false`면 알아보지 못한 훅, `none`이면 명령이 없어 알 수 없다는 뜻이다(위 **훅 명령 값**).
    오류 문구는 출력하지 않으므로 원인은 그 훅 명령을 직접 실행하거나 디버그 로그(아래 절차)로 확인한다. 예를 들어
    이 플러그인의 `run_python_hook.sh`는 Python 3.10 이상을 찾지 못하면 종료 코드 127로 끝난다.
  - `observed_ran`(`session_start_hook_recorded`, `none`): SessionStart 훅이 실행된 기록(`hook_success`)이 있다.
  - `observed_truncated`(`session_start_output_truncated`, `reduce_the_session_start_output_below_the_host_cap`): 그
    가운데 하나 이상이 호스트 상한(10,000자)을 넘어 잘렸다. 그 훅의 출력을 줄이는 것이 다음 조치다. 이 플러그인의
    `session_start.py`는 출력을 9,500자로 묶는다. 잘린 기록이 어느 훅인지도 `command_mentions_session_start_py`로
    보되, `false`는 알아보지 못했다는 뜻일 뿐이다.
  - `not_recorded`(`no_session_start_hook_record`, `measure_with_the_debug_log_if_needed`): 훅 기록(성공·실패)이
    없다. **실행되지 않았다는 뜻이 아니다.** 비동기 훅, SDK 세션, 기록을 남기지 않는 판에서는 기록이 없다.
  - `unsupported_host_version`(`host_version_seen_without_hook_records`,
    `measure_with_the_debug_log_or_update_claude_code`): 훅 기록이 없고, 파일의 가장 높은 `version`이 기록 없이
    관측된 판(2.1.252) 이하다.
  - `unparsed`(아래 사유, `check_the_named_transcript_file`): 파일을 열 수 없거나(`file_not_found`·
    `not_a_regular_file`·`file_unreadable`), 기록 줄이 없거나(`no_transcript_records`), `version`이 없거나 읽을 수
    없거나(`version_missing`·`version_unparsed`, 이때는 다른 값도 해석하지 않는다), 훅 기록 모양이
    다르거나(`hook_record_shape_unrecognized`), 읽는 중 예상하지 못한 오류가 났다(`probe_failed`). 읽기 모듈을
    불러오지 못한 경우(설치 손상)도 `probe_failed`이며, 이때 `session_evidence`에는 `schema_version`·`status`·
    `reason_code`·`next_action`·`trust_boundary`·`complete`만 있고, 텍스트 출력은 `session_evidence`·
    `session_trust_boundary`·`session_complete`·`session_next_action` 네 줄뿐이다. 예외 문구는 출력하지 않고, 종료 코드는 이때도 볼트 진단의 값 그대로다.
- **판 범위는 관측값이다:** 2026-10-24에 Windows PC 한 대의 기록에서 키 이름·길이·숫자만 보았다. 2.1.252 세션
  173개에는 훅 기록이 없었고, 2.1.278~2.1.309에서는 동기 훅만 기록됐으며, 2.1.253~2.1.277은 관측하지 않았다.
  두 범위는 `observed_ranges`로 함께 보고한다. 훅 기록이 없을 때 상태를 가르는 경계는 기록 없이 관측된 가장 높은
  판(2.1.252) 하나다. 그 이하는 관측하지 않은 더 낮은 판도 `unsupported_host_version`이고, 그보다 높은 판은
  관측하지 않은 2.1.253~2.1.277과 2.1.309보다 새 판도 `not_recorded`다.
- **잘림 판정:** Claude Code 2.1.309 실행 파일은 훅 stdout 앞뒤의 공백·줄바꿈 문자를 지우고(JavaScript `trim`)
  그 길이가 10,000자(UTF-16 단위)를 넘을 때만 출력을 아래 세 안내 가운데 하나로 바꾼다. 그래서 세 안내 모두
  기록의 `stdout`을 같은 방식으로 다듬은 길이가 10,000자를 넘고 `content`가 다듬은 `stdout`과 다를 때만 센다
  (2.1.309가 저장된 훅 출력을 가려낼 때도 `content`가 다듬은 stdout과 다른지 본다). 상한 이하의 훅 출력은 안내
  문구를 인용하거나 안내의 틀을 그대로 담아도 잘림으로 보지 않는다. `stdout`이 없거나 문자열이 아닌
  `hook_success` 기록은 `unrecognized`로 세고 잘림을 판정하지 않는다. 셋 다 `truncated`(참·거짓)로만 보고하고
  안내 문구·크기·오류 문구는 출력하지 않는다. 그 밖의 모양은 판정하지 않으므로 `emitted_chars`와 `injected_chars`의 차이로 본다.
  - 파일에 저장함: `<persisted-output>`, `Output too large (<크기>). Full output saved to: <경로>`, 빈 줄,
    `Preview (first 2KB):`, 출력의 앞부분, `</persisted-output>` 순서다(저장 한도를 넘어 저장본도 잘리면
    `Output too large (` 대신 `Output exceeded the …`가 오는 같은 틀이다). `content`가
    `<persisted-output>`으로 시작하고 `</persisted-output>`으로 끝날 때만 본다. 2.1.309 실행 파일은 이 틀을 늘
    열고 닫으며, 저장된 훅 출력도 이 시작·끝 표지로 가려낸다. 그래서 안내 문구를 인용하기만 한 일반 훅 출력은
    잘림으로 보지 않는다. 저장 경로가 안내 문구와 `Preview (first` 사이에 있어 경로가 길면 `Preview (first`가
    200자 뒤로 밀린다. 그래서 안내 문구는 `content`의 첫 200자에서, `Preview (first`는 첫 4,096자에서 찾는다.
  - 저장하지 않음: `content`가 `<truncated-output>`으로 시작하고 첫 200자에 `Output too large (`가 있다
    (`It could not be saved, so only the first <크기> are shown` 뒤에 출력의 앞부분만 붙는다).
  - 저장 실패: 출력의 앞 10,000자 뒤에 빈 줄과
    `[Hook stdout truncated at 10000 chars — persist-to-disk failed: <오류>]` 한 줄이 이어지고, 그 줄이
    `content`의 맨 끝이다(마지막 4,096자 안에서 찾는다).
- **보이지 않는 것:** 기록은 훅이 실행됐다는 증거이지 모델이 기억을 사용했다는 증거가 아니다. 비동기
  `vault_healthcheck.py`의 출력과 resume 때 다시 붙는지(아래 절차 5)는 이 기록에 나오지 않는다. 그 밖의
  SessionStart 첨부(JSON 훅의 추가 문맥 `hook_additional_context`, 시스템 메시지 `hook_system_message`, 처음 보는
  종류)는 실패로 보지 않고, 해석하지 않은 채 `other_records`로만 센다. JSON으로 출력하는 훅은 `content`가 비어
  있거나 상태 문구이고 추가 문맥은 그 별도 기록에 들어가므로 `injected_chars`가 주입량이 아니다(이 플러그인의
  `session_start.py`는 일반 텍스트로 출력한다). Codex 기록은 읽지 않는다.
- **늦게 실행된 훅의 사본:** 세션이 시작된 뒤 불러온 플러그인(세션 중 설치·활성화 등)의 SessionStart 훅은 늦게
  실행된다. 2.1.309 실행 파일은 이때 `hook_success` 기록의 사본(`content`는 빈 문자열이고 원래 내용은
  `heldContent`에 있다)을 원래 기록보다 먼저 남길 수 있다. 이 읽기 모듈은 사본을 가려내지 않고 보통 기록으로 센다.
  그래서 한 번의 실행이 `records`에 두 번 셀 수 있고, 사본의 `injected_chars`는 0이다. `heldContent`는 읽지 않으므로
  사본만 있으면 원래 출력이 잘렸어도 잘림으로 판정하지 않는다. 이 모양은 실행 파일 문자열에서 확인했고 실제 세션
  기록에서 관측하지 않았다.
- **개인정보:** 세션 기록에는 입력·답변·도구 결과·훅 출력이 모두 들어 있다. 내용·stdout·stderr·명령(명령
  자리에 기록된 상태 문구 포함)·사용자 입력·식별자·예외 문구·기록 파일 경로를 출력하지 않는다. 지침 파일 경로는
  계정 이름이 들어갈 수 있어 `--session-instruction-paths`를 함께 줄 때만 지침 기록마다 `paths`(파일 경로)와
  `removed_paths`(빠진 파일 경로)로 보인다. 두 목록은 기록마다 각각 50개까지 싣는다(`limits.paths_per_record`).
  50개를 넘는 경로와
  문자열이 아니거나 비었거나 1,024자를 넘는 경로, 출력한 줄을 끊거나 글자 순서를 바꿔 보이게 할 수 있는 문자가 든
  경로는 싣지 않고 `paths_omitted`에 센다. 그런 문자는 Unicode 일반 범주가 제어 문자(Cc, C0·C1)·서식 문자(Cf, 양방향 제어·폭
  없는 문자 등)·서로게이트(Cs)·줄 구분자(Zl, U+2028)·문단 구분자(Zp, U+2029)인 문자이고, 범주는 실행하는 Python의
  Unicode 데이터를 따른다. `str.splitlines()`가 줄을 나누는 문자는 모두 여기에 든다. 줄 구분자가 든 경로 하나가
  텍스트 출력에 `session_next_action: none` 같은 가짜 상태 줄을 만들 수 있기 때문이다.
- **출처:** 사용량 필드와 (`message.id`, `requestId`) 중복 제거 규칙은
  [ccusage](https://github.com/ryoppippi/ccusage)(MIT)의 Claude 사용량 파서에서 방식만 빌렸다. 코드와 의존성은
  가져오지 않았다(2026-10-24 기준 main은 Rust). 기록 모양은 위 관측에서, 실패 기록 모양·잘림 안내 문구·늦게
  실행된 훅의 사본은 2.1.309 실행 파일의 문자열에서, 기록된 `command`가 표시 문구라는 것과 표시 문구를 만드는
  방식은 2.1.309·2.1.310 실행 파일의 문자열에서 확인했다.

### SessionStart 훅 실측 절차 (아직 실행하지 않음)

> 이 절차는 작성만 했고 아직 실행하지 않았다. 이 절차의 측정값(훅 소요 시간·resume 재주입)은 아직 없다.
> 실행하면 결과를 날짜·버전과 함께 [검증 기록](verification/)에 따로 남긴다.

훅별 소요 시간은 Claude Code 2.1.296 이상의 디버그 로그에 남는다. 같은 변경 기록의 2.1.296 항목:
"Improved `--debug` output for hooks: command hooks on tool calls, prompts, SessionStart and
Stop now log their command, plugin, outcome and duration when they finish, so a slow hook is
identifiable". `--debug-file <path>`는 지정한 경로에 디버그 로그를 쓰고 디버그 모드를 켠다
(2026-10-19 로컬 `claude --help` 2.1.252의 설명).

1. 시험용 볼트(실제 볼트면 git 작업 트리가 깨끗한 상태)에서 `claude --version`과 OS를 기록한다.
2. 볼트 디렉터리에서 `claude --debug-file <로그1>`로 새 세션을 열고, 짧은 메시지 하나에 답을
   받은 뒤 종료한다(resume할 대화를 남기기 위해서다).
3. 로그에서 `session_start.py`와 `vault_healthcheck.py`가 든 훅 완료 줄을 찾아 결과(outcome)와
   소요 시간을 옮긴다. 줄 형식은 확인하지 않았으므로 로그의 단위 그대로 기록한다. 동기 훅
   `session_start.py`는 같은 세션의 기록 파일에 `--session-evidence`를 써서 `exit_code`·`duration_ms`로도
   옮길 수 있다(호스트가 기록을 남기는 판에서만).
4. 같은 세션을 `claude --resume <세션 ID> --debug-file <로그2>`(ID 없이 쓰면 선택 화면)로 다시
   열어 3을 반복한다. resume은 두 번 이상 한다. `--fork-session`은 쓰지 않는다. 2.1.214부터 fork로
   시작한 세션의 SessionStart source는 `fork`이며(변경 기록) 이 플러그인의 matcher에 없다.
5. resume마다 `--verbose` 또는 transcript 모드에서 `[vault-healthcheck]` 요약이 다시 붙었는지
   센다. 2.1.75부터 비동기 훅 완료 메시지는 기본으로 숨겨지고 두 모드에서 보인다(변경 기록).
   화면 표시가 대화 문맥 포함과 같은지는 확인하지 않았다.
6. 가능하면 2.1.295 미만과 이상에서 같은 절차를 반복해 비교한다. 2.1.296 미만 로그에는 훅
   소요 시간 줄이 없을 수 있다.

| 기록 항목 | 내용 |
|---|---|
| 일시·실행자 | KST 일시, 측정한 사람 또는 세션 |
| 호스트 | `claude --version`, 설치 방식(네이티브·npm 등), `--host-version` 결과 |
| 환경 | OS·버전, Python 버전, Windows의 Git Bash 버전, 플러그인 버전·커밋 |
| 볼트 규모 | 건강 검사 요약의 검사 노트 수, handoff/hot 예산 |
| 훅 소요 시간 | startup·resume별 `session_start.py`·`vault_healthcheck.py`의 결과와 소요 시간. 동기 `session_start.py`는 세션 기록(`--session-evidence`, 기록을 남기는 판 — Windows PC 한 대의 관측 2.1.278~2.1.309)이나 디버그 로그에서, 비동기 `vault_healthcheck.py`는 디버그 로그에서만 |
| resume 재주입 | resume 횟수 N, 그 뒤 `[vault-healthcheck]` 요약이 다시 보인 횟수 |

디버그 로그에는 경로·명령·설정이 남을 수 있다. 원문 로그는 저장소에 올리지 않고, 공유할 값만
옮긴다.

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
