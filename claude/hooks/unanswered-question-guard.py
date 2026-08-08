#!/usr/bin/env python3
"""unanswered-question-guard — Stop hook：把用户这一轮问的每个问题重新摆到眼前。

## 为什么有它（2026-08-08 用户点名）

用户原话：「你回答问题的时候总是漏问题，这也是很大的问题，看看怎么改掉。」

当轮实例：用户问「已经默认打开 ADHD 模式做好了吗？」——我**做了**，却只在总结表格里
塞了半句「ADHD 那套已经并进去了」，没有正面回答。用户当场指出「怎么没回答我呢」。

这与本仓已有的 `todo-silent-drop-guard` 同源，且更根本：

| | 验收范围被谁改窄 | 已有防线 |
|---|---|---|
| todo 静默蒸发 | 我改写 TodoWrite 清单，对着改窄的清单报「全做完」 | todo-silent-drop-guard |
| **漏答问题** | **我按「自己关注什么」筛选要答哪几条，用户提的其余问题悄悄消失** | **本 hook** |

共同的病根：**拿我自己控制、且我自己动过的东西，去证明我做完了。** 用户点到的最要命
一层同样适用：「可能我忘记了我之前提了什么问题，可能就被你糊弄过去了」——漏答比做漏活
更隐蔽，因为它**专门躲过用户的复查**。

「记得逐条答」是散文级约束，会被忘（AGENTS.md 卡#18 的主题）。所以由 hook 从 transcript
里**自己把问题抽出来打印**，不依赖 agent 的自觉。

## 判据：只抽取，不判断「答没答」

刻意**不做**「这个问题答了没」的语义判断——那需要理解回复内容，必然误报，误报几次这个
提醒就会被无视（本仓踩过：符号断链检查初版 9 命中 8 误报，当场推翻重写）。

本 hook 只干一件确定性的事：**把用户这条消息里的问题原样列出来**，让 agent 自己对着
点手指。信号（问号、疑问词、请求动词）稳定可判，不涉及理解。

## 抽哪些（四类，后三类最容易漏）

1. 显式问句——带 `？` `?`。
2. 含疑问词的陈述句——吗/呢/怎么/为什么/能不能/是不是/有没有/要不要/如何…
   （**最容易漏**：夹在一大段陈述中间，视觉上不突出。）
3. 祈使形式的请求——「帮我…」「看看…」「查一下…」，没问号但要求回应。
4. 重复提问——同一个问题在更早的消息里出现过 → 标 🔁 **优先答**（重复 = 上次就漏了）。

## 阈值：≥2 个问题才响

单个问题漏答概率低，报出来是噪音。漏答几乎只发生在「一条消息里问了好几件事」时——
用户当轮正是问了 4 件、答漏 1 件。宁可放过单问题，不制造噪音让提醒失信。

## 能力边界（诚实四条）

1. **只抽取、不判对错**。它不知道你答没答，只负责让「我忘了」这个借口不成立。
2. **中文启发式**，不是语法分析。反问句（「这不是很明显吗」）会被当成问题——宁可多列。
3. **只看最后一条真实用户消息**。system-reminder、tool_result、斜杠命令都跳过。
4. **不 block**。exit 0 + additionalContext，agent 可据此补答。

输入：stdin JSON（session_id / transcript_path）。
输出：抽到 ≥2 个问题 → 打印清单，exit 0；否则静默 exit 0。异常一律 fail-open。

自测：`python3 unanswered-question-guard.py --selftest`
"""

# ══════════════════════════════════════════════════════════════════════════
# 用户级 hook（~/.claude/hooks/）——由 Ona-dotfiles 装到**每一个**项目。
# 防双响：项目级装了同名 hook 时静默让位（与 session-change-digest 同规矩）。
# ══════════════════════════════════════════════════════════════════════════
import os as _os
import sys as _sys

_proj = _os.environ.get("CLAUDE_PROJECT_DIR") or ""
if _proj and _os.path.isfile(
        _os.path.join(_proj, ".claude", "hooks", _os.path.basename(__file__))):
    _sys.exit(0)

import hashlib
import json
import os
import re
import sys

CACHE_DIR = os.path.expanduser("~/.cache/claude-question-guard")
MIN_QUESTIONS = 2          # 少于这个数不响——见文档「阈值」一节
MAX_LISTED = 8             # 一条消息里问题再多也只列前 8 条，别刷屏
MAX_LEN = 70               # 每条截断长度（够认出是哪句就行）

# 疑问词：出现即视为问题，即使没有问号。
_INTERROGATIVE = re.compile(
    r"(吗[？?]?$|呢[？?]?$|怎么|怎样|如何|为什么|为啥|why|"
    r"能不能|能否|可不可以|可否|是不是|有没有|要不要|行不行|好不好|"
    r"什么时候|哪一?个|哪些|多少|几个|谁来|还是说)")

# 祈使形式的请求：没有问号，但要求回应或行动。
# 刻意收窄到明确的「请求动词 + 对象」组合，避免把叙述句（"我看看再说"）误当请求。
_IMPERATIVE = re.compile(
    r"(^|[，,、；;])\s*(帮我|帮忙|请你|麻烦你|你去|你来)|"
    r"(看看怎么|看看能不能|看看有没有|查一下|核对一下|确认一下|处理一下|"
    r"解释一下|说明一下|告诉我|给我讲|列一下|统计一下)")

# 这些不是提问，是应答/客套，别抽进来。
_NOT_A_QUESTION = re.compile(r"^(好的?|行|嗯+|ok|OK|收到|可以|同意|继续|谢谢|辛苦了?)[。.!！~]*$")

# URL 必须在切句前挖掉。真实误报（2026-08-08 在 6 个真实会话上实测发现）：
# 用户常贴 Google Drive 链接 `https://drive.google.com/open?id=...&usp=drive_fs`，
# 里面的 `?` 是查询串分隔符、不是问号——按标点切句会把一句话切成三四个碎片，
# 每个碎片都带 `?`、于是全被判成「问题」，一条消息虚报 6 个。
# 这个形态我自己造的夹具里根本没有，只有拿真实 transcript 当基线才照得出来。
# 注意边界：不能用 `\S+`——中文标点不是空白字符，`\S+` 会从 URL 一路吞掉后面
# 「。这个能不能直接用？」整句，把真问题一起吃掉（第一版就是这么修过头的）。
# 所以显式排除中英文句读，让 URL 在标点处自然结束；`?` `&` `=` 是 URL 合法字符，保留。
_URL = re.compile(r"(https?://|www\.)[^\s，。、；：！？“”（）【】「」]+", re.I)
_URL_PLACEHOLDER = "〔链接〕"


def _split_sentences(text):
    """按中英文句末标点 + 换行切句。保留原始标点便于识别问号。

    先把 URL 整体换成占位符——URL 里的 `?` `.` 都会被误当句末标点。
    """
    text = _URL.sub(_URL_PLACEHOLDER, text)
    # `?` 后面紧跟字母/数字/= 的，是残留的查询串（如 `open?id=`），不是句末。
    parts = re.split(r"(?<=[。！；;\n])|(?<=[？?!！])(?![A-Za-z0-9=&_-])", text)
    return [p.strip() for p in parts if p and p.strip()]


def extract_questions(text):
    """从一段用户消息里抽出「需要回应的条目」。返回 [(句子, 是否带问号)]。"""
    out = []
    for s in _split_sentences(text):
        s_clean = s.strip()
        if len(s_clean) < 3 or _NOT_A_QUESTION.match(s_clean):
            continue
        has_qmark = bool(re.search(r"[？?]", s_clean))
        if has_qmark or _INTERROGATIVE.search(s_clean) or _IMPERATIVE.search(s_clean):
            out.append((s_clean, has_qmark))
    return out


def _strip_noise(text):
    """去掉 system-reminder 等注入内容——那不是用户说的话。"""
    text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.S)
    text = re.sub(r"<[a-z_-]+-hook[^>]*>.*?</[a-z_-]+-hook>", " ", text, flags=re.S)
    return text


def _msg_text(entry):
    """从 transcript 一行里取出真实的用户文本；非用户消息返回 None。"""
    if entry.get("type") != "user":
        return None
    # **不是用户真说的话，一律排除**。Skill / slash-command 的正文是以 role=user
    # 注入 transcript 的，肉眼看不出区别，但会被当成「用户这条消息」抽问题——
    # 2026-08-08 实测：调一次 lesson-capture，它 SKILL.md 里的散文（「场景 = 什么
    # 时候会踩」之类）被抽成 **19 个问题** 报给我。后果不是多几行噪音，而是把真正
    # 漏答的问题淹掉，几次之后这个提醒就彻底失信——本 hook 存在的意义正是「别漏答」。
    # 判据取自实物：拿本会话 transcript 逐行核过，注入行带 isMeta=True + sourceToolUseID，
    # 5 条真实用户消息两者皆无，零重叠。两个都查是双保险（不同注入源可能只带其一）。
    if entry.get("isMeta") or entry.get("sourceToolUseID"):
        return None
    msg = entry.get("message") or {}
    if msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_result":
                return None          # 工具回执，不是用户说话
            if c.get("type") == "text":
                chunks.append(c.get("text") or "")
        return "\n".join(chunks) if chunks else None
    return None


def collect(transcript_path):
    """返回 (最后一条用户消息的问题清单, 更早消息里出现过的问题指纹集合)。"""
    users = []
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                t = _msg_text(entry)
                if t is None:
                    continue
                t = _strip_noise(t).strip()
                if not t or t.startswith("/"):     # 斜杠命令不算提问
                    continue
                users.append(t)
    except Exception:
        return [], set()
    if not users:
        return [], set()

    latest = extract_questions(users[-1])
    earlier = set()
    for t in users[:-1]:
        for s, _ in extract_questions(t):
            earlier.add(_fingerprint(s))
    return latest, earlier


def _fingerprint(s):
    """粗指纹：去标点空格后取前 24 字，用于识别「这个问题之前问过」。"""
    return re.sub(r"[\s，。、；：！？,.;:!?~—…\-]", "", s)[:24]


def render(questions, earlier):
    if len(questions) < MIN_QUESTIONS:
        return ""
    lines = ["🙋 **用户这条消息里提了 %d 个问题**——发送前逐条点手指，"
             "别在表格/长段落里顺带带过（那等于没答）：" % len(questions), ""]
    shown = questions[:MAX_LISTED]
    for i, (s, has_qmark) in enumerate(shown, 1):
        body = s if len(s) <= MAX_LEN else s[:MAX_LEN] + "…"
        mark = "🔁 " if _fingerprint(s) in earlier else ("" if has_qmark else "· 无问号 ")
        lines.append("%d. %s%s" % (i, mark, body))
    if len(questions) > MAX_LISTED:
        lines.append("…另有 %d 条未列出，回读原消息。" % (len(questions) - MAX_LISTED))
    if any(_fingerprint(s) in earlier for s, _ in shown):
        lines.append("")
        lines.append("🔁 = **之前问过、这次又问了一遍**，说明上次就漏了——优先答这条。")
    lines.append("")
    lines.append("答不了的也要明说「答不了 + 为什么 + 替代方案」；沉默不等于回答。")
    return "\n".join(lines)


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0
    session_id = data.get("session_id") or ""
    transcript_path = data.get("transcript_path") or ""
    if not session_id or not transcript_path or not os.path.isfile(transcript_path):
        return 0

    try:
        questions, earlier = collect(transcript_path)
        text = render(questions, earlier)
    except Exception:
        return 0                     # fail-open：本 hook 绝不卡住会话
    if not text:
        return 0

    # 同一条用户消息只提醒一次：Stop 可能在一轮里触发多次（agent 补答后再停）。
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        key = hashlib.sha1(
            (session_id + "|" + "|".join(q for q, _ in questions)).encode("utf-8")
        ).hexdigest()
        stamp = os.path.join(CACHE_DIR, key)
        if os.path.exists(stamp):
            return 0
        open(stamp, "w").close()
    except Exception:
        pass                         # 缓存写不了就多报一次，不影响正确性

    # 刻意**不设 systemMessage**：清单是给 agent 自查的，用户不需要每轮看一遍自己
    # 刚问过的话（实测真实会话里 ~半数消息会触发，弹给用户就是刷屏、提醒很快失信）。
    # 用户看到的应该是「答全了的回复」这个结果，不是「提醒 agent 别漏」这个过程。
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": text},
    }, ensure_ascii=False))
    return 0


# ── 自测：好输入要抽对、坏输入要静默（没红过的绿 = 没测过）────────────────────
def _selftest():
    checks = []
    bad = 0

    def want(cond, desc):
        nonlocal bad
        checks.append(("PASS" if cond else "FAIL", desc))
        if not cond:
            bad += 1

    # 1) 当轮真实案例：4 个问题，其中一个没问号、一个是祈使句
    real = ("要再单独存一条教训？说声「沉淀一下」。"
            "所以你说这个做不了的事儿，还需要我做什么吗？"
            "已经默认打开 ADHD 模式做好了吗？怎么没回答我呢？"
            "你回答问题的时候总是漏问题，看看怎么改掉。")
    qs = extract_questions(real)
    want(len(qs) >= 4, "真实案例：抽到 ≥4 个问题（实抽 %d）" % len(qs))
    want(any("ADHD" in q for q, _ in qs), "抽到「ADHD 模式做好了吗」")
    want(any("看看怎么改掉" in q for q, _ in qs), "抽到无问号的祈使请求「看看怎么改掉」")

    # 2) 无问号但有疑问词——最容易漏的一类
    want(len(extract_questions("我想知道这个能不能默认打开。另外那个文件放哪里比较好。")) >= 1,
         "无问号的疑问词句被抽到")

    # 3) 纯陈述/应答 → 不抽（否则每轮都刷屏，提醒会失信）
    want(extract_questions("好的。") == [], "应答语「好的」不算问题")
    want(extract_questions("我昨天把报告发出去了，附件是终稿。") == [],
         "纯陈述句不算问题")
    want(render(extract_questions("这样行吗？"), set()) == "",
         "只有 1 个问题 → 静默（低于阈值）")

    # 3b) URL 回归（真实误报，见 _URL 处注释）：链接里的 `?` 不是问号。
    #     这条是拿 6 个真实会话当基线才照出来的，自造夹具里没有 URL。
    url_msg = ("请你根据这个文件夹链接 https://drive.google.com/open?"
               "id=1uQBDPpfMdgCs2tj9cTbh8tJDWdhC9a7V&usp=drive_fs 做一版研究设计的PPT，"
               "包括研究背景和入组流程图。")
    url_qs = extract_questions(url_msg)
    want(len(url_qs) <= 1,
         "URL 里的 ? 不被当问号（实抽 %d，修复前是 4+）" % len(url_qs))
    want(all("usp=drive_fs" not in q for q, _ in url_qs),
         "查询串碎片没被当成独立问题")
    # 别修过头：URL 后面真的跟着问题，仍要抽到。
    want(len(extract_questions(
        "文件在 https://drive.google.com/open?id=abc&usp=drive_fs。"
        "这个能不能直接用？另外帮我查一下版本号。")) >= 2,
        "别修过头：URL 之后的真问题照抽")

    # 4) 重复提问要标 🔁
    out = render([("已经默认打开 ADHD 模式做好了吗？", True), ("还需要我做什么吗？", True)],
                 {_fingerprint("已经默认打开 ADHD 模式做好了吗？")})
    want("🔁" in out, "之前问过的问题标 🔁")
    want("优先答这条" in out, "🔁 出现时给出优先级说明")

    # 5) transcript 级：tool_result 与 system-reminder 不得被当成用户提问
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for rec in [
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": "怎么办？能不能重试？"}]}},
            {"type": "user", "message": {"role": "user", "content":
                "<system-reminder>要不要跑闸门？是不是该检查？</system-reminder>好的"}},
            {"type": "user", "message": {"role": "user",
                "content": "这个能默认打开吗？另外帮我查一下版本号。"}},
        ]:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    qs2, earlier2 = collect(path)
    want(len(qs2) == 2, "只抽最后一条真实用户消息的 2 个问题（实抽 %d）" % len(qs2))
    want(all("重试" not in q for q, _ in qs2), "tool_result 里的问句没被误抽")
    want(all("闸门" not in q for q, _ in qs2), "system-reminder 里的问句没被误抽")
    os.unlink(path)

    # 5b) Skill / slash-command 注入的正文**不得**被当成用户提问。
    #     2026-08-08 实测：调一次 lesson-capture，其 SKILL.md 的散文被抽成 19 个
    #     「问题」报出来，把真正漏答的淹掉。注入行以 role=user 落进 transcript，
    #     肉眼与真实提问无异，只有 isMeta / sourceToolUseID 能区分（判据取自实物：
    #     本会话 transcript 里注入行两者皆有、5 条真实用户消息两者皆无，零重叠）。
    #     **注入行必须排在最后**——collect() 只对 users[-1] 抽问题，注入行放在
    #     真实消息之前的话，排除逻辑失效也测不出来。初版就是这么写的：把判据改成
    #     `if False:` 仍 20/20 全绿，纯假绿。这是「变异测试要验测试本身跑没跑到
    #     被测代码」在同一天内的第二次实例（第一次见 session-change-digest 的 ④c），
    #     也正是这条教训刚被沉淀进 lessons-index 的原因。
    fd3, path3 = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd3, "w", encoding="utf-8") as fh:
        for rec in [
            {"type": "user", "message": {"role": "user",
                "content": "这个能默认打开吗？另外帮我查一下版本号。"}},
            {"type": "user", "sourceToolUseID": "toolu_y",
             "message": {"role": "user", "content": "要不要开闸门？要不要建 skill？"}},
            {"type": "user", "isMeta": True, "sourceToolUseID": "toolu_x",
             "message": {"role": "user", "content": [{"type": "text", "text":
                 "场景 = 什么时候会踩？教训 = 该怎么做？这样落可以吗？"}]}},
        ]:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    qs3, _e3 = collect(path3)
    want(len(qs3) == 2, "skill 注入被排除后，只抽真实用户消息的 2 个问题（实抽 %d）" % len(qs3))
    want(all("场景" not in q and "教训" not in q for q, _ in qs3),
         "isMeta 注入（skill 正文）里的问句没被误抽")
    want(all("闸门" not in q and "skill" not in q for q, _ in qs3),
         "带 sourceToolUseID 的注入里的问句没被误抽")
    os.unlink(path3)

    # 6) 坏输入：文件不存在 / 空文件 → 静默且不抛异常
    want(collect("/nonexistent/xx.jsonl") == ([], set()), "坏输入：文件不存在 → 静默")
    fd2, empty = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd2)
    want(collect(empty) == ([], set()), "坏输入：空 transcript → 静默")
    os.unlink(empty)

    for status, desc in checks:
        print("  %s: %s" % (status, desc))
    print("\n%d/%d 通过" % (len(checks) - bad, len(checks)))
    if bad == 0:
        print("\n--- 渲染样例 ---\n" + render(qs, set()))
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
