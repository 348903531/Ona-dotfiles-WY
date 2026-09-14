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

── 但有一类项的时间尺度不是「天」，是「秒」（2026-09-14 实测，本段由来）────────
上面那句「以天为尺度的漂移」对**标题栏 / 扩展 / dotfiles 落后**成立，对**免弹窗
那几项不成立**——因为 no-prompt-guard 是**自愈**的：它每次会话启动都把配置写回去。
于是新容器上必然出现这个时序：

    02:34  容器建好，后台体检跑 → 此刻 ask 还没设 → 结论文件记下「❌ 2 处没生效」
    02:59  某个会话启动 → no-prompt-guard 自动把 ask 修回 []（真的修好了）
    03:00  我这个会话启动 → 读到 02:34 那份结论 → 向用户报「有 2 项没生效」

**配置是好的，提醒是红的。** 真实代价（当轮）：用户看到红，说「去修」，跑去修一个
已经好了的东西。而这套提醒存在的全部理由是「别让真问题埋 12 天」——**假红会训练
用户无视它，它一旦被无视就等于没有**（同 shell/env_health_notice.sh 里「只摘 ❌ 不
摘 ⚠️」那段的理由：常态噪音会让人整段跳过）。

修法刻意**不是**去剔那几行——那要枚举「哪几行归 no-prompt-guard 管」，而枚举
「什么算」在这里是发散的（doctor 3b 节的行文会变、汇总句与逐项句格式还不一样）。
改成问一个**不需要枚举**的问题：

    「结论文件说有红，而那个自愈脚本现在说自己是绿的」→ 这份结论过期了。

判据直接调 `no-prompt-guard.py --check`（**用被测方自己的判据，不另写一份**，
否则两份判据必漂移、漂移的那份报的绿是假绿）。实测 37ms，够在会话启动时当场跑。
命中就做两件事：① 本轮报文里标注「下面是旧值、免弹窗那几项刚复查是好的」，
免得 agent 又让用户白跑一趟；② 后台补跑一次完整体检，让**下一轮**的结论是对的
（整份重算，顺带把标题栏那些项也刷新，不用我知道哪行归谁）。

`--check` 跑不起来（脚本没装 / 超时 / 报错）→ **返回 None，什么都不改，原样输出**。
判不了的时候拒绝判断，别硬给一个绿——那就成了「我把真红吞掉了」，比假红更坏。

逃生阀：export ONA_ENV_HEALTH_NOTICE=off
自检：  python3 env-health-notice.py --selftest
"""

import os
import re
import subprocess
import sys
import time

STATE = os.path.join(os.environ.get("TMPDIR", "/tmp"), ".ona-env-health")
ANSI = re.compile(r"\033\[[0-9;]*m")

# 自愈脚本（判据的唯一来源）与完整体检的刷新入口。
NPG = os.path.expanduser("~/.claude/hooks/no-prompt-guard.py")
REFRESH = os.path.expanduser("~/dotfiles/shell/env_health_notice.sh")
# 后台重算的节流戳：真红且修不了的项（如 VS Code 准入开关，只有用户点得了）会让
# 「结论有红」长期成立，不节流就每开一个会话白跑一次 1.3 秒的体检。
RECHECK_STAMP = STATE + ".recheck"
RECHECK_THROTTLE_S = 600


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


def npg_verdict(npg=NPG, timeout=5):
    """自愈脚本此刻的判定：True=绿 / False=红 / **None=判不了**。

    None 这一档是刻意的（同 --check 自己「判不了就退 3」的约定）：脚本没装、超时、
    解释器报错时，既不能报绿（会吞掉真红），也不该报红（会造出一个不存在的问题）。
    """
    try:
        if not os.path.isfile(npg):
            return None
        p = subprocess.run([sys.executable, npg, "--check"],
                           capture_output=True, timeout=timeout)
        return p.returncode == 0
    except Exception:
        return None


def _throttled(stamp=RECHECK_STAMP, window=RECHECK_THROTTLE_S):
    """True = 窗口内已经触发过，这次别再 spawn。读不到戳一律放行（宁可多跑一次）。"""
    try:
        return (time.time() - os.path.getmtime(stamp)) < window
    except Exception:
        return False


def spawn_refresh(script=REFRESH, stamp=RECHECK_STAMP):
    """后台补跑一次完整体检，把结论文件整份重算。detach、不等、不看输出。

    整份重算而不是只改免弹窗那几行——这样不需要知道「哪行归谁管」，
    标题栏 / 扩展 / dotfiles 落后那些项也顺带刷新到最新。
    """
    try:
        if not os.path.isfile(script):
            return False
        try:
            with open(stamp, "w") as f:       # 先落戳：spawn 失败也不要陷入每轮重试
                f.write(str(int(time.time())))
        except Exception:
            pass
        subprocess.Popen(["bash", script, "--refresh"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


STALE_BANNER = (
    "\n\n"
    "⚠️ **上面这份结论已经过期了，别照着它报给用户。**\n"
    "  免弹窗那几项（defaultMode / sandbox / ask 名单）刚用 `no-prompt-guard.py --check`\n"
    "  当场复查过：**现在是绿的**。它们是自愈项——体检写下结论之后，自愈脚本把配置修回去了，\n"
    "  而没有任何东西回头改这份结论。已在后台重算，下一轮会话读到的就是对的。\n"
    "  → 本轮：**免弹窗相关的 ❌ 一律不要转达**（转达了用户会跑去修一个已经好的东西）；\n"
    "    其余项（标题栏 / 扩展 / dotfiles 落后）不在复查范围内，照常如实转达。\n"
    "    要亲眼确认：`python3 ~/.claude/hooks/no-prompt-guard.py --check > /tmp/c.log 2>&1; echo $?`"
)


def render(body, stale=False):
    """把结论包成给 agent 看的指令。刻意写明「用人话说」——用户看不懂原始措辞。

    stale=True 时在**正文之后、行动指令之前**插一条过期声明。位置是刻意的：
    放最后会被当成脚注略过，而这条声明恰恰要改写上面每一个 ❌ 的含义。
    """
    return (
        "【环境体检 · 有几项设定在这个容器里没生效】\n"
        + body
        + (STALE_BANNER if stale else "")
        + "\n\n"
        "↑ 这是后台体检的结论。**请在本轮回复里用大白话主动告诉用户**（他不会自己去跑体检，\n"
        "  这正是上次标题栏名字错了 12 天没人发现的原因）。说清三件事：哪几项没生效、\n"
        "  各自的实际影响是什么、修的话敲哪一条命令（dotfiles-fix）。\n"
        "  用户已明确选择「只提醒不动手」——**不要自动去修**，把决定权留给他。"
    )


def main(state=STATE, npg=NPG, stamp=RECHECK_STAMP, refresh=REFRESH):
    """四个路径都是**可注入的参数**，不是直接读全局。

    为什么这么写：初版把判定逻辑在 selftest 里**复刻**了一份来测（因为 main 读全局、
    没法注入）。变异测试当场打脸——把 main 里那行判据改成「不看守卫返回值」，
    25 条自检**全绿放行**，因为测的是复刻的那份、不是真正跑的那行。
    重新实现一遍被测逻辑 = 又引入一个会错的东西，而它错了没人拦。
    """
    if os.environ.get("ONA_ENV_HEALTH_NOTICE") == "off":
        return 0
    body = read_notice(state)
    if not body:
        return 0
    # 只有「结论说有红」时才值得花 37ms 去问一次——全绿的结论没有过期风险。
    stale = False
    if "❌" in body and npg_verdict(npg) is True:
        stale = True
        if not _throttled(stamp):
            spawn_refresh(refresh, stamp)
    print(render(body, stale))
    return 0


def selftest():
    """不在 try 里的自检——fail-open 的 hook 会把自己的 bug 一起吞掉，
    所以必须有一条独立的、真的会红的验证路径。"""
    import io
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

    # ── 过期判定（2026-09-14 新增）────────────────────────────────────────
    # 造三个假的 no-prompt-guard：绿 / 红 / 崩。真调 subprocess，不 mock——
    # mock 掉的正是「跑外部脚本」这段，而 bug 恰恰长在那里。
    def _fake_npg(name, body_):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body_)
        return p

    npg_green = _fake_npg("npg_green.py", "import sys\nsys.exit(0)\n")
    npg_red = _fake_npg("npg_red.py", "import sys\nprint('❌ permissions.ask')\nsys.exit(1)\n")
    npg_boom = _fake_npg("npg_boom.py", "raise SystemExit(3)\n")
    npg_hang = _fake_npg("npg_hang.py", "import time\ntime.sleep(30)\n")

    check(npg_verdict(npg_green) is True, "守卫退 0 → True（绿）")
    check(npg_verdict(npg_red) is False, "守卫退 1 → False（红）")
    check(npg_verdict(npg_boom) is False, "守卫退 3 → False（非 0 即红）")
    check(npg_verdict(os.path.join(d, "nope.py")) is None,
          "守卫脚本不存在 → None，不是 True（失败路径：判不了不许报绿）")
    t0 = time.time()
    check(npg_verdict(npg_hang, timeout=1) is None,
          "守卫卡住 → 超时后 None（失败路径）")
    check(time.time() - t0 < 5, "超时真的生效了（没干等 30 秒）")

    # render 三态
    red_body = "⚠️  有 2 处没生效\n   ❌ permissions.ask = (未设置)"
    check(STALE_BANNER not in render(red_body, False), "stale=False → 不插过期声明")
    check("已经过期了" in render(red_body, True), "stale=True → 插了过期声明")
    check(render(red_body, True).index("已经过期了")
          < render(red_body, True).index("请在本轮回复里用大白话"),
          "过期声明在行动指令之前（放最后会被当脚注略过）")

    # ── 阴性对照：最要紧的一条——真红绝不许被吞掉 ─────────────────────────
    # **端到端跑真正的 main()**，不复刻它的判定：初版复刻过，结果把 main 里那行
    # 改成「不看守卫返回值」时 25 条自检全绿放行（变异测试抓到的）。
    import contextlib

    def _run_main(body_, npg_path):
        """真调 main()，返回它实际打印出来的东西。"""
        sf = os.path.join(d, "state_%d" % (len(body_) + hash(npg_path) % 997))
        with open(sf, "w", encoding="utf-8") as f:
            f.write(body_)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(state=sf, npg=npg_path,
                 stamp=os.path.join(d, "st_stamp"),
                 refresh=os.path.join(d, "nope.sh"))   # 刷新脚本不存在 → 不会真 spawn
        return buf.getvalue()

    check("已经过期了" in _run_main(red_body, npg_green),
          "main：结论有红 + 守卫绿 → 输出带过期声明")
    check("已经过期了" not in _run_main(red_body, npg_red),
          "main：结论有红 + 守卫也红 → **不**带（阴性对照：真红照报）")
    check("已经过期了" not in _run_main(red_body, os.path.join(d, "nope.py")),
          "main：结论有红 + 守卫判不了 → **不**带（阴性对照：判不了不许吞红）")
    check("已经过期了" not in _run_main("✅ 全都好", npg_green),
          "main：结论本来就全绿 → 不带（阴性对照）")
    check(_run_main(red_body, npg_red).count("❌") >= 1,
          "main：真红时原文里的 ❌ 一条都没少（没被顺手剔掉）")

    # ── 节流与 spawn 的失败路径 ───────────────────────────────────────────
    stamp = os.path.join(d, "stamp")
    check(_throttled(stamp) is False, "戳不存在 → 不节流（宁可多跑一次）")
    open(stamp, "w").close()
    check(_throttled(stamp, window=600) is True, "刚落的戳 → 节流生效")
    check(_throttled(stamp, window=0) is False, "窗口为 0 → 不节流（边界）")
    check(spawn_refresh(os.path.join(d, "nope.sh"), stamp) is False,
          "刷新脚本不存在 → 返回 False 且不抛（失败路径）")
    ro_stamp = "/proc/nonexistent/stamp"   # 一定写不进去的路径
    check(spawn_refresh(os.path.join(d, "nope.sh"), ro_stamp) is False,
          "连戳都写不进去 → 仍不抛异常（失败路径：并发/只读盘）")

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
