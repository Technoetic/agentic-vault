# agentic-vault — Antigravity CLI 패키지

이 폴더는 [agentic-vault](https://github.com/Technoetic/agentic-vault)를 Antigravity CLI(`agy`)에 설치하는 플러그인 패키지다. 명령 15개가 같은 이름의 스킬로 들어 있다. `hooks.json`의 훅은 세션을 시작할 때 볼트의 인계를 주입하고, 에이전트가 완전히 멈췄을 때(Stop 입력의 `fullyIdle`이 참일 때)만 checkpoint를 남기도록 만들었다. 이 훅 동작은 실제 agy 대화 세션으로는 확인하지 않았다.

설치(v0.48.0 태그 고정):

```text
agy plugin install https://github.com/Technoetic/agentic-vault/tree/v0.48.0/antigravity
```

이 폴더는 생성물이다. 직접 고치지 말고 저장소 루트에서 `python scripts/build_antigravity_package.py build`로 다시 만든다. 설치·확인 방법과 한계는 [docs/antigravity.md](https://github.com/Technoetic/agentic-vault/blob/v0.48.0/docs/antigravity.md)에 있다.

## English

This folder is the Antigravity CLI (`agy`) plugin package of [agentic-vault](https://github.com/Technoetic/agentic-vault). The 15 commands are skills with the same names. The hooks in `hooks.json` are built to inject the vault handoff when a session starts and to write a checkpoint only when the agent is fully idle (a Stop input with `fullyIdle` true). This hook behavior is not yet confirmed in a real agy conversation.

Install (pinned to the v0.48.0 tag):

```text
agy plugin install https://github.com/Technoetic/agentic-vault/tree/v0.48.0/antigravity
```

This folder is generated; do not edit it. Rebuild it from the repository root with `python scripts/build_antigravity_package.py build`. Installation, a manual check and the limits: [docs/antigravity.md](https://github.com/Technoetic/agentic-vault/blob/v0.48.0/docs/antigravity.md).
