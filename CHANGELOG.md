# Changelog

## 0.1.13 - 2026-05-20

### Added

- Browser Companion workflow for interacting with already-open Edge and Chrome tabs without remote debugging.
- Companion browser commands for setup, start, status, active tab inspection, snapshots, tab listing, tab selection, clicking, typing, and screenshots.
- Automatic browser minimization guidance and a `browser_minimize` tool so Donovan does not close or leave browser windows visibly open after browser work.
- Product workflows for timelines, replay, reusable recipes, sandboxes, profiles, contracts, evals, workspace graphs, impact checks, PR summaries, watchers, inbox triage, marketplace installs, memory citations, recovery, routing, stats, handoffs, doctor-ai, workspace profiles, and agent tests.
- Natural-language auto-configuration for common Donovan setup tasks, including contracts, recipes, sandboxes, routing, watchers, and workspace graph setup.
- Tests for product workflows, browser companion tooling, prompt behavior, and timeout suppression.

### Changed

- Refined terminal spacing around prompts, status indicators, messages, and the footer.
- Made the footer transparent and matched it to the rest of the terminal styling.
- Restored in-place thinking and tool status indicators while responses are being generated.
- Suppressed the noisy provider timeout panel when the provider raises an empty timeout error.
- Updated README documentation for Browser Companion and the new product workflows.

## 0.1.12

- Previous public release.
