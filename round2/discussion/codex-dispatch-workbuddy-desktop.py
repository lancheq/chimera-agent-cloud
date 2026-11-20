"""Prepare portable WorkBuddy desktop task cards and validate returned results."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

PREPARED = "workbuddy.desktop_prepared"
RESULT = "workbuddy.desktop_result"


def prepare(dispatch_id, prompt_file, log_file, root):
    prompt = Path(prompt_file).read_bytes()
    text = prompt.decode("utf-8")
    if not text.strip():
        raise ValueError("任务卡为空")
    digest = hashlib.sha256(prompt).hexdigest()
    outbox = Path(os.environ.get(
        "CODEX_DISPATCH_WORKBUDDY_DESKTOP_OUTBOX", str(Path(root) / "desktop-outbox")
    )).expanduser().resolve()
    outbox.mkdir(mode=0o700, parents=True, exist_ok=True)
    card = outbox / f"{dispatch_id}.md"
    receipt = json.dumps({
        "dispatch_id": dispatch_id, "prompt_sha256": digest,
        "status": "completed", "result": "任务正文的实际结果",
    }, ensure_ascii=False, indent=2)
    header = f"""# WorkBuddy 桌面任务交接

请在个人桌面版 WorkBuddy 新建任务，在模型选择器中选 DeepSeek V4.1 Flash。
将本卡交给该会话。任务结束后，请按下面的 JSON 格式回复，或将 JSON 保存为
`{dispatch_id}.result.json`。任务正文指定的输出格式用于 `result` 字段。
原样复制派发编号和指纹。任务执行失败时，把
`status` 改成 `failed`，在 `result` 写清已完成部分与阻塞原因。

```json
{receipt}
```

## 任务正文

"""
    with NamedTemporaryFile(dir=outbox, prefix=".dispatch-", delete=False) as output:
        temporary = Path(output.name)
        output.write(header.encode("utf-8") + prompt)
    try:
        with Path(log_file).open("x", encoding="utf-8") as log:
            print(json.dumps({
                "type": PREPARED, "dispatch_id": dispatch_id, "prompt_sha256": digest,
                "card": str(card),
            }, ensure_ascii=False), file=log)
        os.replace(temporary, card)
    finally:
        temporary.unlink(missing_ok=True)
    print(card)


def receive(dispatch_id, log_file, result_file):
    # A result file is supplied by the operator or by the explicitly configured shared folder.
    result = json.loads(Path(result_file).read_text(encoding="utf-8-sig"))
    if (
        not isinstance(result, dict) or result.get("dispatch_id") != dispatch_id
        or result.get("status") not in ("completed", "failed")
        or not isinstance(result.get("result"), str) or not result["result"].strip()
    ):
        raise ValueError("结果格式、派发编号或正文不合法")
    with Path(log_file).open("r+", encoding="utf-8") as log:
        fcntl.flock(log, fcntl.LOCK_EX)
        events = (json.loads(line) for line in log)
        prepared = next(events, {})
        if (
            prepared.get("type") != PREPARED or prepared.get("dispatch_id") != dispatch_id
            or result.get("prompt_sha256") != prepared["prompt_sha256"]
        ):
            raise ValueError("交接卡与结果文件的编号或任务指纹不一致")
        event = {
            "type": RESULT, "result_status": result["status"], "text": result["result"],
            "model_verification": "desktop_ui_required",
        }
        existing = next((entry for entry in events if entry.get("type") == RESULT), None)
        if existing is None:
            print(json.dumps(event, ensure_ascii=False), file=log, flush=True)
        elif existing != event:
            raise ValueError("此任务已有不同的结果回执")
    print("已读回桌面结果。模型型号由 WorkBuddy 界面核验。")


def main(command, *args):
    try:
        if command == "prepare":
            prepare(*args)
        elif command == "receive":
            receive(*args)
        else:
            raise ValueError("未知桌面交接操作")
    except (OSError, ValueError) as exc:
        print(f"个人桌面版 WorkBuddy 交接失败：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
