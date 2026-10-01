#!/usr/bin/env python3
"""git-stash-guard — PreToolUse·Bash：拦下会改动 stash 栈的 git stash 命令。

## 治什么

`git stash` 的栈存在 `.git/refs/stash`，**所有 worktree 共用同一个栈**。
本仓同时开着多个会话、多个 worktree 是常态，于是 A 会话 `git stash` 收走的东西，
B 会话一句 `git stash pop` 就取到了自己的工作区里——**两边都不报错**。

真实事故（2026-10-01，同一天两起）：两个并行子 agent 各自在自己的 worktree 里
`git stash` / `git stash pop`，第二个 pop 出来的是第一个的在制品。
靠逐文件比对才分开，期间谁也不知道自己的活被搬到了别处。

全局 CLAUDE.md 早有一句「别为了弄个干净基线去 git stash」，但那是散文——
**写命令那一秒它不在眼前**（卡 #18：有稳定可判信号就做成闸门）。

## 判据

只拦**会改栈**的子命令：裸 `git stash`、push / save / pop / apply / drop / clear / store / branch。
`git stash list` / `git stash show` / `git stash create` 不改栈，放行。
识别 `git -C <dir> stash`、`git -c k=v stash`、前置 `FOO=1 git stash`。
用 shlex 在 token 层切 `| && || ;`——别用正则切原始字符串（卡 #41 记过：
grep 的 `\\|` 会被正则从中间劈开）。引号里的 "git stash"（grep 关键词、echo 文本）不误拦。

## 该改用什么（所以是 deny 不是 ask：agent 自己就能换做法，不用打扰用户）

- 想要干净基线做对照 → 把旧版本 + 依赖整套复制到 /tmp 去跑，别动工作区
- 想临时换分支 → `git worktree add /tmp/x <分支>` 开独立副本
- 想暂存在制品 → 提交到自己的临时分支，不进共享栈

## 边界

- 拿不到 command / 解析失败 → 放行（判不出来不许拦；本闸门防的是习惯性误用，不是对抗）
- 逃生阀 `GIT_STASH_GUARD_SKIP=1`（确知当前只有你一个会话、且没有别的 worktree 时）
- 自检：bash .claude/hooks/git-stash-guard.test.sh
"""

# ══════════════════════════════════════════════════════════════════════════
# 用户级副本（~/.claude/hooks/）——由 Ona-dotfiles 装到**每一个**项目
#
# 来源：WY-workspace-P 仓库的 .claude/hooks/git-stash-guard.py
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
import shlex
import sys

MUTATING = {"push", "save", "pop", "apply", "drop", "clear", "store", "branch"}
READONLY = {"list", "show", "create"}   # create 只生成对象不入栈；入栈要再 store
SEPS = {"|", "||", "&&", ";", "&", ";;", "|&"}
OPT_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}

HELP = """git-stash-guard — PreToolUse·Bash 闸门

拦什么
  会改动 stash 栈的 git stash（裸 stash / push / save / pop / apply / drop / clear / store / branch）。
  stash 栈是**所有 worktree 共用的**，并行会话之间会互相取走对方的在制品，且不报错。
  2026-10-01 同一天发生两起。

改用什么（不需要逃生阀）
  · 要干净基线 → 旧版本连同依赖整套复制到 /tmp 跑
  · 要换分支   → git worktree add /tmp/x <分支>
  · 要暂存     → 提交到自己的临时分支

放行
  git stash list / show / create（不改栈）

逃生阀（确知没有并行会话、没有别的 worktree 时）
  GIT_STASH_GUARD_SKIP=1 <你的命令>

自检：bash .claude/hooks/git-stash-guard.test.sh
"""


def _segments(cmd):
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    lex.commenters = ""
    seg = []
    for tok in lex:
        if tok in SEPS:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def stash_violation(cmd):
    """返回命中的那段命令（字符串），没有则 None。"""
    for seg in _segments(cmd):
        i = 0
        while i < len(seg) and "=" in seg[i] and seg[i].split("=", 1)[0].isidentifier():
            i += 1                                   # 跳过前置环境变量赋值
        if i >= len(seg) or os.path.basename(seg[i]) != "git":
            continue
        j = i + 1
        while j < len(seg) and seg[j].startswith("-"):
            j += 2 if seg[j] in OPT_WITH_ARG else 1
        if j >= len(seg) or seg[j] != "stash":
            continue
        sub = next((t for t in seg[j + 1:] if not t.startswith("-")), None)
        if sub in READONLY:
            continue
        return " ".join(seg[i:])                     # 裸 stash（含只带选项）、改栈子命令、未知子命令
    return None


SELFTEST_CASES = [
    # (期望命中?, 命令)
    (True, "git stash"),
    (True, "git stash -u"),
    (True, "git stash push -m wip"),
    (True, "git stash --keep-index"),
    (True, "git stash pop"),
    (True, "git -C /workspaces/x stash apply stash@{0}"),
    (True, "cd /tmp && git stash drop"),
    (True, "FOO=1 git stash save hi"),
    (True, "git status; git stash clear"),
    (True, "git status\ngit stash pop"),
    # 阴性对照
    (False, "git stash list"),
    (False, "git stash show -p stash@{0}"),
    (False, "git -C /x stash list | head"),
    (False, 'grep -rn "git stash" docs/'),
    (False, 'echo "dont git stash pop here"'),
    (False, "git status"),
]


def selftest():
    """用户级副本没有 .test.sh 跟着走，sync-hooks.sh 靠它判同步后的副本还好不好用。
    （2026-10-01：初版没有这个入口，sync-hooks 收到 --selftest 时脚本空跑退 0，报了一个假的 ✓。）"""
    bad = [(want, c) for want, c in SELFTEST_CASES if bool(stash_violation(c)) != want]
    for want, c in bad:
        print(f"FAIL want={'deny' if want else 'allow'} :: {c!r}")
    print(f"git-stash-guard selftest {len(SELFTEST_CASES) - len(bad)}/{len(SELFTEST_CASES)}")
    return 0 if not bad else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--help" in sys.argv or "-h" in sys.argv:
        print(HELP)
        sys.exit(0)
    if os.environ.get("GIT_STASH_GUARD_SKIP") == "1":
        sys.exit(0)
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if "stash" not in cmd:
        sys.exit(0)
    try:
        hit = stash_violation(cmd)
    except Exception:
        sys.exit(0)
    if not hit:
        sys.exit(0)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"🛑 git-stash-guard：`{hit[:80]}` 会改动 stash 栈，而这个栈是**所有 worktree 共用的**——"
            "并行会话会互相取走对方的在制品，且两边都不报错（2026-10-01 同一天发生两起）。\n\n"
            "改用：要干净基线 → 旧版本连同依赖整套复制到 /tmp 跑；要换分支 → "
            "`git worktree add /tmp/x <分支>`；要暂存 → 提交到自己的临时分支。\n"
            "确知没有并行会话、没有别的 worktree：`GIT_STASH_GUARD_SKIP=1`。"
        )}}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
