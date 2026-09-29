<!-- agentic-vault:adapter aside begin engine=0.17.0 — /vault-init·/vault-upgrade가 사용자 승인 후 넣은 선택 조항. 이 볼트의 브라우저 도구 결정(vault-browser 규칙 1)이며 엔진 소유가 아니므로 자유롭게 고쳐도 된다. 마커 쌍은 중복 추가 방지용이니 유지하라. -->
## 브라우저 도구: Aside CLI

- **도구:** 브라우저로 사이트를 조회·조작·렌더링(스크린샷·PDF)할 때는 Aside CLI(`aside`)만 쓴다. 다른 브라우저·헤드리스 엔진을 폴백으로 띄우지 않는다.
- **두 모드와 데이터 경로:** `aside "자연어 작업"`(= `aside exec`)은 Aside 에이전트가 모델로 페이지를 읽으므로 **페이지 내용이 Aside가 쓰는 클라우드 모델 제공자로 간다** — 공개 웹 조사에만 쓴다. `aside repl "<JS>"`는 Playwright식 JS를 Aside 안에서 실행하지만, `console.log()`로 출력한 내용은 이 명령을 부른 에이전트(Claude Code·Codex)의 모델 제공자로 간다. 이 볼트가 외부 전송 금지로 분류한 페이지는 `repl`로만 열고, 원문을 출력하지 말고 볼트가 정한 로컬 경로에 파일로 저장한 뒤 건수·합계·해시 같은 최소 결과만 출력한다.
- **기동(Windows):** Claude Code에서 자동 기동 훅(`.claude/settings.local.json`)을 켰다면 따로 실행하지 않는다. 훅이 없으면 세션의 첫 aside 명령 전이나 "Aside isn't running"을 받았을 때, 볼트 루트에서 `powershell -NoProfile -ExecutionPolicy Bypass -File 00-meta/scripts/aside-up.ps1`을 실행한다. 꺼져 있으면 띄운 뒤 새 창을 바로 최소화하고 이전 창에 포커스를 돌려 달라고 요청한 다음 CLI 응답까지 기다린다. 이미 떠 있는 브라우저의 창은 건드리지 않는다. `Aside.exe`를 직접 실행하지 않는다.
- **기동(macOS·Linux):** 기동 도우미가 없다. 앱이 꺼져 있으면 사용자에게 알리고 사용자가 띄우게 한다(vault-browser 규칙 9). 포커스를 뺏지 않는 기동 방법을 실측하면 이 줄을 고친다.
- **창을 닫지 않는다:** 브라우저 창이 닫히면 CLI 데몬도 함께 종료된다. 작업이 끝나면 연 탭만 `closeTab`으로 닫는다.
- **사용자 세션 안에서 돈다:** 격리 프로필이 없으므로 시작 전 "작업 중 Aside 탭을 만지지 말라"고 알린다. 저장된 비밀번호 자동입력은 사용자가 그 계정을 명시했을 때만 한다.
- **제약은 실측으로:** 파일 URL·PDF 크기·출력 경로·호출 시간 제한 같은 제약은 [[Aside CLI 운영 가이드]]에 실행해 본 결과로 기록하고 그 노트를 따른다.
<!-- agentic-vault:adapter aside end -->
