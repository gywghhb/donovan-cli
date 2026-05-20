# Donovan Agent v0.1.14

This release makes Browser Companion properly cross-browser instead of being centered around Microsoft Edge.

## Highlights

- **Cross-browser Browser Companion**
  Donovan now generates companion extension folders for both Chromium-family browsers and Firefox.

- **Wider browser support**
  The companion setup now targets Chrome, Edge, Brave, Vivaldi, Opera, Arc, Chromium, and Firefox on Windows, macOS, and Linux.

- **No remote debugging required**
  Users can still use the companion extension path when they do not want to launch browsers with debugging flags.

- **Clearer setup instructions**
  `/browser companion setup [browser]` now accepts a browser name and opens the matching extension page where possible.

## Notes

Safari uses a separately packaged, signed Safari Web Extension flow, so the unpacked Donovan companion cannot be loaded directly into Safari yet.

## Verification

- Full test suite passes with `pytest -q`.

## Publish Checklist

```powershell
git status
python -m pytest
git add .
git commit -m "Release v0.1.14"
git tag v0.1.14
git push origin main
git push origin v0.1.14
```
