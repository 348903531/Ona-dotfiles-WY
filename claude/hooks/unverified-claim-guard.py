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
#
# 判定顺序（issue #544 重写）：**显式退出码 > 进程崩溃 > 字符信号**。
#
# 为什么改：初版把 `❌` 直接当失败标志，而本仓脚本**普遍用 ✅/❌ 做逐项状态标记**
# ——体检、探测、待办清单类脚本的产品形态就是"列出哪几项不通过"，一次成功的运行
# 里出现十几个 ❌ 完全正常。2026-08-22 一次会话里本 hook 因此**连续误报四次**，
# 被引为"失败输出"的分别是：token 权限探测结论、配置体检报告、新上游待办清单、
# 以及用 Bash 跑 grep 看到的**源码内容**（那段源码里有个 print("❌ …")）。
# 误报的代价不是"多说一句"——它逼人去按 SKIP 静音，而静音之后真的假绿也一并看不见，
# 比不报更糟（AGENTS.md：误报会让人把闸门关掉，等于没有）。

# ① 显式退出码：命令自己报了成绩，就别再拿字符去猜。
#    本仓纪律要求核退出码时不接管道（`| tail` 后的 $? 是 tail 的），
#    所以 `rc=N` / `EXIT=N` / `退出码 N` 在输出里很常见，是最可靠的信号。
#    前置用「非字母数字」的后顾断言而不是「行首或空格」：中文语境里它常常紧跟
#    标点（「FAIL 命中 3 处，退出码 1」），要求空格会漏掉；同时挡住 src= / arch=
#    这类把 rc 夹在词中间的误匹配。
# 退出码必须出现在**行首附近**（前 16 字符内，典型形态 `rc=0` / `  测试 rc=0`），
# 且前面不是引号或等号。否则会把「文本里提到 rc」当成真退出码——
# 实测回归：一段诊断输出 `[1] kind=result why=["EXIT_BAD:'rc=1'"]` 里的 rc=1
# 被当成命令失败了。这是「描述≠发生」在退出码判据上的重演。
EXIT_ANY_RE = re.compile(
    r"^[^\n]{0,16}?(?<![A-Za-z0-9_'\"=])(?:rc|RC|EXIT|exit code|退出码)"
    r"\s*[=:：]?\s*([0-9]+)",
    re.M,
)


def _exit_verdict(text):
    """以**最后出现**的显式退出码为准。True=失败 / False=成功 / None=没报过。

    为什么是「最后一个」而不是「有没有非零」（这是本次改版最关键的一处，
    初版就栽在这里）：本仓纪律要求**喂坏输入看它真红**，于是一次工具调用里
    经常故意制造一串 rc≠0、最后还原成 rc=0 收尾——变异测试、
    「逼它走回退路径」、「喂三种坏输入验证真拦得住」全是这个形状。
    拿「出现过非零」当判据，会把**做得最认真的那类验证**统统判成失败。

    实测：改版初稿用「出现过非零就算失败」，在一份真实会话 transcript 上
    命中 24 处，**全部**是这种预期中的红。改成看最后一个之后才降下来。
    """
    last = None
    for m in EXIT_ANY_RE.finditer(text):
        # 2026-09-17 补：**逐项报告行里的退出码是用例名的一部分，不是本次的退出码**。
        # 真实误报：本 hook 自己的测试输出里有一条用例叫
        #   「✅ 诊断输出里**提到** rc=1（描述≠发生，#544 回归） (静默)」
        # 它排在开头的 `TEST_RC=0` 之后，于是「最后一个退出码」被读成 1，
        # 整段全绿、末行 `0 FAILED` 的测试结果被判成失败——而 ③④ 两档豁免
        # （OVERALL_GREEN / TALLY）在 ① 之后，根本走不到。
        # 判据：该行以逐项标记（✅/❌/⏭️/☑/✔/✗）开头 → 那是报告条目，跳过。
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_head = text[line_start:m.end()].lstrip()
        if line_head[:1] in ("✅", "❌", "⏭", "☑", "✔", "✗", "·", "-"):
            continue
        last = m.group(1)
    if last is None:
        return None
    return last.lstrip("0") != ""      # "0"/"00" → 成功

# ② 进程级崩溃：正常报告里不会出现这些，见到即失败。
HARD_FAIL_RE = re.compile(
    r"No such file or directory"
    r"|command not found"
    r"|Traceback \(most recent call last\)"
    r"|SyntaxError|ModuleNotFoundError|ImportError|AssertionError"
    r"|Permission denied",
    re.I,
)

# ②' 只读查看命令的「自己失败」判据：shell 工具报错的标准形态是
#     行首「命令名: 说明」（`cat: x.py: No such file or directory`）。
#     命令名必须字母开头——否则 `grep -n` 输出的行号前缀 `133:` 会被当成命令名，
#     于是"文件第 133 行恰好写着 No such file"就被误判成命令失败。
VIEW_HARD_RE = re.compile(
    r"^\s*[A-Za-z_][\w.-]*:\s[^\n]*"
    r"(?:No such file or directory|Permission denied|command not found)",
    re.M,
)

# ③ 弱信号：**只在"这段输出不像一份逐项报告"时才作数**。
#    `fatal:` 也放在这一档——它常常是脚本**如实打印的别处报错原文**
#    （实测：体检脚本把上游 403 的 `fatal: unable to access …` 原样贴出来，
#    那是它工作正常的证据，不是它自己崩了）。
SOFT_FAIL_RE = re.compile(
    # 计数式 FAIL/FAILED **必须前面跟非零数字**——本仓测试脚本的标准结尾是
    # 「11 PASSED / 0 FAILED」，把裸 \bFAILED\b 当失败标志会把每一次全绿都误判成失败
    # （初版就是这么误报的，selftest 当场红三条）。
    r"(?<![0-9])[1-9]\d*\s+FAILED?\b|(?<![0-9\s])FAILED?:"
    r"|❌"
    r"|fatal:",
    re.I,
)

# ⚠️ 已知误报形态（2026-09-12 实测，暂不改判据）：**测试用例的名字里含失败措辞**。
#    unittest 打印的是「<用例名> ... ok」，而本仓大量用例刻意取名为
#    「必错锚：标题写 CR 85%，正文一个 85 都没有 → 必须报。」——「必须报」被 ③ 档
#    的弱信号扫到，紧随其后的「24 个单测全过」就被判成「失败后宣称通过」。
#    那一次实跑是 RC=0 / `Ran 24 tests ... OK` / FAILED|ERROR|Traceback 计数为 0。
#    **刻意不为它放宽判据**：要区分「用例名里的失败词」与「真实失败行」，得解析
#    unittest 输出格式，而本 hook 面对的是任意工具的自由文本；放宽必然把真的假绿
#    一起放过。代价对称——这一档误报时，补跑一次并贴退出码就能澄清（本次即如此），
#    而漏报一次假绿的代价是整条交付链失信。

# 逐项报告的指纹：同一段输出里出现 ✅，说明它在做**逐项状态标记**，
# 那么其中的 ❌ 是"某一项不通过"，不是"这条命令失败了"。
TALLY_RE = re.compile(r"✅|PASS \d+|通过 \d+")

# 整段输出里若有「0 FAILED / 全绿」这类**收尾结论**，说明这次运行整体是通过的，
# 中间出现的 FAIL 字样是测试用例名或过程噪音，不作失败计。
OVERALL_GREEN_RE = re.compile(
    r"\b0\s+FAILED?\b|\bALL\s+PASS(?:ED)?\b|全部通过|0 FAIL\b", re.I)

# 这些工具的 tool_result 是**文件内容 / 结构信息**，不是命令执行结果——
# 其中出现的失败字样属「描述」而非「发生」，不作失败源，也不作「重跑转绿」的证据。
# 刻意不含 Bash / BashOutput / WebFetch：那些是真会失败的执行通道。
READONLY_TOOLS = frozenset({
    "Read", "Edit", "Write", "MultiEdit", "NotebookEdit",
    "Glob", "Grep", "TodoWrite",
})

# 用 **Bash 跑的只读查看类命令**，其输出是文件内容 / 目录列表，同样是「描述」
# 而非「发生」。与 READONLY_TOOLS 同一条思路，补上"用 Bash 跑 grep/cat"这条路径。
# 真实误报（2026-08-22）：`grep -n upstream staleness_probe.py` 把源码里的
# `print("❌ … 用法错误")` 打了出来，被判成这条命令失败了。
READONLY_BASH_HEADS = frozenset({
    "grep", "rg", "egrep", "fgrep", "cat", "bat", "head", "tail", "less",
    "awk", "sed", "wc", "ls", "find", "file", "stat", "diff", "column",
    "echo", "printf", "true", "cd", "pwd", "basename", "dirname",
    "sort", "uniq", "cut", "tr", "jq", "xxd", "od", "realpath", "readlink",
})
# git 只放行确定只读的子命令——`git push` / `git commit` 当然要算执行。
READONLY_GIT_SUBS = frozenset({
    "show", "log", "diff", "cat-file", "status", "ls-files", "ls-remote",
    "rev-parse", "rev-list", "branch", "config", "describe", "blame",
})


def _is_readonly_bash(cmd: str) -> bool:
    """整条命令是否**全部**由只读查看类子命令组成。

    保守设计：只要有一段不在白名单（python3 / bash / git push / 任何自建脚本），
    整条就当成会真正执行的命令 —— 判不出来不许放行。
    """
    if not cmd.strip():
        return False
    seen = False
    for line in cmd.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 先把引号内的内容抹掉再切分：`grep -nE "clone|fetch|https://"` 里的 `|`
        # 是**正则的一部分**，不是管道。不抹就会切出 `fetch` 这样的假命令头，
        # 于是一条纯 grep 被判成"会执行的命令"（#544 实测漏网的那一处）。
        line = re.sub(r"'[^']*'|\"[^\"]*\"", " ", line)
        for seg in re.split(r"\|\||&&|\||;", line):
            seg = seg.strip().lstrip("(").strip()
            if not seg:
                continue
            first = re.split(r"\s+", seg, 1)[0]
            head = os.path.basename(first)
            if head == "git":
                parts = re.split(r"\s+", seg)
                sub = next((p for p in parts[1:] if not p.startswith("-")), "")
                if sub not in READONLY_GIT_SUBS:
                    return False
            elif head not in READONLY_BASH_HEADS:
                return False
            seen = True
    return seen


def _is_failure(text: str, viewing: bool = False) -> bool:
    """这段 tool_result 代表「这条命令失败了」吗？

    优先级：显式退出码 > 进程崩溃 > 字符信号（见上方 A 节的说明）。

    viewing=True 用于 **Bash 跑的只读查看命令**（grep/cat/ls…）：它们的输出主体是
    文件内容，里面的 ❌ / fatal: 多半是"文件里写着这些字"。但**仍要认进程级失败**
    ——`ls: … No such file` 是 ls 自己失败了，那正是本 hook 的头号真实事故。
    初版把整条只读命令排除在外，当场打红三条必报用例（其中一条名字就叫
    「别修过头」）——**放松保护比误报更危险，这个方向的错要立刻退回来。**
    """
    # ① 命令自己报了退出码 —— 最权威，直接采信，不再拿字符猜
    verdict = _exit_verdict(text)
    if verdict is not None:
        return verdict
    # ② 进程级崩溃 —— 只读命令也照认，但判据要收紧
    if viewing:
        # `grep`/`cat` 看的文件里**本来就可能写着** "No such file or directory"
        # （测试 fixture、文档、错误处理代码）。区分「这条命令自己失败了」与
        # 「文件内容里有这句话」：前者是 shell 工具的标准报错格式——
        # **行首就是命令名 + 冒号**（`cat: x.py: No such file`）。
        # 命令名限定为字母开头，否则 grep -n 的行号前缀 `133:` 会被当成命令名。
        return bool(VIEW_HARD_RE.search(text))
    if HARD_FAIL_RE.search(text):
        return True
    # ③ 整体成功收尾结论
    if OVERALL_GREEN_RE.search(text):
        return False
    # ④ 弱信号：先排除「这是一份逐项报告」的情形
    if TALLY_RE.search(text):
        return False
    return bool(SOFT_FAIL_RE.search(text))

# ── B：成功断言（助手正文）──────────────────────────────────────────
CLAIM_RE = re.compile(
    r"验证(?:全部)?(?:通过|都过)|双验证通过|已验(?:证|过)"
    r"|全绿|都绿了|全部通过|均通过|全过"
    r"|已装(?:好|上|完)|安装成功|部署成功"
    r"|(?:实物|逐项|逐条)核验(?:全部)?(?:通过|在位)"
    r"|测试全过|全部 ?PASS",
)

# ── B'：把「自我纠正时引述的旧说法」排除掉 ──────────────────────────
# 2026-08-15 实测误报：正文写「我之前说"六道全绿"是把这道红排除在计数之外了」——
# 这是在**承认上一轮说错**，却因为引号里含「全绿」被判成新的成功断言。
# 后果很坏：**越老实复述自己说错的话，越会被再报一次**，等于惩罚认错。
# 判据：成功断言若落在「引述+否定/纠正」的语境里，不计。
SELF_CORRECT_RE = re.compile(
    r"(?:我)?(?:之前|上一?[轮条次]|刚才|先前)[^\n]{0,12}(?:说|写|报|讲)"
    r"|说法(?:有|不)|表述(?:有|不)|(?:这|那)(?:句|话|说法)(?:是)?(?:错|不准|有毛病)"
    r"|准确(?:的)?说法|应该(?:说|写)成|改成|纠正|更正|口径不准|把话改成",
)


def _is_self_correction(text: str, m: "re.Match") -> bool:
    """成功断言 m 是否是**引述自己说错的旧话**，而非新的断言。

    判据刻意收紧到「引述」这一层，不能只看段落里有没有纠正字眼——否则
    「我之前说错了。不过其实全部通过」这种**既认错又照样宣称通过**的写法
    会被整段豁免掉（初版就是这么漏的，新加的必报用例当场抓到）。

    只认两种引述形态：
      ① 断言被引号包住：我之前说「六道全绿」/ "六道全绿"
      ② 断言紧跟在「之前/上一轮 说/写/报」之后 12 字内
    """
    lo, hi = m.start(), m.end()
    # ① 引号包裹
    left = text[max(0, lo - 3): lo]
    right = text[hi: hi + 3]
    quotes_open = "「『\"“'‘"
    quotes_close = "」』\"”'’"
    if any(c in left for c in quotes_open) and any(c in right for c in quotes_close):
        return True
    # ② 紧跟在「之前说」之后
    seg = text[max(0, lo - 14): lo]
    return bool(re.search(r"(?:之前|上一?[轮条次]|刚才|先前)[^\n]{0,10}(?:说|写|报|讲)[^\n]{0,4}$", seg))


# ── C：重跑并转绿的痕迹 ─────────────────────────────────────────────
GREEN_RE = re.compile(
    r"\bPASSED\b|\bPASS\b|✅|EXIT=0|退出码\s*0|0 FAILED|0 FAIL\b|全部通过",
)

# ── D：如实说明「这一项确实失败了、且为什么」──────────────────────────
#
# 为什么要有这条（2026-08-15 补，本 hook 上线次日就撞上）：
# 原判据只给两条出路——「重跑转绿」或「把话改成实际状态」，但**只有前者可机械识别**。
# 于是当失败来自**外部客观限制**（对方设了禁止下载 / 接口下线 / 凭据在别人手里 /
# 403、404），「重跑转绿」这条路**永远走不通**：那不是我能修的东西。
# 实测形态：一次批量下载 135 个文件，134 个 OK、1 个 403（文件被所有者设了
# canDownload:false）。我当场诊断、试了两条替代路径、并在报告 / PR 正文 / memory
# 三处都写明「这份拿不到，只取到封面」——**完全按第二条出路处置了**，但因为它
# 不可能转绿，本 hook 每一轮都重报一次同一处，连报三轮。
#
# 这种「正确处置了却永远报」的误报最危险：它逼人去按 UNVERIFIED_CLAIM_GUARD_SKIP=1
# 静音，而静音之后**真的假绿也一并看不见了**——比不报更糟。
# 所以补第三条出路：**在失败与成功断言之间，正文如实说明了该项失败及原因** → 放过。
#
# 注意它放过的不是「假绿」：假绿的定义是「没看输出就宣称通过」。这里 agent 已经
# 把失败摆到台面上了，用户看得见，判断权回到人手里——这正是本闸门想要的结果。
ACK_RE = re.compile(
    r"(?:拿不到|取不到|下不了|读不到|获取不到|无法(?:获取|下载|读取|访问|完成))"
    r"|(?:被(?:限制|拒绝|禁止))|禁止下载|canDownload"
    r"|(?:该|这|那)(?:一)?(?:项|条|份|个)?(?:确实|的确)?失败"
    r"|(?:未|没)(?:能|有)?成功|失败了|报了\s*40[0-9]|\b40[34]\b"
    r"|SKIP(?:ped)?\b|跳过(?:了)?|只(?:取|拿|下)到"
    r"|(?:客观|外部)(?:限制|原因)|不是我能修"
    # ── 「故意制造的红」这一大类（#544 补）──────────────────────────
    # 本仓纪律要求**喂坏输入看它真红**（没红过的绿=没测过），于是变异测试、
    # 「逼它走回退路径」、「喂三种坏输入」都会留下一串 rc≠0。这些是**验证做得
    # 到位的证据**，不是假绿。agent 报告这类结果时的固定说法就是下面这些词——
    # 它已经把红摆到台面上了，正是本闸门想要的行为，不该再被报一次。
    r"|真(?:的)?红了|确实红了|必红|该红|都红了"
    r"|(?:符合|如)预期(?:的)?(?:红|失败)?|预期(?:中)?的(?:红|失败)"
    # 2026-09-17 补：本仓写必错锚时的**实际说法**是「（期望 1）」「（期望非0）」，
    # 而上面只认「预期」——一字之差全漏。真实误报：
    #   「事故版退出码=1（期望 1）」被判成假绿。
    r"|期望\s*(?:非\s*)?[0-9]|期望(?:值)?(?:是|为)?\s*(?:非\s*)?[0-9]",
)


# ── D'：**补验声明**（与 D 的「承认失败」分开）────────────────────────
#
# D（ACK）说的是「这一项确实失败了」，规则要求它**不能和成功断言同句**——
# 防止「有一个拿不到，不过整体通过」这种既认错又吹牛的写法蒙混。
#
# 但还有一类完全不同的句子：「五道闸门全绿（**我独立重跑，退出码单独取**）」。
# 这里的补充说明不是在承认失败，而是在**交代验证方式**——恰恰是本 hook 想要的行为
# （AGENTS.md：核退出码别接管道、要独立重跑）。它天然与成功断言同句，
# 按 D 的规则会被判成假绿，于是**最负责任的表述反而挨罚**。
# 实测：这是 #544 收尾时剩余误报里占比最大的一类。
#
# 所以单列一档：出现补验声明 → 放过，即使同句有成功断言。
# 风险是 agent 可以靠说这句话免罚——但它已经被「跨度 ≤6」框住，
# 且「声称自己重跑过却没重跑」已超出文本判据能力，那属于诚信问题、不是判据问题。
REVERIFY_RE = re.compile(
    r"(?:独立|重新|再)?重跑|重新跑|再跑(?:了)?一(?:次|遍)"
    r"|退出码单独取|单独取(?:的)?退出码|不接管道"
    r"|回到实物|实物(?:层)?核验|逐条(?:核验|追到)"
    r"|补跑(?:了)?|复验(?:过|了)?"
    # 变异测试类同属「补验声明」而非「承认失败」：
    # 「29 条全绿，变异 3 现在被抓住」是在说**我额外做了破坏性验证、而且它有效**，
    # 是本仓「没红过的绿=没测过」要求的动作，天然与成功断言同一句。
    # 归进 ACK 会因「不能与断言同句」被判假绿——等于惩罚做了额外验证的人。
    # 2026-09-17 补「判别力」：全仓 217 处用这个写法、只有 7 处写「辨别力」，
    # 而判据恰好只认了那个几乎没人用的。gate_discriminative_power.py 的中文名就是判别力。
    r"|(?:被)?抓住|变异\s*[0-9A-Za-z]?|变异测试|辨别力|判别力"
    r"|喂(?:坏|错)(?:输入|数据)|见它红过|红过",
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


def _events(path, current_turn_only=True):
    """把 transcript 摊平成时间序事件。

    current_turn_only=True 时**只看最后一条用户消息之后**的事件——这是本 hook
    从「每轮刷屏」变成「可用」的关键：

    Stop hook 每一轮都跑，而 transcript 是只增不减的。若扫全量，会话早期的一次
    命中会在**此后每一轮永远重报**，哪怕当轮早已如实交代或修好——用户没有任何
    办法让它闭嘴，最终只会把闸门关掉。2026-08-14 实测：同 4 处连报两轮，
    第二轮是在我已逐条如实说明之后。

    只看当轮的代价：跨轮的「上一轮失败、这一轮才宣称通过」抓不到。
    这是刻意取舍——**误报每轮刷屏的代价，远大于漏掉跨轮那一档**。
    """
    """把 transcript 摊平成时间序的 (kind, cmd, text) 列表。"""
    out = []
    tool_of_id, last_tool = {}, ""
    last_cmd_ro = False
    records = list(_iter(path))
    if current_turn_only:
        # 从后往前找最后一条**用户自然语言**消息（tool_result 也挂在 user 角色下，
        # 不能只看 role=user，必须是 type=text 的那种）
        start = 0
        for idx in range(len(records) - 1, -1, -1):
            msg = records[idx].get("message")
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            c = msg.get("content")
            if isinstance(c, str) or (isinstance(c, list) and any(
                    isinstance(b, dict) and b.get("type") == "text" for b in c)):
                start = idx
                break
        records = records[start:]
    for d in records:
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
                name = b.get("name") or ""
                if b.get("id"):
                    tool_of_id[b["id"]] = name
                last_tool = name
                cmd_text = inp.get("command") or ""
                last_cmd_ro = (name == "Bash" and _is_readonly_bash(cmd_text))
                out.append(("cmd", cmd_text[:400], ""))
            elif t == "tool_result":
                # 只读/编辑类工具的 result 是**文件内容**，不是命令执行结果——
                # 里面出现「退出码 1」「EXIT=1」「❌」是在**描述**闸门行为，不是在**发生**失败。
                # 真实误报（2026-08-21）：Edit 完 SKILL.md 后回显的文件内容里有一句
                # 「命中 FAIL 即退出码 1」，被当成命令失败，随后正文的「验证通过」就被判假绿。
                # 判不出工具名时**保守当成 Bash**（判不出不许放行）。
                tool = tool_of_id.get(b.get("tool_use_id"), last_tool) or "Bash"
                if tool in READONLY_TOOLS:
                    kind = "result_ro"        # 工具本身就返回文件内容，整段不作失败源
                elif last_cmd_ro:
                    kind = "result_view"      # Bash 只读查看：只认进程级失败
                else:
                    kind = "result"
                # content 是字符串时**直接用**，不要再 json.dumps 一遍：那会把
                # 真换行重新转义成字面的 `\n` 两个字符，于是「…\nrc=1」里的 rc
                # 前面变成字母 n，任何带「非字母数字」后顾断言的正则都会漏判。
                # （#544 实测：退出码识别就是这么失灵的，测试当场红。）
                cval = b.get("content")
                txt = cval if isinstance(cval, str) else json.dumps(
                    cval, ensure_ascii=False)
                out.append((kind, "", txt[:4000]))
            elif t == "text" and role != "user":
                out.append(("say", "", b.get("text") or ""))
    return out


_CMD_SKIP_HEADS = frozenset({"echo", "printf", "cd", "set", "export", ":", "true"})


def _norm_cmd(c: str) -> str:
    """取命令**主体**，用于判「是不是同一条命令重跑」。

    为什么不做整串精确比对（issue #544 实测踩到）：重跑时人几乎总会顺手改一点
    ——加 `| grep 关键字` 只看要紧那几行、加 `>/dev/null` 再单独取退出码、
    或者把它塞进一段带 `echo` 小标题的多行脚本。整串比对一个都认不出来，
    于是「我修好了并重跑转绿」被判成从没补验过，正是最伤的一类误报：
    **它专门惩罚"认真重跑了的人"。**

    做法：跳过 echo/cd 这类装饰性开头，取第一条实质命令，砍掉管道与重定向之后的部分。
    """
    for line in c.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # **同一行内**也要逐段找：`cd /tmp/x && python3 gate.py …` 是极常见的写法，
        # 只看行首会取到 cd、跳过后又没有"下一行"可看，于是回退成整串比对
        # ——重跑时参数一变就认不出。这是 #544 剩余误报的最大单一来源
        # （实测那份会话 39 处命中里，大半是「闸门红→修→重跑绿→说全绿」被漏认）。
        for seg in re.split(r"\s*(?:\|\||&&|\||>>|>|;|2>&?1?)\s*", line):
            seg = seg.strip()
            if not seg:
                continue
            head = os.path.basename(re.split(r"\s+", seg, 1)[0])
            if head in _CMD_SKIP_HEADS:
                continue
            return _cmd_key(seg)
    return re.sub(r"[\s'\"]+", "", c)[:120]


def _cmd_key(seg: str) -> str:
    """把一段命令压成「跑的是什么」——解释器 + 被跑的那个东西。

        python3 /long/path/deai_check.py --titles a.txt --body b.md → python3:deai_check.py
        bash tools/x/gate.test.sh 2>&1                              → bash:gate.test.sh
        git push origin main                                        → git:push

    **刻意忽略参数**：重跑时参数几乎总会变（换输入文件、加 --verbose、改 grep 关键字），
    带上参数比对等于永远认不出重跑。代价是「同一脚本跑不同输入」会被当成同一条命令，
    可能漏掉一次真的假绿；但本 hook 是 reminder 级，**误报把闸门变成噪音的代价，
    远大于偶尔漏报一次**——何况「同一个脚本重跑并转绿」本身就是很强的补验信号。
    """
    parts = [p for p in re.split(r"\s+", seg) if p]
    if not parts:
        return seg[:120]
    head = os.path.basename(parts[0])
    obj = ""
    for p in parts[1:]:
        if p.startswith("-"):
            continue
        obj = os.path.basename(p.strip("'\""))
        break
    return (head + ":" + obj)[:120]


# 失败之后隔了多少个事件，那句成功断言还算「在说这次失败」。
#
# 这是 #544 收尾时换上的**结构性**判据，替掉了"逐个形态打补丁"的老路。
# 起因：前几轮每修好一类误报，真实数据只降 1–2 处，再诊断又是**新的**形态
# （输出重定向到文件后用 tail 看、多命令混在一次调用里…）。本仓有条教训：
# 同一处连打三个补丁，说明工具选错了、不是规则不够。
#
# 换的判据来自实测分布：六份真实 transcript 里，命中的「失败→断言」跨度
# **中位数 16、最大 138**；而真正危险的形态——跑完验证、失败、**紧接着就宣称通过**
# ——跨度是 1。全部 7 条必报用例的跨度也都是 1。
# 隔了十几个工具调用才出现的那句"通过"，agent 中间早已在做别的事，
# 它指向这次失败的可能性很低，报了只会变成噪音。
# 取 6（必报用例的 6 倍余量）：真实数据上命中从 74 → 22 处，必报用例一条不丢。
MAX_CLAIM_GAP = 6


def analyze(events):
    """返回命中的 (失败片段, 成功断言片段) 列表。"""
    hits = []
    for i, (kind, _c, text) in enumerate(events):
        if kind not in ("result", "result_view"):
            continue
        if not _is_failure(text, viewing=(kind == "result_view")):
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
                    if events[m][0] in ("result", "result_view"):
                        if not _is_failure(
                                events[m][2],
                                viewing=(events[m][0] == "result_view")
                        ) or GREEN_RE.search(events[m][2]):
                            failed_cmd = "__RERAN_OK__"
                        break
                if failed_cmd == "__RERAN_OK__":
                    break
            if kind2 == "say" and REVERIFY_RE.search(text2):
                # D'：明确交代了「我重跑过 / 退出码单独取 / 回到实物核过」——
                # 这是补验声明，可以和成功断言同句，见 REVERIFY_RE 处的说明。
                break
            if kind2 == "say" and ACK_RE.search(text2) and not CLAIM_RE.search(text2):
                # D：正文如实说明了这一项失败（且这段话里没顺带宣称通过）→ 已合规处置
                break
            if kind2 == "say" and CLAIM_RE.search(text2):
                if k - i > MAX_CLAIM_GAP:
                    # 隔太远——中间做了一大串别的事，这句"通过"多半不是在说这次失败。
                    # 更靠后的断言只会更远，直接收工。
                    break
                m = CLAIM_RE.search(text2)
                # 引述自己说错的话不算新断言，否则会惩罚「认错」这个行为
                if _is_self_correction(text2, m):
                    continue
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
TAIL = (
    "\n（两条出路都会被自动放过：① 重跑那条命令并转绿（红测试的常规形态）；"
    "② **在正文如实说明这一项失败及原因**——用于外部客观限制致败、根本不可能"
    "转绿的情形（对方禁止下载 / 接口下线 / 403）。但「既承认失败又宣称通过」仍会报。"
    "\n误判逃生阀：UNVERIFIED_CLAIM_GUARD_SKIP=1）"
)


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
