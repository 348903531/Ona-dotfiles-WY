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

# 交付动作识别的单一事实源（含 wrapper 盲点补丁，见该模块 docstring）。
# 防御性 import：模块丢了就退化成纯命令串判定，hook 照常工作、不崩。
try:
    import sys as _sys_dc

    _sys_dc.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _delivery_cmd as _dc
except Exception:
    _dc = None

# ── B：本会话由本地脚本重新生成 ──────────────────────────────────────
#
# ⚠️ 2026-09 换引擎后补的两个洞（本仓 10 个产片技能的出片引擎换成上游 ppt-master 之后，
#    模拟交付验证当场测出：新链路产的片，本闸门**一次都不响**）。两个原因各自独立、
#    各自都足以让判据失灵：
#
#  ① **脚本名不在词表里**。旧链路叫 `build_editable_pptx.py` / `build_onepage.py` /
#     `condense_deck.py`（都命中 build|make|gen|…），这批已随换引擎退役删除；新链路
#     出片走引擎的 `svg_to_pptx.py`——不含 build/make/gen/render/create 任一词根。
#  ② **解释器不是字面 `python3`**。技能文档里的真实写法是
#     `$PY $E/scripts/svg_to_pptx.py <proj> -o <proj>/exports/xx.pptx` 与
#     `(cd "$ENG" && "$VP" scripts/svg_to_pptx.py "$PROJ" -o "$OUT")`——
#     用的是 `$PY` / `"$VP"` 变量或 venv 路径，`python3?\s+` 这一段直接匹配不上。
#
# 所以这里做两件事：把解释器放宽成「python / venv 路径 / $变量 / uv run」，
# 并把引擎的出片脚本按名收进来。**新加的两段都刻意要求解释器前缀**——本 regex 只在
# tool_use 里 name=Bash 的 `command` 上跑（见 _scan），但 `cat build_report.py`、
# `ls scripts/ | grep build_deck.py` 这类**读**命令同样出现在 command 字段里，
# 不要解释器前缀就会把它们误判成「重新生成过」。要求解释器前缀 = 要求执行形态。
#
# ⚠️ 已知残留误报，**查清了、刻意不修**（不是没查）：中间那段
# `\bbuild_deck\.py|\bbuild_pptx\.py|\bmake_deck\.py` 是历史遗留的**裸文件名**分支，
# 不要求解释器，于是 `cat scripts/build_deck.py` 也会被判成「重新生成过」。
# 不修的理由：① 要修就得同时兼容 `./build_deck.py`、`make deck` 这类不带 python
# 字样的执行形态，收紧容易顺手砍掉真检出；② 误报方向是**保守**的——多要求你下载线上
# 那份比对一次，与本闸门 docstring 里「首次上传全新文件也一并拦下，这是刻意的保守」
# 同一取向，且有 REGEN_OVERWRITE_GUARD_SKIP=1 逃生阀。
# .test.sh 里留了对应用例，把这条行为钉成**已知的**，免得下次有人当成新 bug 重查一遍。
_INTERP = r"(?:\"?\$[A-Za-z_]\w*\"?|[\w./~-]*(?:bin/)?python3?|uv\s+run)"
REGEN_CMD_RE = re.compile(
    rf"{_INTERP}\s+[^\n]*\b(?:build|make|gen|generate|render|create)[_-]?\w*\.py"
    r"|\bbuild_deck\.py|\bbuild_pptx\.py|\bmake_deck\.py"
    # 上游 ppt-master 引擎：出片（写出 .pptx）的那一步。pptx_to_svg.py 刻意**不收**
    # ——它是「把现成 deck 拆成 SVG」的入料步，本身不产成品；而若那份现成 deck 是从
    # 线上下载来的，COMPARED_RE 会认到 get_media，本就该放行。
    rf"|{_INTERP}\s+[^\n]{{0,200}}?\bsvg_to_pptx\.py",
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
    # wrapper 盲点补丁（2026-09-08 事故）：命令被包进脚本时（`python3 upload.py`），
    # 判据全在文件里、命令串上一个字没有，本闸门会静默放行。本 hook 尤其该补——
    # 它守的正是卡 #33「重新生成整份再覆盖线上文件」，与那次事故形态完全一致。
    # 判定收进 _delivery_cmd 共用一份；模块缺失时退化成原来的纯命令串判定。
    _hit_a = (_dc.is_delivery_command(
        "Bash", payload.get("tool_input") or {}, UPLOAD_RE, ARTIFACT_RE)
        if _dc is not None
        else bool(UPLOAD_RE.search(cmd) and ARTIFACT_RE.search(cmd)))
    if not _hit_a:
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
