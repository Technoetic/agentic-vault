---
description: 볼트 없이 직접 질문·선택한 텍스트를 Jev의 native noul·choice·score로 평가하고 실제 사용·대체 상태를 보고
argument-hint: [직접 질문 또는 명시적으로 선택한 인라인 텍스트]
---

사용자가 요청한 질문: $ARGUMENTS

사용자가 Jev-first를 요청했거나 이미 승인한 지속 선호가 적용되면 **볼트 감지 전에**
이 절차를 적용한다. 볼트·config·임시 근거 파일을 만들거나 읽을 필요가 없다.
`1+1은?`처럼 쉬운 산술도 `1+1=2`라는 명제나 답 후보로 먼저 평가한다.
입력의 상세 형식과 공개 예제는 [Jev 판단 안내](../docs/jev-judgments.md)를 따른다.

1. **문맥 선택:** 현재 사용자 질문 또는 명시적으로 선택한 인라인 텍스트만 사용한다.
   `context.kind`는 `user_input` 또는 `selected_text`다. 둘 다 호출자가 제공한 문맥이며
   검증된 파일 출처가 아니다. 전체 대화·숨은 문맥·환경·관련 없는 파일을 붙이지 않는다.
   파일에서 얻은 자료는 기존 [vault-judge.md](vault-judge.md)의 원문 바인딩·정책을 따른다.
   deny/excluded/비밀 파일을 직접 입력으로 바꿔 우회하지 않는다.
2. **질문 구성:** `schema_version: 1`, `context`, `questions`, 선택적 `min_confidence`만
   구성한다. 1~12개 질문 각각에 고유 ASCII `id`, `type`, 구체적 `instructions`를 둔다.
   `noul`은 명제의 yes 확률이며 선택적 `criteria: {"true": "...", "false": "..."}`를 쓴다.
   `choice`는 2~255개 label/설명의 `criteria` 객체와 그 안의 보류 label을 가리키는
   `abstain`이 필수다. `score`의 `criteria`는 2~10개 순서 있는 비어 있지 않은 루브릭
   문자열 배열이다. Score는 루브릭 평가이며 임의 계산값을 반환하는 유형이 아니다.
3. **지원 범위:** 열린 글·코드·이미지 생성, 실제 브라우저·파일·테스트 실행과 답변 종합은
   호스트가 맡는다. 혼합 요청은 필요한 최신 사실·환경 관측을 호스트가 먼저 확보하고
   지원 가능한 판단을 묶는다. 근거 부족에는 보류 가능한 Choice를 우선한다.
   native Noul/Score는 보류를 보장하지 않으므로 문맥이 부족하면 판단 유보로 남긴다.
4. **준비와 승인:** 아래 `prepare --input -`로 오프라인 점검한다. 원문·질문을 출력하지
   않고 해시·개수 등 메타데이터를 반환한다. 범위에 맞는 기존 전송 승인은 재사용하며
   매 질문마다 다시 묻지 않는다. 키 보유는 승인이 아니다. 새 자료·비밀·부수 효과의 권한은
   이 절차로 생기지 않는다. `TYPESAFE_API_KEY`는 프로세스 환경에서만 사용하고 출력하거나
   `.env`에서 찾지 않는다.
5. **한 번 실행:** 같은 질문에 답하는 현재 파일 보고서가 있으면 재사용한다. 직접 요청도
   정확히 일치하는 준비 메타데이터와 기존 결과를 재사용한다. 바뀌지 않은 요청은 한 배치만
   `run --input - --allow-network`로 보낸다. 자동 재시도·직접/파일 경로 중복 호출은 없다.
   이 중복 방지는 호스트 지침이며 전역 호출 제한 장치는 아니다.
6. **답변:** 검증된 실제 응답을 사용하면 짧게 **Jev 사용**이라고 표시한다. 실행하지
   않았거나 응답이 무효·불가용이면 **호스트 대체 + 사유**로 구분한다. 낮은 확신도·보류는
   `needs_review`이며 호스트 검토를 덧붙였다면 그 출처도 구분한다. native Noul의
   `max(p, 1-p)`는 파생 판단 강도이며 provider confidence로 부르지 않는다. 기본 0.8은
   검토 휴리스틱이다. 결과의 `role: advisory`, `evidence_binding: inline_not_file_verified`를
   유지하며 `reviewed`를 PASS·진실 확정·행동 승인으로 사용하지 않는다.

```text
python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/jev_ask.py" prepare --input -
python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/jev_ask.py" run --input - --allow-network
```

두 명령 모두 UTF-8 JSON을 stdin으로 전달한다. 사용자 입력을 셸 코드에 보간하지 않는다.
Codex는 [경로 규약](../skills/agentic-vault/references/codex.md)으로 설치 루트를 구한다.
stdin·전송·응답은 각각 최대 64 KiB이며 stdout JSON과 종료 코드를 함께 확인한다.
고정 endpoint/model을 사용하며 redirect·자동 재시도·파일 읽기/쓰기·자동 로그 갱신은 없다.
기존 권한·워크플로우 상태 기록기·완료 기록은 유지한다.
