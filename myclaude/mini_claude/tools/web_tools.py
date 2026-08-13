import html
import json
import re
import urllib.error
import urllib.request

from .base import Tool, ToolContext


class WebFetchTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__(
            "web_fetch",
            "访问一个 HTTP(S) URL 并返回可读文本；HTML 会去除标签",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整的 HTTP(S) URL"},
                "max_length": {"type": "number", "description": "最大字符数，默认 50000"},
            },
            "required": ["url"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        url = args.get("url", "").strip()
        max_length = int(args.get("max_length", 50_000))
        if not url.lower().startswith(("http://", "https://")):
            return "Error: web_fetch only supports HTTP(S) URLs"

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "mini-agent/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = response.headers.get("Content-Type", "")
                charset = response.headers.get_content_charset() or "utf-8"
                text = response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            return f"Error: HTTP {exc.code} {exc.reason}"
        except urllib.error.URLError as exc:
            return f"Error fetching {url}: {exc.reason}"
        except TimeoutError:
            return "Error: request timed out after 30 seconds"
        except OSError as exc:
            return f"Error fetching {url}: {exc}"

        if "html" in content_type.lower():
            text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]*>", " ", text)
            text = html.unescape(text)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if len(text) > max_length:
            text = text[:max_length] + f"\n\n[... truncated at {max_length} characters]"
        return text or "(empty response)"


class WebSearchTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__("web_search", "使用 Tavily 搜索互联网信息")

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        try:
            import tavily
        except ImportError:
            return "Error: 使用 web_search 前请先安装 tavily-python"

        response = tavily.search(query=args["query"], max_results=5)
        return json.dumps(response, ensure_ascii=False, default=str)
