#!/usr/bin/env bash
# codex-dispatch — 派发和续写 Codex 或 WorkBuddy 会话
#
# 为什么存在这个脚本：
#   codex_app 这个 MCP（create_thread / send_message_to_thread）走 HTTP
#   http://127.0.0.1:<随机端口>/mcp，端口随桌面端启动漂移，且会话创建时会把
#   当时的端点固化进上下文，一旦 app 重启该会话就永久指向死端口。
#   默认使用桌面 app-server 的稳定 Unix 控制套接字。exec 保留为显式后端。
#
# 依赖：codex CLI（PATH 或 CODEX_BIN）、Python 3、util-linux（setsid、flock）

set -uo pipefail

CODEX_BIN="${CODEX_BIN:-$(command -v codex || echo "$HOME/.local/bin/codex")}"
ROOT="${CODEX_DISPATCH_HOME:-$HOME/.codex-dispatch}"
LOG_DIR="$ROOT/logs"
REG="$ROOT/registry.jsonl"
EFFORT_DEFAULT="${CODEX_DISPATCH_EFFORT:-high}"
SANDBOX_DEFAULT="${CODEX_DISPATCH_SANDBOX:-workspace-write}"
APPROVAL_DEFAULT="${CODEX_DISPATCH_APPROVAL:-never}"
BACKEND="${CODEX_DISPATCH_BACKEND:-app-server}"
APP_DIR="${CODEX_DISPATCH_APP_DIR:-$HOME/.local/share/codex-dispatch}"

die() { echo "错误: $*" >&2; exit 2; }

usage() {
  cat <<'EOF'
codex-dispatch — 派发、续写和查看回执

用法：
  codex-dispatch new  --name <标签> --cd <工作目录> [--model M] [--effort E] [--goal-file G]
                      (--prompt-file F | --prompt "文本")
  codex-dispatch send <thread-id> --cd <工作目录> [--model M] [--effort E] [--goal-file G]
                      (--prompt-file F | --prompt "文本")
  codex-dispatch new --backend workbuddy --name <标签> --cd <本地登记目录> --prompt-file F
  codex-dispatch send wb:<session-id> --backend workbuddy --cd <本地登记目录> --prompt-file F
  codex-dispatch new --backend workbuddy-desktop --name <标签> --cd <本地登记目录> --prompt-file F
  codex-dispatch receive <dispatch-id> --result-file F
  codex-dispatch read <thread-id> [--limit N] [--cursor CURSOR]
  codex-dispatch list
  codex-dispatch status <dispatch-id>
  codex-dispatch log <dispatch-id> [-f]

子命令：
  new     新建一个 Codex 会话，在后台跑第一轮（等价 create_thread + 首条消息）
  send    向已有会话追加一轮（等价 send_message_to_thread）
  read    读取会话元数据、最近对话与原生 Goal
  list    列出本脚本发起过的派发
  status  查看某个派发的运行状态（running / completed / failed）
  log     打印派发日志末尾，加 -f 持续跟随
  receive 导入个人桌面版 WorkBuddy 结果文件

可选环境变量：
  CODEX_BIN                    codex 可执行文件路径
  CODEX_DISPATCH_HOME          运行目录（默认 ~/.codex-dispatch）
  CODEX_DISPATCH_MODEL         默认模型（CHIMERA-agent 为 gpt-6.2-sol，其他目录为 gpt-6.1-sol）
  CODEX_DISPATCH_EFFORT        默认推理强度（high）
  CODEX_DISPATCH_SANDBOX       默认沙箱（workspace-write）
  CODEX_DISPATCH_APPROVAL      默认审批策略（never）
  CODEX_DISPATCH_BACKEND       默认 app-server，可显式选择 exec
  CODEX_DISPATCH_WORKBUDDY_DESKTOP_OUTBOX  个人桌面版交接卡目录，默认 ~/.codex-dispatch/desktop-outbox
EOF
}

# ---------- 参数解析 ----------
CMD="${1:-}"; [ -n "$CMD" ] || { usage; exit 1; }; shift || true

NAME=""; CWD=""; MODEL="${CODEX_DISPATCH_MODEL:-}"; EFFORT="$EFFORT_DEFAULT"
PROMPT=""; PROMPT_FILE=""; GOAL_FILE=""; THREAD=""; FOLLOW=0; DID=""; LIMIT=3; CURSOR=""
EXPLICIT_BACKEND=""; EXPLICIT_MODEL=""; EXPLICIT_EFFORT=""
RESULT_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --name|--cd|--model|--effort|--prompt|--prompt-file|--goal-file|--backend|--limit|--cursor|--result-file)
      [ "$#" -ge 2 ] && [ -n "$2" ] || die "$1 需要参数" ;;
  esac
  case "$1" in
    --name)        NAME="${2:-}"; shift 2 ;;
    --cd)          CWD="${2:-}"; shift 2 ;;
    --model)       MODEL="$2"; EXPLICIT_MODEL="$2"; shift 2 ;;
    --effort)      EFFORT="$2"; EXPLICIT_EFFORT="$2"; shift 2 ;;
    --prompt)      PROMPT="${2:-}"; shift 2 ;;
    --prompt-file) PROMPT_FILE="${2:-}"; shift 2 ;;
    --goal-file)   GOAL_FILE="$2"; shift 2 ;;
    --result-file) [ "$CMD" = receive ] || die "--result-file 仅用于 receive"
                   RESULT_FILE="$2"; shift 2 ;;
    --backend)     BACKEND="$2"; EXPLICIT_BACKEND="$2"; shift 2 ;;
    --limit)       LIMIT="$2"; shift 2 ;;
    --cursor)      CURSOR="$2"; shift 2 ;;
    -f|--follow)   FOLLOW=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    -*)            die "未知选项 $1" ;;
    *)
      if [[ "$CMD" = send || "$CMD" = read ]] && [ -z "$THREAD" ]; then THREAD="$1"
      elif [[ "$CMD" = status || "$CMD" = log || "$CMD" = receive ]] && [ -z "$DID" ]; then DID="$1"
      else die "多余参数 $1"
      fi
      shift ;;
  esac
done

dispatch_client() {
  local name="$1" client
  client="$(dirname -- "${BASH_SOURCE[0]}")/codex-dispatch-$name.py"
  [ -r "$client" ] || client="$APP_DIR/$name.py"
  [ -r "$client" ] || die "缺少 $name 派发客户端"
  realpath -- "$client" || die "无法定位 $name 派发客户端"
}

load_prompt() {
  if [ -n "$PROMPT_FILE" ] && [ -n "$PROMPT" ]; then die "--prompt 与 --prompt-file 只能给一个"; fi
  if [ -n "$PROMPT_FILE" ]; then
    [ -f "$PROMPT_FILE" ] && [ -r "$PROMPT_FILE" ] || die "读不到 $PROMPT_FILE"
    [ -s "$PROMPT_FILE" ] || die "任务卡为空：$PROMPT_FILE"
  else
    [ -n "$PROMPT" ] || die "必须提供 --prompt 或 --prompt-file"
  fi
}

new_id() { printf 'd%s-%s' "$(date +%Y%m%d%H%M%S)" "$(head -c3 /dev/urandom | od -An -tx1 | tr -d ' \n')"; }

# 守护 shell 持有派发锁直到 CLI 退出，记录退出码后再释放。
spawn() {
  local log="$1"; local pidfile="$2"; local stdinsrc="$3"; local exitfile="$4"; shift 4
  setsid bash -c '
    printf "%s\n" "$$" > "$1" || exit 1
    exitfile="$2"
    shift 2
    "$@" < "$0" 9>&-
    result=$?
    printf "%s\n" "$result" > "$exitfile.tmp" && mv -f -- "$exitfile.tmp" "$exitfile"
    exit "$result"
  ' "$stdinsrc" "$pidfile" "$exitfile" "$@" >"$log" 2>&1 &
  for _ in $(seq 1 50); do
    [ -s "$pidfile" ] && return 0
    sleep 0.1
  done
  return 1
}

record() {
  local id="$1" mode="$2" thread="$3" log="$4" pidfile="$5" name="$6" cwd="$7"
  flock "$ROOT/registry.lock" python3 - "$id" "$mode" "$thread" "$log" "$pidfile" "$name" "$cwd" "$(date -Is)" "$BACKEND" >>"$REG" <<'PY'
import json
import sys

fields = ("id", "mode", "thread", "log", "pidfile", "name", "cwd", "started_at", "backend")
print(json.dumps(dict(zip(fields, sys.argv[1:])), ensure_ascii=False))
PY
}

case "$CMD" in
  read)
    [[ "$THREAD" =~ ^[[:xdigit:]]{8}(-[[:xdigit:]]{4}){3}-[[:xdigit:]]{12}$ ]] || die "read 需要 UUID 格式的 thread-id"
    [[ "$LIMIT" =~ ^([1-9]|[1-4][0-9]|50)$ ]] || die "--limit 需要 1 到 50"
    exec "$APP_DIR/venv/bin/python" "$APP_DIR/app-server.py" read "${THREAD,,}" "$LIMIT" "$CURSOR"
    ;;

  new|send)
    if [ "$CMD" = new ]; then
      [ -n "$NAME" ] || die "new 需要 --name"
    elif [ "$BACKEND" = workbuddy ]; then
      [[ "$THREAD" =~ ^wb:[A-Za-z0-9_-]+$ ]] || die "WorkBuddy send 需要 wb:<session-id>"
    else
      [[ "$THREAD" =~ ^[[:xdigit:]]{8}(-[[:xdigit:]]{4}){3}-[[:xdigit:]]{12}$ ]] || die "send 需要 UUID 格式的 thread-id"
      THREAD="${THREAD,,}"
    fi
    [ -n "$CWD" ] || die "$CMD 需要 --cd"
    CWD="$(cd -- "$CWD" && pwd -P)" || die "无法进入工作目录"
    if [ -z "$MODEL" ]; then
      case "$CWD" in
        "$HOME/CHIMERA-agent"|"$HOME/CHIMERA-agent/"*) MODEL="gpt-6.2-sol" ;;
        *) MODEL="gpt-6.1-sol" ;;
      esac
    fi
    load_prompt
    case "$BACKEND" in
      app-server)
        [ -x "$APP_DIR/venv/bin/python" ] && [ -r "$APP_DIR/app-server.py" ] ||
          die "缺少 app-server 派发客户端"
        APP_DIR="$(cd -- "$APP_DIR" && pwd -P)" || die "无法定位 app-server 派发客户端" ;;
      exec)
        [ -z "$GOAL_FILE" ] || die "--goal-file 需要 app-server 后端"
        CODEX_BIN="$(command -v "$CODEX_BIN")" || die "找不到 Codex CLI"
        [[ "$CODEX_BIN" = /* ]] || CODEX_BIN="$PWD/$CODEX_BIN" ;;
      workbuddy)
        [ "$EXPLICIT_BACKEND" = workbuddy ] || die "WorkBuddy 需要显式 --backend workbuddy"
        [ -n "$PROMPT_FILE" ] || die "WorkBuddy 需要 --prompt-file"
        [ -z "$GOAL_FILE$EXPLICIT_EFFORT" ] || die "WorkBuddy 不接受 --goal-file 或 --effort"
        [[ "$EXPLICIT_MODEL" = "" || "$EXPLICIT_MODEL" = deepseek-v4.1-flash ]] ||
          die "WorkBuddy 后端固定使用 deepseek-v4.1-flash"
        WORKBUDDY_CLIENT="$(dispatch_client workbuddy)" || exit 2
        python3 "$WORKBUDDY_CLIENT" --check-config >&2 || exit 2
        ;;
      workbuddy-desktop)
        [[ "$EXPLICIT_BACKEND" = workbuddy-desktop && "$CMD" = new ]] ||
          die "个人桌面版需要显式 new --backend workbuddy-desktop"
        [ -n "$PROMPT_FILE" ] || die "WorkBuddy 需要 --prompt-file"
        [ -z "$GOAL_FILE$EXPLICIT_EFFORT" ] || die "个人桌面版不接受 --goal-file 或 --effort"
        [[ "$EXPLICIT_MODEL" = "" || "$EXPLICIT_MODEL" = deepseek-v4.1-flash ]] ||
          die "个人桌面版任务卡要求选择 DeepSeek V4.1 Flash"
        DESKTOP_CLIENT="$(dispatch_client workbuddy-desktop)" || exit 2
        ;;
      *) die "未知后端 $BACKEND" ;;
    esac
    umask 077
    mkdir -p -- "$LOG_DIR" "$ROOT/locks" || die "无法创建派发目录 $ROOT"
    ROOT="$(cd -- "$ROOT" && pwd -P)" || die "无法定位派发目录"
    LOG_DIR="$ROOT/logs"; REG="$ROOT/registry.jsonl"
    if [ "$CMD" = send ] && [ "$BACKEND" != workbuddy ]; then
      exec 9>"$ROOT/locks/$THREAD.lock" || die "无法打开派发锁"
      flock -n 9 || die "该会话已有在途 send，请先查看上一笔回执"
    fi

    DID="$(new_id)"
    LOG="$LOG_DIR/$DID.log"; PIDF="$LOG_DIR/$DID.pid"; PSF="$LOG_DIR/$DID.prompt"
    if [ -n "$PROMPT_FILE" ]; then
      cp -- "$PROMPT_FILE" "$PSF" || die "无法保存任务卡"
    else
      printf '%s' "$PROMPT" >"$PSF" || die "无法保存任务卡"
    fi
    if [ -n "$GOAL_FILE" ]; then
      [ -f "$GOAL_FILE" ] && [ -s "$GOAL_FILE" ] || die "Goal 文件不存在或为空"
      cp -- "$GOAL_FILE" "$LOG_DIR/$DID.goal" || die "无法保存 Goal 文件"
      GOAL_FILE="$LOG_DIR/$DID.goal"
    fi

    if [ "$BACKEND" = workbuddy-desktop ]; then
      if CARD="$(python3 "$DESKTOP_CLIENT" prepare "$DID" "$PSF" "$LOG" "$ROOT")"; then
        record "$DID" "$CMD" "$THREAD" "$LOG" "$PIDF" "$NAME" "$CWD" || die "无法写入派发台账"
        printf 'dispatch-id: %s\ncard:        %s\nlog:         %s\n' "$DID" "$CARD" "$LOG"
        echo "已生成交接卡，等待 WorkBuddy 桌面端处理。"
        exit 0
      fi
      exit 2
    fi
    record "$DID" "$CMD" "$THREAD" "$LOG" "$PIDF" "$NAME" "$CWD" || die "无法写入派发台账"
    cd -- "$CWD" || die "无法进入 $CWD"
    if [ "$BACKEND" = app-server ]; then
      RUN=("$APP_DIR/venv/bin/python" "$APP_DIR/app-server.py" "$CMD" "$THREAD" "$CWD" "$NAME"
           "$MODEL" "$EFFORT" "$SANDBOX_DEFAULT" "$APPROVAL_DEFAULT" "$DID" "$GOAL_FILE")
    elif [ "$BACKEND" = workbuddy ]; then
      RUN=(python3 "$WORKBUDDY_CLIENT" "${THREAD#wb:}" "$ROOT/locks")
    else
      RUN=("$CODEX_BIN" exec)
      if [ "$CMD" = send ]; then
        RUN+=(resume)
      else
        RUN+=(-C "$CWD")
      fi
      RUN+=(--json --skip-git-repo-check
            -c "model=\"$MODEL\"" -c "model_reasoning_effort=\"$EFFORT\""
            -c "sandbox_mode=\"$SANDBOX_DEFAULT\"" -c "approval_policy=\"$APPROVAL_DEFAULT\"")
      [ -z "$THREAD" ] || RUN+=("$THREAD")
      RUN+=(-)
    fi
    spawn "$LOG" "$PIDF" "$PSF" "$LOG_DIR/$DID.exit" "${RUN[@]}" || die "启动失败，见 $LOG"
    echo "dispatch-id: $DID"
    [ -z "$THREAD" ] || echo "thread:      $THREAD"
    echo "log:         $LOG"
    echo "已登记请求，使用 status $DID 查看完成回执。"
    ;;

  receive)
    [[ "$DID" =~ ^d[0-9]{14}-[0-9a-f]{6}$ ]] || die "receive 需要 dispatch-id"
    [ -n "$RESULT_FILE" ] || die "receive 需要 --result-file"
    DESKTOP_CLIENT="$(dispatch_client workbuddy-desktop)" || exit 2
    umask 077
    exec python3 "$DESKTOP_CLIENT" receive "$DID" "$LOG_DIR/$DID.log" "$RESULT_FILE"
    ;;

  list)
    [ -f "$REG" ] || { echo "（无记录）"; exit 0; }
    python3 - "$REG" <<'PY'
import json
import sys

print(f"{'ID':22} {'MODE':6} {'BACKEND':17} {'THREAD':38} NAME")
with open(sys.argv[1], encoding="utf-8") as registry:
    for number, line in enumerate(registry, 1):
        try:
            row = json.loads(line)
            print(f"{row['id']:22} {row['mode']:6} {row.get('backend', '—'):17} {row['thread']:38} {row['name']}")
        except (ValueError, KeyError, TypeError):
            print(f"台账第 {number} 行无法读取", file=sys.stderr)
PY
    ;;

  status|log)
    [[ "$DID" =~ ^d[0-9]{14}-[0-9a-f]{6}$ ]] || die "$CMD 需要 dispatch-id"
    LOG="$LOG_DIR/$DID.log"; PIDF="$LOG_DIR/$DID.pid"
    [ -f "$LOG" ] || die "找不到 $LOG"
    if [ "$CMD" = "log" ]; then
      if [ "$FOLLOW" = "1" ]; then tail -f "$LOG"; else tail -n 40 "$LOG"; fi
      exit 0
    fi
    python3 - "$DID" "$LOG" "$PIDF" "$LOG_DIR/$DID.exit" <<'PY'
import json
from pathlib import Path
import subprocess
import sys

dispatch, log, pidfile, exitfile = sys.argv[1:]
pid = Path(pidfile).read_text().strip() if Path(pidfile).is_file() else ""
exit_code = Path(exitfile).read_text().strip() if Path(exitfile).is_file() else ""
live = False
if not exit_code and pid.isdecimal():
    status = subprocess.run(["ps", "-o", "stat=", "-p", pid], capture_output=True, text=True)
    live = status.returncode == 0 and not status.stdout.strip().startswith(("Z", "X"))

thread = ""
turn = ""
writer_conflict = False
waiting = False
turn_id = ""
goal_status = ""
workbuddy = False
model = ""
desktop_card = ""
desktop_result = ""
with open(log, encoding="utf-8", errors="replace") as events:
    for line in events:
        try:
            event = json.loads(line)
        except ValueError:
            writer_conflict |= "thread-store conflict:" in line and "already has an active writer" in line
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "thread.started":
            thread = event.get("thread_id", "")
        elif kind == "workbuddy.session":
            workbuddy = True
            thread = "wb:" + event["session_id"]
        elif kind == "dispatch.waiting":
            waiting = True
        elif kind == "turn.started":
            waiting = False
            turn_id = event.get("turn_id", "")
        elif kind in ("goal.updated", "goal.event"):
            goal_status = event.get("status", "")
        elif kind == "goal.status":
            goal_status = (event.get("goal") or {}).get("status", "missing")
        elif kind in ("turn.completed", "turn.failed"):
            turn = kind
        elif kind in ("workbuddy.completed", "workbuddy.failed"):
            workbuddy = True
            model = event.get("model", "")
            turn = kind
        elif kind == "workbuddy.desktop_prepared":
            desktop_card = event["card"]
        elif kind == "workbuddy.desktop_result":
            desktop_result = event["result_status"]
        elif kind == "error":
            writer_conflict |= "already has an active writer" in str(event.get("message", ""))

if desktop_card:
    state = {"": "awaiting_desktop", "completed": "returned", "failed": "failed"}[desktop_result]
elif live:
    state = "running"
elif (turn == "turn.completed" and exit_code in ("", "0")) or (
    turn == "workbuddy.completed" and exit_code == "0"
):
    state = "completed"
else:
    state = "failed"

print(f"dispatch: {dispatch}")
print(f"state:    {state}")
if not desktop_card:
    print(f"pid:      {pid or '?'}")
if desktop_card:
    print("backend:  workbuddy-desktop")
    print(f"card:     {desktop_card}")
    print("model:    DeepSeek V4.1 Flash (等待或依 WorkBuddy 界面核验)")
elif workbuddy:
    print("backend:  workbuddy")
    print(f"session:  {thread or '（没有 WorkBuddy 会话回执）'}")
    if model:
        print(f"model:    {model}")
else:
    print(f"thread:   {thread or '（没有 thread.started 回执）'}")
print(f"log:      {log}")
if exit_code:
    print(f"exit:     {exit_code}")
if turn_id:
    print(f"turn:     {turn_id}")
if goal_status:
    print(f"goal:     {goal_status}")
if live and waiting:
    print("进度：目标会话正在执行上一轮，派发器会在该轮结束后继续。")
if state == "failed" and writer_conflict:
    print("原因：会话已有写入者。请等待原轮结束并由持有客户端释放会话后再续写。")
PY
    ;;

  *) usage; exit 1 ;;
esac
