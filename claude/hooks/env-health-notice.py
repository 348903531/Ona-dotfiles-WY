#!/usr/bin/env python3
"""SessionStart hook：会话一开始就把「你的通用设定没生效」这件事送到 agent 眼前。

── 为什么要有它（2026-09-07 用户点名）───────────────────────────────────────
体检脚本 dotfiles-doctor 一直都在、判据也对，但**要人主动敲命令才会跑**。真实后果：
VS Code 标题栏的环境名错了 12 天、三个功能装了没接上、设定仓库落后 16 个版本，
体检天天查得出来，而没有一次结论到达过用户眼前。用户原话：
「像这次标题栏名字错了，你其实也没提醒我呀。」

用户同轮明确选了「只提醒不动手」，并要求「一定要提醒到位」。于是做两层触达：
  ① shell/aliases.sh —— 每开一个新终端就显示（给用户直接看）
  ② 本 hook        —— 每次 Claude Code 会话开始时注入（给 agent 看，由 agent 用
                        人话主动告诉用户）
两层受众不同，不算重复。第 ② 层补的是「用户一直用同一个终端、不开新的」那个缺口。

── 刻意不自己跑体检 ────────────────────────────────────────────────────────
体检要约 1.3 秒（含一次 git fetch），挂在会话启动上会被感知到。本 hook 只读
shell/env_health_notice.sh 后台写下的那份结论文件，毫秒级。代价是结论可能是
一天内的旧值——对「设定过期」这种以天为尺度的漂移完全够用。

逃生阀：export ONA_ENV_HEALTH_NOTICE=off
自检：  python3 env-health-notice.py --selftest
"""

import os
import re
import sys

STATE = os.path.join(os.environ.get("TMPDIR", "/tmp"), ".ona-env-health")
ANSI = re.compile(r"\033\[[0-9;]*m")


def read_notice(path=STATE):
    """读结论文件，返回去掉颜色码的正文；没有 / 空 / 读不了都返回 ''。"""
    try:
        if not os.path.isfile(path):
            return ""
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return ""
    return ANSI.sub("", raw).strip()


def render(body):
    """把结论包成给 agent 看的指令。刻意写明「用人话说」——用户看不懂原始措辞。"""
    return (
        "【环境体检 · 有几项设定在这个容器里没生效】\n"
        + body
        + "\n\n"
        "↑ 这是后台体检的结论。**请在本轮回复里用大白话主动告诉用户**（他不会自己去跑体检，\n"
        "  这正是上次标题栏名字错了 12 天没人发现的原因）。说清三件事：哪几项没生效、\n"
        "  各自的实际影响是什么、修的话敲哪一条命令（dotfiles-fix）。\n"
        "  用户已明确选择「只提醒不动手」——**不要自动去修**，把决定权留给他。"
    )


def main():
    if os.environ.get("ONA_ENV_HEALTH_NOTICE") == "off":
        return 0
    body = read_notice()
    if body:
        print(render(body))
    return 0


def selftest():
    """不在 try 里的自检——fail-open 的 hook 会把自己的 bug 一起吞掉，
    所以必须有一条独立的、真的会红的验证路径。"""
    import tempfile

    ok = fail = 0

    def check(cond, name):
        nonlocal ok, fail
        if cond:
            ok += 1
            print("  ✅ %s" % name)
        else:
            fail += 1
            print("  ❌ %s" % name)

    d = tempfile.mkdtemp()
    empty = os.path.join(d, "empty")
    missing = os.path.join(d, "nope")
    colored = os.path.join(d, "colored")

    open(empty, "w").close()
    with open(colored, "w", encoding="utf-8") as f:
        f.write("\n\033[33m⚠️  你的通用设定有 2 处没生效\033[0m\n   \033[31m❌\033[0m 落后远端 3 个版本\n")

    check(read_notice(missing) == "", "文件不存在 → 空（阴性对照）")
    check(read_notice(empty) == "", "文件为空 → 空（阴性对照）")
    body = read_notice(colored)
    check("落后远端 3 个版本" in body, "有内容 → 读得到正文")
    check("\033[" not in body, "颜色码已去掉（注入 context 不带乱码）")
    check("用大白话主动告诉用户" in render(body), "包装里带了「用人话说」的指令")
    check("不要自动去修" in render(body), "包装里写明了只提醒不动手")

    os.environ["ONA_ENV_HEALTH_NOTICE"] = "off"
    check(main() == 0, "逃生阀 off → 直接返回 0")
    os.environ.pop("ONA_ENV_HEALTH_NOTICE", None)

    print("\n自检：%d 过 / %d 败" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    try:
        sys.exit(main())
    except Exception:
        # fail-open：体检提醒挂了绝不能拖累会话启动。
        sys.exit(0)
