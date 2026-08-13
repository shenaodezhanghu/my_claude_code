import os
from openai import OpenAI

def create_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")

    if not api_key:
        raise RuntimeError("缺少api_key")
    if not base_url:
        raise RuntimeError("缺少base_url")

    return OpenAI(api_key=api_key, base_url=base_url)


def get_models() -> str:
    return os.environ.get("MINI_CLAUDE_MODEL", "qwen-plus").strip('"\'')
