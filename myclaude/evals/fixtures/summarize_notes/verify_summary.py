from pathlib import Path


content = Path("notes/summary.md").read_text(encoding="utf-8")
assert "alpha" in content
assert "first" in content
assert "beta" in content
assert "second" in content
