from __future__ import annotations


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, text.strip()

    end = normalized.find("\n---\n", 4)
    if end < 0:
        return {}, text.strip()

    header = normalized[4:end]
    body = normalized[end + 5 :].strip()
    metadata: dict[str, str] = {}

    for raw_line in header.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        clean_key = key.strip().lower()
        clean_value = value.strip().strip('"\'')
        if clean_key:
            metadata[clean_key] = clean_value

    return metadata, body