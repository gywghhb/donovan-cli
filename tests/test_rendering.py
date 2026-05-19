from __future__ import annotations

from io import StringIO

from rich.console import Console

from donovanagent.ui.render import plain_text
from donovanagent.ui.status import ActivityIndicator


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


def test_activity_indicator_can_run_silently() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True)
    with ActivityIndicator(console, visible=False) as indicator:
        indicator.set_word("Thinking")
    assert output.getvalue() == ""
