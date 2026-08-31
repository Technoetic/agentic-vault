# Jarvis v0.8.2 보안·내구성 보강 설계

날짜: 2026-08-31 / 상태: 사용자 검토 대기

## 1. 목적

현재 `master`의 복수 브리핑 커밋 `11aa293`을 공개 가능한 상태로 완결하면서, 감사에서 확인된 두 P0 문제인 그룹 채팅 정보 노출과 Telegram 업데이트·캡처 유실 가능성을 제거한다. Python 3.10+ 표준 라이브러리만 사용하며 기존 단일 `briefing_time` 설정은 계속 동작해야 한다.

## 2. 범위

- Telegram 메시지는 `chat.type == "private"`이고 `chat.id == from.id`이며 발신자가 화이트리스트에 있을 때만 볼트 응답을 생성한다.
- 비공개 채팅이 아닌 업데이트와 미허용 발신자는 응답하지 않고 로그만 남긴 뒤 처리 완료로 간주한다.
- 캡처 파일명은 Telegram `update_id`를 포함해 같은 초에 여러 메시지가 와도 충돌하지 않는다. 본문의 수신 시각은 재시도 때의 현재 시각이 아니라 Telegram message `date`에서 계산해 동일 업데이트 재처리가 같은 파일·내용으로 수렴해야 한다.
- offset은 업데이트 처리가 성공한 뒤에만 저장한다. 상태 쓰기는 같은 디렉터리의 임시 파일을 닫기 전에 `flush`·`fsync`한 뒤 `replace`하는 방식으로 수행한다.
- 상태 파일은 `vault.resolve(strict=False)`를 Windows에서는 `normcase`한 경로 해시와 Telegram token의 안정적인 숫자 bot ID로 네임스페이스를 나눠 서로 다른 볼트·봇이 offset과 집사 상태를 공유하지 않는다. 숫자 bot ID를 해석할 수 없는 토큰은 설정 오류다. 로그 파일은 기존처럼 전역 `~/.vault-jarvis/jarvis.log`에 둔다.
- 기존 전역 상태 파일은 `legacy-owner.json`을 원자적으로 선점한 첫 볼트·bot 조합만 새 네임스페이스로 이동한다. 이미 다른 조합이 선점했거나 소유권을 증명할 수 없으면 자동 이동하지 않고 경고한다.
- `tg_send`와 업데이트 처리 함수는 성공 여부를 반환한다. 응답 전송이 실패하면 offset을 전진시키지 않고 해당 업데이트부터 재시도한다.
- `briefing_times`는 `HH:MM` 문자열 배열이며 중복 제거 후 시간순 정렬한다. 키가 없을 때만 기존 `briefing_time` 문자열을 사용한다.
- 잘못된 타입, 빈 배열, 범위를 벗어난 시·분은 설명 가능한 설정 오류로 종료한다. Python traceback만 남기지 않는다.
- 브리핑 생성 프롬프트는 “아침”으로 고정하지 않고 “정기 브리핑”으로 표현한다. Telegram 메시지 표면의 아침·점심·저녁 라벨은 유지한다.

## 3. 구조

`jarvis_bridge.py` 안에 다음 순수 경계를 둔다.

- `parse_briefing_slots(block) -> list[tuple[int, int]]`: 복수/단일 설정을 검증·정규화한다.
- `state_dir_for(vault, token) -> Path`: 비밀 토큰 전체를 경로나 로그에 기록하지 않고 볼트·bot별 상태 경로를 계산한다.
- `atomic_write_text(path, text)`: offset과 집사 상태를 원자 저장한다.
- `is_authorized_private_message(message, whitelist) -> bool`: private chat과 발신자·채팅 ID 일치를 함께 강제한다.
- `process_update(...) -> bool`: 하나의 업데이트를 처리하고 재시도 가능 여부를 반환한다.

네트워크 루프는 위 함수들의 결과만 조합한다. 순수 설정·인가·파일명·offset 정책은 네트워크 없이 단위 테스트할 수 있어야 한다.

## 4. 오류와 재시도

- Telegram 전송 실패: `False` 반환, offset 유지, 이후 업데이트 처리를 중단하고 다음 poll에서 재시도한다.
- 여러 조각 중 일부만 전송된 뒤 실패하면 재시도 과정에서 이미 전달된 조각이 중복될 수는 있지만, offset을 먼저 전진시켜 답변 전체를 잃는 것보다 내구성을 우선한다.
- 응답 전송 성공 뒤 offset 저장만 실패해도 다음 poll에서 같은 응답이 중복될 수 있다. 이 전달 방식은 손실보다 중복을 택하는 at-least-once다.
- 캡처 저장 성공 후 확인 메시지 실패: 같은 `update_id` 파일을 재사용하므로 재시도해도 캡처가 중복되지 않는다.
- 미허용/그룹/텍스트 없는 업데이트: 의도적으로 폐기하고 `True`를 반환해 큐가 막히지 않게 한다.
- 잘못된 설정: 시작 단계에서 로그 한 줄과 non-zero exit. 부분 활성화하지 않는다. 비밀 토큰 원문은 오류·경로·로그 어디에도 출력하지 않는다.
- 손상된 offset은 0으로 되돌려 중복 폭주를 만들지 않고 설명 가능한 오류로 시작을 중단한다.
- 정기 브리핑과 집사 상태는 Telegram 전송 성공 뒤에만 완료로 기록한다. 실패하면 다음 루프에서 재시도한다.
- Q&A 시도 횟수는 `update_id`별로 한 번만 rate-limit에 반영해 동일 업데이트 재시도가 할당량을 반복 소비하지 않게 한다.
- Q&A의 deny-zone 제한은 프롬프트·도구 정책이지 OS 수준 보안 경계가 아님을 README와 설정 문서에 명시한다.

## 5. 테스트와 문서

`unittest` 기반 `tests/test_jarvis_bridge.py`를 추가한다. private/group 판정, 같은 초 캡처 충돌, 동일 update 재처리, 처리 실패 시 offset 유지, 원자 상태 저장과 replace 전 실패, 볼트·bot 네임스페이스, legacy 소유권·부분 이전 재개, 손상 offset 중단, 단일 설정 하위호환, 복수 슬롯 정렬·중복 제거, 잘못된 시각, 날짜 전환·전송 실패 재시도를 네트워크 없이 검증한다. 기존 `--self-test`도 새 파서와 경계 함수를 사용한다.

`assets/templates/vault-config.json`, `commands/vault-jarvis-setup.md`, README와 기존 Jarvis 설계 설명을 `briefing_times` 우선·`briefing_time` 폴백으로 맞춘다.

## 6. 비목표

- Telegram webhook 전환, 외부 DB·큐 도입
- Q&A 결과 영속 캐시
- OS 샌드박스나 별도 읽기 전용 볼트 미러
- backup·healthcheck 경로 안전성 보강(후속 v0.9.0 범위)
