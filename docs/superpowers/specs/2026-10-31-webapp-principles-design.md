# Shared webapp creation principles

## Goal

Make six user-selected webapp principles discoverable by Claude Code and Codex
through agentic-vault. These are a webapp-scoped development contract, not a
vault engine operation or an application auto-patcher.

1. Separate JavaScript by responsibility.
2. Separate CSS tokens, base styles and components.
3. Use class-oriented screen/service responsibility boundaries.
4. Use async/await for data loading and dependent rendering, including failure
   and stale-request handling.
5. Use a single entry point with screen URLs, deep links, refresh and browser
   Back/Forward support.
6. Apply new deployments without requiring hard refresh: detect a new app build
   in open tabs, load it on the next safe navigation, and protect unsaved input.

Existing project architecture and explicit user instructions take precedence.
A bundled single-file deployment is compatible with modular development source.
Do not rewrite an existing framework merely to make it resemble an example.

## Integration

- Add `skills/webapp-principles/SKILL.md`, with a narrow creation/change/review
  trigger. It is usable outside a vault and does not start Harness14 or another
  workflow, create a vault, or grant deployment/install/write permission.
- Add `docs/webapp-principles.md` as the canonical six-rule reference and
  application acceptance checklist. A skill is guidance; do not claim a host
  always selects it or that reading it proves application compliance.
- Link the new skill/reference from the core vault skill, its Codex reference,
  README, documentation index and Codex usage guide.
- Extend the existing isolated-home Codex verifier to require both enabled skill
  resources and verify installed skill/reference bytes. Preserve the existing
  primary `skill` result field and add an explicit list without changing hook
  counts or trust handling.
- Keep plugin versions, installed caches, host settings, vault templates,
  SessionStart hooks, engine hashes, certificate policy and operating apps as-is.
  Source integration and publication/installation are separate operations.

## Deployment-update contract

Distinguish HTTP cache behavior from an already running document. `no-store` or
revalidation of entry HTML is necessary, but cannot replace loaded JavaScript.
Accept no-cache/ETag or private no-store as appropriate, not an unconditional
public-caching mandate for authenticated pages. A version comes from the loaded
HTML/build identity and a same-origin uncached version endpoint or framework
equivalent. Do not establish the loaded baseline by fetching the server's
latest version after page startup. A rollback is a different identity too.

On internal navigation, tab return and relevant history/BFCache restoration,
check for a changed app build. A clean next navigation loads the target URL as
a full document automatically. Dirty forms and in-flight saves receive an
update notice and keep their data until saved, cancelled or explicitly
discarded; no background forced reload. Network/version-check errors keep the
current page and drafts usable. Data-only updates use fresh data requests and
do not needlessly count as app-code deployments. Reuse framework equivalents;
do not install a service worker or polling dependency merely to satisfy a rule.

## Acceptance

Native discovery and installed byte checks prove delivery, not model adherence.
Use synthetic baseline/candidate pressure scenarios to observe the policy's
behavior, and label these as agent-plan evaluations rather than real app tests.
The observed baseline already detects the open-tab issue but offers a manual
reload/accept-update path; the new policy must explicitly require automatic
clean next-navigation upgrade as well as protecting dirty input.

Real app acceptance must cover A-to-B without hard refresh, an already-open A
tab navigating to B, background/history return, dirty and saving forms,
rollback, transient version failure, deep links and separate data freshness.
Record what was actually run and any project exception. Do not declare success
from static keyword matches, screenshots or response headers alone.
