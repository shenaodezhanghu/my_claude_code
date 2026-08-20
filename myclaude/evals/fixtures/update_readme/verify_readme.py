from pathlib import Path


content = Path("README.md").read_text(encoding="utf-8")
assert "Usage" in content
assert "python main.py" in content
assert Path("app.py").read_text(encoding="utf-8") == 'print("do not edit")\n'
