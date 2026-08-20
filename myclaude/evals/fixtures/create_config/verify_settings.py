import json
from pathlib import Path


data = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
assert data["mode"] == "dev"
assert data["retry"] == 3
