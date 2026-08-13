# Day1

## 今日完成

- 完成基础 Agent Loop，并接入 `ToolRegistry` 和 `ToolContext`。
- 实现文件读取、写入、编辑、文件列表、内容搜索、Shell、网页读取和联网搜索等工具。
- 使用 `read-before-edit` 和文件修改时间检查，降低覆盖外部修改的风险。

## 遇到的问题/修改

### 1. 模型读取了任务之外的 `main.py`

任务是“在 `test_tools.py` 中新增 `calculate_sum` 函数”，但模型先后读取了 `AGENTS.md`、目标文件、相似文件以及 `main.py`。

原因：项目中存在 `test_tools.py`，但是不在运行和子级文件夹中，于是他自己创建了一个在运行目录中，忽略了 `mini_claude/tools/test_tool.py`。模型为了判断目标文件、项目结构和运行入口继续进行了探索。读取 `main.py` 是因为在prompt中添加了“修改前先理解相关代码和现有风格。”。

优化：可以优化 System Prompt：当用户已经明确目标文件时，优先读取目标文件及其直接依赖；如果文件不存在，应先说明歧义或选择最接近的文件，避免无关地读取入口文件。

### 2. `run_shell` 出现 GBK 解码错误

执行 Python 文件时出现：

```text
UnicodeDecodeError: 'gbk' codec can't decode byte ...
```

原因：Windows 子进程默认使用 GBK 解码输出，而被执行程序输出了 GBK 无法解析的字节，导致 `subprocess` 的输出读取线程报错。

工具优化：在 `RunShellTool` 的 `subprocess.run()` 中明确指定输出编码和错误处理：

```python
text=True,
encoding="utf-8",
errors="replace",
```

这样即使输出中包含异常字节，工具也不会因为解码失败而中断。

### 3. 流式文字成块出现，不够平滑

背景：第五章接入流式输出后，程序会在收到 `delta.content` 时立即打印模型返回的内容。

问题：模型返回的 `delta.content` 本身是不等长的文本块。直接打印整个 chunk 时，终端中的文字会一片一片出现，而不是平滑显示。

优化方法：在 Agent 中增加平滑输出方法，将每个文本块拆成字符依次显示：

```python
def print_smooth(
    self,
    text: str,
    delay: float = 0.01,
) -> None:
    for char in text:
        print(char, end="", flush=True)
        time.sleep(delay)
```

收到流式文本后调用：

```python
self.print_smooth(delta.content)
```

这种方式改善了终端显示效果，但逐字符等待会轻微降低整体输出速度。后续可以使用“接收队列 + 独立输出任务”，避免阻塞流的接收。

### 4. 删除深层文件时没有先搜索

背景：用户要求删除 `test_tool.py`，但只提供了文件名，没有提供完整相对路径。实际文件位于 `mini_claude/tools/test_tool.py`。

问题：模型只在项目根目录检查 `test_tool.py`，随后认为文件不存在，没有使用 `list_files` 搜索整个项目，因此遗漏了深层目录中的同名文件。

优化方法：在 System Prompt 中明确文件定位流程：

```text
当用户只提供文件名、没有提供完整相对路径时，
必须先调用 list_files(pattern="**/文件名") 搜索整个项目。

只有找到唯一目标路径后才能执行操作；
如果存在多个同名文件，必须先让用户确认。
```

同时优化 `run_shell` 的描述，强调文件定位优先使用 `list_files`，不要使用 Shell 的 `dir`、`find` 或 `Get-ChildItem` 代替已有专用工具。删除属于破坏性操作，确定目标路径后仍应经过权限确认。

