#!/usr/bin/env python3
r"""destructive-command-guard — PreToolUse hook：普通命令不再打扰你，只拦「做完回不去」的。

## 为什么有它

用户的诉求是「技术类弹窗我看不懂、每次只会点 yes，等于净打断」。但把确认权整个放掉的话，
`git reset --hard`、`rm -rf`、`git push --force` 这类**做完就回不去**的命令会裸奔——尤其
`parallel-session-stage-guard` 只在**检测到 ≥2 个并行会话**时才响，单会话下 `reset --hard`
此前唯一的防线就是那个 permission prompt。本 hook 补这一段，并且**弹窗正文写中文人话**
（治的正是「看不懂它在请求什么」）。

（注：配套方案里「往 permissions.allow 加裸 `Bash`」那条已撤回——官方对 allow 与 hook ask
的优先级没有明文，赌错会让本仓 20 道闸门静默失效。现行三层见 settings.json 的 `_note`。）

## 判定核心是「命令位置」，不是「文本包含」（第二版，血泪换来的）

第一版用正则在整条命令文本里找危险字样，**连续三轮误报**，每次都是本 hook 自己的 Stop
清单抓到的：heredoc 正文 → `echo "…rm -rf…"` 的参数 → for 循环里的字符串字面量、
commit message 里的中文说明。前两轮我逐个形态打补丁，第三轮才承认方向错了：

    shell 字符串可以跨越换行和 `&&`，正则切段会把字符串**切开**，切出来的碎片看起来
    就像真命令。铁证（本 hook 自己报的）：
      · `\nrm -rf /workspaces/x"}}' \\\npython3 …`   ← for 循环里的字符串字面量
      · ` rm -rf 安全区里，导致 \`rm -r`               ← commit message 里的中文

本仓元教训：**补下游永远堵不住上游**。上游是「用正则解析 shell」这个方法本身。改用
`shlex` 词法分析——引号内的内容成为**单个 token**，永远不会出现在命令位置，于是所有
「文本里提到危险命令」的形态一次性全部消失，不用再为每种形态各打一个补丁。判定也从
「文本包含」升级为「**命令名 + 结构化参数**」：`rm` 必须是这一段的命令名，
`reset --hard` 必须是 git 的子命令。

一个必要的例外：`python3 -c "…"` / `bash -c "…"` 的**参数就是要执行的代码**（不像 echo
的参数是文本），对这类 head 退回文本级扫描，否则会漏掉
`python3 -c "os.system('rm -rf /')"`——这条正是回归测试抓出来的漏拦。

## 判据（刻意窄：只拦「做完回不去」的，不拦「跑歪了大不了重跑」的）

- **文件系统**：`rm -r`（递归删目录）、`rm` 带通配符（批量删）、`shred`、
  `find … -delete / -exec rm`、`dd of=`、`mkfs`、写 `/dev/sd*`、`chmod/chown -R` 打到
  `/` 或家目录、`truncate -s 0`。
- **git 不可逆**：`reset --hard`、`clean -f`、`checkout -- <path>`、`restore`（非
  `--staged`）、`stash drop/clear`、`push --force`（含 `--force-with-lease`）、
  `push --mirror`、`push --delete` / `push origin :branch`、`branch -D`、
  `filter-branch`、`reflog expire`、`gc --prune=now`。
- **远端/外部**：`gh repo delete`、`gh release delete`、Drive `files().delete(`。
- **自伤**：`pkill -f` / `killall`（`pkill -f <关键字>` 匹配完整命令行，会把 agent 自己
  那条含同样关键字的 bash 命令一起杀掉——本仓真实踩过：exit 144、命令链后半段静默没执行，
  看着像跑完了其实没）。

**刻意不拦**（跑歪了能重来，拦了只会变回噪音）：`rm -f 单个文件`、`mv`、`cp`、
`git commit --amend`、`git rebase`（有 reflog）、`git reset --soft/--mixed`、
`git clean -n`、`git stash pop/apply`、`git branch -d`（只删已合并）、任何只读命令。

### rm -r 的安全区豁免（否则会变成天天弹）

目标**全部**落在公认可再生目录时静默放行：`/tmp`、`/var/tmp`、`$TMPDIR`、
`node_modules`、`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`、
`dist`、`build`、`out`、`coverage`、`.venv`/`venv`、`.next`、`*.egg-info`。
只要有**一个**目标不在安全区（尤其 `/workspaces`、`~`、`.git`、`.claude`）就弹。

## 为什么是 ask 不是 deny

这些命令都有正当用途（清工作区、强推自己的分支、删临时目录）。deny 会焊死合法操作、
逼人去关闸门；ask 的价值是**在那一秒把「这一步做完就回不去」摆到眼前**，判断权留给人。

## 与 parallel-session-stage-guard 的关系：刻意允许双响，不做互斥

两者的 git 破坏性模式有重叠。本可以「多会话时让位给它」，但它的第三条判据（脏文件归属 /
stash 栈归属）在本 hook 里无法预判——**猜错就是漏拦一次不可逆操作**。漏拦比多弹一次糟得多，
故不做互斥。重叠只在「≥2 并行会话 + 破坏性 git + 有别人的脏文件」这个罕见交集发生，且两个
弹窗说的是不同的事（它说「这些文件不是你的」，本 hook 说「这一步做完回不去」）。

## 弹窗正文一律中文人话

治的正是用户放弃 permission prompt 的原因——「我看不懂它在请求什么」。每条 reason 都写清：
**这条命令会做什么、丢什么、还能不能找回来**。

输入：stdin JSON（tool_name / tool_input）。命中 → 打印 PreToolUse ask 决策，exit 0；
否则静默 exit 0。任何异常都 fail-open（exit 0）——闸门自己的 bug 不该卡住工作。

自测：`python3 destructive-command-guard.py --selftest`（含必错输入，见它红过才可信）。
"""

# ══════════════════════════════════════════════════════════════════════════
# 用户级副本（~/.claude/hooks/）——由 Ona-dotfiles 装到**每一个**项目
#
# 来源：WY-workspace-P 仓库的 .claude/hooks/destructive-command-guard.py
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

import ast
import json
import os
import re
import shlex
import sys

# ── heredoc 剥离：`python3 - <<'PY' … PY` 里的是**数据**，不是要执行的 shell ──
# 上线第一轮就被自己的 Stop hook 抓到：写文档、写正则表时命令里出现 "rm -rf /" 字样，
# 被当成真要删根目录而弹窗——治弹窗的 hook 自己制造了最烦的一类误弹。
# 取舍（诚实）：剥离后，`bash <<EOF … rm -rf / … EOF` 这种「heredoc 里真是 shell」的
# 情况会漏拦。选剥离，因为①误弹是高频且直接违背本 hook 的目的，②那种写法本身就是任意
# 代码执行，permission 层与 auto 模式 classifier 仍在管它。
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1.*?^\s*\2\s*$", re.S | re.M)


def _strip_heredocs(cmd):
    """把 heredoc 正文替换成占位，只留命令行本身供扫描。"""
    return HEREDOC_RE.sub("<<HEREDOC_BODY_STRIPPED", cmd)


# ── 安全区：rm -r 打到这些地方不弹（公认可再生） ─────────────────────────────
SAFE_RM_RE = re.compile(
    r"^(?:"
    r"/tmp/|/var/tmp/|\$TMPDIR|\$\{TMPDIR\}"          # 临时区
    # 刻意**不含** `out`：这名字太泛（不像 node_modules/dist 是明确的构建产物约定），
    # 而 `/workspaces/<项目>/out` 完全可能是放成品的地方。自测里就抓到它被静默放行。
    r"|(?:[\w./-]*/)?(?:node_modules|__pycache__|\.pytest_cache|\.mypy_cache"
    r"|\.ruff_cache|dist|build|coverage|\.venv|venv|\.next|\.turbo"
    r"|[\w.-]+\.egg-info)/?$"
    r")",
)

# 明确高危、即使字面落在安全区规则外也必弹（防 rm -rf ~/ 之类）
DANGER_TARGET_RE = re.compile(
    r"^(?:/|~|\$HOME|/home/|/root|/workspaces|/etc|/usr|/var(?!/tmp)|/bin|/boot|/dev)"
    r"|(?:^|/)\.git/?$|(?:^|/)\.claude/?$",
)

_G = r"\bgit\b(?:\s+-[^\s]+(?:\s+[^\s-][^\s]*)?)*"  # git，允许 -C <path> 等前缀选项

# ── 危险模式表：(标识, 正则, 中文说明) ───────────────────────────────────────
PATTERNS = [
    # —— git：做完回不去 ——
    ("git-reset-hard", re.compile(_G + r"\s+reset\b[^&|;]*?\s--hard\b"),
     "`git reset --hard` 会**丢掉工作区里所有未提交的改动**，且不进 stash、不可撤销。"
     "本仓当前工作区里可能有别的会话/别的任务留下的未提交文件，一起没。"),
    ("git-clean-force",
     re.compile(_G + r"\s+clean\b(?![^&|;]*?(?:-n\b|--dry-run\b))[^&|;]*?\s(?:-\w*f|--force)"),
     "`git clean -f` 会**永久删除未被 git 跟踪的文件**（新写还没 add 的、下载的素材、"
     "生成的产物）。git 里没有它们的副本，删了就没了。先跑 `git clean -n` 看会删哪些。"),
    ("git-checkout-discard", re.compile(_G + r"\s+checkout\b[^&|;]*?\s--\s"),
     "`git checkout -- <路径>` 会把这些文件**还原成上次提交的样子**，改了一半的内容直接丢，"
     "不可撤销。"),
    ("git-restore", re.compile(_G + r"\s+restore\b(?![^&|;]*?--staged\b)"),
     "`git restore` 默认动的是**工作区文件**，会丢掉未提交的改动（`--staged` 才只动暂存区）。"),
    ("git-stash-drop", re.compile(_G + r"\s+stash\s+(?:drop|clear)\b"),
     "`git stash drop/clear` 会**销毁 stash 里的存档**。stash 栈是全仓共享的，"
     "里面可能有别的会话存进去、还没取回的活。clear 是一次清空整个栈。"),
    ("git-push-force", re.compile(r"\bgit\b[^&|;]*?\spush\b[^&|;]*?\s(?:--force\b|--force-with-lease\b|-f\b)"),
     "`git push --force` 会**覆盖远端分支历史**。如果这个分支别人也在用，他们的 commit 会被"
     "冲掉；已合并的分支强推更可能把 main 上的东西弄乱。"),
    ("git-push-delete", re.compile(r"\bgit\b[^&|;]*?\spush\b[^&|;]*?(?:\s--delete\b|\s--mirror\b|\s:[\w./-]+)"),
     "这条 `git push` 会**删除远端分支**（`--delete` / `:branch`）或用本地状态整体覆盖远端"
     "（`--mirror`）。远端分支删掉后，只在那上面的 commit 就找不回来了。"),
    ("git-branch-D", re.compile(_G + r"\s+branch\b[^&|;]*?\s-D\b"),
     "`git branch -D` 是**强制删分支**（-d 只删已合并的，-D 不管有没有合并）。"
     "分支上还没合并的 commit 会变成悬挂对象，很难找回。"),
    ("git-history-rewrite",
     re.compile(_G + r"\s+(?:filter-branch|filter-repo)\b|\breflog\s+expire\b|\bgc\b[^&|;]*?--prune=now"),
     "这条命令会**改写或清理 git 历史/回收站**。改写历史不可逆；`reflog expire` / "
     "`gc --prune=now` 会把「误删后还能捞回来」的那条后路一起清掉。"),
    # —— 文件系统：删了就没了 ——
    ("shred", re.compile(r"\bshred\b"),
     "`shred` 会**覆写文件内容再删除**，专门设计成不可恢复。"),
    ("find-delete", re.compile(r"\bfind\b[^&|;]*?(?:-delete\b|-exec\s+rm\b|-execdir\s+rm\b)"),
     "`find … -delete` / `find … -exec rm` 会**批量删除所有匹配到的文件**。"
     "匹配范围写宽一点就会连带删掉不想删的，且没有确认、没有回收站。"),
    ("dd-write", re.compile(r"\bdd\b[^&|;]*?\bof=(?!/dev/null)"),
     "`dd of=…` 会**直接覆写目标文件或设备**，原内容无法恢复。"),
    ("mkfs", re.compile(r"\bmkfs(\.\w+)?\b|\bmkswap\b"),
     "`mkfs` 会**格式化整个文件系统**，该分区上的数据全部清空。"),
    ("dev-write", re.compile(r">\s*/dev/(?:sd|nvme|hd|vd)\w*"),
     "这条命令在**往块设备直接写数据**，会破坏磁盘上的文件系统。"),
    ("chmod-chown-root",
     re.compile(r"\bch(?:mod|own)\b[^&|;]*?\s-R\b[^&|;]*?\s(?:/|~|\$HOME|/home/|/etc|/usr)(?:\s|$)")
     ,
     "`chmod -R` / `chown -R` 递归打到系统目录或家目录，会**改坏大量文件的权限**，"
     "很难逐个还原，可能让环境无法正常工作。"),
    ("truncate-zero", re.compile(r"\btruncate\b[^&|;]*?-s\s*0\b"),
     "`truncate -s 0` 会**把文件内容清空**（保留文件名），原内容不可恢复。"),
    # —— 远端/外部 ——
    ("gh-delete", re.compile(r"\bgh\s+(?:repo|release|cache)\s+delete\b"),
     "`gh … delete` 会**删除 GitHub 上的仓库/release**，属于远端不可逆操作。"),
    ("drive-delete", re.compile(r"files\(\)\.delete\(|\.trash\(|drive[_-]?delete"),
     "这条命令会**删除 Google Drive 上的文件**。Drive 回收站有保留期，但脚本批量删很容易"
     "删错范围，且用户不一定看得到。"),
    # —— 自伤（本仓真实踩过）——
    ("pkill-f", re.compile(r"\bpkill\s+-f\b|\bkillall\b"),
     "`pkill -f <关键字>` 匹配的是**完整命令行**，会把 agent 自己那条含同样关键字的 bash "
     "命令链一起杀掉——本仓踩过：exit 144、命令链后半段静默没执行，看着像跑完了其实没。"
     "建议先 `pgrep -af <关键字>` 确认目标，再按 PID 杀。"),
]

# rm：单独处理（要看目标是不是落在安全区）
# 说明文案按标识索引（第二版判定按命令位置结构化判，不再逐条走 PATTERNS 的正则，
# 但复用同一批中文说明——单一事实源，改文案只改 PATTERNS 一处）。
_WHY = {name: why for name, _rx, why in PATTERNS}
DRIVE_DELETE_RE = [rx for name, rx, _w in PATTERNS if name == "drive-delete"][0]
# `python3 -c` / `bash -c` 参数里的 rm：那是要执行的代码，只能文本级近似（结构化判定
# 解析不了别的语言）。刻意不要求目标不在安全区——代码里拼出来的路径本就看不准，宁可多问。
CODE_RM_RE = re.compile(r"\brm\s+-\w*[rR]")


def _rm_is_dangerous_tokens(args):
    """token 版 rm 判定。args 已由 shlex 去引号，故 `rm -rf "$DIR"` 与 `rm -rf $DIR`
    等价看待；通配符不会被 shlex 展开，仍能看出 `rm *.pptx` 是批量删。"""
    flags = [t for t in args if t.startswith("-") and t != "--"]
    targets = [t for t in args if not t.startswith("-") and t != "--"]
    recursive = any("r" in f.lstrip("-").lower() for f in flags)
    wildcard = any(("*" in t) or ("?" in t) for t in targets)
    if not recursive and not wildcard:
        return False, ""                 # 删单个具名文件 → 不拦（否则变回噪音）
    if not targets:
        return False, ""
    for t in targets:
        # 顺序要紧：先放行安全区，再判危险。反过来 `/tmp/mtg` 会因 `^/` 命中危险规则
        # 而误报（第一版自测就栽在这里）。
        if SAFE_RM_RE.match(t):
            continue
        if DANGER_TARGET_RE.search(t):
            return True, "目标是**系统目录 / 家目录 / 工作区根 / .git / .claude**这类地方"
        return True, "目标 `%s` 不在「可再生」安全区（/tmp、node_modules、dist 之类）" % t
    return False, ""


# ══ 命令位置解析：本 hook 的第二版判定核心 ═══════════════════════════════════
#
# 第一版用正则在**整条命令文本**里找危险字样，然后逐个形态打补丁（heredoc → echo 参数
# → 注释 → 赋值）。三轮之后仍在误报，因为方向本身就错：
#
#     shell 字符串可以跨越换行和 `&&`，正则切段会把字符串**切开**，切出来的碎片看起来
#     就像真命令。本 hook 自己的 Stop 清单抓到两个铁证：
#       · `\nrm -rf /workspaces/x"}}' \\\npython3 …`  ← for 循环里的字符串字面量被切开
#       · ` rm -rf 安全区里，导致 \`rm -r`             ← commit message 里的中文说明
#
# 本仓元教训：补下游永远堵不住上游，要回到「错误进管线那一段」去堵。上游是「用正则解析
# shell」这个方法本身。换成**词法分析**（shlex）：引号内的内容会成为**单个 token**，
# 永远不会出现在命令位置，于是所有「文本里提到危险命令」的形态一次性全部消失——不用再
# 为 heredoc / echo / 注释 / 赋值 / commit message / 循环变量各打一个补丁。
#
# 判定也从「文本包含」升级为「**命令名 + 结构化参数**」：`rm` 必须是这一段的命令名，
# `git reset --hard` 必须是 git 的子命令，而不是任何位置出现的字样。
#
# 一个必要的例外：`python3 -c "…"` / `bash -c "…"` 的**参数就是要执行的代码**（不像
# echo 的参数是文本）。对这类 head 退回文本级扫描，否则会漏掉
# `python3 -c "os.system('rm -rf /')"`。
SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "{", "}", "\n"}
CODE_ARG_HEADS = {"python", "python3", "bash", "sh", "zsh", "ksh", "node", "perl",
                  "ruby", "php", "eval", "xargs", "env", "flock", "script"}
GIT_PREFIX_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def _command_units(cmd):
    """把 shell 文本切成处于**命令位置**的 [(head, args), …]。解析失败返回 None。

    引号内的内容经 shlex 成为单个 token，天然不会被当成 head——这正是第一版正则做不到、
    也是三轮误报的根源。
    """
    try:
        lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = "#"
        toks = list(lex)
    except ValueError:
        return None                      # 引号不配对等 → 调用方 fail-open
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
        head = os.path.basename(u[0])
        args = u[1:]
        while head in ("sudo", "time", "nice", "nohup", "command", "timeout") and args:
            # 这些是包装器，真正的命令在后面（timeout 还带一个时长参数）
            if head == "timeout" and args and re.match(r"^[\d.]+[smhd]?$", args[0]):
                args = args[1:]
            if not args:
                break
            head, args = os.path.basename(args[0]), args[1:]
        out.append((head, args))
    return out


def _git_subcommand(args):
    """跳过 `-C <path>` 这类前缀选项，返回 (子命令, 其后的参数)。"""
    i = 0
    while i < len(args):
        a = args[i]
        if a in GIT_PREFIX_OPTS:
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a, args[i + 1:]
    return "", []


# 会把字符串**当命令执行**的函数：危险与否取决于参数内容
_EXEC_FUNCS = {"system", "popen", "run", "call", "check_call", "check_output",
               "Popen", "spawn", "spawnl", "spawnv", "execv", "execvp", "execl",
               "getoutput", "getstatusoutput"}
# **函数本身就是破坏性删除**：不看参数，出现即危险。
# 刻意不含 rename / replace —— os.replace 是原子写入的标准手法（本仓多处在用），
# 判它危险会天天误报；也不含 copy* / move（可逆）。
_DESTRUCTIVE_FUNCS = {"remove", "unlink", "rmtree", "rmdir", "removedirs", "truncate"}


def _python_exec_danger(head, args):
    """判 `python -c "<code>"`：'inert'（确认惰性）/ 'danger'（确认危险）/ None（判不出）。

    背景：对 CODE_ARG_HEADS 退回文本扫描是有道理的——`python3 -c
    "os.system('rm -rf /')"` 必须拦得住。代价是它分不清代码和数据：
    把 'git checkout -- a.md' 当**测试输入字符串**打印出来，也会被当成真要执行，
    于是弹一次窗。2026-08-08 实测：一轮里两条纯只读的诊断命令全被标破坏性——
    这正是用户最烦的那种「看不懂、只会点 yes」的无意义打断。

    做法：把 -c 的代码用 AST 解析，只看**危险字样有没有落在会真正执行它的位置**：
    · 落在 os.system / subprocess.* 的参数里 → 'danger'
    · 调了 os.remove / shutil.rmtree 这类**本身即删除**的函数 → 'danger'
    · 只是列表元素、print 参数、赋给变量的字面量 → 'inert'

    **判不出一律 None（退回文本扫描）**：解析失败、不是 python、没有 -c、
    出现 eval/exec/compile 这类静态追不了的间接执行渠道。降噪只作用于「已确证
    无关」，绝不作用于「判不出」——这是本轮反复确认的判据。

    三态而不是二态的原因：写这条时用两个真实漏洞验出来的——
    `subprocess.run(['rm','-rf','/x'])` 把命令拆成列表元素，文本扫描的
    `\\brm\\s+-\\w*[rR]` 要求 rm 后跟空格，拆开后匹配不到；`shutil.rmtree(...)`
    文本层压根没有对应模式。两者都是**原有漏洞**，只退回文本扫描等于放行。
    所以 AST 层必须能主动报危险，不能只会说「我判不出、你去文本扫」。
    """
    if head not in ("python", "python3", "python2"):
        return None
    if "-c" not in args:
        return None
    i = args.index("-c")
    if i + 1 >= len(args):
        return None
    src = args[i + 1]
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return None                       # 解析不了 → 交回文本扫描
    for node in ast.walk(tree):
        # 间接执行渠道：静态追踪不了，从严
        if isinstance(node, ast.Name) and node.id in ("eval", "exec", "compile"):
            return None
    any_str = any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                  for n in ast.walk(tree))
    saw_call = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else (
            f.id if isinstance(f, ast.Name) else "")
        if name in _DESTRUCTIVE_FUNCS:
            return "danger"               # 函数本身就是删除，不看参数
        if name not in _EXEC_FUNCS:
            continue
        saw_call = True
        # 参数不是字面量（变量拼出来的）→ 静态看不见，从严
        if not any(isinstance(a, ast.Constant) for a in ast.walk(node)):
            return None
        payload = " ".join(a.value for a in ast.walk(node)
                           if isinstance(a, ast.Constant)
                           and isinstance(a.value, str))
        for _name, rx, _why in PATTERNS:
            if rx.search(payload):
                return "danger"
        if CODE_RM_RE.search(payload) or DRIVE_DELETE_RE.search(payload):
            return "danger"
    if saw_call:
        return None                       # 有执行调用但没判出危险 → 交回文本扫描
    if not any_str:
        return None                       # 没字符串可判 → 交回文本扫描
    return "inert"                        # 只有数据、没有任何执行调用


def _judge_unit(head, args):
    """对**一个命令位置**做结构化判定，返回 [(标识, 中文说明), …]。"""
    hits = []
    joined = " ".join(args)

    if head == "rm":
        bad, why = _rm_is_dangerous_tokens(args)
        if bad:
            hits.append(("rm",
                         "`rm` 在**递归删目录或用通配符批量删**，%s。删掉的文件不进回收站、"
                         "git 里也没有未跟踪文件的副本，找不回来。" % why))
    elif head == "git":
        sub, rest = _git_subcommand(args)
        r = set(rest)
        if sub == "reset" and "--hard" in r:
            hits.append(("git-reset-hard", _WHY["git-reset-hard"]))
        elif sub == "clean" and not ({"-n", "--dry-run"} & r) and any(
                a.startswith("-") and "f" in a.lstrip("-") for a in rest):
            hits.append(("git-clean-force", _WHY["git-clean-force"]))
        elif sub == "checkout" and "--" in rest:
            hits.append(("git-checkout-discard", _WHY["git-checkout-discard"]))
        elif sub == "restore" and "--staged" not in r:
            hits.append(("git-restore", _WHY["git-restore"]))
        elif sub == "stash" and rest and rest[0] in ("drop", "clear"):
            hits.append(("git-stash-drop", _WHY["git-stash-drop"]))
        elif sub == "push":
            if {"--force", "-f", "--force-with-lease"} & r:
                hits.append(("git-push-force", _WHY["git-push-force"]))
            elif {"--delete", "--mirror"} & r or any(a.startswith(":") for a in rest):
                hits.append(("git-push-delete", _WHY["git-push-delete"]))
        elif sub == "branch" and "-D" in r:
            hits.append(("git-branch-D", _WHY["git-branch-D"]))
        elif sub in ("filter-branch", "filter-repo"):
            hits.append(("git-history-rewrite", _WHY["git-history-rewrite"]))
        elif sub == "reflog" and "expire" in r:
            hits.append(("git-history-rewrite", _WHY["git-history-rewrite"]))
        elif sub == "gc" and "--prune=now" in r:
            hits.append(("git-history-rewrite", _WHY["git-history-rewrite"]))
    elif head == "shred":
        hits.append(("shred", _WHY["shred"]))
    elif head == "find":
        if "-delete" in args or any(
                args[i] in ("-exec", "-execdir") and i + 1 < len(args)
                and os.path.basename(args[i + 1]) == "rm" for i in range(len(args))):
            hits.append(("find-delete", _WHY["find-delete"]))
    elif head == "dd":
        if any(a.startswith("of=") and a != "of=/dev/null" for a in args):
            hits.append(("dd-write", _WHY["dd-write"]))
    elif head.startswith("mkfs") or head == "mkswap":
        hits.append(("mkfs", _WHY["mkfs"]))
    elif head in ("chmod", "chown"):
        if any(a.startswith("-") and "R" in a.lstrip("-") for a in args) and any(
                a in ("/", "~") or a.startswith(("/etc", "/usr", "/home", "$HOME")) for a in args):
            hits.append(("chmod-chown-root", _WHY["chmod-chown-root"]))
    elif head == "truncate":
        if "-s" in args:
            i = args.index("-s")
            if i + 1 < len(args) and args[i + 1] in ("0", "0B"):
                hits.append(("truncate-zero", _WHY["truncate-zero"]))
        elif any(a in ("-s0", "--size=0") for a in args):
            hits.append(("truncate-zero", _WHY["truncate-zero"]))
    elif head == "gh":
        if len(args) >= 2 and args[0] in ("repo", "release", "cache") and args[1] == "delete":
            hits.append(("gh-delete", _WHY["gh-delete"]))
    elif head in ("pkill", "killall"):
        if head == "killall" or "-f" in args:
            hits.append(("pkill-f", _WHY["pkill-f"]))
    elif head in CODE_ARG_HEADS:
        # 参数即代码（不同于 echo 的参数是文本）→ 只能退回文本级扫描。
        # **但对 python 先用 AST 精确化**（见 _python_code_is_inert 的说明）：
        # 纯数据的代码（把危险命令当字符串打印/存进列表）不该拦，那是纯打断。
        _v = _python_exec_danger(head, args)
        if _v == "inert":
            return hits                   # 确认是纯数据 → 放行，别做无意义打断
        if _v == "danger":
            hits.append(("code-exec-destructive",
                         "这段 **%s -c** 代码会真的执行破坏性操作（删文件 / 跑 rm / "
                         "调删除 API）。AST 已确认危险字样落在会被执行的位置上，"
                         "不是当字符串打印。做完找不回来。" % head))
            return hits
        for name, rx, why in PATTERNS:
            if rx.search(joined):
                hits.append((name, why))
        if DRIVE_DELETE_RE.search(joined):
            hits.append(("drive-delete", _WHY["drive-delete"]))
        # rm 走的是结构化判定（不在 PATTERNS 里），所以这里要单独补一条文本级近似，
        # 否则 `python3 -c "os.system('rm -rf /')"` 会漏拦——回归测试抓到的。
        if CODE_RM_RE.search(joined):
            hits.append(("rm",
                         "这段代码里有 **`rm -rf`**（在 %s 的参数里，会被真正执行）。"
                         "删掉的文件不进回收站、git 里也没有未跟踪文件的副本，找不回来。" % head))

    # 重定向到块设备：shlex 把 `>` 单独成 token
    for i, a in enumerate(args):
        if a == ">" and i + 1 < len(args) and re.match(r"^/dev/(sd|nvme|hd|vd)", args[i + 1]):
            hits.append(("dev-write", _WHY["dev-write"]))
    return hits


def _scan(cmd):
    """返回命中列表 [(标识, 中文说明)]，无命中返回 []。"""
    units = _command_units(_strip_heredocs(cmd))
    if units is None:
        return []                        # 解析不了 → fail-open（退回原有弹窗流程）
    hits, seen = [], set()
    for head, args in units:
        for name, why in _judge_unit(head, args):
            if name not in seen:
                seen.add(name)
                hits.append((name, why))
    return hits


def _reason(hits):
    head = (
        "🛑 destructive-command-guard：这条命令属于**做完就回不去**的那一类，"
        "所以它是少数还会停下来问你的命令之一（普通命令已按你的要求不再弹窗）。\n\n"
    )
    body = "\n".join("· **%s** — %s" % (n, w) for n, w in hits)
    tail = (
        "\n\n确认要这么做 → 选允许；不确定 → 选拒绝，让 agent 换个可逆的做法"
        "（先备份 / 先 `--dry-run` / 改成显式列文件）。"
    )
    return head + body + tail


_PATHISH_RE = re.compile(r"[\w.\-/~$]{2,}")


def _targets(cmd):
    """从命令里挑出「像操作目标」的 token（路径 / 文件名 / 分支名）。

    只做粗筛：去掉 flag、命令名、纯符号。用于判断 agent 有没有在对话里点过名。
    """
    out = []
    try:
        units = _command_units(_strip_heredocs(cmd)) or []
    except Exception:
        return out
    for head, args in units:
        for a in args:
            if a.startswith("-") or a in ("&&", "||", ";", "|", ">", ">>", "--"):
                continue
            if not _PATHISH_RE.fullmatch(a):
                continue
            if a in ("origin", "upstream", "HEAD", "."):
                continue
            out.append(a)
    return out


def _said_in_chat(transcript_path, targets):
    """agent 有没有在**最近一条回复正文**里点名过这些目标。

    用户 2026-08-08 定的形态：「涉及关键文件删除/修改的确认非常有必要，但要在
    **对话里**确认，而不是弹 bash 界面。对话里面你更多的是告诉我干什么。」

    所以这道 hook 从「执行那一刻拦住用户」改成「执行前检查 agent 有没有先交代」：
    · 交代过 → 放行，不弹窗（用户已经在对话正文里读到并可以叫停）
    · 没交代 → 照旧 ask，且正文改成「你还没在对话里说清楚」——**惩罚落在 agent
      身上，不是打扰用户**。

    只看**最后一条** assistant 文本：早几轮说过的不算，用户当下未必还记得。
    只认 type=="text"（用户看得见的正文），不认 thinking——用户看不见思考，
    那不构成告知。同一判据在 session-change-digest 的降噪里已验证可用。

    读不到 transcript / 没有目标可核 → 返回 False（照旧弹窗）。**故意保守**：
    判不出时要退回更安全的一侧，这是本轮刚沉淀的教训——降噪只能作用于「已确证
    无关」的东西，不能作用于「判不出」的东西。
    """
    if not transcript_path or not targets:
        return False
    last = ""
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                chunks = [b.get("text") or "" for b in content
                          if isinstance(b, dict) and b.get("type") == "text"]
                if chunks:
                    last = "\n".join(chunks)          # 只保留最后一条，前面的覆盖掉
    except Exception:
        return False
    if not last:
        return False
    # 每个目标都要被点到名（basename 命中即可——回复里通常写文件名而非全路径）
    for t in targets:
        base = os.path.basename(t.rstrip("/")) or t
        if base and base in last:
            continue
        if t in last:
            continue
        return False
    return True


_NOT_SAID_HINT = (
    "\n\n──────────\n"
    "⚠️ **你还没在对话正文里交代这次操作**。按用户 2026-08-08 的要求："
    "涉及删除/覆盖关键文件的确认要**在对话里用人话说清楚**，而不是靠这个弹窗——"
    "他看不懂原始命令，弹了也只会点 yes，等于没确认。\n\n"
    "**先回到对话里说清楚这几件事，再执行**：\n"
    "① 具体动哪些东西（列出来，给个数）；② 做完能不能找回来；"
    "③ 这条指令**有没有第二种理解**——有就把几种理解列出来让用户选，别替他猜。\n\n"
    "说清楚之后再跑同一条命令，本 hook 会自动放行、不再打扰他。"
)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)                                    # fail-open
    if payload.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not cmd.strip():
        sys.exit(0)
    try:
        hits = _scan(cmd)
    except Exception:
        sys.exit(0)                                    # fail-open
    if not hits:
        sys.exit(0)
    # 先在对话里交代过 → 放行。用户已经在正文里读到、有机会叫停，再弹一次就是
    # 他说的「反复确认、增加工作量」。permissions.ask 里那 11 条仍在更外层兜底，
    # 那是官方保证在任何模式下都会弹的确定层，本 hook 放行不影响它。
    try:
        if _said_in_chat(payload.get("transcript_path"), _targets(cmd)):
            sys.exit(0)
    except Exception:
        pass                                           # 判不出就照旧弹窗
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": _reason(hits) + _NOT_SAID_HINT,
        }
    }
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


# ── 自测：好输入必须静默、坏输入必须命中（没红过的绿 = 没测过）─────────────────
SELFTEST = [
    # (命令, 期望命中?)  —— 期望命中的是「必错输入」，见它红过才算闸门可信
    ("git status", False),
    ("ls -la /workspaces", False),
    ("python3 build.py && cp out.pptx /tmp/", False),
    ("rm /tmp/a.txt", False),
    ("rm -f nohup.out", False),                        # 删单个具名文件 → 不拦
    ("rm -rf /tmp/mtg", False),                        # 安全区
    ("rm -rf node_modules", False),
    ("rm -rf .venv dist build", False),
    ("git clean -n", False),
    ("git stash pop", False),
    ("git reset --soft HEAD~1", False),
    ("git branch -d feat/done", False),
    ("git commit --amend --no-edit", False),
    ("dd if=x of=/dev/null", False),
    ("pgrep -af uvicorn", False),
    # —— 以下必须命中 ——
    ("git reset --hard origin/main", True),
    ("cd /workspaces/x && git reset --hard", True),    # 藏在 && 后半截
    ("git clean -fdx", True),
    ("git checkout -- .", True),
    ("git restore src/", True),
    ("git stash clear", True),
    ("git stash drop stash@{0}", True),
    ("git push --force origin main", True),
    ("git push -f", True),
    ("git push origin :feat/old", True),
    ("git branch -D feat/wip", True),
    ("git reflog expire --expire=now --all", True),
    ("rm -rf /workspaces/WY-workspace-P/.claude", True),
    ("rm -rf ~/backups", True),
    ("rm -rf $HOME/x", True),
    ("rm -rf output-reports", True),                   # 不在安全区
    ("rm *.pptx", True),                               # 通配符批量删
    ("rm -r .git", True),
    ("find . -name '*.tmp' -delete", True),
    ("shred -u secret.key", True),
    ("dd if=/dev/zero of=/dev/nvme1n1", True),
    ("mkfs.ext4 /dev/sdb1", True),
    ("truncate -s 0 important.log", True),
    ("chmod -R 777 /", True),
    ("gh repo delete owner/name", True),
    ("pkill -f uvicorn", True),
    ('python3 -c "d.files().delete(fileId=x).execute()"', True),
    # —— heredoc 正文是数据不是命令：上线首轮的真实误弹，回归用例 ——
    ("cd /w && python3 - <<'PY'\ns = \"文档里写 rm -rf / 很危险\"\nPY", False),
    ("python3 - <<'PY'\npat = r\"git reset --hard\"\nPY", False),
    ("cat <<'EOF' > note.md\n危险操作示例：git push --force\nEOF", False),
    # 但 heredoc **之外**的危险部分仍要抓到（别剥过头）
    ("python3 - <<'PY'\nprint(1)\nPY\nrm -rf /workspaces/x", True),
    # —— 第二轮 Stop 清单抓到的同类形态：命令里的「文本」不是「要执行的命令」——
    ('echo "=== 复现：命令文本里提到 rm -rf 就被判危险 ==="', False),
    ('echo "小心 git reset --hard"', False),
    ('printf "%s\\n" "rm -rf /"', False),
    ('grep -n "git reset --hard" *.py', False),
    ('rg "rm -rf" .claude/hooks', False),
    ('MSG="rm -rf /tmp/x"', False),
    ('ls -la  # 顺手删掉 rm -rf /workspaces', False),
    # 别剥过头：真命令、以及混在安全段之间的真命令，仍要抓到
    ('rm -rf "$HOME/backups"', True),
    ('rm -rf "/workspaces/WY-workspace-P/out"', True),
    ('echo "开始清理" && rm -rf /workspaces/x', True),
    ('grep -n foo a.py && git reset --hard', True),
    # —— 第三轮：本 hook 自己的 Stop 清单抓到的真实误报，全部必须静默 ——
    # 这些是「正则扫文本」路线堵不住、换成命令位置解析后才根治的形态。
    ('for c in \'echo "提到 rm -rf 就被判危险"\' \\\n\'echo "第二条"\'\ndo\n  true\ndone', False),
    ('python3 .claude/hooks/destructive-command-guard.py --selftest >/dev/null 2>&1', False),
    ('git worktree remove .claude/worktrees/fix-text-forms', False),
    ('mkdir -p /tmp/g && cp .claude/hooks/destructive-command-guard.py /tmp/g/', False),
    # 参数即代码的 head 仍要扫到（回归测试抓到过漏拦）
    ('python3 -c "import os; os.system(\'rm -rf /\')"', True),

    # ── python -c：区分「代码」与「数据」（2026-08-08 用户点名的误报）─────────
    # 退回文本扫描会把「把危险命令当字符串打印/存列表」也当成真要执行，于是弹一次
    # 无意义的窗。实测一轮里两条纯只读的诊断命令全被标破坏性。现用 AST 精确化。
    # 必须成对验：惰性的放行（下面 False 组）+ 真执行的照拦（True 组）——只验一边
    # 会让「一律放行」这种退化也全绿。
    ('python3 -c "for c in [\'git checkout -- a.md\']: print(c)"', False),
    ('python3 -c "print(\'rm -rf /workspaces/x\')"', False),
    ('python3 -c "cases = [\'git reset --hard\', \'git clean -fd\']; print(len(cases))"', False),
    ('python3 -c "import os; os.system(\'git checkout -- a.md\')"', True),
    ('python3 -c "import subprocess; subprocess.run([\'rm\', \'-rf\', \'/x\'])"', True),
    ('python3 -c "import shutil; shutil.rmtree(\'/workspaces/out\')"', True),
    # 间接执行渠道静态追不了 → 从严照拦（判不出不许放行）
    ('python3 -c "import os; c=\'rm -rf /x\'; eval(\'os.system(c)\')"', True),
    # 参数不是字面量（变量拼出来）→ 同样从严
    ('python3 -c "import os; c=\'rm -rf /x\'; os.system(c)"', True),
]


def _selftest():
    bad = 0
    for cmd, want in SELFTEST:
        got = bool(_scan(cmd))
        ok = got == want
        if not ok:
            bad += 1
        print("%s  want=%-5s got=%-5s  %s" % ("OK " if ok else "FAIL", want, got, cmd))
    total = len(SELFTEST)

    # ── 「先在对话里说清楚才放行」层（用户 2026-08-08 定的确认形态）─────────
    # 这层判的不是命令危不危险（上面 SELFTEST 已覆盖），而是**弹不弹窗**：
    # agent 已在回复正文里点过名 → 放行；没点名 → 照旧 ask。
    import tempfile

    def _mk_transcript(assistant_texts):
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for t in assistant_texts:
                fh.write(json.dumps({"type": "assistant", "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": t}]}},
                    ensure_ascii=False) + "\n")
        return p

    def chk(cond, desc):
        nonlocal bad, total
        total += 1
        if not cond:
            bad += 1
        print("%s  %s" % ("OK " if cond else "FAIL", desc))

    CMD = "rm -rf /workspaces/proj/out"
    p_said = _mk_transcript(["先说一句无关的。", "我要删掉 out 这个目录，里面是旧产物。"])
    p_mute = _mk_transcript(["我先看一下代码结构。"])
    # 早几轮说过、最近一条没说 → 不算（用户当下未必还记得）
    p_stale = _mk_transcript(["我要删掉 out 目录。", "现在改别的文件。"])
    chk(_said_in_chat(p_said, _targets(CMD)) is True,
        "A① 回复正文里点过名 → 放行（不再弹窗）")
    chk(_said_in_chat(p_mute, _targets(CMD)) is False,
        "A② 回复里没交代 → 照旧 ask")
    chk(_said_in_chat(p_stale, _targets(CMD)) is False,
        "A③ 只有早几轮说过、最近一条没说 → 不算交代过")
    chk(_said_in_chat(None, _targets(CMD)) is False,
        "A④ 拿不到 transcript → 保守，照旧 ask（判不出不许放行）")
    chk(_said_in_chat(p_said, []) is False,
        "A⑤ 挑不出操作目标 → 保守，照旧 ask")
    # thinking 块不算「告诉过用户」——用户看不见思考
    fd_t, p_think = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd_t, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                 "content": [{"type": "thinking", "thinking": "我要删 out 目录"}]}},
                 ensure_ascii=False) + "\n")
    chk(_said_in_chat(p_think, _targets(CMD)) is False,
        "A⑥ 只在思考块里提到不算交代（用户看不见思考）")
    # 多目标：必须**每个**都点过名，漏一个就不算
    CMD2 = "rm -rf /w/alpha /w/beta"
    p_half = _mk_transcript(["我要删掉 alpha 目录。"])
    chk(_said_in_chat(p_half, _targets(CMD2)) is False,
        "A⑦ 多个目标只交代了一个 → 不算（漏的那个用户不知情）")
    for _p in (p_said, p_mute, p_stale, p_think, p_half):
        os.unlink(_p)

    print("\n%d/%d 通过" % (total - bad, total))
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    main()
