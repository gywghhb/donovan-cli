# Donovan Agent v0.1.13

This release focuses on making Donovan easier to use as a real product, especially for people who do not want to configure technical details by hand.

## Highlights

- **Browser Companion for Edge and Chrome**
  Donovan can now work with tabs you already have open through a local companion extension. This avoids remote debugging flags and avoids opening a separate browser just to inspect or interact with a page.

- **Natural-language setup and product workflows**
  Donovan now includes higher-level workflows for contracts, recipes, sandboxes, profiles, evals, workspace graphs, impact checks, PR summaries, watchers, inbox triage, routing, stats, handoffs, recovery, and agent tests. Users can configure many of these through normal chat instead of editing config files manually.

- **Cleaner terminal UX**
  The footer is transparent, message spacing is calmer, the status indicator shows while Donovan is thinking or working, and browser/tool status no longer duplicates or crowds the conversation.

- **Browser work cleanup**
  Donovan is instructed to minimize browser windows after completing browser work instead of closing them or leaving them visibly open.

- **Less noisy timeout handling**
  The old provider timeout panel has been suppressed so users are not shown the raw timeout message in the conversation.

## New Commands

```text
/browser companion setup
/browser companion start
/browser companion status
/browser companion active
/browser companion snapshot
/browser companion tabs
/browser companion use <tab>
/browser companion click <selector>
/browser companion type <selector> <text>
/browser companion screenshot
/browser minimize
/timeline
/replay
/recipe create|run
/sandbox start|run|diff|promote|discard
/profile create|use|lock
/contract
/eval create|run
/graph build|query
/impact
/pr
/watch add|check|remove
/inbox add|run
/marketplace install
/memory-citations
/recover
/router
/stats
/handoff
/doctor-ai
/workspace-profile
/agent-test
```

## Verification

- Full test suite passes with `pytest -q`.
- Added coverage for Browser Companion generation and registration.
- Added coverage for product workflows and prompt behavior.
- Added coverage for timeout suppression.

## Publish Checklist

```powershell
git status
python -m pytest
git add .
git commit -m "Release v0.1.13"
git tag v0.1.13
git push origin main
git push origin v0.1.13
```

Then create a GitHub release from tag `v0.1.13` and paste these notes into the release body.
