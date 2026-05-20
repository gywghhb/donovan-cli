# Donovan Agent v0.1.15

This patch fixes Browser Companion setup on Windows.

## Fixed

- Donovan no longer opens `edge://extensions/`, `chrome://extensions/`, or similar browser pages through Windows' generic URL handler.
- Setup now launches the actual browser executable when possible, such as `msedge`, `chrome`, `firefox`, `brave`, `vivaldi`, or `opera`.
- This prevents the Windows "Get an app to open this link" popup during `/browser companion setup edge`.
- If Donovan cannot find the browser executable, it prints the extension page to open manually instead of triggering the popup.

## Verification

- Full test suite passes with `pytest -q`.
