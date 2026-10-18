# 릴리스 무결성 확인

공개 릴리스에 올리는 파일, v0.23.0부터 적용하는 GitHub 불변 릴리스(immutable release) 게시 절차,
사용자가 선택해서 할 수 있는 확인 방법과 각 확인의 보장 범위를 정리한다. 확인은 선택 사항이다.
건너뛰어도 설치·사용 방법은 같고, 플러그인 런타임은 이 확인을 위해 네트워크나 모델을 호출하지 않는다.

GitHub 기능 설명은 공식 문서 [Immutable releases](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/immutable-releases)가
보고한 내용이다. 아래 2026-10-17의 개수·종료 코드·출력은 당시 GitHub API와 GitHub CLI 2.98.0으로
직접 조회·실행한 측정값이며 각 항목에 대상 수를 적는다. 저장소의 현재 불변 릴리스 설정은
2026-10-18 API 조회에서 `"enabled": true`였다. v0.23.0은 불변 릴리스로 게시됐고, v0.24.0의
최종 소스·CI·ZIP/blob 대조는 [릴리스 검증](verification/v0.24.0.md)과 게시 전 릴리스 자산
`verification.json`에 기록한다. 게시 뒤 릴리스 증명·공개 재다운로드 확인은 별도
`post-publication.json`에 보관한다. 게시 전 대기 상태를 검증 통과로 해석하지 않는다.

## 공개 자산

| 파일 | 내용 |
|---|---|
| `agentic-vault-VERSION.zip` | 태그 커밋의 소스 ZIP. 릴리스에 직접 올린 파일이며 GitHub가 내려받을 때 만드는 "Source code (zip/tar.gz)"와 다르다 |
| `SHA256SUMS` | ZIP과 `verification.json`의 SHA-256 목록 |
| `verification.json` | 최종 Git 소스, 메타데이터 검사, CI, 압축본·blob 대조, 독립 검토를 담은 검증 영수증 |

아래 `TAG`는 확인할 릴리스 태그(예: `v0.24.0`), `VERSION`은 태그에서 앞의 `v`를 뺀 번호다.
2026-10-17 v0.23.0 게시 전 GitHub 릴리스 23개 중 v0.15.0부터 v0.22.1까지 10개가
이 세 파일을 올렸다(당시 측정).
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

1. 저장소 Settings의 Releases 섹션에서 **Enable release immutability**가 켜져 있는지 확인한다.
   `GET /repos/Technoetic/agentic-vault/immutable-releases`의 2026-10-18 응답은 `"enabled": true`였다.
   v0.23.0 게시 전인 2026-10-17 조회의 `"enabled": false`는 당시 관측이며 현재 설정이 아니다.
2. 태그를 push한 뒤 릴리스를 **초안**(draft)으로 만든다. 초안과 그 태그는 아직 잠기지 않는다.
3. 세 자산을 모두 초안에 올리고 이름과 개수를 확인한다.
4. 초안을 게시한다. 게시하면 자산과 태그가 잠기고 릴리스 증명이 생긴다. 이후 자산을 고쳐야 하면 새 태그로 다시
   릴리스한다.
5. 게시 후 아래 사용자 확인을 직접 실행하고 별도 `post-publication.json`에 결과를 남긴다.
   게시 전의 `verification.json`은 이미 잠긴 자산이며 자기 자신의 나중 검증 결과를 추가할 수 없다.
   게시 후 기록은 불변 릴리스 자산 밖에 보관하고 운영 인계에서 경로를 연결한다.

```text
gh release create TAG --draft --verify-tag --title "TAG — 제목" --notes-file NOTES.md -R Technoetic/agentic-vault
gh release upload TAG agentic-vault-VERSION.zip SHA256SUMS verification.json -R Technoetic/agentic-vault
gh release view TAG --json isDraft,assets -R Technoetic/agentic-vault
gh release edit TAG --draft=false -R Technoetic/agentic-vault
gh release view TAG --json isImmutable -R Technoetic/agentic-vault
```

`--verify-tag`는 원격에 태그가 없으면 릴리스를 만들지 않는다. 업로드와 게시를 나눈 것은 되돌릴 수 없는 게시 전에
자산을 확인하기 위해서다.

v0.23.0부터 텍스트 자산 `SHA256SUMS`와 `verification.json`은 LF 바이트로 쓴다(CR 없음). 이전 릴리스는 그렇지 않았다.
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
zizmor, pip-audit 설치, pip-audit 감사 세 단계와 작업 모두 `continue-on-error: true`라 실패해도 워크플로가 실패하지
않는다. 마지막 보고 단계가 세 단계의 결과를 읽어 성공하지 않은 단계마다 `::warning` 주석을 남긴다. 권한은
`contents: read`뿐이며 SARIF 업로드(`security-events`)와 OIDC 토큰(`id-token`)을 쓰지 않는다. 기존 테스트 작업과
게이트는 바뀌지 않는다.

- **로컬 측정(2026-10-17):** zizmor 1.31.1을 오프라인 모드로 `.github/workflows`(워크플로 1개)에 실행했다.
  기본(regular) persona는 0건이고 1건은 persona 기준으로 숨겨졌다. pedantic·auditor persona는 각 1건(Low,
  `concurrency-limits`: 워크플로에 동시 실행 제한이 없음)이었다. 이 작업을 추가하기 전 v0.22.1 워크플로도
  같은 결과였다. 동시 실행 제한을 넣으면 기존 작업의 실행 방식이 바뀌므로 고치지 않았다.
  같은 날 pip-audit 2.10.1을 Python 3.12에서 `requirements-dev.txt`(고정 의존성 2개)에 실행한 결과는
  `No known vulnerabilities found`였다.
- **로컬 측정(2026-10-19):** CI가 쓰는 버전인 zizmor 1.30.1(PyPI 휠)을 오프라인 모드로, 아래 해시 고정 설치를 넣기
  전(v0.24.0, `d2b9315`)과 넣은 뒤의 `tests.yml`에 각각 실행했다. 두 파일 모두 regular persona 0건(1건 숨김),
  pedantic·auditor persona 각 1건(Low, `concurrency-limits`)으로 같았다. 새 발견은 없었다.
- **고정 버전(게시 14일 유예):** zizmor action v0.7.0과 v0.7.1은 둘 다 2026-10-15에 게시되어 2026-10-17 기준
  14일이 지나지 않았다. 그래서 2026-09-09에 게시된 v0.6.4를 커밋 `cc914d7f3750a2d13d75c7f184a1060aa0e9d482`로
  고정한다. v0.6.4는 자기 저장소 `support/versions`에 digest로 고정한 Docker 이미지
  (`ghcr.io/zizmorcore/zizmor:1.30.1@sha256:a2eb396d886c053073405c7a980f2139ba2248ec172243cfa3841e57196e8101`)로
  zizmor를 실행하며, 이 목록의 최신 버전이자 CI가 지정하는 버전은 2026-09-09에 게시된 1.30.1이다. v0.6.4의
  `action.yml`은 사용 중인 `version`·`advanced-security` 입력을 지원한다. 게시일·태그 대상 커밋·`action.yml`·
  `support/versions`는 2026-10-17에 GitHub API로 조회했다(측정). pip-audit은 action 대신 아래 해시 고정 파일로
  설치하며, 그 파일의 유예 기준은 생성 명령의 `--uploaded-prior-to`다.
- **한계:** CI의 zizmor는 GitHub API를 쓰는 온라인 감사도 실행하므로 로컬 오프라인 측정과 결과가 다를 수 있다.
  pip-audit 설치·감사의 한계는 아래 "해시 고정 설치의 한계"에 적는다.

### pip-audit 해시 고정 설치

v0.24.0까지 이 작업은 `pypa/gh-action-pip-audit` v1.1.0을 썼다. action 코드는 커밋 SHA로 고정했지만 실행할 때
PyPI에서 pip-audit(`~= 2.0, >= 2.5.6`)을 해시 없이 설치했다(두 action의 고정 커밋 소스를 읽어 확인, 2026-10-17).
지금은 setup-python이 준 작업의 Python 3.12로 다음 두 명령을 실행한다.

```text
python -m pip install --require-hashes --only-binary :all: -r ci/requirements-audit.txt
python -m pip_audit --no-deps --disable-pip --progress-spinner off -r requirements-dev.txt
```

- **해시 확인 설치:** pip 공식 문서 [Secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)는 pip가
  기본으로는 원격 변조를 확인하지 않고 배포본의 코드를 실행한다고 설명하고, 해시 확인 모드(`--require-hashes`)와
  소스 배포본 금지(`--only-binary :all:`)를 함께 쓰라고 안내한다. 이 모드에서는 모든 요구사항과 의존성에 해시가
  있어야 하고 버전을 `==` 등으로 고정해야 하며, 파일에 적히지 않은 의존성은 오류가 된다(2026-10-19에 읽은 pip 26.2.1
  문서의 설명). [`ci/requirements-audit.txt`](../ci/requirements-audit.txt)는 pip-audit 2.10.1과 그 의존성을 합친
  29개 패키지를 고정하고, 각 고정 버전에 대해 PyPI에 올라온 파일 전체의 sha256 362개를 담는다. 해시가 맞지 않는
  파일, 목록에 없는 의존성, 소스 배포본은 설치하지 않는다. 설치가 실패하면 감사 단계를 건너뛰고 경고 두 개(설치,
  pip-audit)가 남는다.
- **설치 없는 감사:** `requirements-dev.txt`는 hypothesis 6.168.5와 sortedcontainers 2.4.0을 `==`로 고정한다.
  `--no-deps`와 `--disable-pip`를 함께 주면 pip-audit은 파일을 직접 읽어 적힌 고정값만 감사하고, `==`로 고정하지 않은
  줄은 `is not pinned to an exact version` 오류(종료 코드 1)로 거부한다(2026-10-19 임시 파일로 1회 측정). 설치를
  막는 것은 `--disable-pip`다. pip-audit 2.10.1 README는 `--no-deps`로 의존성 해석을 건너뛸 수 있다고 설명한다(README
  보고). 그러나 하위 프로세스 명령을 기록해 보니 `--disable-pip` 없이 `-r`로 실행하면 `--no-deps`가 있어도 임시
  가상환경을 만들고, 그 안에서 `pip install --upgrade pip wheel setuptools`와
  `pip install --dry-run --report … -r requirements-dev.txt`를 해시 없이 실행했다. `--disable-pip`를 더하면 하위
  프로세스는 `python -m pip --version` 하나였다(2026-10-19 Windows, 세 방식 각 1회 측정). pip-audit 2.10.1 소스
  (`_dependency_source/requirement.py`, `_virtual_env.py`)도 같은 동작이다.
- **감사 범위:** PyPI 메타데이터에서 hypothesis 6.168.5의 의존성은 sortedcontainers와, Python 3.11 미만에서만 필요한
  exceptiongroup이다. Python 3.12에서 새 방식과 `-r`만 준 기본 해석 방식은 같은 두 패키지를 감사했고 결과는 둘 다
  `No known vulnerabilities found`였다(2026-10-19 측정, PyPI 취약점 서비스). `stateful` 작업이 Python 3.10에서
  설치하는 exceptiongroup은 이전처럼 감사 대상이 아니다.

#### 로컬 확인(2026-10-19, Windows 11, CPython 3.12.10)

- **설치:** 저장소 밖 새 가상환경(ensurepip의 pip 25.0.1)에서 위 설치 명령이 종료 코드 0으로 끝났고 29개 패키지가
  설치됐다(pip는 파일의 26.2.1로 바뀜). `pip check`는 `No broken requirements found.`였다.
- **해시 대조:** PyPI JSON API와 비교하면 29개 고정 버전 모두 파일의 해시 집합이 PyPI에 올라온 그 버전의 파일 전체와
  같았다(charset-normalizer 172개, msgpack 91개, tomli 47개, 나머지 26개 패키지는 각 2개). 29개 모두 CPython 3.12용
  manylinux x86_64 휠과 win_amd64 휠이 있고, msgpack을 뺀 28개는 순수 Python 휠이다.
- **Linux 휠:** 같은 파일을 `pip download --require-hashes --only-binary :all: --no-deps`로 받되 manylinux x86_64
  `--platform` 태그들과 `--python-version 3.12 --implementation cp --abi cp312`를 주자 29개 휠을 모두 받았고 pip가
  각 해시를 확인했다(종료 코드 0, 1회). 플랫폼 휠이 필요한 charset-normalizer·msgpack·tomli는 cp312 manylinux
  휠이었다. Linux에서 설치를 실행한 것은 아니다.
- **의존성 범위:** PyPI 메타데이터의 Requires-Dist를 Python 3.12의 Linux·Windows 환경 표지로 평가해 pip-audit 2.10.1부터
  따라간 집합은 두 플랫폼 모두 고정한 29개와 같았다(빠짐 0, 남음 0). 조건이 붙은 의존성은 두 개뿐이다(CacheControl의
  `filecache` 추가 기능이 요구하는 filelock, Python 3.13 미만에서만 필요한 typing-extensions). Windows에서 만든 파일을
  Linux CI에 쓰는 근거다.
- **유예 효과:** 기준 시각 없이 같은 명령을 실행하면 29개 중 3개가 더 새 버전이 됐다. filelock 4.2.2, platformdirs
  4.13.4, tomli 2.5.0이며 PyPI 업로드는 각각 2026-10-18, 2026-10-17, 2026-10-07(UTC)이다.
- **재현:** 같은 기준 시각과 `--upgrade`로 빈 캐시에서 다시 만든 파일은 처음 파일과 바이트 단위로 같았다(1회).
- **감사:** 위 감사 명령은 종료 코드 0, `No known vulnerabilities found`였다(2개 패키지).
- **거부:** mdurl의 해시 2개를 틀린 값으로 바꾼 사본은 `THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE`,
  mdurl 항목을 뺀 사본은 `In --require-hashes mode, all requirements must have their versions pinned with ==` 오류로
  종료 코드 1이었고, 두 경우 모두 pip-audit이 설치되지 않았다(각 1회). 보고 단계 스크립트를 bash로 따로 실행해 설치
  `failure`·감사 `skipped`를 주면 경고 두 개를 내고 종료 코드 0으로 끝났다.
- **워크플로 파싱:** 임시 가상환경의 PyYAML 6.0.3으로 `tests.yml`을 읽었다. `unittest`·`stateful` 작업, 최상위 설정,
  감사 작업의 설정과 checkout·setup-python·zizmor 단계는 v0.24.0(`d2b9315`)과 같았다.

#### 갱신 절차

유지관리자가 손으로 갱신한다. CI는 이 파일을 만들거나 고치지 않는다.

1. **기준 시각을 정한다.** 갱신하는 날(UTC)의 14일 전 00:00Z를 `CUTOFF`로 쓴다. 지금 파일은 2026-10-18(UTC)에
   만들었고 `CUTOFF`는 `2026-10-04T00:00:00Z`다. 이보다 늦게 PyPI에 올라온 버전은 고르지 않는다(게시 14일 유예).
   pip-audit을 올리려면 PyPI 게시일이 `CUTOFF`보다 앞선 버전으로 [`ci/requirements-audit.in`](../ci/requirements-audit.in)의
   고정값을 바꾼다.
2. **저장소 밖 새 가상환경에 도구를 설치한다.** `VENV`는 저장소 밖 경로다. Windows에서는 `VENV/bin/` 대신
   `VENV\Scripts\`를 쓴다.

   ```text
   python -m venv VENV
   VENV/bin/python -m pip install pip==26.2.1
   VENV/bin/python -m pip install --uploaded-prior-to CUTOFF --only-binary :all: pip-tools==7.6.1 click==8.4.2
   ```

   pip-tools 7.6.1을 click 8.5.0과 함께 쓰면 머리 주석의 명령에 쓰지 않은 `--no-index`가 적혔다(2026-10-19 측정,
   요구사항 본문은 같았다). 그래서 click 8.4.2를 고정한다. 도구 버전을 바꾸면 머리 주석의 명령을 다시 확인한다.
3. **저장소 루트에서 다시 만든다.** `--upgrade`는 기존 고정값을 버리고 `CUTOFF` 전의 최신 버전을 고른다.
   `--allow-unsafe`는 pip-api가 요구하는 pip까지 해시로 고정한다.

   ```text
   VENV/bin/pip-compile --upgrade --allow-unsafe --generate-hashes --strip-extras --newline lf --uploaded-prior-to CUTOFF --output-file ci/requirements-audit.txt ci/requirements-audit.in
   ```

   pip-compile은 머리 주석을 새로 쓴다. 지금 파일 맨 위 8줄처럼 `Generated <UTC 시각>` 줄, 도구 설치 명령,
   `Tool versions:` 줄을 이번 값으로 고쳐 다시 붙인다. `tests/test_supply_chain.py`가 이 줄들과 생성 명령의 필수
   옵션, 생성 시각과 `CUTOFF`의 간격(14일 이상)을 확인한다.
4. **CI와 같은 명령으로 확인한다.** 새 가상환경에서 위 설치 명령과 감사 명령을 실행한다.
5. **diff를 검토한다.** `git diff -- ci/`로 바뀐 버전, 새로 생기거나 빠진 패키지, 버전별 해시 수, 머리 주석의
   `CUTOFF`를 확인하고, 버전이 바뀐 패키지는 변경 기록을 읽는다. 버전이 그대로인데 해시가 바뀌었으면 반영하지 말고
   원인부터 확인한다.
6. **경고 전용을 유지한다.** 갱신 뒤 감사가 실패해도 작업과 단계의 `continue-on-error: true`를 바꾸지 않는다.

#### 해시 고정 설치의 한계

- 위 확인은 모두 로컬 Windows에서 했다. 새 단계를 ubuntu-latest CI에서 처음 실행한 결과는 릴리스 검증 기록에 남긴다.
  GitHub Actions의 `if:`·`outcome` 평가 자체는 로컬에서 실행하지 않았다.
- 파일은 감사 작업의 CPython 3.12에 맞춰 만들었다. 작업의 Python 버전을 바꾸면 조건부 의존성과 휠 유무가 달라질 수
  있으므로 다시 만들고 확인한다.
- 해시는 생성할 때 PyPI가 내준 파일의 값이다. 이후 같은 버전에 다른 파일이 들어오는 것은 막지만, 생성 전에 이미
  악성이던 릴리스는 막지 못한다. 14일 유예는 그 위험을 줄이는 운영 규칙이며 보장이 아니다.
- `--uploaded-prior-to`는 업로드 시각 정보를 주는 인덱스에서만 동작한다(pip 26.2.1 도움말). 이번 생성에서는 PyPI에서
  기준 시각 뒤의 버전 3개가 제외됐다(위 유예 효과).
- 해시 확인 설치를 실행하는 첫 pip는 setup-python이 준 것이며 이 절차로 검증하지 않는다. 설치 중 파일의 pip 26.2.1로
  바뀐다.
- `--disable-pip` 동작은 pip-audit 2.10.1에서 확인했다. pip-audit을 올리면 하위 프로세스 기록으로 다시 확인한다.
- 감사 결과는 실행 시점의 PyPI 취약점 데이터에 따른다. pip-audit README의 보안 모델은 pip-audit이 악성 패키지를
  막아 준다고 가정하지 말라고 적는다.
- 해시 고정은 CI의 pip-audit 설치에만 적용된다. 플러그인 런타임은 계속 표준 라이브러리만 쓰며 새 런타임 의존성은 없다.

## 참고

- [Immutable releases](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/immutable-releases):
  보호 범위, 릴리스 증명, 초안 → 자산 → 게시 권장 순서
- [Preventing changes to your releases](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes):
  저장소 설정
- [Verifying the integrity of a release](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/verify-release-integrity):
  `gh release verify`·`verify-asset`, 자동 생성 소스 압축본 제외
- [GitHub CLI v2.93.0 릴리스 노트](https://github.com/cli/cli/releases/tag/v2.93.0): GHSA-8xvp-7hj6-mcj9
- [Secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/): pip 해시 확인 모드(`--require-hashes`)와
  소스 배포본 금지(`--only-binary :all:`)
