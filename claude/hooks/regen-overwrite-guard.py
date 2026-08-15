#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regen-overwrite-guard —— 用本地脚本**重新生成**成品再覆盖线上文件之前，
先确认线上那份不是别人（含用户本人）手改过的定稿。

## 事故（2026-08-14，本闸门的由来）

用户在 Google Drive 上**手工改过**一份 32 页的方案 PPT：把我写的浮夸措辞改严谨
（「几乎完全失效」→「疗效不佳」、「细胞治疗」→「CART」）、删掉一个不要的治疗环节
（「双药巩固」）、调整了多处标题。这些改动**只存在于 Drive 那个文件里，我的
build_deck.py 源码里没有**。

我接到「只改这三页」的指令后，做的是：改 build_deck.py → **重新生成整份 34 页** →
上传覆盖。结果用户的每一处手改**全部被冲掉**，且我全程没察觉——因为我比对的基准
一直是「我自己生成的上一版」，不是「线上真正那一份」。

更刺眼的是：用户报页码时说「待回答问题在 p27/p28」，而线上那份**真的就是 p27/p28**
（32 页）；我手上是 34 页，于是判定「用户页码差 4」，改成按内容定位。
**只要当时下载一次实物比对，一秒就能发现是两份不同的文件。**

## 为什么已有的闸门拦不住

AGENTS.md 有「用在线文件：读/写/对比/汇报前都须确认副本新鲜度」，safe_drive.py 也会在
`files().update()` 覆盖前核 modifiedTime。但那条链路防的是**「拿旧副本覆盖新副本」**，
前提是「我手上有一份从线上下载的副本」。

**本次是另一种形态：我根本没有副本，是从源码重新生成了一份全新文件再上传。**
modifiedTime 守卫看不到这种覆盖——对它来说这就是一次普通的新文件上传。
`stale-file-guard` 只管 git 仓库里的文件，管不到 Drive。这是一段真空。

## 判据（三条同时命中才拦）

A. 本次要**上传/覆盖**一个成品文件（Drive 上传、files().update、gws upload）；
B. 该文件在本会话中是**由本地脚本重新生成**的（跑过 build_*.py / make_*.py 之类，
   或对生成脚本做过 Edit），而不是「下载→局部改→传回」；
C. 本会话**没有**证据表明比对过线上那一份——既没下载它（get_media / files().get），
   也没做过页数或文本层的比对。

命中即 `deny`，报文给出三条具体动作。逃生阀：`REGEN_OVERWRITE_GUARD_SKIP=1`。

## 诚实边界

* 判不了「线上那份到底有没有被人改过」——那要真的下载来比。本闸门只强制**你去比一次**。
* 首次上传一个全新文件（线上还不存在同名物）会被一并拦下。这是刻意的保守：
  agent 分不清「全新交付」与「覆盖定稿」，而后者代价高、前者只是多花十几秒确认。

## 退出码 / 输出

命中输出 PreToolUse deny 决策；其余情况静默（fail-open，绝不因本 hook 卡住会话）。
"""
from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════
# 用户级副本（~/.claude/hooks/）——由 Ona-dotfiles 装到**每一个**项目
#
# 来源：WY-workspace-P 仓库的 .claude/hooks/regen-overwrite-guard.py
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

# ── A：上传 / 覆盖成品 ────────────────────────────────────────────────
UPLOAD_RE = re.compile(
    r"MediaFileUpload|files\(\)\.create\(|files\(\)\.update\("
    r"|\bgws\b[^\n]*\bupload\b|drive[_-]?upload|upload[_-]?to[_-]?drive",
    re.I,
)
ARTIFACT_RE = re.compile(r"\.pptx\b|\.docx\b|\.xlsx\b|\.pdf\b", re.I)

# ── B：本会话由本地脚本重新生成 ──────────────────────────────────────
REGEN_CMD_RE = re.compile(
    r"python3?\s+[^\n]*\b(?:build|make|gen|generate|render|create)[_-]?\w*\.py"
    r"|\bbuild_deck\.py|\bbuild_pptx\.py|\bmake_deck\.py",
    re.I,
)
GENERATOR_FILE_RE = re.compile(
    r"\b(?:build|make|gen|generate)[_-]?\w*\.py$|\btheme\.py$", re.I)

# ── C：比对过线上那一份的痕迹 ────────────────────────────────────────
COMPARED_RE = re.compile(
    r"get_media\("                       # 下载线上文件
    r"|files\(\)\.get\("                 # 取线上元数据
    r"|modifiedTime"                     # 核新鲜度
    r"|MediaIoBaseDownload"
    r"|verify_deck_identity\.py"         # 本仓的成品身份核对脚本
    r"|verify_media_conservation\.py"
    r"|线上(?:那)?一?份|线上版本|下载(?:下来)?比对|逐页比对|页数比对",
    re.I,
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


def _scan(path):
    """返回 (本会话重新生成过成品?, 比对过线上那份?)。"""
    regenerated = compared = False
    for d in _iter(path):
        msg = d.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "tool_use":
                name = b.get("name") or ""
                inp = b.get("input") or {}
                blob = json.dumps(inp, ensure_ascii=False)
                if name == "Bash" and REGEN_CMD_RE.search(inp.get("command") or ""):
                    regenerated = True
                if name in ("Write", "Edit", "MultiEdit"):
                    fp = inp.get("file_path") or inp.get("path") or ""
                    if GENERATOR_FILE_RE.search(os.path.basename(fp)):
                        regenerated = True
                if COMPARED_RE.search(blob):
                    compared = True
            elif t == "tool_result":
                if COMPARED_RE.search(json.dumps(b.get("content"), ensure_ascii=False)):
                    compared = True
            elif t == "text":
                if COMPARED_RE.search(b.get("text") or ""):
                    compared = True
    return regenerated, compared


MSG = (
    "🛑 regen-overwrite-guard：这次要上传的成品是**本会话用本地脚本重新生成**的，"
    "而本会话没有任何「比对过线上那一份」的痕迹。\n\n"
    "重新生成再上传 = 拿你的源码状态整体覆盖线上文件。**线上那份若被人手改过"
    "（改措辞、删段落、调页序），改动只存在于那个文件里，你的源码不知道，会被静默冲掉。**\n"
    "2026-08 真实事故：用户在 Drive 上把浮夸措辞改严谨、删掉一个治疗环节，"
    "我重新生成 34 页覆盖上去，手改全没了；且因页数不同（线上 32 / 本地 34），"
    "还把用户报的正确页码误判成「差 4 页」。\n\n"
    "先做完这三件再传：\n"
    "  1. 下载线上那一份：files().get_media(fileId=...) 存成 ORIG.pptx\n"
    "  2. 比页数与逐页文本层：页数不一致 = 两份不同的文件，立刻停下核对\n"
    "  3. 有手改就**在线上那份上局部改**（python-pptx 改指定页），别整份重生成覆盖\n\n"
    "确属全新交付、线上无同名定稿：REGEN_OVERWRITE_GUARD_SKIP=1"
)


def main():
    if os.environ.get("REGEN_OVERWRITE_GUARD_SKIP"):
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not (UPLOAD_RE.search(cmd) and ARTIFACT_RE.search(cmd)):
        return 0
    tp = payload.get("transcript_path")
    if not tp or not os.path.exists(tp):
        return 0
    try:
        regenerated, compared = _scan(tp)
    except Exception:
        return 0          # fail-open
    if regenerated and not compared:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": MSG,
            }
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
