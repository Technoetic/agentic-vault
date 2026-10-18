# 한국어 모델·검색 비교와 Windows 편집 보강

2026-10-18 후속 개발 브랜치의 도구다. 공개 v0.23.0 설치본에 포함됐다는 뜻은 아니다.
기본 세션 훅·모델 호출·런타임 의존성·검색 설정을 추가하지 않는다.

## 요청·설명 언어와 문자열 보존 비교

```text
python scripts/evaluate_tool_matrix.py
python scripts/evaluate_tool_matrix.py --backend local-model --model <local-model-id> --api-key-env <environment-variable-name> --request-language all --instruction-language all --prompt-variant all --condition all --repeats 2 --seed 17 --receipt-out <new-file>
```

첫 명령은 모델을 호출하지 않는 가상 프로토콜 대조군이다. 두 번째 명령만 명시적으로
기존 루프백 전송을 사용한다. endpoint의 기본값은 `http://127.0.0.1:8080/v1/chat/completions`다.
인증값은 지정한 환경변수에서만 읽고 파일·영수증·명령 인자에 복사하지 않는다.

- 요청 언어는 기존 질문과 문서의 언어를 함께 선택한다. 사용자 문장만 바꾸는 실험이 아니다.
- 설명 언어는 시스템 문장과 도구 설명을 함께 바꾼다. 함수명·인자명·업무 식별자는 유지한다.
- `literal-identifiers`는 주소·코드·식별자를 번역·요약하지 않고 복사하라는 지시를 더한다.
  채점기의 정답이나 공격 목표를 모델 입력에 넣지 않는다. `canonical`은 기존 프롬프트다.
- 반복은 1~5회, 최대 80개 에피소드다. 선언한 seed로 순서를 섞고 같은 반복 조건에 같은 seed를 요청한다.
  서버가 seed를 따르는지, 반복들이 독립적인지는 인증하지 않는다.
- 정상 업무 성공, 공격 목표 달성, 모델 오류, 인자 실패를 따로 센다. 오류도 전체 분모에 남긴다.
  공격률의 분모는 공격 문구가 들어간 에피소드이며, 오류 없는 조건의 별도 비율도 제공한다.
- Wilson 구간은 작은 합성 표본을 설명하는 구간이다. 독립 표본이나 업무 전체의 정확도 보장이 아니다.
- 원 응답은 바이트 상한을 검사하고 알려진 인증값을 정제한 뒤 보관한다. 알 수 없는 모든 비밀을
  찾아낸다는 보장은 없다. 모델 오류를 답변 보류나 공격 방어 성공으로 바꾸지 않는다.
- 실행 코드 지문을 시작·종료에 비교한다. 변경되면 원 관측을 보존하되 현재 코드 비교로 사용하지 않는다.
  실제 제공자 사용량과 문자 기반 추정을 분리하고 청구 금액은 알 수 없으면 null로 둔다.

`evaluate_tool_safety.py`의 선택형 `system_language`, `tool_language`, `prompt_variant`, `seed`,
`capture_responses` 인자는 기존 기본 호출에 영향을 주지 않는다. 기존 canonical golden도 유지한다.

## Kiwi·qmd 검색 비교

```text
python scripts/evaluate_retrieval_backends.py --fixture recall_hard
python scripts/evaluate_retrieval_backends.py --fixture recall_hard --kiwi
python scripts/evaluate_retrieval_backends.py --fixture recall_hard --kiwi --qmd-cli <absolute-qmd-dist-cli-qmd.js> --node <node-executable> --qmd-mode search --artifact-dir <external-experiment-directory> --output <report.json>
```

기본은 기존 BM25와 한글 bigram 조건만 비교한다. Kiwi가 필요한 별도 실험 환경에는
`kiwipiepy==0.24.0`, qmd에는 `@tobilu/qmd@2.8.3`을 설치해 이번 관측을 재현할 수 있다.
핵심 엔진의 설치 의존성으로 넣지 않는다. qmd는 셸 문자열 대신 node와 절대 JS 경로로 실행한다.

입력은 저장소에 포함된 `recall`, `recall_advanced`, `recall_hard` fixture만 허용한다.
실제 볼트 경로를 받지 않는다. 저장소에서 fixture까지의 접합점·링크를 검사하고 정책이 허용한
노트만 숫자 ID로 복사한다. qmd 설정·색인·캐시는 실험 디렉터리에 둔다. 상속된 `INDEX_PATH`가
다른 색인으로 보내지 못하도록 격리하고, 반환 URI는 이번 복사본의 정확한 ID에만 연결한다.
입력·정책·관련 코드는 실행 전후 해시로 확인한다. 선택한 모델은 캐시에서 복사할 때 원본과
복사본의 해시를 확인하며 질의 전체의 종료 시점 모델 파일 검증을 제공하지 않는다.
외부 프로그램 자체의 OS 샌드박스는 아니다.

qmd `search`는 키워드 조건이다. 선택형 `query`는 `--allow-model-download`를 함께 명시해야 한다.
이번 벡터 조건은 **CPU, typed lex+vec, no-rerank**다. query expansion·LLM reranker와 전체 qmd 기본
파이프라인을 평가하지 않는다. `--qmd-model-cache <existing-cache>`는 이미 내려받은 공개
embeddinggemma GGUF 하나만 해시를 확인해 복사한다. 전체 모델 묶음을 자동 설치하지 않는다.

top5 경로 순위의 recall·nDCG, 언어/시나리오별 분모, 답 없음 오탐과 호출 오류를 보고한다.
이는 답변 정확도나 시간 유효성 판정이 아니다. Kiwi는 미리 만든 토큰을 사용하고 기존 경로는
매 질의 스캔, qmd는 프로세스 시작이 포함되므로 지연을 같은 생산 환경 속도로 일반화하지 않는다.
색인·모델 준비 비용은 질의 지연과 따로 기록한다.

## Windows 부분 편집

Windows의 원자적 교체가 WinError 5/32로 거부되면 최대 6회, 총 대기 0.31초 범위에서 재시도한다.
매번 lease·설정·대상 revision·관련 출처 해시를 재검사한다. 그 사이 원문이나 정책이 바뀌면
중단하고 덮어쓰지 않는다. 지속적인 잠금이나 다른 오류도 실패로 남기며 정책이 막은 정리를 강행하지 않는다.

Python 3.12의 경로 해석 중 다른 프로세스가 guard를 지우면 남을 수 있는 DOS drive `\\?\` 접두어는
동일한 물리적 root임을 확인한 비교에서만 보강한다. 원래 OS 입출력 경로를 유지한다.
UNC/device 표기를 임의로 같은 경로로 취급하지 않는다. 협조 잠금은 잠금에 참여하는 writer를 위한
것이며 다중 파일 트랜잭션·외부 앱 편집의 완전한 차단을 제공하지 않는다.

## 연구와 관측

- [LongMemEval-V2](https://arxiv.org/abs/2605.12493), [MemoryAgentBench](https://arxiv.org/abs/2507.05257): 상태 변화와 실제 기억 사용을 분리하는 평가 설계 참고.
- [MLCL](https://arxiv.org/abs/2601.05366), [BFCL](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard): 언어별 도구 인자 실패를 가르는 실험 틀. MLCL의 한국어 검증을 재현한 것은 아니다.
- [Kiwi](https://doi.org/10.23287/KJDH.2024.1.1.6), [qmd](https://github.com/tobi/qmd): 선택형 검색 대조군.
- [Git의 Windows rename 수정](https://github.com/git/git/commit/391bceae4350): 열린 핸들 때문에 교체가 거부되는 원인과 제한된 재시도의 참고.

공식 논문의 데이터·코드를 복사하거나 공식 벤치마크 점수를 재현했다고 주장하지 않는다.
이번 실제 실행과 검증 범위는 [후속 검증 기록](verification/eval-stability-20261018.md)에 남긴다.
