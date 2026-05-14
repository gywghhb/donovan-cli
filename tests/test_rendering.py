from __future__ import annotations

from donovanagent.ui.render import plain_text


def test_plain_text_strips_common_markdown() -> None:
    raw = """# Title

Here is **bold** and `code`.

```python
print("hi")
```

[Link](https://example.com)
"""
    rendered = plain_text(raw)
    assert "# Title" not in rendered
    assert "**" not in rendered
    assert "```" not in rendered
    assert "Title" in rendered
    assert 'print("hi")' in rendered
    assert "Link (https://example.com)" in rendered
