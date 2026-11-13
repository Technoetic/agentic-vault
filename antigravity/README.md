# agentic-vault — Antigravity CLI 패키지

이 폴더는 [agentic-vault](https://github.com/Technoetic/agentic-vault)를 Antigravity CLI(`agy`)에 설치하는 플러그인 패키지다. 명령 15개가 같은 이름의 스킬로 들어 있고, `hooks.json`의 훅이 세션을 시작할 때 볼트의 인계를 주입하고 에이전트가 멈출 때 checkpoint를 남긴다.

설치(v0.48.0 태그 고정):

```text
agy plugin install https://github.com/Technoetic/agentic-vault/tree/v0.48.0/antigravity
```

이 폴더는 생성물이다. 직접 고치지 말고 저장소 루트에서 `python scripts/build_antigravity_package.py build`로 다시 만든다. 설치·확인 방법과 한계는 [docs/antigravity.md](https://github.com/Technoetic/agentic-vault/blob/v0.48.0/docs/antigravity.md)에 있다.

## English

This folder is the Antigravity CLI (`agy`) plugin package of [agentic-vault](https://github.com/Technoetic/agentic-vault). The 15 commands are skills with the same names, and the hooks in `hooks.json` inject the vault handoff when a session starts and write a checkpoint when the agent stops.

Install (pinned to the v0.48.0 tag):

```text
agy plugin install https://github.com/Technoetic/agentic-vault/tree/v0.48.0/antigravity
```

This folder is generated; do not edit it. Rebuild it from the repository root with `python scripts/build_antigravity_package.py build`. Installation, a manual check and the limits: [docs/antigravity.md](https://github.com/Technoetic/agentic-vault/blob/v0.48.0/docs/antigravity.md).
