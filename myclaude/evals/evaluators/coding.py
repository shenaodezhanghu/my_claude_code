from __future__ import annotations


def score_changed_files(
    changed: list[str],
    expected: list[str],
    forbidden: list[str],
) -> list[str]:
    errors: list[str] = []
    changed_set = set(changed)
    expected_set = set(expected)

    for name in expected:
        if name not in changed_set:
            errors.append(f"预期文件没有变化：{name}")
    for name in forbidden:
        if name in changed_set:
            errors.append(f"修改了禁止文件：{name}")

    unexpected = changed_set - expected_set
    if expected_set and unexpected:
        errors.append(
            "修改范围超出预期：" + ", ".join(sorted(unexpected))
        )
    return errors


def score_verify_result(
    passed: bool,
    output: str,
) -> list[str]:
    if passed:
        return []
    trimmed = output.strip()
    if len(trimmed) > 1200:
        trimmed = trimmed[:1200] + "\n... verify output truncated ..."
    return ["验证命令失败：" + (trimmed or "无输出")]


def score_expected_answer(
    answer: str,
    expected: str | None,
) -> list[str]:
    if not expected:
        return []
    if expected.lower() in answer.lower():
        return []
    return [f"最终回答没有包含预期内容：{expected}"]
