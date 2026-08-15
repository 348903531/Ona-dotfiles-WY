#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unverified-claim-guard —— 你说了「验证通过 / 已装好 / 全绿」，
但那次验证的输出其实是失败的，而且**之后没重跑过**。

## 为什么有这道闸门（同一形态一轮内犯了三次）

2026-08-14 一轮会话里：

1. 「单臂精度法 → exit 0」的测试一直绿，但 fixture 里含「单阶段」，
   design_method 被判成 ahern，**压根没跑到精度法分支**——绿在别处。
2. 新写的 hook 测试「该拦」两条静默放行，fixture 少了 `message.content`
   外层包装，`_scan` 一条都读不到。我先看到 6/10 才发现。
3. commit message 里写「装到 ~/.claude/hooks/ 后行为双验证通过」，
   而那一刻文件**根本还没装**——验证命令报了 `No such file`，
   我没看输出就把 commit 推了出去。

三次的共同形态不是「测试写得不好」，是**「我在报告里写了『验证通过』，
而那次验证的输出其实是失败的，我没看就往下走」**。

这有稳定可判信号：工具输出里有失败标志（`No such file`、`Traceback`、`❌`、
`FAILED`、非零退出），紧接着的助手正文却出现「通过 / 成功 / 已装好 / 全绿」，
**且中间没有重跑那条命令并转绿**。

## 判据（三条同时命中才提醒）

A. 某次 tool_result 里含**失败标志**；
B. 在它**之后**的助手正文出现**成功断言**（验证通过 / 已装好 / 全绿 / 都过了 …）；
C. A 与 B 之间**没有**一次「同一条命令重跑且这次没报错」的记录。

C 是误报控制的核心：本仓大量测试是**故意制造失败**（「没红过的绿=没测过」，
写闸门必须先看它红）。那种情况一定伴随「回退→重跑→转绿」，C 就把它放过。

## 诚实边界

* 判不了「这句成功断言到底指哪次验证」——只做时序上的邻接近似。
* 只看文本层信号。工具静默失败（退出码 0 但结果是错的）抓不到，
  那类靠各闸门自己的真绿判据。
* 定 reminder 不是 deny：Stop 阶段话已出口，拦不住；目的是让**下一轮**回去补验。

逃生阀：`UNVERIFIED_CLAIM_GUARD_SKIP=1`。
"""
from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════
# 用户级副本（~/.claude/hooks/）——由 Ona-dotfiles 装到**每一个**项目
#
# 来源：WY-workspace-P 仓库的 .claude/hooks/unverified-claim-guard.py
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
import sys

# ── A：失败标志 ──────────────────────────────────────────────────────
FAIL_RE = re.compile(
    r"No such file or directory"
    r"|command not found"
    r"|Traceback \(most recent call last\)"
    r"|SyntaxError|ModuleNotFoundError|ImportError|AssertionError"
    # 计数式 FAIL/FAILED **必须前面跟非零数字**——本仓测试脚本的标准结尾是
    # 「11 PASSED / 0 FAILED」，把裸 \bFAILED\b 当失败标志会把每一次全绿都误判成失败
    # （初版就是这么误报的，selftest 当场红三条）。
    r"|(?<![0-9])[1-9]\d*\s+FAILED?\b|(?<![0-9\s])FAILED?:"
    r"|❌"
    r"|EXIT=[1-9]\d*|exit code [1-9]\d*|退出码\s*[1-9]\d*"
    r"|Permission denied|fatal:",
    re.I,
)

# 整段输出里若有「0 FAILED / 全绿」这类**收尾结论**，说明这次运行整体是通过的，
# 中间出现的 FAIL 字样是测试用例名或过程噪音，不作失败计。
OVERALL_GREEN_RE = re.compile(
    r"\b0\s+FAILED?\b|\bALL\s+PASS(?:ED)?\b|全部通过|0 FAIL\b", re.I)


def _is_failure(text: str) -> bool:
    if OVERALL_GREEN_RE.search(text):
        return False
    return bool(FAIL_RE.search(text))

# ── B：成功断言（助手正文）──────────────────────────────────────────
CLAIM_RE = re.compile(
    r"验证(?:全部)?(?:通过|都过)|双验证通过|已验(?:证|过)"
    r"|全绿|都绿了|全部通过|均通过|全过"
    r"|已装(?:好|上|完)|安装成功|部署成功"
    r"|(?:实物|逐项|逐条)核验(?:全部)?(?:通过|在位)"
    r"|测试全过|全部 ?PASS",
)

# ── C：重跑并转绿的痕迹 ─────────────────────────────────────────────
GREEN_RE = re.compile(
    r"\bPASSED\b|\bPASS\b|✅|EXIT=0|退出码\s*0|0 FAILED|0 FAIL\b|全部通过",
)


def _iter(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def _events(path):
    """把 transcript 摊平成时间序的 (kind, cmd, text) 列表。"""
    out = []
    for d in _iter(path):
        msg = d.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "tool_use":
                inp = b.get("input") or {}
                out.append(("cmd", (inp.get("command") or "")[:400], ""))
            elif t == "tool_result":
                out.append(("result", "", json.dumps(b.get("content"),
                                                     ensure_ascii=False)[:4000]))
            elif t == "text" and role != "user":
                out.append(("say", "", b.get("text") or ""))
    return out


def _norm_cmd(c: str) -> str:
    """命令归一化：去空白与引号，用于判「是不是同一条命令重跑」。"""
    return re.sub(r"[\s'\"]+", "", c)[:120]


def analyze(events):
    """返回命中的 (失败片段, 成功断言片段) 列表。"""
    hits = []
    for i, (kind, _c, text) in enumerate(events):
        if kind != "result" or not _is_failure(text):
            continue
        # 这次失败对应的命令（往前找最近一条 cmd）
        failed_cmd = ""
        for j in range(i - 1, max(-1, i - 4), -1):
            if events[j][0] == "cmd":
                failed_cmd = _norm_cmd(events[j][1])
                break
        # 往后扫：先遇到「同一条命令重跑且转绿」→ 放过；先遇到成功断言 → 命中
        for k in range(i + 1, len(events)):
            kind2, cmd2, text2 = events[k]
            if kind2 == "cmd" and failed_cmd and _norm_cmd(cmd2) == failed_cmd:
                # 找它的结果，绿了就认为已补验
                for m in range(k + 1, min(k + 3, len(events))):
                    if events[m][0] == "result":
                        if not _is_failure(events[m][2]) or GREEN_RE.search(events[m][2]):
                            failed_cmd = "__RERAN_OK__"
                        break
                if failed_cmd == "__RERAN_OK__":
                    break
            if kind2 == "say" and CLAIM_RE.search(text2):
                m = CLAIM_RE.search(text2)
                hits.append((
                    re.sub(r"\s+", " ", text)[:110],
                    re.sub(r"\s+", " ", text2[max(0, m.start() - 40): m.end() + 40]),
                ))
                break
    # 同一段断言只报一次
    seen, out = set(), []
    for a, b in hits:
        if b in seen:
            continue
        seen.add(b)
        out.append((a, b))
    return out


HEAD = (
    "🔎 unverified-claim-guard：本轮出现「工具输出是失败的，但随后的正文说验证通过」，"
    "且中间没有把那条命令重跑并转绿。\n\n"
    "这是本仓反复栽的一类假绿——**不是测试写得不好，是报告里写了「通过」而那次"
    "验证的输出其实是失败的，人没看就往下走**。2026-08-14 一轮内犯三次：\n"
    "  · 测试绿了但没跑到被测分支；· hook 测试 fixture 少了外层包装；\n"
    "  · commit message 写「已装并验证通过」，而文件那一刻根本不存在。\n\n"
    "逐条回看下面这几处：要么补跑一次并贴真实输出，要么把话改成实际状态。\n"
)
TAIL = "\n（红测试故意制造失败属正常——只要之后重跑转绿，本 hook 会自动放过。误判：UNVERIFIED_CLAIM_GUARD_SKIP=1）"


def main():
    if os.environ.get("UNVERIFIED_CLAIM_GUARD_SKIP"):
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tp = payload.get("transcript_path")
    if not tp or not os.path.exists(tp):
        return 0
    try:
        hits = analyze(_events(tp))
    except Exception:
        return 0          # fail-open：绝不因本 hook 出错卡住会话
    if not hits:
        return 0
    body = HEAD
    for a, b in hits[:4]:
        body += f"\n  ⚠️ 失败输出：…{a}…\n     随后却说：…{b}…\n"
    body += TAIL
    print(json.dumps({"decision": "block", "reason": body}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
