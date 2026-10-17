# 릴리스 무결성 확인

공개 릴리스에 올리는 파일, v0.23.0부터 적용하는 GitHub 불변 릴리스(immutable release) 게시 절차,
사용자가 선택해서 할 수 있는 확인 방법과 각 확인의 보장 범위를 정리한다. 확인은 선택 사항이다.
건너뛰어도 설치·사용 방법은 같고, 플러그인 런타임은 이 확인을 위해 네트워크나 모델을 호출하지 않는다.

GitHub 기능 설명은 공식 문서 [Immutable releases](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/immutable-releases)가
보고한 내용이다. 개수·종료 코드·출력은 2026-10-17에 GitHub API와 GitHub CLI 2.98.0으로 직접 조회·실행한
측정값이며 각 항목에 대상 수를 적는다.

## 공개 자산

| 파일 | 내용 |
|---|---|
| `agentic-vault-VERSION.zip` | 태그 커밋의 소스 ZIP. 릴리스에 직접 올린 파일이며 GitHub가 내려받을 때 만드는 "Source code (zip/tar.gz)"와 다르다 |
| `SHA256SUMS` | ZIP과 `verification.json`의 SHA-256 목록 |
| `verification.json` | 최종 Git 소스, 메타데이터 검사, CI, 압축본·blob 대조, 독립 검토를 담은 검증 영수증 |

아래 `TAG`는 확인할 릴리스 태그(예: `v0.23.0`), `VERSION`은 태그에서 앞의 `v`를 뺀 번호다.
GitHub 릴리스 23개 중 v0.15.0부터 v0.22.1까지 10개가 이 세 파일을 올렸다(측정).
ZIP은 유지관리자가 `git archive`로 만들어 올린다. CI가 만든 파일이 아니다(v0.22.1 `verification.json` 기록).

## v0.23.0부터: 불변 릴리스

불변 릴리스는 게시한 뒤 자산과 연결된 Git 태그를 바꿀 수 없는 릴리스다. 공식 문서의 내용은 다음과 같다.

- 태그는 특정 커밋에 고정되어 릴리스가 있는 동안 이동·삭제할 수 없다. 릴리스를 지우면 태그도 지울 수 있지만
  같은 태그 이름은 다시 쓸 수 없다.
- 릴리스에 올린 자산은 수정·삭제할 수 없다. 제목·릴리스 노트, pre-release·latest 표시는 바꿀 수 있다.
- 게시할 때 GitHub가 **릴리스 증명**(release attestation)을 자동으로 만든다. 태그, 커밋 SHA, 릴리스 자산을 담은
  서명 기록이다.

불변 설정은 켠 뒤에 게시하는 릴리스에만 적용되고 이미 게시한 릴리스에는 소급되지 않는다. 이 내용은 저장소 설정 안내
[Preventing changes to your releases](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes)가
보고한 것이다.

게시 순서(유지관리자):

1. 저장소 Settings의 Releases 섹션에서 **Enable release immutability**를 켠다. 2026-10-17 조회에서 이 저장소의
   설정은 꺼져 있었다(`GET /repos/Technoetic/agentic-vault/immutable-releases` → `"enabled": false`, 측정).
2. 태그를 push한 뒤 릴리스를 **초안**(draft)으로 만든다. 초안과 그 태그는 아직 잠기지 않는다.
3. 세 자산을 모두 초안에 올리고 이름과 개수를 확인한다.
4. 초안을 게시한다. 게시하면 자산과 태그가 잠기고 릴리스 증명이 생긴다. 이후 자산을 고쳐야 하면 새 태그로 다시
   릴리스한다.
5. 게시 후 아래 사용자 확인을 직접 실행하고 결과를 검증 기록에 남긴다.

```text
gh release create TAG --draft --verify-tag --title "TAG — 제목" --notes-file NOTES.md -R Technoetic/agentic-vault
gh release upload TAG agentic-vault-VERSION.zip SHA256SUMS verification.json -R Technoetic/agentic-vault
gh release view TAG --json isDraft,assets -R Technoetic/agentic-vault
gh release edit TAG --draft=false -R Technoetic/agentic-vault
gh release view TAG --json isImmutable -R Technoetic/agentic-vault
```

`--verify-tag`는 원격에 태그가 없으면 릴리스를 만들지 않는다. 업로드와 게시를 나눈 것은 되돌릴 수 없는 게시 전에
자산을 확인하기 위해서다.

v0.23.0의 텍스트 자산 `SHA256SUMS`와 `verification.json`은 LF 바이트로 쓴다(CR 없음). 이전 릴리스는 그렇지 않았다.
v0.14.0부터 v0.22.1까지 올린 `SHA256SUMS` 11개 중 6개가 CRLF였다
(v0.14.0·v0.15.0·v0.18.0·v0.20.0·v0.21.0·v0.22.1, 바이트 단위 측정). GNU coreutils 8.32의 `sha256sum -c`는
줄 끝 CR을 파일 이름의 일부로 읽어 v0.22.1의 두 파일을 찾지 못하고 종료 코드 1을 냈다. LF인 v0.16.0에서는
같은 명령이 통과했다(측정). 불변 릴리스에서는 게시 뒤 이 파일을 고칠 수 없다.

## 사용자 확인(선택)

세 파일을 한 폴더에 내려받는다. 릴리스 페이지에서 받거나 다음 명령을 쓴다.

```text
gh release download TAG -R Technoetic/agentic-vault
```

### 1. SHA256SUMS 대조(모든 릴리스)

v0.22.1까지의 일부 `SHA256SUMS`는 CRLF라서 `sha256sum -c`로 대조하려면 CR을 지워야 한다. 아래 명령은 CR을 지운 뒤
대조하므로 LF로 쓴 v0.23.0 파일과 이전 CRLF 파일에 모두 그대로 쓸 수 있다.

Linux·Git Bash:

```text
tr -d '\r' < SHA256SUMS | sha256sum -c -
```

macOS(실행해 보지 않았다):

```text
tr -d '\r' < SHA256SUMS > SHA256SUMS.lf
shasum -a 256 -c SHA256SUMS.lf
```

Windows PowerShell:

```powershell
Get-Content SHA256SUMS | ForEach-Object {
  $expected, $name = $_ -split '\s+', 2
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $name).Hash
  if ($actual -eq $expected) { "OK      $name" } else { "FAILED  $name" }
}
```

모든 줄이 `OK`이면 통과다. CRLF인 v0.22.1과 LF인 v0.16.0의 자산 각 2개로 Git Bash(GNU coreutils 8.32)와
Windows PowerShell 5.1에서 모든 줄이 `OK`였다. 한 바이트를 덧붙인 v0.22.1 사본은 두 방법 모두 `FAILED`로
표시했다(측정).

### 2. 릴리스 증명 확인(v0.23.0부터)

GitHub CLI는 2.81.0부터 아래 두 명령을 제공하고, 2.93.0에서 이 명령들이 TUF 저장소 미러 요청에 인증 헤더를
넣던 취약점(GHSA-8xvp-7hj6-mcj9)을 고쳤다(GitHub CLI 릴리스 노트 보고). 2.93.0 이상을 쓴다.

```text
gh release verify TAG -R Technoetic/agentic-vault
gh release verify-asset TAG agentic-vault-VERSION.zip -R Technoetic/agentic-vault
```

- `gh release verify`는 릴리스 증명이 있고 서명이 유효한지 확인하고, 증명에 담긴 자산의 digest를 출력한다.
- `gh release verify-asset`은 내려받은 파일의 digest가 그 릴리스 증명의 대상(subject)에 있는지 확인한다.
  `SHA256SUMS`와 `verification.json`도 같은 명령으로 확인한다.
- 성공하면 종료 코드 0이다. `verify`는 `✓ Release <태그> verified!`, `verify-asset`은
  `✓ Verification succeeded! <파일> is present in release <태그>`를 출력한다(다른 저장소의 불변 릴리스로 각 1건
  측정: zizmorcore/zizmor-action v0.7.1, zizmorcore/zizmor v1.31.1 자산 1개). 증명이 없거나 파일이 다르면 종료
  코드 1이다. 이 저장소의 v0.22.1 ZIP은 `no attestations found for tag v0.22.1`, 그 릴리스에 없는 파일은
  `does not contain subject`로 실패했다(측정).
- GitHub가 자동으로 만드는 "Source code (zip/tar.gz)"는 내려받을 때 생성되므로 `verify-asset`으로 확인할 수 없다
  (공식 문서). 릴리스에 올린 `agentic-vault-VERSION.zip`을 확인한다.
- 이 확인은 건너뛰어도 된다. 하는 경우 1의 `SHA256SUMS` 대조와 함께 쓴다.

## 증명하는 것과 증명하지 않는 것

| 확인 | 증명하는 것 | 증명하지 않는 것 |
|---|---|---|
| `SHA256SUMS` 대조 | 내려받은 파일이 같은 릴리스의 `SHA256SUMS` 목록과 같다. 전송 중 손상을 찾는다 | 목록의 진위. 불변이 아닌 릴리스에서는 자산과 목록을 함께 바꿀 수 있다 |
| 릴리스 증명(`verify`·`verify-asset`) | 파일이 GitHub가 그 태그로 게시한 자산과 같고, 태그가 증명에 적힌 커밋을 가리킨다 | 누가 어떤 환경에서 ZIP을 만들었는지(빌드 출처), ZIP이 태그 소스에서 재현되는지, 게시를 승인한 사람의 신원, 내용의 안전성 |

- **서명 주체:** 다른 저장소의 불변 릴리스 1건(zizmorcore/zizmor-action v0.7.1)을 `--format json`으로 보면 인증서
  발급자는 `CN=Fulcio Intermediate l1,O=GitHub\, Inc.`, 주체 대체 이름은 `https://dotcom.releases.github.com`이다
  (측정). 서명하는 쪽은 GitHub 릴리스 서비스이며 유지관리자나 CI 실행의 신원은 증명에 없다.
- **빌드 출처:** 앞으로 CI에서 ZIP을 만들고 [`actions/attest`](https://github.com/actions/attest)로 빌드 출처
  증명(SLSA provenance)을 남기면 `gh attestation verify`로 그 ZIP을 만든 워크플로를 확인할 수 있다. 이 방식에는
  `id-token: write`와 `attestations: write` 등 쓰기 권한이 필요하며(actions/attest README) 아직 도입하지 않았다.
- **승인자 신원:** 릴리스 증명도 `verification.json`의 `verified_by`도 게시를 승인한 사람의 신원을 증명하지 않는다.
- **설치본:** README의 Claude Code 설치 명령은 태그를 지정하지 않으므로 릴리스 증명이 설치된 커밋을 알려 주지 않는다.
  Codex 설치는 `--ref` 태그를 쓴다. 불변 릴리스부터 그 태그는 게시 후 다른 커밋으로 옮겨지지 않지만, 설치된
  캐시 파일 자체를 검증하지는 않는다.

## v0.22.1까지의 릴리스

v0.22.1까지 게시한 GitHub 릴리스 23개는 불변 릴리스로 게시되지 않았다. GitHub API는 23개 모두
`"immutable": false`를 반환했고, `gh release verify v0.22.1 -R Technoetic/agentic-vault`는
`No attestations for tag v0.22.1`과 함께 종료 코드 1로 끝났다(2026-10-17 측정). 이 릴리스들은 `SHA256SUMS`
대조만 할 수 있다.

이전 검증 기록([v0.20.0](verification/v0.20.0.md)·[v0.21.0](verification/v0.21.0.md)·[v0.22.0](verification/v0.22.0.md)·[v0.22.1](verification/v0.22.1.md))에
쓴 "immutable"은 커밋·트리 해시와 `SHA256SUMS`로 고정한 소스·자산 내용을 뜻한다. GitHub 불변 릴리스
기능을 뜻하지 않는다.

## CI 공급망 점검(경고 전용)

`.github/workflows/tests.yml`의 `supply-chain-audit` 작업은 push·PR·수동 실행마다 zizmor로 GitHub Actions 워크플로를
정적 분석하고, pip-audit으로 `requirements-dev.txt`의 알려진 취약점을 조회한다. 결과는 경고로만 표시한다.
두 단계와 작업 모두 `continue-on-error: true`라 실패해도 워크플로가 실패하지 않는다. 권한은 `contents: read`뿐이며
SARIF 업로드(`security-events`)와 OIDC 토큰(`id-token`)을 쓰지 않는다. 기존 테스트 작업과 게이트는 바뀌지 않는다.

- **로컬 측정:** zizmor 1.31.1을 오프라인 모드로 `.github/workflows`(워크플로 1개)에 실행했다(2026-10-17).
  기본(regular) persona는 0건이고 1건은 persona 기준으로 숨겨졌다. pedantic·auditor persona는 각 1건(Low,
  `concurrency-limits`: 워크플로에 동시 실행 제한이 없음)이었다. 이 작업을 추가하기 전 v0.22.1 워크플로도
  같은 결과였다. 동시 실행 제한을 넣으면 기존 작업의 실행 방식이 바뀌므로 고치지 않았다.
  같은 날 pip-audit 2.10.1을 Python 3.12에서 `requirements-dev.txt`(고정 의존성 2개)에 실행한 결과는
  `No known vulnerabilities found`였다.
- **고정 버전(게시 14일 유예):** zizmor action v0.7.0과 v0.7.1은 둘 다 2026-10-15에 게시되어 2026-10-17 기준
  14일이 지나지 않았다. 그래서 2026-09-09에 게시된 v0.6.4를 커밋 `cc914d7f3750a2d13d75c7f184a1060aa0e9d482`로
  고정한다. v0.6.4는 자기 저장소 `support/versions`에 digest로 고정한 Docker 이미지
  (`ghcr.io/zizmorcore/zizmor:1.30.1@sha256:a2eb396d886c053073405c7a980f2139ba2248ec172243cfa3841e57196e8101`)로
  zizmor를 실행하며, 이 목록의 최신 버전이자 CI가 지정하는 버전은 2026-09-09에 게시된 1.30.1이다. v0.6.4의
  `action.yml`은 사용 중인 `version`·`advanced-security` 입력을 지원한다. pip-audit action은 2024-08-08에 게시된
  v1.1.0을 유지한다. 게시일·태그 대상 커밋·`action.yml`·`support/versions`는 2026-10-17에 GitHub API로 조회했다(측정).
- **한계:** CI의 zizmor는 GitHub API를 쓰는 온라인 감사도 실행하고, 버전도 위 로컬 측정(1.31.1)과 달라(1.30.1)
  결과가 다를 수 있다. 1.30.1은 로컬에서 실행하지 않았다. pip-audit action(v1.1.0)은
  코드를 SHA로 고정했지만 실행할 때 PyPI에서 pip-audit(`~= 2.0, >= 2.5.6`)을 해시 없이 설치한다(두 action의
  고정 커밋 소스를 읽어 확인). pip-audit은 CI가 쓰는 Python 3.12 기준으로 의존성을 해석한다.

## 참고

- [Immutable releases](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/immutable-releases):
  보호 범위, 릴리스 증명, 초안 → 자산 → 게시 권장 순서
- [Preventing changes to your releases](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes):
  저장소 설정
- [Verifying the integrity of a release](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/verify-release-integrity):
  `gh release verify`·`verify-asset`, 자동 생성 소스 압축본 제외
- [GitHub CLI v2.93.0 릴리스 노트](https://github.com/cli/cli/releases/tag/v2.93.0): GHSA-8xvp-7hj6-mcj9
