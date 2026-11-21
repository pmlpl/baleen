#!/usr/bin/env python
"""baleen / eval · 全库笔记质检扫描(AI 全量筛 → 主人只看 flag)。

背景:whisper 错听字(Claude→Cloud)organizer 跟着错,"读起来通顺"没有信号,导入时
三道 AI 筛都拦不住;错字进嵌入向量伤检索;废帧有漏网实锤(台账模式⑥)。

三阶段(对应 ADR 里的成本闸:启发式零成本先跑,LLM 每篇 ≤1 次调用):
    uv run python ../eval/scripts/quality_scan.py --stats   # 只打印分层统计(免费)
    uv run python ../eval/scripts/quality_scan.py --scan    # 启发式初筛(免费)→ runs/quality_scan_<date>.csv
    uv run python ../eval/scripts/quality_scan.py --llm     # LLM 终审(花 token;--resume 可续跑)
    uv run python ../eval/scripts/quality_scan.py --report  # 汇总分诊报告 runs/quality_scan_<date>.md(免费)

分层判据(只读库,不改任何数据):
    video = sources.parse_meta 带 video_url/video_mode(视频导入)
    qa    = sources.filename 形如 YYYY-MM-DD-*.md(问答导出走 notes_out 落盘入库)
    doc   = 其余(文档上传)

三个维度:
    typo   术语错字:外文 token 编辑距离近似匹配(启发式)+ LLM 终审(含中文错字)
    frame  废帧混入:图片 alt 特征词(互动引导/搜索框/黑屏转场)+ alt 过短 + LLM/识图终审
    style  口语残留:逐字稿特征词/超长段落/重复段落(organizer 没重写干净)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]  # eval/
BACKEND_DIR = EVAL_DIR.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

RUNS_DIR = EVAL_DIR / "runs"
OWNER_ID = 1
CONTENT_BUDGET = 12000  # LLM 终审喂给单篇的正文预算(头尾各半)
MAX_TOKENS = 4000
RANDOM_SEED = 42
SAMPLE_N = 5  # 主人抽查包篇数

# ---------------------------------------------------------------- 领域词典
# AI/技术领域专有名词(启发式的种子;语料高频词会自动并入白名单,词典只补低频正确词)
DOMAIN_DICT = {
    # 模型/公司
    "claude", "chatgpt", "anthropic", "openai", "gemini", "llama", "qwen", "glm",
    "deepseek", "kimi", "mistral", "grok", "ollama", "huggingface", "midjourney",
    "sora", "perplexity", "cursor", "copilot", "codex", "doubao", "ernie",
    # 架构/概念
    "transformer", "bert", "attention", "embedding", "embeddings", "token",
    "tokenizer", "prompt", "rag", "lora", "rlhf", "sft", "moe", "cot", "agent",
    "langchain", "llamaindex", "vllm", "instruct", "finetune", "fine-tune",
    "checkpoint", "softmax", "encoder", "decoder", "gan", "diffusion",
    # 后端/存储
    "hadoop", "spark", "flink", "kafka", "rocketmq", "rabbitmq", "elasticsearch",
    "kubernetes", "docker", "nginx", "redis", "mysql", "mongodb", "postgres",
    "postgresql", "pgvector", "neo4j", "sqlite", "oracle", "clickhouse",
    "ingress", "gateway", "asyncpg", "fastapi", "arq", "sqlalchemy", "alembic",
    "higress", "opencv", "vuex", "gitlab",
    "bigram", "trigram", "unigram", "ngram",
    # 前端
    "vue", "react", "typescript", "javascript", "vite", "webpack", "tailwind",
    "pinia", "router", "axios", "fetch", "tiptap", "prosemirror", "katex",
    "mermaid", "virtualdom", "webpack",
    # 协议/格式
    "http", "https", "graphql", "grpc", "websocket", "jwt", "oauth",
    "cors", "tcp", "dns", "cdn", "ssh", "yaml", "json", "xml", "csv", "utf",
    "ascii", "base64", "markdown", "html", "css",
    # 视频链路
    "whisper", "ffmpeg", "ytdlp", "bilibili", "douyin", "sessdata",
    "playwright", "chromium", "linux", "bash", "powershell", "windows", "macos",
    # 语言/工具
    "python", "java", "rust", "numpy", "pandas", "sklearn", "pytorch",
    "tensorflow", "cuda", "onnx", "git", "github", "dockerhub", "conda", "pip",
    "uvicorn", "dockerfile", "jenkins", "sonarqube",
}

SUFFIXES = ("s", "es", "ed", "ing", "'s")  # 词形还原:去掉后命中白名单就放过

# 常用英文单词二级白名单:技术文档高频普通词,与领域专名近邻纯属巧合。
# 豁免条件:该 token 全局出现 ≥2 次(稳定形态),或最近邻不在领域词典(巧合对);
# 真错字(Claude→Cloud)通常只出现 1-2 次且邻居是领域专名,仍会被报。
COMMON_WORDS = {
    "worker", "mode", "reset", "rest", "data", "date", "feature", "fixture",
    "decide", "figure", "lock", "mock", "connect", "context", "long", "server",
    "window", "delete", "code", "core", "style", "framework", "pattern",
    "module", "param", "default", "render", "request", "response", "target",
    "value", "process", "stream", "buffer", "config", "option", "service",
    "manager", "handler", "adapter", "wrapper", "template", "layout",
    "component", "state", "store", "event", "signal", "filter", "query",
    "sort", "merge", "split", "parse", "cache", "sync", "async", "limit",
    "offset", "timeout", "retry", "batch", "schema", "index", "table",
    "column", "record", "field", "entry", "item", "node", "edge", "root",
    "leaf", "path", "link", "port", "host", "client", "proxy", "router",
    "switch", "bridge", "rule", "policy", "script", "engine", "driver",
    "plugin", "extension", "runtime", "library", "package", "version",
    "release", "build", "deploy", "launch", "install", "setup", "init",
    "start", "stop", "test", "debug", "log", "trace", "monitor", "alert",
    "report", "summary", "detail", "group", "list", "map", "array", "queue",
    "stack", "heap", "tree", "graph", "matrix", "vector", "tensor", "scalar",
    "note", "notes", "chat", "session", "user", "role", "auth", "login",
    "file", "image", "video", "audio", "media", "text", "title", "content",
    "block", "page", "screen", "point", "area", "color", "font", "width",
    "height", "border", "margin", "padding", "flex", "grid", "box", "card",
    "modal", "button", "input", "label", "form", "submit", "upload",
    "download", "export", "import", "preview", "view", "nav", "menu", "tab",
    "badge", "chip", "chat", "word", "words", "text", "hash", "frac", "delta",
    "server", "host", "proxy",
}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+_./'-]{2,}")

# ---------------------------------------------------------------- 废帧特征
BAD_FRAME_PATTERNS: list[tuple[str, str]] = [
    (r"关注|点赞|订阅|一键三连|三连|弹幕|评论区|留言|粉丝群|公众号|加微信|进群|主页|频道|账号"
     r"|互动引导|互动页|下期|预告", "互动引导"),
    (r"搜索框|搜索栏|输入框|地址栏|浏览器界面|播放器|进度条|弹窗|广告", "界面元素"),
    (r"黑屏|纯色|转场|片头|片尾|logo|水印|台标|logo墙", "黑屏/转场/水印"),
    (r"表情包|表情|梗图|搞笑|滑稽", "表情包"),
]

MIN_ALT_LEN = 4  # alt 短于此值视为「无信息描述」

# ---------------------------------------------------------------- 口语残留特征
COLLOQUIAL_RE = re.compile(
    r"大家好|今天我们|这期视频|本期视频|这一期|咱们|对吧|是不是|说白了|你知道吗"
    r"|呃,|呃、|嗯,|嗯、|哈,|啊,|一样的|然后呢|接下来我们|来我们"
)
COLLOQUIAL_MIN_HITS = 3   # 单段命中特征词数 ≥ 此值 → 残留嫌疑
LONG_PARA_CHARS = 600     # 单段超长(organizer 没切分)
DUP_MIN_CHARS = 30        # 重复段落检测的最短段长


def levenshtein(a: str, b: str, cap: int = 3) -> int:
    """编辑距离,超过 cap 提前短路返回 cap+1。"""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(v)
            best = min(best, v)
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------- doc 遍历


def walk_doc(doc: dict) -> dict:
    """遍历 TipTap doc,收集:(heading 路径, 段文本) 序列 + 图片 attrs + 代码块文本。

    - 顶层块为粒度;paragraph/heading 内 text 节点直接拼接
    - codeBlock 与带 code mark 的 inline 文本归入 code_texts(不进错字扫描)
    - 列表/引用/表格内的文本归入所在层级的段文本
    """
    paras: list[dict] = []   # {"path": str, "text": str}
    images: list[dict] = []  # {"alt","timestamp","path"}
    code_texts: list[str] = []
    current_path: list[str] = []  # 当前 heading 路径(heading 块更新,兄弟块共用)

    def collect_text(node: dict) -> str:
        """块内文本。inline code 保留原文(错字扫描容忍它;剥掉会让 LLM 看到
        「空格占位」误判成笔记缺陷——2026-11-21 实证);嵌套 codeBlock 由外层处理。"""
        t = node.get("type")
        if t == "text":
            return node.get("text") or ""
        if t == "image":
            return ""
        parts = []
        for child in node.get("content") or []:
            if child.get("type") == "codeBlock":
                continue  # 代码块单独收集,不混进段落文本
            parts.append(collect_text(child))
        return "".join(parts)

    def flatten_table(node: dict) -> str:
        """表格 → 标准 markdown 表(表头行+分隔行)——直接拼文本会把整张表
        压成流水串,LLM 会误判「表格拍平」(doc 里表是完好的);
        自造「表头:」前缀也会被 LLM 当成笔记残留(2026-11-21 两案实证)。"""
        rows: list[list[str]] = []
        for tr in node.get("content") or []:
            if tr.get("type") != "tableRow":
                continue
            cells = [collect_text(cell).strip() for cell in tr.get("content") or []]
            if any(cells):
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        md = "| " + " | ".join(rows[0]) + " |\n"
        md += "|" + "---|" * width + "\n"
        md += "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
        return md

    def flatten_block(node: dict) -> str:
        parts = []
        for child in node.get("content") or []:
            t = child.get("type")
            if t == "text":
                parts.append(child.get("text") or "")
            elif t == "codeBlock":
                parts.append(flatten_block(child))
            elif t == "image":
                pass
            else:
                parts.append(flatten_block(child))
        return "".join(parts)

    def loc() -> str:
        return " > ".join(current_path[-2:]) or "(文首)"

    def walk(node: dict) -> None:
        nonlocal current_path
        t = node.get("type")
        if t == "heading":
            text = collect_text(node)
            if text:
                current_path = current_path + [text]
            return
        if t == "image":
            attrs = node.get("attrs", {})
            images.append({
                "alt": attrs.get("alt") or "",
                "timestamp": attrs.get("timestamp"),
                "media": attrs.get("media"),
                "path": loc(),
            })
            return
        if t == "codeBlock":
            code_texts.append(flatten_block(node))
            return
        if t == "table":
            text = flatten_table(node)
            if text.strip():
                paras.append({"path": loc(), "text": text, "table": True})
            return
        text = collect_text(node)
        if text.strip():
            paras.append({"path": loc(), "text": text})
            return
        for child in node.get("content") or []:
            walk(child)

    for block in doc.get("content") or []:
        walk(block)
    return {"paras": paras, "images": images, "code": code_texts}


# ---------------------------------------------------------------- 取数


async def fetch_notes() -> list[dict]:
    """active 笔记全量 + 分层 + doc 解析。只读。"""
    import asyncpg

    from app.core.config import get_settings

    s = get_settings()
    conn = await asyncpg.connect(s.database_url.replace("+asyncpg", ""))
    try:
        rows = await conn.fetch(
            "SELECT n.id, n.title, n.quality, s.filename AS src_file,"
            " s.parse_meta::text AS meta, n.doc::text AS doc_text"
            " FROM notes n LEFT JOIN sources s ON s.id = n.source_id"
            " WHERE n.status='active' ORDER BY n.id"
        )
    finally:
        await conn.close()

    notes: list[dict] = []
    for r in rows:
        meta = r["meta"] or ""
        if '"video_url"' in meta or '"video_mode"' in meta:
            layer = "video"
        elif r["src_file"] and re.match(r"^\d{4}-\d{2}-\d{2}-", r["src_file"] or ""):
            layer = "qa"
        else:
            layer = "doc"
        parsed = walk_doc(json.loads(r["doc_text"] or "{}"))
        notes.append({
            "id": r["id"],
            "title": r["title"] or "",
            "quality": r["quality"] or "-",
            "layer": layer,
            **parsed,
        })
    return notes


# ---------------------------------------------------------------- 维度1:术语错字启发式


def build_whitelist(all_notes: list[dict]) -> tuple[set[str], dict[str, int]]:
    """词典 + 语料高频词(全局 ≥4 次、≥4 字符)。返回 (白名单, 全局频率表)。"""
    freq: dict[str, int] = {}
    for n in all_notes:
        for p in n["paras"]:
            for tok in TOKEN_RE.findall(p["text"]):
                t = tok.strip("._'-").lower()
                if len(t) >= 4:
                    freq[t] = freq.get(t, 0) + 1
    wl = {w.lower() for w in DOMAIN_DICT}
    wl |= {t for t, c in freq.items() if c >= 4}
    return wl, freq


def strip_suffix(token: str) -> str:
    for suf in SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 4:
            return token[: -len(suf)]
    return token


def scan_typos(note: dict, whitelist: set[str], freq: dict[str, int]) -> list[dict]:
    """外文 token 近似匹配 → 错字嫌疑。

    豁免链(层层过滤,宁缺毋滥):
    1. 含数字/内部标点(Vue3/exp-03/notes/161)——版本号/路径不是错字
    2. 全大写缩写(≤6 字符:RAG/CVSS/FLAC)——形态稳定,错写风险低
    3. 在白名单(领域词典+高频词)或词形还原后命中
    4. 与最近邻**形态不一致**(Worker(Title)→docker(lower) 纯巧合)
    5. token 在常用词表且(全局 ≥2 次 或 邻居不在领域词典)——稳定形态放行
    真错字画像:低频形态 + 领域专名近邻 + 同形态(Claude→Cloud)。
    """
    seen_tokens: dict[str, list[dict]] = {}  # token → 出现位置
    for p in note["paras"]:
        for m in TOKEN_RE.finditer(p["text"]):
            tok_raw = m.group(0)
            tok = tok_raw.strip("._'-")
            if len(tok) < 4 or tok.isdigit():
                continue
            seen_tokens.setdefault(tok, []).append({"path": p["path"], "ctx": p["text"]})

    flags: dict[str, dict] = {}
    cache: dict[str, tuple[str, int] | None] = {}
    max_dist = lambda t: 1 if len(t) <= 5 else 2  # noqa: E731
    dict_words = {x.lower() for x in DOMAIN_DICT}

    for tok, spots in seen_tokens.items():
        low = tok.lower()
        # 1. 含数字/内部标点:版本号、路径、标识符
        if any(ch.isdigit() for ch in tok) or re.search(r"[._/'-]", tok[1:-1]):
            continue
        # 2. 全大写缩写
        if tok.isupper() and len(tok) <= 6:
            continue
        # 3. 白名单/词形还原
        if low in whitelist or strip_suffix(low) in whitelist:
            continue
        # 最近邻
        if low in cache:
            near = cache[low]
        else:
            near = None
            dmax = max_dist(low)
            for w in whitelist:
                if abs(len(w) - len(low)) > dmax:
                    continue
                d = levenshtein(low, w, cap=dmax)
                if d <= dmax and (near is None or d < near[1]):
                    near = (w, d)
            cache[low] = near
        if near is None:
            continue
        w, d = near
        # 4. 邻居不在领域词典 → 豁免:近邻只是碰巧的常用词(hash/rest/data),非错字信号
        if w not in dict_words:
            continue
        # 5. 常用词稳定形态(全局 ≥2 次)放行
        if low in COMMON_WORDS and freq.get(low, 0) >= 2:
            continue
        spot = spots[0]
        key = low
        if key not in flags:
            flags[key] = {
                "dim": "typo",
                "signal": f"近似词(距离{d})",
                "evidence": tok,
                "location": spot["path"],
                "context": spot["ctx"][:80],
                "near": f"{w}({d})",
                "count": len(spots),
            }
    return list(flags.values())


# ---------------------------------------------------------------- 维度2:废帧启发式


def scan_frames(note: dict) -> list[dict]:
    flags: list[dict] = []
    for img in note["images"]:
        alt = (img.get("alt") or "").strip()
        for pat, label in BAD_FRAME_PATTERNS:
            if re.search(pat, alt, re.I):
                flags.append({
                    "dim": "frame",
                    "signal": label,
                    "evidence": alt or "(空)",
                    "location": f"ts={img['timestamp']:.0f}s {img['path']}" if img.get("timestamp") is not None else img["path"],
                    "near": "",
                    "count": 1,
                })
                break
        else:
            if alt and len(alt) < MIN_ALT_LEN:
                flags.append({
                    "dim": "frame",
                    "signal": "alt过短",
                    "evidence": alt,
                    "location": img["path"],
                    "near": "",
                    "count": 1,
                })
    return flags


# ---------------------------------------------------------------- 维度3:口语残留启发式


def scan_style(note: dict) -> list[dict]:
    flags: list[dict] = []
    prev_text = ""
    for p in note["paras"]:
        text = p["text"].strip()
        if not text:
            continue
        if p.get("table"):
            prev_text = text
            continue  # 表格再长也正常,不报超长段(6095 误报教训)
        hits = COLLOQUIAL_RE.findall(text)
        if len(hits) >= COLLOQUIAL_MIN_HITS:
            flags.append({
                "dim": "style",
                "signal": f"口语特征词×{len(hits)}",
                "evidence": text[:80],
                "location": p["path"],
                "near": ",".join(dict.fromkeys(hits))[:40],
                "count": 1,
            })
        if len(text) > LONG_PARA_CHARS:
            flags.append({
                "dim": "style",
                "signal": "超长段落",
                "evidence": f"{len(text)}字:" + text[:60],
                "location": p["path"],
                "near": "",
                "count": 1,
            })
        norm = re.sub(r"\s+", "", text)
        if len(norm) >= DUP_MIN_CHARS and norm == re.sub(r"\s+", "", prev_text):
            flags.append({
                "dim": "style",
                "signal": "相邻段落重复",
                "evidence": text[:80],
                "location": p["path"],
                "near": "",
                "count": 1,
            })
        prev_text = text
    return flags


# ---------------------------------------------------------------- LLM 终审


def llm_system_prompt() -> str:
    return (
        "你是「智能笔记库」的质检终审员。输入一篇笔记(标题/来源/正文/图片描述清单/启发式嫌疑)。"
        "按三个维度找问题,宁缺毋滥——只报「确定或高度可疑」的,别把正常内容报成问题:\n"
        "1. typos 术语错字:专有名词错听/错写(whisper 错听类如 Claude 被写成 Cloud、"
        "Llama 被写成别的;中文成语/名词错字如「大名鼎鼎」被写成「大明零零零」)。"
        "判据:结合上下文,这个词在这个语境里几乎不可能是对的。代码块/命令/变量名不算错字。\n"
        "2. bad_frames 废帧:图片描述(alt)显示该图是互动引导(求关注/点赞/订阅/评论)、"
        "搜索框/浏览器界面、片头片尾/黑屏/水印、口播人像特写、表情包,或与所在位置的内容明显无关。\n"
        "3. colloquial 口语残留:organizer 没重写干净的逐字稿段落(大家好/今天咱们/对吧/呃嗯/"
        "整段口语流水账),或明显的结构问题(整段重复、逻辑断裂)。只报大问题,不吹毛求疵。\n\n"
        "只输出一个 JSON 对象(不要多余文字、不要 markdown 围栏):\n"
        '{"typos":[{"evidence":"原文片段","suggest":"建议修正","reason":"一句话"}],'
        '"bad_frames":[{"alt":"图片描述","reason":"一句话"}],'
        '"colloquial":[{"evidence":"原文片段","reason":"一句话"}]}'
        "\n没有问题的维度给空数组。"
    )


def llm_judge_note(client, note: dict, heur_flags: list[dict]) -> tuple[dict | None, str]:
    """单篇终审,≤1 次调用。返回 (json 结果|None, 原始输出摘要)。"""
    texts = [p["text"] for p in note["paras"]]
    body = "\n".join(texts)
    code_section = ""
    code_joined = "\n".join(c for c in note["code"] if c.strip())
    if code_joined:
        # 预算要给足:截短会让 LLM 把「工具输入的截断」误判成「笔记代码截断」
        # (156/157 实证:两篇代码 10-16k 字符,4000 预算下 LLM 报「结尾硬截断」假 flag)
        code_section = f"\n代码块内容(上下文参考;代码/命令/变量名不算错字,清单可能截断不算笔记缺陷):\n{code_joined[:20000]}\n"
    if len(body) > CONTENT_BUDGET:
        half = CONTENT_BUDGET // 2
        body = body[:half] + "\n……(中段截断)……\n" + body[-half:]
    img_lines = []
    for img in note["images"][:60]:
        ts = f"ts={img['timestamp']:.0f}s" if img.get("timestamp") is not None else ""
        img_lines.append(f"- [{ts} {img['path']}] {img.get('alt') or '(无描述)'}")
    heur_lines = [
        f"- [{f['dim']}/{f['signal']}] {f['evidence'][:60]}"
        + (f" → 疑似 {f['near']}" if f.get("near") else "")
        for f in heur_flags[:30]
    ]
    user = (
        f"标题:{note['title']}\n"
        f"来源:{note['layer']}  quality:{note['quality']}\n\n"
        + (f"图片描述清单({len(note['images'])}张):\n" + "\n".join(img_lines) + "\n\n" if img_lines else "")
        + (f"启发式嫌疑(仅供参考,需你独立判断):\n" + "\n".join(heur_lines) + "\n\n" if heur_lines else "")
        + code_section
        + f"正文:\n{body}"
    )
    messages = [
        {"role": "system", "content": llm_system_prompt()},
        {"role": "user", "content": user},
    ]
    try:
        result = client.chat(messages, temperature=0.0, max_tokens=MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001
        return None, f"LLM 调用失败:{exc}"
    content = result.content or ""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None, content[:150].replace("\n", " ")
    try:
        obj = json.loads(m.group(0))
        return obj, ""
    except json.JSONDecodeError:
        return None, content[:150].replace("\n", " ")


async def build_client():
    """owner 的 DB 配置优先,回退 .env/默认,环境变量最后覆盖(与 review_agent 同语义)。"""
    import asyncpg

    from app.core.config import get_settings
    from app.service.llm import LLMClient

    s = get_settings()
    base_url, api_key, model = s.llm_base_url, s.llm_api_key, s.llm_model
    conn = await asyncpg.connect(s.database_url.replace("+asyncpg", ""))
    try:
        row = await conn.fetchrow(
            "SELECT llm_base_url, llm_api_key, llm_model FROM app_settings WHERE owner_id=$1",
            OWNER_ID,
        )
        if row:
            base_url = row["llm_base_url"] or base_url
            api_key = row["llm_api_key"] or api_key
            model = row["llm_model"] or model
    finally:
        await conn.close()
    base_url = os.environ.get("LLM_BASE_URL", base_url)
    api_key = os.environ.get("LLM_API_KEY", api_key)
    model = os.environ.get("LLM_MODEL", model)
    if not api_key:
        raise SystemExit("[error] 没配 LLM key(设置页配,或环境变量 LLM_API_KEY)")
    return LLMClient(base_url=base_url, api_key=api_key, model=model)


# ---------------------------------------------------------------- CSV


CSV_COLUMNS = ["note_id", "layer", "title", "dim", "signal", "evidence",
               "location", "near", "count", "source", "verdict", "reason"]


def csv_path() -> Path:
    return RUNS_DIR / f"quality_scan_{date.today():%Y-%m-%d}.csv"


def report_path() -> Path:
    return RUNS_DIR / f"quality_scan_{date.today():%Y-%m-%d}.md"


def load_rows() -> list[dict]:
    p = csv_path()
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def save_rows(rows: list[dict]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with csv_path().open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})


def row_key(r: dict) -> str:
    return f"{r['note_id']}|{r['dim']}|{r['signal']}|{r['evidence'][:50]}"


# ---------------------------------------------------------------- 阶段


def phase_stats(notes: list[dict]) -> None:
    by = {}
    for n in notes:
        by.setdefault(n["layer"], []).append(n)
    print(f"[stats] active notes = {len(notes)}")
    for layer in ("video", "qa", "doc"):
        recs = by.get(layer, [])
        imgs = sum(len(r["images"]) for r in recs)
        chars = sum(len(p["text"]) for r in recs for p in r["paras"])
        print(f"  {layer:<6} {len(recs):>3} 篇  嵌图 {imgs:>4} 张  正文 {chars:>7} 字符")
    no_img_video = [r["id"] for r in by.get("video", []) if not r["images"]]
    print(f"[stats] 零图视频笔记:{no_img_video}")


def phase_scan(notes: list[dict]) -> list[dict]:
    """启发式初筛(零 LLM):三维度。"""
    whitelist, freq = build_whitelist(notes)
    rows: list[dict] = []
    for n in notes:
        base = {"note_id": n["id"], "layer": n["layer"], "title": n["title"], "source": "heur"}
        for f in scan_typos(n, whitelist, freq):
            rows.append({**base, **f, "verdict": "", "reason": ""})
        for f in scan_frames(n):
            rows.append({**base, **f, "verdict": "", "reason": ""})
        for f in scan_style(n):
            rows.append({**base, **f, "verdict": "", "reason": ""})
    save_rows(rows)
    by_dim = {}
    for r in rows:
        by_dim[r["dim"]] = by_dim.get(r["dim"], 0) + 1
    print(f"[scan] {len(rows)} 条启发式 flag → {csv_path()}")
    for dim, c in sorted(by_dim.items()):
        notes_hit = len({r['note_id'] for r in rows if r['dim'] == dim})
        print(f"  {dim:<6} {c:>3} 条(涉 {notes_hit} 篇)")
    return rows


def _state_path() -> Path:
    return RUNS_DIR / "quality_scan_llm_state.json"


def _load_judged() -> set[int]:
    """已终审篇的状态文件——全 0 flag 的篇在 CSV 里没有 llm 行,
    只靠 CSV 判重会把它们重跑一遍(2026-11-21 实证),所以另存状态。"""
    if _state_path().exists():
        return {int(x) for x in json.loads(_state_path().read_text(encoding="utf-8"))["judged"]}
    return set()


def _save_judged(judged: set[int]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(
        json.dumps({"judged": sorted(judged)}, ensure_ascii=False), encoding="utf-8"
    )


def phase_llm(notes: list[dict], delay: float, limit: int | None, resume: bool) -> None:
    """LLM 终审:全库每篇 ≤1 次调用;启发式 flag 标 confirmed/dismissed,LLM 新发现 source=llm。"""
    from app.service.llm import LLMError

    rows = load_rows()
    try:
        client = asyncio.run(build_client())
        caps = client.probe()
        print(f"probe: model={caps.model} chat={caps.chat} {caps.notes}")
    except (SystemExit, LLMError) as exc:
        print(f"[error] LLM 不可用:{exc}", file=sys.stderr)
        return

    judged = _load_judged() if resume else set()
    targets = [n for n in notes if n["id"] not in judged]
    if limit:
        targets = targets[:limit]
    total = len(targets)
    est = sum(
        min(sum(len(p["text"]) for p in n["paras"]), CONTENT_BUDGET) + 400 * len(n["images"]) + 500
        for n in targets
    )
    print(f"[llm] 终审 {total} 篇(每篇 ≤1 调用),预估输入 ~{est // 1000}k tokens")

    t0 = time.perf_counter()
    for i, n in enumerate(targets, 1):
        heur = [r for r in rows if int(r["note_id"]) == n["id"] and r["source"] == "heur"
                and not r.get("verdict")]
        obj, raw = llm_judge_note(client, n, heur)
        if obj is None:
            print(f"  ({i}/{total}) note {n['id']} unparsed: {raw[:80]}")
            time.sleep(delay)
            continue
        new_rows: list[dict] = []
        # 启发式 flag 的确认/否决:LLM 判了 typo 维度 → 对齐 evidence
        llm_typo_evid = {str(t.get("evidence", "")).strip() for t in obj.get("typos", [])}
        for r in rows:
            if int(r["note_id"]) == n["id"] and r["source"] == "heur" and not r.get("verdict"):
                if r["dim"] == "typo":
                    ev = r["evidence"].strip()
                    hit = any(ev.lower() in e.lower() or e.lower() in ev.lower()
                              for e in llm_typo_evid if e)
                    r["verdict"] = "confirmed" if hit else "dismissed"
                    if hit:
                        match = next((e for e in llm_typo_evid
                                      if e and (ev.lower() in e.lower() or e.lower() in ev.lower())), "")
                        mrow = next((t for t in obj.get("typos", [])
                                     if str(t.get("evidence", "")).strip() == match), {})
                        r["reason"] = str(mrow.get("suggest", ""))[:80]
        for t in obj.get("typos", []):
            new_rows.append({
                "note_id": n["id"], "layer": n["layer"], "title": n["title"],
                "dim": "typo", "signal": "LLM终审",
                "evidence": str(t.get("evidence", ""))[:100],
                "location": "", "near": str(t.get("suggest", ""))[:60],
                "count": 1, "source": "llm", "verdict": "open",
                "reason": str(t.get("reason", ""))[:100],
            })
        for t in obj.get("bad_frames", []):
            alt = str(t.get("alt", ""))[:100]
            match = next((r for r in rows if int(r["note_id"]) == n["id"]
                          and r["dim"] == "frame" and alt and alt[:40] in r["evidence"]), None)
            if match:
                match["verdict"] = "confirmed"
                match["reason"] = str(t.get("reason", ""))[:100]
            else:
                new_rows.append({
                    "note_id": n["id"], "layer": n["layer"], "title": n["title"],
                    "dim": "frame", "signal": "LLM终审",
                    "evidence": alt, "location": "", "near": "", "count": 1,
                    "source": "llm", "verdict": "open",
                    "reason": str(t.get("reason", ""))[:100],
                })
        for t in obj.get("colloquial", []):
            new_rows.append({
                "note_id": n["id"], "layer": n["layer"], "title": n["title"],
                "dim": "style", "signal": "LLM终审",
                "evidence": str(t.get("evidence", ""))[:100],
                "location": "", "near": "", "count": 1, "source": "llm",
                "verdict": "open", "reason": str(t.get("reason", ""))[:100],
            })
        rows.extend(new_rows)
        n_typo = len(obj.get("typos", []))
        n_frame = len(obj.get("bad_frames", []))
        n_style = len(obj.get("colloquial", []))
        print(f"  ({i}/{total}) note {n['id']} {n['title'][:24]:<26} "
              f"typo={n_typo} frame={n_frame} style={n_style}")
        save_rows(rows)
        judged.add(n["id"])
        _save_judged(judged)
        time.sleep(delay)
    save_rows(rows)
    print(f"[llm] 完成 {total} 篇,{time.perf_counter() - t0:.0f}s → {csv_path()}")


# ---------------------------------------------------------------- 报告


DIM_LABEL = {"typo": "术语错字", "frame": "废帧混入", "style": "口语残留"}


def phase_report(notes: list[dict]) -> None:
    rows = load_rows()
    open_rows = [r for r in rows if r["verdict"] in ("open", "confirmed", "")]
    dismissed = [r for r in rows if r["verdict"] == "dismissed"]
    by = {layer: [n for n in notes if n["layer"] == layer] for layer in ("video", "qa", "doc")}

    lines = [
        f"# 全库笔记质检分诊报告({date.today():%Y-%m-%d})",
        "",
        f"> 生成:`eval/scripts/quality_scan.py --report` · 数据:{csv_path().name} · "
        f"只 flag 不修正,主人裁决后另开修正批(PUT 修改留痕)",
        "",
        "## 量化指标(#5/#6 首批测量)",
        "",
        "| 指标 | 值 |",
        "|---|---|",
    ]
    n_video = len(by["video"])
    typo_rows = [r for r in open_rows if r["dim"] == "typo"]
    typo_notes = {r["note_id"] for r in typo_rows}
    video_typo = [r for r in typo_rows if r["layer"] == "video"]
    video_typo_notes = {r["note_id"] for r in video_typo}
    frame_rows = [r for r in open_rows if r["dim"] == "frame"]
    style_rows = [r for r in open_rows if r["dim"] == "style"]
    total_imgs = sum(len(n["images"]) for n in notes)
    lines += [
        f"| 总笔记数 | {len(notes)}(video {len(by['video'])} / qa {len(by['qa'])} / doc {len(by['doc'])}) |",
        f"| 视频笔记嵌图总数 | {total_imgs} |",
        f"| 错字 flag | {len(typo_rows)} 条 / 涉 {len(typo_notes)} 篇 |",
        f"| 视频笔记错字率 | {len(video_typo_notes)}/{n_video} 篇 |",
        f"| 废帧 flag | {len(frame_rows)} 条(嵌图 {total_imgs} 张,混入率 {len(frame_rows) / total_imgs:.1%}) |",
        f"| 口语残留 flag | {len(style_rows)} 条 |",
        f"| 启发式 dismissed(LLM 否决) | {len(dismissed)} 条 |",
        "",
        "## flag 清单(按严重度:错字伤检索 > 废帧 > 口语残留)",
        "",
    ]

    def fmt_row(r: dict) -> str:
        loc = f" @ {r['location']}" if r["location"] else ""
        near = f" → 建议改 **{r['near']}**" if r["near"] else ""
        reason = f"({r['reason']})" if r["reason"] else ""
        return (f"- note {r['note_id']}「{r['title'][:20]}」[{DIM_LABEL.get(r['dim'], r['dim'])}"
                f"/{r['signal']}]「{r['evidence'][:60]}」{loc}{near}{reason}")

    if open_rows:
        order = {"typo": 0, "frame": 1, "style": 2}
        for r in sorted(open_rows, key=lambda r: (order.get(r["dim"], 9), int(r["note_id"]))):
            lines.append(fmt_row(r))
    else:
        lines.append("(无 flag)")

    # 主人抽查包:随机 5 篇(seed 固定),列该篇全部 flag——测筛选器自己的召回
    rng = random.Random(RANDOM_SEED)
    sample_ids = sorted(rng.sample([str(n["id"]) for n in notes], min(SAMPLE_N, len(notes))))
    lines += [
        "",
        "## 主人抽查包(测筛选器召回——抽查 2 篇看有没有**漏报**)",
        "",
        f"> seed={RANDOM_SEED} 随机抽 {len(sample_ids)} 篇:note {', '.join(sample_ids)}。",
        "该篇全部 flag 列在下面;flag 为空也要抽查(看 AI 是不是漏报了)。",
        "",
    ]
    for sid in sample_ids:
        n = next((x for x in notes if str(x["id"]) == sid), None)
        if n is None:
            continue
        mine = [r for r in rows if r["note_id"] == sid and r["verdict"] != "dismissed"]
        head = f"### note {sid}「{n['title'][:30]}」({n['layer']}/{n['quality']})——"
        head += f"{len(mine)} flag" if mine else "**0 flag(重点抽查:AI 说它干净)**"
        lines.append(head)
        for r in mine:
            lines.append("  " + fmt_row(r)[2:])
        lines.append("")

    report_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[report] → {report_path()}")


# ---------------------------------------------------------------- 入口


def main() -> int:
    ap = argparse.ArgumentParser(description="全库笔记质检扫描(AI 全量筛 → 主人只看 flag)")
    ap.add_argument("--stats", action="store_true", help="只打印分层统计(免费)")
    ap.add_argument("--scan", action="store_true", help="启发式初筛(免费)")
    ap.add_argument("--llm", action="store_true", help="LLM 终审(花 token;每篇 ≤1 调用)")
    ap.add_argument("--report", action="store_true", help="汇总分诊报告(免费)")
    ap.add_argument("--layer", choices=("video", "qa", "doc"), help="只扫某层")
    ap.add_argument("--limit", type=int, default=None, help="LLM 终审只跑前 N 篇")
    ap.add_argument("--resume", action="store_true", help="LLM 终审跳过已有结果的篇")
    ap.add_argument("--delay", type=float, default=0.5)
    args = ap.parse_args()
    if not any((args.stats, args.scan, args.llm, args.report)):
        ap.print_help()
        return 2

    notes = asyncio.run(fetch_notes())
    if args.layer:
        notes = [n for n in notes if n["layer"] == args.layer]
    if args.stats:
        phase_stats(notes)
    if args.scan:
        phase_scan(notes)
    if args.llm:
        phase_llm(notes, args.delay, args.limit, args.resume)
    if args.report:
        phase_report(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
