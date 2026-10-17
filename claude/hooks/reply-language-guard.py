#!/usr/bin/env python3
"""reply-language-guard —— 用户用中文问，我却用英文答，收尾时拦下来重说一遍。

## 为什么要有这个 hook

全局偏好第一条就是「用中文回复（除非我用英文提问或明确要求英文）」，output style
「说人话」也写着。**写了，但长会话后段管不住**：2026-10-17 一个跑了一整天的会话，
上下文被压缩过几次之后，好几轮回复整段变成英文（"Blocked as expected…"、
"The other helper is still running…"），用户只好专门发一句「你用中文再帮我回复一下」。

偏好写在提示词里属于散文级约束——越是长会话、越是被压缩过，越容易漂。
这一格有稳定可判的信号（回复正文里中文字符与拉丁字母的比例），所以做成闸门，
不再靠「记得」。

## 判据

1. 找到**最后一条真人消息**（复用 unanswered-question-guard 的判据：排除
   system-reminder、工具回执、技能正文注入、压缩摘要、后台任务通知）。
   它的正文以中文为主才往下判；用户自己写了「英文 / English / 英语」就整轮放过
   （要英文邮件、要翻译成英文这类是正当的英文产出）。
2. 取**这一轮我写给用户的正文**（最后一条真人消息之后的所有 assistant text），
   先剥掉代码块、行内代码、链接、路径——这些天然是英文，不算语言漂移。
3. 剩下的正文里拉丁字母 ≥ 120 个、且中文字符占比 < 25% → 判为「这一轮在用英文答」，
   `block`，让我当场用中文把结论重说一遍（不用重做工作）。

阈值来自真实会话：见 `--baseline <transcript>` 的输出。

## 边界

- `stop_hook_active` 为真（本 hook 已经拦过一次）时不再拦，防死循环。
- 拿不到 transcript、解析出错 → 放行（fail-open，判不出来不许拦）。
- 逃生阀：`REPLY_LANGUAGE_GUARD_SKIP=1`。
- 只看语言，不看内容对不对；不弹给用户（block 的理由只给 agent 看）。

自测：`python3 reply-language-guard.py --selftest`
基线：`python3 reply-language-guard.py --baseline <transcript.jsonl>`（逐轮打印判定）
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))

MIN_EN_PARA_LETTERS = 150  # 「整段英文」累计字母数达到这个才拦（≈两句完整英文；一句漏网的英文不拦）
EN_PARA_MIN_WORDS = 8      # 一段里至少这么多个英文单词才算「整段英文」
EN_PARA_MAX_CJK = 0.10     # 且这一段中文字符占比低于这个（中文句子里夹英文名词不算）
USER_CJK_RATIO = 0.30    # 用户消息里中文占比达到这个才算「用中文问」
WANTS_ENGLISH = re.compile(r"英文|英语|English|english|in English|ENGLISH")


def _load_sibling():
    """复用 unanswered-question-guard 里「哪条才是用户真说的话」的判据，不另写一份。"""
    path = os.path.join(HERE, "unanswered-question-guard.py")
    spec = importlib.util.spec_from_file_location("_uqg", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _strip_non_prose(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)        # 代码块
    text = re.sub(r"`[^`\n]*`", " ", text)                     # 行内代码
    text = re.sub(r"https?://\S+", " ", text)                  # 链接
    text = re.sub(r"(?<![\w])[\w.\-]*/[\w./\-]+", " ", text)   # 路径 a/b/c
    text = re.sub(r"#\d+", " ", text)                          # PR 编号
    # 引用的英文原文不算我在用英文说话：块引用行、引号里的整句（本仓常逐字引文献原句）
    text = re.sub(r"(?m)^\s*>.*$", " ", text)
    text = re.sub(r'"[^"\n]{12,}"|“[^”\n]{12,}”', " ", text)
    return text


def lang_stats(text: str) -> tuple[int, int, float]:
    t = _strip_non_prose(text)
    cjk = len(re.findall(r"[一-鿿]", t))
    latin = len(re.findall(r"[A-Za-z]", t))
    ratio = cjk / (cjk + latin) if (cjk + latin) else 1.0
    return cjk, latin, ratio


def _assistant_text(entry) -> str | None:
    if entry.get("type") != "assistant":
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text") or "" for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def _is_notification(text: str) -> bool:
    """后台任务完成通知：不是真人说话，但它会开启新的一轮回复。

    判据取自实物（2026-10-17 本会话 transcript）：通知以 `<task-notification>` 开头、
    不包在 system-reminder 里，`_msg_text` 会把它当成用户原话——初版因此把一轮
    「通知 → 我用英文回复」的参照语言误取成通知正文，是拿真实会话跑基线才照出来的。
    """
    head = text.lstrip()[:300]
    return head.startswith("<task-notification>") or "[SYSTEM NOTIFICATION" in head


def turns(transcript_path: str, uqg):
    """切成 [(参照用的真人原话, 这一轮我的正文)]。

    一轮 = 一次真人消息或一次后台通知之后、下一次之前我写的全部正文；
    参照语言永远取**最近一条真人消息**（通知不改变用户用什么语言跟我说话）。
    """
    out, ref_user, cur_reply, started = [], None, [], False
    with open(transcript_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = uqg._msg_text(e)
            if t is None:
                t = uqg._queued_text(e)
            if t is not None:
                notif = _is_notification(t)
                t = uqg._strip_noise(t).strip()
                if notif or (t and not t.startswith("/")):
                    if started and ref_user is not None:
                        out.append((ref_user, "\n".join(cur_reply)))
                    if not notif:
                        ref_user = t
                    cur_reply, started = [], True
                    continue
            a = _assistant_text(e)
            if a and started:
                cur_reply.append(a)
    if started and ref_user is not None:
        out.append((ref_user, "\n".join(cur_reply)))
    return out


def english_paragraph_letters(text: str) -> tuple[int, int]:
    """返回（整段英文的字母总数, 段数）。

    为什么按段、不按全文比例（2026-10-17 拿真实会话量出来的）：全文比例分不开两种轮次——
    一轮是「整篇中文、但夹了很多文件名库名」（中文占 30%，正当），另一轮是「中文开头、
    中间插了好几段整句英文」（中文占 32%，违规）。按段数整句英文，前者 0、后者 369 字母。
    """
    letters = paras = 0
    for ln in _strip_non_prose(text).split("\n"):
        # 表格行只要有一格是中文，就是「中文表格里引了英文原文」，不算我在用英文说话。
        # 真实误报（2026-10-17 跑 12 份真实会话基线时照出来的）：一行左格逐字引 FDA 说明书
        # 的英文适应症、右格中文批注「写明『包含』」，整行中文占比不到 10%，初版判成英文段落。
        if ln.lstrip().startswith("|") and re.search(r"[一-鿿]", ln):
            continue
        words = re.findall(r"\b[A-Za-z]{2,}\b", ln)
        if len(words) < EN_PARA_MIN_WORDS:
            continue
        cjk = len(re.findall(r"[一-鿿]", ln))
        lat = sum(len(w) for w in words)
        if cjk / (cjk + lat) < EN_PARA_MAX_CJK:
            letters += lat
            paras += 1
    return letters, paras


def judge(user_text: str, reply_text: str):
    """返回 None（放过）或 (整段英文字母数, 段数, 全文中文占比)。"""
    if WANTS_ENGLISH.search(user_text):
        return None
    _, _, u_ratio = lang_stats(user_text)
    if u_ratio < USER_CJK_RATIO:
        return None
    letters, paras = english_paragraph_letters(reply_text)
    if letters >= MIN_EN_PARA_LETTERS:
        return letters, paras, lang_stats(reply_text)[2]
    return None


def main() -> int:
    if os.environ.get("REPLY_LANGUAGE_GUARD_SKIP") == "1":
        return 0
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("stop_hook_active"):
        return 0
    tp = data.get("transcript_path") or ""
    if not tp or not os.path.isfile(tp):
        return 0
    try:
        uqg = _load_sibling()
        ts = turns(tp, uqg)
    except Exception:
        return 0
    if not ts:
        return 0
    user_text, reply_text = ts[-1]
    hit = judge(user_text, reply_text)
    if not hit:
        return 0
    letters, paras, ratio = hit
    reason = (
        f"🌐 reply-language-guard：用户用中文跟你说话，而你这一轮写给用户的正文里有 {paras} 段"
        f"整段英文（共 {letters} 个字母；已剔除代码、链接、路径、引文原句）。\n"
        "全局偏好第一条是「用中文回复（除非用户用英文提问或明确要求英文）」。\n"
        "现在用中文把这一轮要告诉用户的内容重说一遍——只换语言、不重做工作，"
        "不用解释为什么换语言，也不要先道歉铺垫。\n"
        "（若用户其实要的就是英文产出、本条误判：REPLY_LANGUAGE_GUARD_SKIP=1）"
    )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


def _baseline(path: str) -> int:
    uqg = _load_sibling()
    flagged = 0
    for i, (u, r) in enumerate(turns(path, uqg), 1):
        if not r.strip():
            continue
        letters, paras = english_paragraph_letters(r)
        hit = judge(u, r)
        flagged += bool(hit)
        mark = "🌐 BLOCK" if hit else "   ok   "
        print(f"{mark} 轮{i:>3}  英文整段字母 {letters:5}  段数 {paras:3}  "
              f"用户:{u[:24]!r}  回复:{r.strip()[:40]!r}")
    print(f"共 {flagged} 轮会被拦")
    return 0


def _selftest() -> int:
    import tempfile
    fails = []

    def want(cond, name):
        print(("  ✅ " if cond else "  ❌ ") + name)
        if not cond:
            fails.append(name)

    def tx(rows):
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return p

    def user(t):
        return {"type": "user", "message": {"role": "user", "content": t}}

    def asst(t):
        return {"type": "assistant", "message": {"role": "assistant",
                                                  "content": [{"type": "text", "text": t}]}}

    def notif(t):
        return {"type": "user", "message": {"role": "user", "content":
                f"<system-reminder>[SYSTEM NOTIFICATION]<task-notification>{t}</task-notification></system-reminder>"}}

    eng = ("Batch 5 is merged. The rebase produced six conflicts, all resolved by keeping both "
           "renames, and every check I reran myself passed. Next I will look at batch six, which "
           "the previous session interrupted before the helper could push its branch.")
    zh = ("第5批已合并。rebase 时有 6 个文件冲突，都按「两边改名都保留」解决了，"
          "我自己重跑的检查全部通过。下一步看第 6 批，PR 是 ppt-master 和 Citeline 相关。")
    code_heavy = "命令如下：\n```bash\n" + ("git log --oneline origin/main..HEAD\n" * 20) + "```\n跑完就好。"
    uqg = _load_sibling()

    def run(rows):
        p = tx(rows)
        try:
            ts = turns(p, uqg)
            return judge(*ts[-1]) if ts else None
        finally:
            os.unlink(p)

    want(run([user("这个任务做完了吗？"), asst(eng)]) is not None, "中文提问 + 英文回复 → 拦")
    want(run([user("这个任务做完了吗？"), asst(zh)]) is None, "中文提问 + 中文回复（夹英文术语）→ 放过")
    want(run([user("这个任务做完了吗？"), asst(code_heavy)]) is None, "回复主要是代码块 → 放过")
    want(run([user("帮我写一封英文邮件给 Anand"), asst(eng)]) is None, "用户要英文 → 放过")
    want(run([user("Is the task done?"), asst(eng)]) is None, "英文提问 + 英文回复 → 放过")
    want(run([user("这个任务做完了吗？"), asst(zh), notif("agent finished"), asst(eng)]) is not None,
         "后台任务通知之后的英文回复，仍按最后一条真人中文消息判 → 拦")
    want(run([user("这个任务做完了吗？"), asst("OK, done.")]) is None, "太短 → 放过")
    want(run([user("进度怎么样？"), asst(zh + "\n\n" + eng + "\n\n" + zh)]) is not None,
         "中文为主、中间插了整段英文 → 拦（全文比例看不出来的那种）")
    ident = ("闸门 8 现在认 `X.test.py`，改了 gmail_compose.py 就跑 gmail_compose.test.py；"
             "markdown_it、cairosvg、check_skills_sync、gen_capability_registry 四个都核过了，"
             "PR #1654 已合并，task-insight-analysis 与 task-compliance-management 也重新生成了。")
    want(run([user("进度怎么样？"), asst(ident * 4)]) is None, "中文句子里夹大量英文名词 → 放过")
    quoted = (zh + "\n> The primary endpoint was met, with an ORR of 83.7% and a CR rate of 77.9% in the full cohort.\n"
              "原文逐字是 \"In patients with mutated TP53, the median PFS was 14.7 months overall.\"" + zh)
    want(run([user("这句原文怎么说的？"), asst(quoted)]) is None, "块引用 / 引号里逐字引的英文原文 → 放过")
    table = (zh + "\n| 药物 | 说明书原文 | 判断 |\n|---|---|---|\n"
             "| Epkinly | DLBCL, not otherwise specified, including DLBCL arising from indolent lymphoma | 写明「包含」 |\n"
             "| Breyanzi | DLBCL not otherwise specified (including DLBCL arising from indolent lymphoma) | 写明「包含」 |\n")
    want(run([user("这几个药说明书怎么写的？"), asst(table)]) is None, "中文表格里有一格逐字引英文原文 → 放过（真实误报复刻）")
    en_table = (zh + "\n| step | status | note |\n|---|---|---|\n" +
                "| rebase onto main | done | six conflicts were resolved by keeping both renames on each line |\n" * 3)
    want(run([user("进度？"), asst(en_table)]) is not None, "整张英文表格 → 照拦")
    real_notif = {"type": "user", "message": {"role": "user", "content":
                  "<task-notification>\n<task-id>x</task-id>\n<result>第5批完成，PR 已开</result>\n</task-notification>"}}
    want(run([user("这个任务做完了吗？"), asst(zh), real_notif, asst(eng)]) is not None,
         "真实格式的通知（不包 system-reminder）之后英文回复 → 仍按真人中文判、拦")
    want(run([user("这个任务做完了吗？"), asst(eng * 3), real_notif, asst(zh)]) is None,
         "前一轮的英文不连累通知后这一轮的中文回复 → 放过")
    want(run([user("Is it done?"), asst(zh), real_notif, asst(eng)]) is None,
         "真人用英文问，通知后英文回复 → 放过（通知不改变参照语言）")

    # main() 层：stop_hook_active 不再拦（防死循环）；逃生阀生效
    import io
    import contextlib
    p = tx([user("这个任务做完了吗？"), asst(eng)])
    try:
        for payload, env, expect_block, name in [
            ({"transcript_path": p}, {}, True, "main：正常命中输出 decision=block"),
            ({"transcript_path": p, "stop_hook_active": True}, {}, False, "main：stop_hook_active 时不再拦"),
            ({"transcript_path": p}, {"REPLY_LANGUAGE_GUARD_SKIP": "1"}, False, "main：逃生阀生效"),
            ({"transcript_path": "/nonexistent.jsonl"}, {}, False, "main：拿不到 transcript 放行"),
        ]:
            old_in, buf = sys.stdin, io.StringIO()
            saved = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            sys.stdin = io.StringIO(json.dumps(payload))
            try:
                with contextlib.redirect_stdout(buf):
                    main()
            finally:
                sys.stdin = old_in
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            want(('"decision": "block"' in buf.getvalue()) == expect_block, name)
    finally:
        os.unlink(p)

    print(f"\n== selftest: {'PASS' if not fails else 'FAIL'}（{len(fails)} 条不过）")
    return 1 if fails else 0


if __name__ == "__main__":
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if "--baseline" in sys.argv:
        sys.exit(_baseline(sys.argv[sys.argv.index("--baseline") + 1]))
    sys.exit(main())
