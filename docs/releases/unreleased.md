# 다음 릴리스 변경 사항

## 기본 결과 변경

볼트를 초기화하거나 업그레이드할 때 Aside 어댑터는 `/aside-repl` 명령을 설치하거나 갱신하지 않는다. 이전 버전으로 설치했거나 직접 작성한 `.claude/commands/aside-repl.md`는 볼트 사용자가 관리한다. `/vault-doctor`는 이 파일의 복원을 제안하지 않는다.

Aside CLI가 설치된 환경에서는 터미널에서 `aside repl`을 실행할 수 있다. Windows 기동 도우미와 브라우저 어댑터는 계속 사용할 수 있다.
