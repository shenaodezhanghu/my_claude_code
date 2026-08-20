from pathlib import Path


content = Path("docs/guide.md").read_text(encoding="utf-8")
assert "Status: done" in content
assert "TODO" not in content
