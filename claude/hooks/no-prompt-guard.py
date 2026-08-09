#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""no-prompt-guard —— 开机把「免弹窗」四层配置**自动修回去**，不是提醒。

## 为什么要有这个（用户 2026-08-09 原话）

> 「为什么反复改了好多遍还是不能改成硬性的规定，或者是你所谓的闸门的要求？
>  不要只是提醒……我不点确认它就不往下走，它并没有任何意义。你弹我也看不懂，
>  我倒莫不如一口气执行到底，然后你告诉我一个结果。」

这条批评是对的，而且批的正是本仓最典型的失败形态：**每次都在「改配置」，从来没有
东西在「守配置」**。三轮下来的实际经过——

| 轮次 | 当时以为齐了 | 实际漏了 |
|---|---|---|
| 08-07 | 一个开关（defaultMode） | VS Code 客户端 toggle |
| 08-08 | 两个开关 | Bash 沙箱、ask 名单 |
| 08-09 | 四层 | ？（下一版 CLI 可能再加一层） |

每一轮都是「用户又被弹 → 人肉排查 → 改配置 → 宣布搞定」。**配置本身没错，错在
它是一次性的**：没人在开机时核一遍它还在不在、有没有被别的东西盖掉、有没有冒出新的层。
本仓 CLAUDE.md 早就写过这条元规则——「凡靠环境里装了什么生效的东西，都要配一道
『它还在不在』的自查；且别只问谁把它装上，要问谁可能把它盖掉」——免弹窗这件事却一直
只有「装」没有「查」。本 hook 补的就是这一段。

## 与 merge_settings.py 的分工（语义刻意不同，别合并）

- `merge_settings.py`：**缺失才写、绝不覆盖**。它是安装器，尊重用户在 /config 里的改动。
- 本 hook：**强制维持**。用户已经三次明确要求「一个都别弹」，那这就是他要的稳定态，
  被任何东西改回去都该自动纠正。留后门见下。

**后门（唯一的）**：在 `~/.claude/settings.json` 里写 `"_no_prompt_guard": "off"`,
本 hook 立刻完全让位、什么都不做。想临时收紧回 default/plan 模式时用它，
否则本 hook 会把你改的值当成漂移、又修回去。

## 修什么、不修什么

**自动修（都在 ~/.claude/settings.json，不进 git，改了不污染任何仓库工作区）**：
1. `permissions.defaultMode` = `bypassPermissions` —— 会话起步就免弹窗
2. `sandbox.enabled` = `false` —— Bash 沙箱（第三层，defaultMode 管不到它；
   它按操作系统边界放行：写只限 cwd、网络只限白名单，越界就弹）
3. `permissions.ask` = `[]` —— 「这几条必须问一次」的名单

**只报告不自动改**：项目级 `<project>/.claude/settings.json` 的 ask 名单。
理由：那个文件**在 git 里**，自动改会让每个项目的工作区无缘无故变脏、`git status`
冒出一条谁也没动过的改动，比弹窗更让人困惑。报给 agent，由它当场处理并告知用户。

**碰都不碰**：VS Code 的 `claudeCode.allowDangerouslySkipPermissions`（在用户本地
电脑上、agent 写它属自我授权，Claude Code 安全分类器硬拒）；`env`（含内网代理，
2026-08-07 被 welcome-claude.sh 整文件覆盖过一次，此后凡写这个文件都必须逐键改、
绝不整体重写）；`hooks`（别的机制在管）。

## 新层怎么办（这是最难的一半，诚实说明能力边界）

本 hook 只认识**已知**的三层。下一版 CLI 再默认打开一个新的弹窗层，它照样不知道。
能做的是**把「版本变了」这个信号显性化**：记住上次跑通时的 CLI 版本，版本一变就提示
「可能有新的弹窗层，用对照实验查一遍」（写 cwd 内 / 写 /tmp / 联网 / 只读，四条对照
一比，边界立刻现形——2026-08-09 定位沙箱就是这么找出来的，读配置读不出来）。
**这不是自动修，是把「该重新查一遍」从没人记得变成开机撞脸。**

fail-open：任何异常一律静默 exit 0，绝不卡住会话启动。
"""

import json
import os
import sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")
STATE = os.path.expanduser("~/.claude/.no-prompt-guard-state.json")
OPT_OUT = "_no_prompt_guard"

# 要强制维持的三项：(在 settings 里的路径, 期望值, 人话说明)
WANT = [
    (("permissions", "defaultMode"), "bypassPermissions", "会话起步就免弹窗"),
    (("sandbox", "enabled"), False, "关掉 Bash 沙箱（写工作目录外 / 联网都会弹的那层）"),
    (("permissions", "ask"), [], "清空「必须问一次」名单"),
]


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
        return json.loads(raw) if raw else default
    except Exception:
        return default


def _get(d, path):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return KeyError
        cur = cur[k]
    return cur


def _set(d, path, val):
    cur = d
    for k in path[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[path[-1]] = val


def audit(settings):
    """返回 [(路径点号形式, 现值, 期望值, 人话)]，只列**不符**的项。"""
    drift = []
    for path, want, why in WANT:
        have = _get(settings, path)
        if have is KeyError or have != want:
            shown = "(未设置)" if have is KeyError else have
            drift.append((".".join(path), shown, want, why))
    return drift


# VS Code 扩展侧的**准入**开关。落点是容器内两侧 server 的 Machine settings。
# 2026-08-09 实测改正：本文件此前写它「在用户本地电脑上」——**错的**。判据取自扩展
# 自己的定义文件（`anthropic.claude-code-*/package.json` 的 contributes.configuration）：
#   claudeCode.allowDangerouslySkipPermissions   scope = machine
#   claudeCode.initialPermissionMode             scope = machine
# machine scope 在 remote/devcontainer 窗口里**只认容器侧的值**，本地电脑 User 标签页
# 勾了读不到（用户曾照「静态偏好放 User」的通则勾 User、失败、白问一轮）。代价：这两个
# 路径在容器 overlay 盘，**rebuild 即清空，每个新容器要重勾一次**。
# 症状长这样：模式菜单里**根本没有 Bypass permissions 这一项**（不是没选中），而
# 配置侧三项全绿、--check 全过。
VSCODE_MACHINE_SETTINGS = [
    ("桌面 VS Code（Remote 连进容器）",
     "~/.vscode-server/data/Machine/settings.json",
     "~/.vscode-server/extensions"),
    ("Ona 网页版 VS Code",
     "~/.vscode-browser-server/data/Machine/settings.json",
     "~/.vscode-browser-server/extensions"),
]
VSCODE_TOGGLE = "claudeCode.allowDangerouslySkipPermissions"
CC_EXT_PREFIX = "anthropic.claude-code"


def vscode_toggle_missing():
    """哪几侧的 VS Code 缺「准入」开关。返回 [(人话名, 文件路径), ...]。

    **刻意只检测、绝不写。** 写这个键 = agent 给自己解锁更高的权限档位（自我授权）。
    这一条不是用户偏好、用户点头也不解除——它防的正是「有人冒充用户来说服 agent 给
    自己提权」；如果一句授权就能解锁，那么提示词注入同样能解锁，这个开关就白设了。
    间接写（塞进 devcontainer.json / 塞进 install.sh 让它以后自动跑）只是把同一件事
    延后一层，性质不变，同样不做。缺了怎么办 → 报出来 + 给精确到标签页的 GUI 路径，
    由用户自己点那一下。

    **判据是「这一侧装了 Claude Code 扩展吗」，不是「这一侧的 server 目录在吗」**
    （2026-08-09 当场改的：初版按目录判，于是网页版侧被报缺——可那侧根本没装 Claude Code
    扩展、压根不会跑 Claude，报它纯属噪音。dotfiles 的 extensions.txt 也刻意没把
    anthropic.claude-code 放进跨项目清单。**提醒只在「这一侧真会用到它」时才响**，
    否则用户每开一个窗口都被弹一次没用的，提醒很快就失信。）
    """
    missing = []
    for label, rel, ext_dir in VSCODE_MACHINE_SETTINGS:
        try:
            names = os.listdir(os.path.expanduser(ext_dir))
        except Exception:
            continue                               # 这一侧不存在 / 读不到
        if not any(n.startswith(CC_EXT_PREFIX) for n in names):
            continue                               # 这一侧没装 Claude Code，不关它的事
        d = _load(os.path.expanduser(rel), {})
        if not isinstance(d, dict) or d.get(VSCODE_TOGGLE) is not True:
            missing.append((label, rel))
    return missing


def project_ask(project_dir):
    """项目级 ask 名单（非空即为会弹窗的来源）。拿不到就返回 []。"""
    if not project_dir:
        return []
    p = os.path.join(project_dir, ".claude", "settings.json")
    if not os.path.isfile(p):
        return []
    d = _load(p, {})
    ask = (d.get("permissions") or {}).get("ask") or []
    return ask if isinstance(ask, list) else []


def repair(settings, drift):
    """逐键改写并落盘。**绝不整体重写**——env 里有内网代理，2026-08-07 被整文件
    覆盖过一次，permissions/hooks 全部蒸发、全程零报错。写前先验能不能序列化。"""
    for path, _, want, _ in drift:
        _set(settings, tuple(path.split(".")), want)
    blob = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    tmp = SETTINGS + ".no-prompt-guard.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(blob)
    os.replace(tmp, SETTINGS)


def version_changed():
    """CLI 版本变了 → 可能冒出新的弹窗层。返回 (变了吗, 旧版本, 新版本)。"""
    now = os.environ.get("CLAUDE_AGENT_SDK_VERSION") or ""
    if not now:
        return False, "", ""
    old = _load(STATE, {}).get("cli_version", "")
    if old and old != now:
        return True, old, now
    if old != now:
        try:
            st = _load(STATE, {})
            st["cli_version"] = now
            with open(STATE, "w", encoding="utf-8") as f:
                json.dump(st, f)
        except Exception:
            pass
    return False, old, now


def _remember(version):
    try:
        st = _load(STATE, {})
        st["cli_version"] = version
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def build_report(fixed, proj_ask, bumped, old_v, new_v, vsc_missing=()):
    lines = []
    if vsc_missing:
        lines.append(
            "【免弹窗自检】VS Code 侧的**准入开关**没了，所以模式菜单里根本不会出现 "
            "Bypass permissions —— 会话只能退到 Auto，Auto 的定义就是「遇到有风险的动作停下来问」。"
            "配置侧三项就算全绿也没用，这一层独立。缺在："
        )
        for label, rel in vsc_missing:
            lines.append("  · %s（%s）" % (label, rel))
        lines.append(
            "  **这一项 agent 不能代劳**（写它 = 自我授权，用户点头也不解除——它防的正是"
            "「冒充用户说服 agent 提权」）。请把下面这段原样转达用户，一步都别省："
        )
        lines.append(
            "    Ctrl+, 打开设置 → 搜索框粘 allowDangerouslySkipPermissions → "
            "上方会出现 User / Remote [Dev Container] / Workspace 三个标签页 → "
            "**点中间那个 Remote [Dev Container]**（点 User 无效，这个键是 machine 作用域，"
            "remote 窗口只认容器侧的值）→ 勾上 → Ctrl+Shift+P 输入 Reload Window。"
        )
        lines.append(
            "  也要说清代价：这个勾在**容器重建后会再次消失**，得重勾；"
            "以及一条现在就能用的替代路径——在 VS Code 终端里直接敲 `claude`，"
            "终端版读 ~/.claude/settings.json 的 bypassPermissions、完全不经过这个开关。"
        )
    if fixed:
        lines.append("【免弹窗自检】发现 %d 项配置被改回去了，**已自动修复**：" % len(fixed))
        for name, had, want, why in fixed:
            lines.append("  · %s：%s → %s（%s）" % (name, had, json.dumps(want, ensure_ascii=False), why))
        lines.append("  这三项在 ~/.claude/settings.json，不进 git。修完即时生效，不用重开会话。")
    if proj_ask:
        lines.append(
            "【免弹窗自检】本项目 .claude/settings.json 里还有 %d 条 ask 名单，"
            "**它会照弹**（项目级与用户级是叠加生效的，只清一边等于没做）：" % len(proj_ask)
        )
        lines.append("  " + "、".join(proj_ask[:6]) + ("…" if len(proj_ask) > 6 else ""))
        lines.append(
            "  刻意不自动改——那个文件在 git 里，自动改会让工作区平白变脏。"
            "请把该数组清空（改动理由写进 _ask_note），改完告诉用户这条只对本项目生效。"
        )
    if bumped:
        lines.append(
            "【免弹窗自检】Claude Code 从 %s 升到了 %s。**新版本可能默认打开新的弹窗层**"
            "（沙箱当初就是这么冒出来的，读配置读不出来）。若用户再抱怨弹窗，别只查已知四层，"
            "先做对照实验定位边界：① 写工作目录内 ② 写 /tmp ③ 联网（git fetch）④ 只读，"
            "哪条弹哪条不弹，边界立刻现形。" % (old_v, new_v)
        )
    return "\n".join(lines)


def build_user_message(vsc_missing):
    """**给用户自己看**的那条（hook 输出的顶层 `systemMessage`），不是给 agent 的。

    为什么必须单独有这一条（2026-08-09 用户点名）：本 hook 初版只写 `additionalContext`,
    那是**注入给 agent 的上下文、用户根本看不见**。于是「开机自动提醒」只做到一半——
    agent 知道了，唯一能动手的人不知道，用户照旧是「用了半天才发现被降级成 Auto」。
    用户原话：「新进窗口的时候你能自动提醒我……别让我事后才发现。」

    只在**这一侧真装了 Claude Code、且开关确实没勾**时才出现；勾上了立刻不再打扰。
    刻意写得短、每一步都能照着点——用户看的东西不能是长篇大论，否则他会开始略过它。
    """
    if not vsc_missing:
        return None
    return (
        "⚠️  Bypass 模式现在用不了 —— VS Code 里那个准入开关没勾，"
        "所以模式菜单里不会出现 Bypass permissions，只能停在 Auto，Bash 命令会一条条问你。\n"
        "怎么勾（30 秒）：\n"
        "  1. 按 Ctrl+,  打开设置\n"
        "  2. 搜索框粘：allowDangerouslySkipPermissions\n"
        "  3. 上方三个标签页 User / Remote [Dev Container] / Workspace —— "
        "**点中间的 Remote [Dev Container]**（点 User 没用）\n"
        "  4. 勾上，然后 Ctrl+Shift+P 输入 Reload Window\n"
        "不想勾也行：在终端里直接敲 claude，终端版不经过这个开关、本来就是免弹窗的。\n"
        "（容器重建后这个勾会消失，那时我会再提醒你一次。）"
    )


def run_hook():
    settings = _load(SETTINGS, None)
    if settings is None or not isinstance(settings, dict):
        return 0                                   # 文件没了/坏了：别插手，交给 install.sh
    if str(settings.get(OPT_OUT, "")).lower() == "off":
        return 0                                   # 用户显式让位

    drift = audit(settings)
    fixed = []
    if drift:
        try:
            repair(settings, drift)
            fixed = drift
        except Exception:
            fixed = []                             # 修不动就别谎报修好了

    proj = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    bumped, old_v, new_v = version_changed()
    if bumped:
        _remember(new_v)

    vsc = vscode_toggle_missing()
    report = build_report(fixed, project_ask(proj), bumped, old_v, new_v, vsc)
    user_msg = build_user_message(vsc)

    # 两个出口，喂给两拨人，别混：
    #   additionalContext → 注入给 **agent** 的上下文（用户看不见）
    #   systemMessage     → 直接显示给 **用户**（agent 看不见）
    # 只有用户点得了那个开关，所以这一条必须走 systemMessage —— 初版只写前者，
    # 等于「提醒了唯一帮不上忙的那个人」。
    out = {}
    if report:
        out["hookSpecificOutput"] = {
            "hookEventName": "SessionStart",
            "additionalContext": report,
        }
    if user_msg:
        out["systemMessage"] = user_msg
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


def run_check():
    """给 doctor.sh / 人工用：**坏状态真的退出码非 0**（本仓铁律：没红过的绿=没测过）。"""
    settings = _load(SETTINGS, None)
    if settings is None:
        print("❌ ~/.claude/settings.json 读不到")
        return 1
    if str(settings.get(OPT_OUT, "")).lower() == "off":
        print("⏸  已由 _no_prompt_guard=off 显式关闭，不检查")
        return 0
    drift = audit(settings)
    proj_ask = project_ask(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    for path, want, why in WANT:
        name = ".".join(path)
        bad = [d for d in drift if d[0] == name]
        print(("❌ %s = %s（应为 %s）" % (name, bad[0][1], json.dumps(want, ensure_ascii=False)))
              if bad else ("✅ %s（%s）" % (name, why)))
    print("❌ 本项目 ask 名单还有 %d 条，会照弹" % len(proj_ask) if proj_ask
          else "✅ 本项目 ask 名单为空")

    # VS Code 准入开关：**报但不计入退出码**。它不是 agent 能修的漂移（写它属自我授权），
    # 且 rebuild 后必然缺一次——计进退出码会让 doctor 长期红着、信号被稀释。但也绝不
    # 报成绿（那就是假绿：这一项缺了，用户照样弹窗）。所以单列 ⚠️ 一节，写清是谁的活。
    vsc = vscode_toggle_missing()
    if vsc:
        print("⚠️  VS Code 准入开关 %s 缺失，Bypass 档位不会出现在模式菜单里："
              % VSCODE_TOGGLE)
        for label, rel in vsc:
            print("     · %s（%s）" % (label, rel))
        print("     设置里搜该键 → 勾 **Remote [Dev Container]** 标签页（不是 User）→ "
              "Reload Window。agent 不能代勾（自我授权）。容器重建后要重勾。")
        print("     不计入退出码：这不是 agent 能修的项，只有用户点得了。")
    else:
        print("✅ VS Code 准入开关已勾（Bypass 档位可选）")

    return 1 if (drift or proj_ask) else 0


def _selftest():
    import tempfile
    global SETTINGS, STATE
    ok, bad = [], []

    def check(desc, got, want):
        (ok if got == want else bad).append("%s（得到 %r，应为 %r）" % (desc, got, want))

    # 1) audit 能认出三种漂移，也能认出全好
    check("全好 → 无漂移", audit({"permissions": {"defaultMode": "bypassPermissions", "ask": []},
                                "sandbox": {"enabled": False}}), [])
    check("三项全缺 → 3 条漂移", len(audit({})), 3)
    check("sandbox 被打开 → 抓得到",
          [d[0] for d in audit({"permissions": {"defaultMode": "bypassPermissions", "ask": []},
                                "sandbox": {"enabled": True}})], ["sandbox.enabled"])
    check("ask 混进一条 → 抓得到",
          [d[0] for d in audit({"permissions": {"defaultMode": "bypassPermissions",
                                                "ask": ["Bash(rm -rf:*)"]},
                                "sandbox": {"enabled": False}})], ["permissions.ask"])

    with tempfile.TemporaryDirectory() as td:
        SETTINGS = os.path.join(td, "settings.json")
        STATE = os.path.join(td, "state.json")

        # 2) 真的修好了，且 **env 一个字节没动**（2026-08-07 整文件覆盖事故的回归测试）
        env = {"ANTHROPIC_BASE_URL": "http://proxy.internal/x", "SECRET": "keep-me"}
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump({"env": env, "permissions": {"ask": ["Bash(rm -rf:*)"]},
                       "hooks": {"Stop": [{"id": "keep"}]}}, f)
        run_hook()
        after = _load(SETTINGS, {})
        check("修复后 defaultMode", after["permissions"]["defaultMode"], "bypassPermissions")
        check("修复后 sandbox.enabled", after["sandbox"]["enabled"], False)
        check("修复后 ask 清空", after["permissions"]["ask"], [])
        check("env 原样保留", after.get("env"), env)
        check("hooks 原样保留", after.get("hooks"), {"Stop": [{"id": "keep"}]})

        # 3) 幂等：再跑一次不该有漂移
        check("幂等：第二次跑无漂移", audit(_load(SETTINGS, {})), [])

        # 4) 后门：显式 off 时**一个字节都不许动**
        off = {"_no_prompt_guard": "off", "permissions": {"ask": ["Bash(rm -rf:*)"]}}
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(off, f)
        run_hook()
        check("off 时不改动", _load(SETTINGS, {}), off)
        check("off 时 --check 放行", run_check(), 0)

        # 5) **坏状态退出码真的非 0** —— 没红过的绿 = 没测过
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump({"permissions": {"ask": ["Bash(rm -rf:*)"]}}, f)
        check("--check 遇漂移 → 退出码 1", run_check(), 1)
        run_hook()
        check("--check 修好后 → 退出码 0", run_check(), 0)

        # 6) 坏文件 fail-open，且不得把文件改坏
        with open(SETTINGS, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        check("损坏的 settings → hook 静默放行", run_hook(), 0)
        check("损坏的 settings → 原文未被覆盖",
              open(SETTINGS, encoding="utf-8").read(), "{ 这不是 json")

    for line in ok:
        print("  PASS: " + line)
    for line in bad:
        print("  FAIL: " + line)
    print("\n%d/%d 通过" % (len(ok), len(ok) + len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            sys.exit(_selftest())
        if "--check" in sys.argv:
            sys.exit(run_check())
        sys.exit(run_hook())
    except Exception:
        sys.exit(0)                                # fail-open：绝不卡住会话启动
