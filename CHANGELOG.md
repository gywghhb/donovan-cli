# Changelog

## 0.1.15 - 2026-05-20

### Fixed

- Fixed Browser Companion setup on Windows so `edge://`, `chrome://`, and other extension pages are opened through the actual browser executable instead of the generic URL handler.
- Prevented the Windows "Get an app to open this link" dialog when setting up Edge or other browser-specific extension pages.
- Browser Companion setup now clearly tells the user when Donovan could not open the extension page automatically and provides the page to open manually.
- Isolated Playwright browser automation on a dedicated worker thread so `browser_open` cannot break the terminal prompt's asyncio loop on Windows.
- Updated Browser Companion guidance so Donovan does not call the old Playwright `browser_open` tool just to open the extension page during companion setup.
- Browser tools now bring the active tab/window forward while Donovan is working, then minimize the browser again after the browser work finishes.
- Browser Companion now supports focus and minimize commands through the extension so already-open browser windows follow the same visible-while-working behavior.

## 0.1.14 - 2026-05-20

### Changed

- Expanded Browser Companion from an Edge/Chrome-oriented setup into a cross-browser WebExtension workflow.
- Generated separate companion extension folders for Chromium-family browsers and Firefox.
- Added setup guidance for Chrome, Edge, Brave, Vivaldi, Opera, Arc, Chromium, and Firefox across Windows, macOS, and Linux.
- Updated browser prompts, tool descriptions, README documentation, and tests to avoid Microsoft Edge-specific assumptions.

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
