#!/usr/bin/env python3
"""session-change-digest — Stop hook：自动列出「这一轮我到底改了什么」。

## 为什么有它（2026-08-06 用户决策的配套件之一）

用户放弃的是**逐次事前确认**（弹窗正文是原始 shell / diff，看不懂、每次只会点 yes），
换来的必须是**一次性事后对账**——否则就是净损失：不弹窗 = 不知道 agent 干了什么。
用户原话是「有什么错误或者有什么更改，你在最后给我那个总的报告里面写清楚就好了……
如果有错误，我在最后报告里告诉你，你可以回溯往回找帮我改回来」。

（注：配套方案里「往 permissions.allow 加裸 `Bash`」那条已撤回——官方对 allow 与 hook
ask 的优先级没有明文，赌错会让本仓 20 道闸门静默失效。现行做法见 settings.json 的
`_note`：ask 安全网 + 中文危险命令闸门 + 本清单，总开关由用户自己切权限模式。）

「让 agent 记得在收尾报告里写清单」是**散文级**约束，会被忘（本仓 AGENTS.md 卡#18 的
主题就是这个）。所以清单由 hook **自己从 transcript 算出来打印**，不依赖 agent 的自觉：
agent 忘了写，清单照样出现。

## 收集什么（只收「改了外部世界」的动作，不收读操作）

- **文件**：Write / Edit / MultiEdit / NotebookEdit 的 file_path（去重、按目录归组）。
- **git**：commit（带 message）、push（带分支）、gh pr create / merge、破坏性操作
  （reset --hard / clean -f / stash drop / push --force / branch -D）——**已执行的**
  破坏性操作单独列一行加 ⚠️，因为那正是最该复核的。
- **外发**：Drive 上传、邮件发送、chat 发送。
- **删除/移动**：rm / mv / mkdir -p 之外的文件系统改动。

只读命令（ls/cat/grep/git status/git log…）一律不进清单——那是噪音，收进来会让清单本身
变得没人看。

## 节流：按 transcript 行号增量，不重复报

cache 存「上次报到 transcript 第几行」，每次只扫新增行。这一轮没有任何改动 → 完全静默
（纯问答/查资料的会话不会看到清单）。长会话里每轮报当轮增量，比「最后一次性报一大坨」
更好复核——用户当轮就能说「这个不对，改回来」。

## 能力边界（诚实三条）

1. **它是账本，不是审计**。只说「做了什么」，不判断「做得对不对」。对不对仍要用户看。
2. **只看得见经工具走的动作**。IDE 里手动改文件、脚本内部又调脚本产生的连带改动，
   transcript 里没有 tool_use 记录就收不到。
3. **不 block**。exit 0 + stdout + systemMessage，不打断会话——本 hook 的存在理由就是
   「少打断」，它自己再去打断就荒谬了。代价是呈现方式取决于客户端。

输入：stdin JSON（session_id / transcript_path / stop_hook_active）。
输出：有增量改动 → 打印清单 JSON，exit 0；否则静默 exit 0。异常一律 fail-open。

自测：`python3 session-change-digest.py --selftest`
"""

# ══════════════════════════════════════════════════════════════════════════
# 用户级副本（~/.claude/hooks/）——由 Ona-dotfiles 装到**每一个**项目
#
# 来源：WY-workspace-P 仓库的 .claude/hooks/session-change-digest.py
# 改动纪律：**以来源仓库为准**。改了那边就跑 ~/dotfiles/claude/sync-hooks.sh
# 同步过来；不要只改这一份，否则两边漂移。
#
# 为什么要有用户级副本：项目级的只跟着那个仓库走，换个项目就没了。用户明确要求
# 「以后所有规定默认跨项目、跨容器通用」（2026-08-07），所以安全网必须装在用户级。
#
# 防双响：在**同时**有项目级同名 hook 的仓库里（如 WY-workspace-P），本副本静默
# 退出、让项目级那份跑——它带着仓库自己的上下文，更准。
# ══════════════════════════════════════════════════════════════════════════
import os as _os
import sys as _sys

_proj = _os.environ.get("CLAUDE_PROJECT_DIR") or ""
if _proj and _os.path.isfile(
        _os.path.join(_proj, ".claude", "hooks", _os.path.basename(__file__))):
    _sys.exit(0)          # 项目级已装同名 hook → 让它来，避免同一件事报两遍

import json
import os
import re
import shlex
import sys

CACHE_DIR = os.path.expanduser("~/.cache/claude-change-digest")
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
MAX_LIST = 12          # 单类最多列几条，超出折叠成「…等 N 项」

# ── Bash 命令分类：(标识, 正则, 中文标签) ───────────────────────────────────
ACTION_LABELS = [
    ("destructive", "⚠️ 破坏性操作"),
    ("commit", "git 提交"),
    ("push", "git 推送"),
    ("pr", "GitHub PR"),
    ("upload", "上传到 Drive"),
    ("send", "外发（邮件/chat）"),
    ("rm", "删除文件"),
    ("move", "移动/复制文件"),
]
UPLOAD_RE = re.compile(
    r"MediaFileUpload|files\(\)\.create\(|files\(\)\.update\(|drive[_-]?upload|\bupload\b")
SEND_RE = re.compile(
    r"gmail[_-]?(?:send|compose)|messages\(\)\.send|spaces\(\)\.messages"
    r"|chat[_-]?send|send[_-]?mail|smtplib")

# commit message / 分支名 提取（尽力而为，提不到就只报动作）
MSG_RE = re.compile(r"-m\s+(['\"])(?P<m>.+?)\1", re.S)
BRANCH_RE = re.compile(r"\bpush\s+(?:-u\s+)?(?:origin|upstream)\s+(?P<b>[\w./-]+)")

# heredoc 正文是**数据**不是命令。上线首轮本 hook 就自己踩到：几条 `python3 - <<'PY'`
# 里的文档文字含 "rm -rf /" 字样，被记成「⚠️ 破坏性操作 ×6」，而 detail 显示的是命令
# 首行 `cd /workspaces/...`——既误报又没信息量。剥离后只扫命令行本身。
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1.*?^\s*\2\s*$", re.S | re.M)


def _strip_heredocs(cmd):
    return HEREDOC_RE.sub("<<HEREDOC_BODY_STRIPPED", cmd)


# ── 命令位置解析（与 destructive-command-guard 同源；改一处要同步另一处）──────
# 第一版用正则扫整条命令文本找动作关键字，连续三轮误报：heredoc 正文 → echo 参数 →
# for 循环里的字符串字面量 / commit message 里的中文。根因是**用正则解析 shell**：
# shell 字符串能跨越换行和 `&&`，正则切段会把字符串切开，碎片看起来就像真命令。
# 换成 shlex 词法分析——引号内的内容成为**单个 token**，永远不会出现在命令位置。
SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "{", "}"}
CODE_ARG_HEADS = {"python", "python3", "bash", "sh", "node", "perl", "ruby", "eval"}
WRAPPERS = ("sudo", "time", "nice", "nohup", "command", "timeout")


def _command_units(cmd):
    """把 shell 文本切成处于命令位置的 [(head, args), …]。解析失败返回 None。"""
    try:
        lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = "#"
        toks = list(lex)
    except ValueError:
        return None
    units, cur = [], []
    for t in toks:
        if t in SEPARATORS:
            if cur:
                units.append(cur)
                cur = []
        else:
            cur.append(t)
    if cur:
        units.append(cur)
    out = []
    for u in units:
        head, args = os.path.basename(u[0]), u[1:]
        while head in WRAPPERS and args:
            if head == "timeout" and args and re.match(r"^[\d.]+[smhd]?$", args[0]):
                args = args[1:]
            if not args:
                break
            head, args = os.path.basename(args[0]), args[1:]
        out.append((head, args))
    return out


# ── destructive 判定：**直接复用 destructive-command-guard**，不自己写第二套 ──────
# 为什么（2026-08-07 本 hook 自己的清单抓到的第四个误报）：两处各写一套 git/rm 规则，
# 必然漂移。实证：`rm -rf claude/hooks/__pycache__` 在 guard 那边命中安全区豁免、**不弹窗**，
# 在这边却被记成「⚠️ 破坏性操作」——同一条命令两个结论，清单因此把「删缓存目录」这种日常
# 动作报成危险，稀释了 ⚠️ 的信号价值。
# 判据只有一个来源：guard 的 _scan()。它已内含 heredoc 剥离、文本段跳过、rm 安全区豁免，
# 这边全部自动继承，以后改一处两处都对。加载失败则退回「不标 destructive」——记账少标一个
# 标记是可接受的降级，绝不因此崩掉整份清单。
def _load_destructive_scan():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "destructive-command-guard.py")
    if not os.path.isfile(path):
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_dcg_for_digest", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "_scan", None)
    except Exception:
        return None


_DESTRUCTIVE_SCAN = _load_destructive_scan()


def _git_sub(args):
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a, args[i + 1:]
    return "", []


def _classify(cmd):
    """返回本条命令命中的动作标识集合。基于命令位置，不再文本匹配。"""
    units = _command_units(_strip_heredocs(cmd))
    if units is None:
        return set()
    hits = set()
    for head, args in units:
        r = set(args)
        joined = " ".join(args)
        if head == "git":
            sub, rest = _git_sub(args)
            rs = set(rest)
            # 只做「记账分类」；危险与否统一由 _DESTRUCTIVE_SCAN 判（见文件上方说明）
            if sub == "commit":
                hits.add("commit")
            elif sub == "push":
                hits.add("push")
        elif head == "gh" and args and args[0] == "pr" and len(args) > 1 and args[1] in (
                "create", "merge", "edit", "close", "comment"):
            hits.add("pr")
        elif head == "rm":
            hits.add("rm")            # 危险与否由 _DESTRUCTIVE_SCAN 统一判
        elif head in ("mv", "cp") and len(args) >= 2:
            hits.add("move")
        elif head in CODE_ARG_HEADS:
            # 参数即代码 → 只能文本级近似（上传/外发都是 API 调用，藏在代码里）
            if UPLOAD_RE.search(joined):
                hits.add("upload")
            if SEND_RE.search(joined):
                hits.add("send")
        if UPLOAD_RE.search(joined) and head in ("gws", "rclone", "gsutil", "aws"):
            hits.add("upload")
    # 破坏性：单一事实源（含安全区豁免），与弹窗闸门永远同一结论
    if _DESTRUCTIVE_SCAN:
        try:
            if _DESTRUCTIVE_SCAN(cmd):
                hits.add("destructive")
        except Exception:
            pass
    return hits


def _first_meaningful_line(cmd):
    """取第一条真正有内容的行做 detail——`cd /path` 之类前缀行没有信息量。"""
    for raw in cmd.splitlines():
        line = raw.strip()
        if not line or re.match(r"^cd\s+\S+\s*(&&)?$", line):
            continue
        return line[:80]
    return cmd.strip().splitlines()[0][:80] if cmd.strip() else ""


FAILED_RESULT_RE = re.compile(
    r"denied by|permission for this action was denied|user doesn't want to|"
    r"operation (?:was )?(?:cancelled|aborted)|tool use was rejected",
    re.I,
)


def _iter_tool_uses(transcript_path, start_line):
    """从 start_line 起逐行读 transcript。

    yield ('use', 行号, id, tool_name, tool_input) 与 ('err', 行号, id, None, None)
    —— 后者标记「这次调用被拒/失败」。**被拒的调用不能进改动清单**：上线首轮本 hook 就
    把一个被安全分类器拒绝、根本没写成盘的文件（auto-approve-guard.py）列成了「已改」，
    等于向用户谎报改动。账本必须只记真正发生的事。
    """
    with open(transcript_path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i < start_line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            msg = d.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    yield "use", i, b.get("id") or "", b.get("name") or "", b.get("input") or {}
                elif b.get("type") == "tool_result":
                    body = b.get("content")
                    if not isinstance(body, str):
                        try:
                            body = json.dumps(body, ensure_ascii=False)
                        except Exception:
                            body = ""
                    if b.get("is_error") or FAILED_RESULT_RE.search(body or ""):
                        yield "err", i, b.get("tool_use_id") or "", None, None


def collect(transcript_path, start_line):
    """返回 (清单 dict, 读到的总行数)。"""
    files, actions, last = [], {}, start_line
    pending, failed = [], set()
    for kind, i, uid, name, inp in _iter_tool_uses(transcript_path, start_line):
        last = max(last, i + 1)
        if kind == "err":
            failed.add(uid)
        else:
            pending.append((uid, name, inp))

    for uid, name, inp in pending:
        if uid and uid in failed:
            continue                                   # 被拒/失败的调用不记账
        if name in FILE_TOOLS:
            p = inp.get("file_path") or inp.get("notebook_path")
            if p and p not in files:
                files.append(p)
        elif name == "Bash":
            raw = inp.get("command") or ""
            hit = _classify(raw)
            if "destructive" in hit:
                # rm/move 与 destructive 会重叠：只记更重要的那条，别把同一条命令报两遍
                hit -= {"rm", "move"}
            for key, label in ACTION_LABELS:
                if key not in hit:
                    continue
                detail = ""
                if key == "commit":
                    m = MSG_RE.search(raw)
                    if m:
                        detail = m.group("m").strip().splitlines()[0][:70]
                elif key == "push":
                    m = BRANCH_RE.search(raw)
                    if m:
                        detail = m.group("b")
                elif key == "destructive":
                    detail = _first_meaningful_line(raw)
                bucket = actions.setdefault(key, {"label": label, "n": 0, "detail": []})
                bucket["n"] += 1
                if detail and detail not in bucket["detail"]:
                    bucket["detail"].append(detail)
    return {"files": files, "actions": actions}, last


def _fmt_paths(paths):
    shown = paths[:MAX_LIST]
    tail = "" if len(paths) <= MAX_LIST else "，…等 %d 项" % len(paths)
    return "、".join("`%s`" % p for p in shown) + tail


def render(data):
    files, actions = data["files"], data["actions"]
    if not files and not actions:
        return ""
    lines = ["📋 **本轮改动清单**（自动生成——普通命令已不再逐条弹窗确认，改为在这里事后对账）"]
    if files:
        lines.append("· 改了 %d 个文件：%s" % (len(files), _fmt_paths(files)))
    # 破坏性操作放最前，其余按固定顺序
    order = ["destructive", "commit", "push", "pr", "upload", "send", "rm", "move"]
    for key in order:
        b = actions.get(key)
        if not b:
            continue
        line = "· %s ×%d" % (b["label"], b["n"])
        if b["detail"]:
            line += "：" + "；".join(b["detail"][:3])
        lines.append(line)
    lines.append("有不对的直接说，我回溯改回来（文件改动可 `git diff` 逐条核，已提交的可 revert）。")
    return "\n".join(lines)


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)                                     # fail-open
    if data.get("stop_hook_active"):                    # 防死循环
        sys.exit(0)
    session_id = data.get("session_id") or ""
    transcript_path = data.get("transcript_path") or ""
    if not session_id or not transcript_path or not os.path.isfile(transcript_path):
        sys.exit(0)

    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    cache_file = os.path.join(CACHE_DIR, safe)
    start = 0
    try:
        with open(cache_file, encoding="utf-8") as fh:
            start = int(fh.read().strip() or "0")
    except Exception:
        start = 0

    try:
        collected, last = collect(transcript_path, start)
    except Exception:
        sys.exit(0)                                     # fail-open

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as fh:
            fh.write(str(last))
    except Exception:
        pass                                            # 写不进只会重复报，不阻断

    text = render(collected)
    if not text:
        sys.exit(0)                                     # 本轮没改任何东西 → 静默

    out = {
        "systemMessage": text,
        "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": text},
    }
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


# ── 自测：构造一段假 transcript，验证「该收的收到、不该收的不收」──────────────
def _selftest():
    import tempfile

    def rec(name, inp):
        return json.dumps({"message": {"content": [
            {"type": "tool_use", "name": name, "input": inp}]}}, ensure_ascii=False)

    lines = [
        rec("Bash", {"command": "ls -la"}),                       # 只读 → 不该收
        rec("Bash", {"command": "git status"}),                   # 只读 → 不该收
        rec("Read", {"file_path": "/x/a.md"}),                    # 只读 → 不该收
        rec("Write", {"file_path": "/x/new.py"}),                 # 收
        rec("Edit", {"file_path": "/x/AGENTS.md"}),               # 收
        rec("Edit", {"file_path": "/x/AGENTS.md"}),               # 去重
        rec("Bash", {"command": "git commit -m 'feat: 加闸门'"}),  # 收 + 取 message
        rec("Bash", {"command": "git push -u origin feat/xyz"}),  # 收 + 取分支
        rec("Bash", {"command": "git reset --hard"}),             # 收为破坏性
        rec("Bash", {"command": "python3 up.py MediaFileUpload x.pptx"}),  # 外发
    ]
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    checks, bad = [], 0
    data, last = collect(path, 0)
    out = render(data)

    def want(cond, desc):
        nonlocal bad
        checks.append(("OK " if cond else "FAIL", desc))
        if not cond:
            bad += 1

    want(len(data["files"]) == 2, "文件去重后为 2（Write 1 + Edit 1，重复的 AGENTS.md 只算一次）")
    want("only-read" not in out and "ls -la" not in out, "只读命令不进清单")
    want("feat: 加闸门" in out, "commit message 被提取")
    want("feat/xyz" in out, "push 分支被提取")
    want("⚠️ 破坏性操作" in out, "reset --hard 被标为破坏性")
    want("上传到 Drive" in out, "Drive 上传被收进外发")
    want(last == 10, "读到的行数 = 10（供增量节流用）")

    # 增量：从 last 再扫一次，应无新增 → 空清单（防重复报同一批改动）
    data2, _ = collect(path, last)
    want(render(data2) == "", "增量节流：同一批改动不会第二次上报")

    # 空 transcript → 静默
    fd2, path2 = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd2)
    data3, _ = collect(path2, 0)
    want(render(data3) == "", "无改动的会话完全静默")

    # —— 上线首轮抓到的两个真实 bug，回归用例 ——
    fd4, path4 = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd4, "w", encoding="utf-8") as fh:
        fh.write("\n".join([
            # bug1: heredoc 正文里提到 rm -rf，不该记成破坏性操作
            rec("Bash", {"command": "cd /w && python3 - <<'PY'\ns='文档写 rm -rf / 危险'\nPY"}),
            # bug2: 被拒绝的 Write 不该进清单
            json.dumps({"message": {"content": [
                {"type": "tool_use", "id": "t_denied", "name": "Write",
                 "input": {"file_path": "/w/never-written.py", "content": "x"}}]}},
                ensure_ascii=False),
            json.dumps({"message": {"content": [
                {"type": "tool_result", "tool_use_id": "t_denied", "is_error": True,
                 "content": "Permission for this action was denied"}]}}, ensure_ascii=False),
        ]) + "\n")
    data4, _ = collect(path4, 0)
    out4 = render(data4)
    want("破坏性" not in out4, "bug1 回归：heredoc 正文里的 rm -rf 不再误报为破坏性操作")
    want("never-written.py" not in out4, "bug2 回归：被拒绝的写入不进改动清单（不谎报）")

    # bug1b（第二轮抓到的同类形态）：echo/grep 参数里的危险字样不是破坏性操作
    fd5, path5 = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd5, "w", encoding="utf-8") as fh:
        fh.write("\n".join([
            rec("Bash", {"command": 'echo "=== 复现：提到 rm -rf 就被判危险 ==="'}),
            rec("Bash", {"command": 'grep -n "git reset --hard" .claude/hooks/x.py'}),
            rec("Bash", {"command": 'MSG="rm -rf /tmp/x"'}),
        ]) + "\n")
    data5, _ = collect(path5, 0)
    want(render(data5) == "",
         "bug1b 回归：echo/grep/赋值里的危险字样不记为破坏性操作")
    # 但同一条命令链里真的执行了危险操作，仍要记
    fd6, path6 = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd6, "w", encoding="utf-8") as fh:
        fh.write(rec("Bash", {"command": 'echo "开始清理" && rm -rf /workspaces/x'}) + "\n")
    data6, _ = collect(path6, 0)
    want("破坏性" in render(data6), "别修过头：安全段之后的真危险操作仍要记账")

    # bug1c（第四轮，本 hook 的清单又抓到自己）：destructive 判据必须与弹窗闸门一致。
    # 曾经两处各写一套 git/rm 规则 → `rm -rf .../__pycache__` 在 guard 那边命中安全区
    # 豁免、不弹窗，在这边却被记成「⚠️ 破坏性操作」。同一条命令两个结论 = 判据漂移。
    for _cmd, _want, _why in [
        ("rm -rf claude/hooks/__pycache__", False, "安全区：删缓存目录不算破坏性"),
        ("rm -rf /tmp/build", False, "安全区：临时目录"),
        ("git reset -q HEAD p || git rm -r --cached -q p", False, "只动索引不碰工作区"),
        ("rm -rf /workspaces/proj/out", True, "工作区目录，回不去"),
        ("git reset --hard", True, "丢未提交改动"),
        ("git push --force origin main", True, "覆盖远端历史"),
    ]:
        want(("destructive" in _classify(_cmd)) == _want,
             "bug1c 判据一致：%s（%s）" % (_cmd[:38], _why))

    # bug1c（第三轮）：真实误报形态——命令位置解析后才根治
    fd7, path7 = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd7, "w", encoding="utf-8") as fh:
        fh.write("\n".join([
            rec("Bash", {"command": "python3 .claude/hooks/x.py --selftest >/dev/null 2>&1"}),
            rec("Bash", {"command": "git worktree remove .claude/worktrees/tmp"}),
            rec("Bash", {"command": "git commit -q -F - <<'EOF'\nfix: `rm -rf /x` 曾被放行\nEOF"}),
        ]) + "\n")
    data7, _ = collect(path7, 0)
    out7 = render(data7)
    want("破坏性" not in out7,
         "bug1c 回归：跑自测/删worktree/commit正文提到删除，都不记为破坏性操作")
    want("git 提交" in out7, "别修过头：真的 commit 仍要记账")
    try:
        os.unlink(path7)
    except Exception:
        pass
    for _p in (path5, path6):
        try:
            os.unlink(_p)
        except Exception:
            pass
    want(out4 == "", "两个 bug 都修掉后，这段 transcript 应完全静默")

    for status, desc in checks:
        print("%s %s" % (status, desc))
    print("\n%d/%d 通过" % (len(checks) - bad, len(checks)))
    if bad == 0:
        print("\n--- 渲染样例 ---\n" + out)
    for p in (path, path2):
        try:
            os.unlink(p)
        except Exception:
            pass
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    main()
