# 첫 인계 실습 · Your first handoff

가상의 도서관 서가 라벨 프로젝트를 이어받아 다음 행동과 결정의 근거를 찾는 10분 목표 실습입니다. 준비물은 이 저장소의 소스와 Python 3.10 이상입니다. 저장소 루트에서 실행하세요. Python 명령 이름이 `python3`이라면 예제의 `python`을 바꾸세요.

Set aside ten minutes to pick up a shelf-label project where the previous session left off. Run these commands from the source checkout with Python 3.10 or later. Use `python3` if that is your interpreter's name. The exercise uses local files and runs without an installed agent, an account or a network connection.

## 1. 인계를 남깁니다 · Leave a handoff

```console
python -B scripts/first_handoff.py
```

명령이 방금 만든 임시 폴더의 위치를 출력합니다. 그 안의 `handoff.md`에는 글자 크기 결정과 다음 행동이, `notes/label-trial.md`에는 결정의 근거가 있습니다. 두 파일을 편집기로 열어 보세요.

Open the printed folder. Read the decision and next action in `handoff.md`, then look at the trial observation in `notes/label-trial.md`. Each run creates a fresh folder. Copy its path for the next two commands.

## 2. 다음 세션을 시작합니다 · Resume in a fresh process

같은 저장소 루트에서 새 터미널을 엽니다. 아래 두 명령의 `DEMO_FOLDER`를 복사한 경로로 바꾸고 양쪽 큰따옴표는 유지하세요.

Open a new terminal at the same checkout. Replace `DEMO_FOLDER` in both commands with the path you copied, keeping the double quotes. Run the session-start hook directly to see the handoff it reads from disk.

```console
python -B hooks/session_start.py --vault "DEMO_FOLDER"
```

출력에서 이 두 줄을 확인하세요. Find these lines in the output.

```text
Decision: Use 14-point type for the shelf labels.
Next: Print one label at actual size.
```

## 3. 결정의 근거를 찾습니다 · Find the evidence

어떤 관찰이 이 결정을 뒷받침했을까요? 검색 결과의 경로와 줄 번호를 노트와 대조해 보세요.

Search for the observation behind the decision, then compare the result with line 3 of the cited note.

```console
python -B skills/agentic-vault/scripts/vault_recall.py --vault "DEMO_FOLDER" --query "reading trial" --limit 1
```

```text
[Source: notes/label-trial.md:3]
Reading trial: all three readers could read 14-point type at arm's length.
```

실습 폴더의 `handoff.md`에서 `Next:` 줄을 고치고 2단계를 다시 실행하면 바뀐 행동이 나타납니다. 마친 뒤 출력된 실습 폴더는 삭제해도 됩니다.

To try your own handoff, edit the `Next:` line in the practice folder and repeat step 2. Your new action should appear. You can remove the printed practice folder when you finish.
