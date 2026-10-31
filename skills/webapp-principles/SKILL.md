---
name: webapp-principles
description: "Use when creating, changing, or reviewing a webapp, including its JavaScript/CSS structure, data loading, routing, deployment versions, stale open tabs, or cache behavior."
---

# Webapp principles

Apply six rules in ordinary projects and vaults. Current user instructions and
project policies take priority; follow the selected workflow and framework.
Reading this grants no permission to install, deploy, rewrite projects, change
host settings, or write vault notes.

**Required reading for applicable app work:** read the canonical
[six-rule guide and real-app acceptance matrix](../../docs/webapp-principles.md)
before implementation or review; this checklist does not replace it.

## Scope

Use for app creation, changes, and reviews, including stale code after deployment
or broken deep links. Read-only reviews report findings and evidence. Ordinary
notes mentioning websites or explanations of web concepts do not start app work.
Do not start another workflow or initialize a vault automatically.

## Six-rule checklist

| Rule | Required outcome |
|---|---|
| 1. JavaScript | Develop modules by responsibility: entry, screens, services, shared utilities. A bundled single-HTML deliverable remains valid. |
| 2. CSS | Separate tokens, base styles, and components in source; preserve the project's styling system. |
| 3. Screen/service boundaries | Use classes for distinct responsibilities by default; retain project/framework equivalents when its conventions or user instructions differ. |
| 4. Async loading | Use async/await for I/O and dependent rendering; handle loading, HTTP/errors, cancellation, and stale-response races. |
| 5. Single entry | Give screens URLs; support direct deep links, refresh, Back/Forward, and genuine asset/API 404s. |
| 6. Deployment updates | Detect changed app code in already-open tabs; automatically load the intended target as a full document on the next clean navigation, protecting edits and saves. |

## Rule 6 must remain explicit

Compare a build identity embedded in the loaded HTML/build with a fresh
same-origin version response or framework equivalent. Do not initialize the
loaded identity from a startup fetch of the latest server version. Any unequal
identity, including rollback, means changed code.

Check on navigation, tab return, and relevant history/BFCache restoration.
After detecting a change, a clean next navigation automatically loads its target
URL as a full document. A notice offering manual reload, an accept-update button,
or instructions to press F5 do not fulfill this requirement. Recheck both dirty
state and saves in flight immediately before transition. Protect and notify
until saved, cancelled, or explicitly discarded; never force a background reload.
Version/network failures keep the current app and drafts usable.

Use entry-HTML revalidation or appropriate private no-store, build-specific asset
identities, and fresh data requests as separate concerns. A query parameter on
HTML alone does not invalidate imported assets. Do not add a service worker or
polling dependency solely for this rule.

## Verify and report

Use the acceptance matrix for affected behavior. Record actual commands/results,
exceptions, and untested cases; distinguish agent-plan evaluations, package
delivery, and application tests. Follow the guide's six-field handoff and
independent `verified_by` requirements for important artifacts. Skill discovery
alone proves neither agent adherence nor app behavior.
