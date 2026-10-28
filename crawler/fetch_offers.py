#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeToken Radar · 数据抓取脚本
--------------------------------
把「人工核验的基线数据(seed.json)」与「线上实时抓取的资讯」合并，
输出页面直接可用的 data/offers.json 与 data/offers.js。

用法：
    python crawler/fetch_offers.py                # 抓一次
    python crawler/fetch_offers.py --watch        # 每 10 分钟抓一次（实时更新）
    python crawler/fetch_offers.py --offline      # 不联网，仅用基线数据重建
    python crawler/fetch_offers.py --watch --interval 300

只依赖标准库，不需要 pip install。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html as html_mod
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEED = DATA / "seed.json"
OUT_JSON = DATA / "offers.json"
OUT_JS = DATA / "offers.js"
SOURCES = Path(__file__).resolve().parent / "sources.json"
CST = timezone(timedelta(hours=8))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 FreeTokenRadar/1.0"
)

DATE_RE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")
URL_RE = re.compile(r"https?://[^\s\"'<>）)，。、\]]+")
TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- #
# 网络
# --------------------------------------------------------------------------- #
def http_get(url: str, timeout: int = 12, retries: int = 2) -> str | None:
    """带 UA / gzip / 重试的轻量抓取，失败返回 None（不抛异常）。"""
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/json,text/plain,*/*",
                    "Accept-Encoding": "gzip, deflate",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                charset = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except Exception as exc:  # noqa: BLE001 - 抓取失败不应中断整体流程
            last_err = exc
            time.sleep(1.2 * (attempt + 1))
    print(f"  [warn] {url} -> {last_err}", file=sys.stderr)
    return None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
NOISE_PREFIX = (
    "官方网站", "收集", "目录", "作者", "微信", "来源", "出处", "产品：", "活动名称",
    "免费额度 / 权益", "参与方式", "截止时间", "提示", "途径链接", "怎么选", "数据解释",
    "本文", "注意", "一、", "二、", "三、", "四、", "·", ">", "|", "**",
)
LABEL_RE = re.compile(r"^(免费额度\s*/\s*权益|免费额度|活动名称|产品|参与方式|提示|截止时间|权益|途径链接)\s*[：:]\s*")
QUOTA_RE = re.compile(r"(\d[\d,.]*\s*(万|亿|千|百万|Token|tokens|次|元|\$|%|天|个月|年))")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def clean_text(raw: str) -> str:
    txt = html_mod.unescape(TAG_RE.sub(" ", raw))
    txt = re.sub(r"[*_`]{1,3}", "", txt)
    txt = re.sub(r"\[([^\]]{2,60})\]\([^)]*\)", r"\1", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def strip_label(text: str) -> str:
    """去掉「免费额度 / 权益 ：」这类字段前缀，只留正文。"""
    return LABEL_RE.sub("", text).strip()


def starts_noise(text: str) -> bool:
    """忽略空格差异，判断是否是「活动名称 ：xxx」这类字段行。"""
    return text.replace(" ", "").startswith(NOISE_PREFIX)


def is_noise(title: str) -> bool:
    if not title or len(title) < 6 or len(title) > 52:
        return True
    if starts_noise(title):
        return True
    if " ：" in title or " :" in title:  # 「标签 ：内容」型字段行
        return True
    if not CJK_RE.search(title) and len(title.split()) < 4:
        return True
    if title.count("：") + title.count(":") > 1:
        return True
    return False


def extract_markdown(payload: str, source: dict, cfg: dict) -> list[dict]:
    """解析 README 里的免费额度表格：| 平台 | 类型 | 政策 | 来源 | 审核 |"""
    items: list[dict] = []
    keywords = cfg.get("keywords", [])
    for row in payload.splitlines():
        if row.count("|") < 4 or set(row.replace("|", "").strip()) <= set("-: "):
            continue
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        name_cell, type_cell, policy_cell = cells[0], cells[1], cells[2]
        name = clean_text(name_cell)
        policy = clean_text(policy_cell)
        if not name or len(name) > 40 or "平台" in name:
            continue
        if not any(k in policy for k in keywords):
            continue
        link_m = URL_RE.search(name_cell) or URL_RE.search(cells[3])
        audit = cells[4] if len(cells) > 4 else ""
        dm = DATE_RE.search(audit)
        date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}" if dm else None
        items.append(
            {
                "date": date,
                "title": f"{name} · {clean_text(type_cell)}",
                "summary": policy[:150],
                "source": source["name"],
                "url": link_m.group(0) if link_m else source["url"],
                "tag": "政策",
                "live": True,
            }
        )
        if len(items) >= int(source.get("limit", 4)):
            break
    return items


def extract_html(payload: str, source: dict, cfg: dict) -> list[dict]:
    """优先取 h2/h3/h4 标题，其次取带额度信号的正文行。"""
    keywords = cfg.get("keywords", [])
    excludes = cfg.get("exclude", [])
    body = re.sub(r"(?is)<(script|style|nav|footer)[^>]*>.*?</\1>", " ", payload)
    items: list[dict] = []
    seen: set[str] = set()

    # 1) 标题
    for m in re.finditer(r"(?is)<h([2-4])[^>]*>(.*?)</h\1>", body):
        title = clean_text(m.group(2))
        title = re.sub(r"^[\d.\-、\s]+", "", title)
        if is_noise(title) or not any(k in title for k in keywords):
            continue
        if any(x in title for x in excludes):
            continue
        # 标题后面最近的段落做摘要（优先带额度信号的段落）
        tail = body[m.end(): m.end() + 2200]
        paras = [clean_text(x) for x in re.findall(r"(?is)<p[^>]*>(.*?)</p>", tail)[:5]]
        paras = [
            x for x in paras
            if 12 < len(x) < 240 and not starts_noise(x)
        ]
        best = next((x for x in paras if any(k in x for k in keywords) and len(x) > 18), None)
        if not best and paras:
            best = max(paras[:2], key=len)
        summary = strip_label(best or "")[:150]
        if summary.strip() == title.strip():
            summary = ""
        link = re.search(r'href="(https?://[^"]+)"', tail[:800])
        fp = re.sub(r"\W", "", title)[:12]
        if fp in seen:
            continue
        seen.add(fp)
        items.append(
            {
                "date": None,
                "title": title[:80],
                "summary": summary,
                "source": source["name"],
                "url": link.group(1) if link else source["url"],
                "tag": "实时",
                "live": True,
            }
        )
        if len(items) >= int(source.get("limit", 4)):
            return items

    # 2) 正文兜底
    text = clean_text(body)
    for line in re.split(r"(?<=[。！？])\s|\n", text):
        line = line.strip()
        if is_noise(line) or len(line) > 120:
            continue
        if not any(k in line for k in keywords):
            continue
        if not QUOTA_RE.search(line):
            continue
        title = re.split(r"[。；;]", line)[0][:80]
        if is_noise(title):
            continue
        fp = re.sub(r"\W", "", title)[:12]
        if fp in seen:
            continue
        seen.add(fp)
        items.append(
            {
                "date": None,
                "title": title,
                "summary": line[:150],
                "source": source["name"],
                "url": source["url"],
                "tag": "实时",
                "live": True,
            }
        )
        if len(items) >= int(source.get("limit", 4)):
            break
    return items


def extract_openrouter(payload: str, source: dict) -> list[dict]:
    """OpenRouter 模型目录：筛出 prompt/completion 都为 0 的免费模型。"""
    try:
        rows = json.loads(payload).get("data", [])
    except Exception:  # noqa: BLE001
        return []
    items: list[dict] = []
    for row in rows:
        price = row.get("pricing") or {}
        if str(price.get("prompt")) != "0" or str(price.get("completion")) != "0":
            continue
        name = str(row.get("name") or row.get("id") or "").strip()
        if not name:
            continue
        desc = re.sub(r"\s+", " ", str(row.get("description") or "")).strip()
        items.append(
            {
                "date": None,
                "title": f"免费模型 · {name}"[:80],
                "summary": (desc[:130] + "…") if len(desc) > 130 else (desc or "OpenRouter 当前标价 $0 的模型"),
                "source": source["name"],
                "url": "https://openrouter.ai/" + str(row.get("id", "")),
                "tag": "模型",
                "live": True,
            }
        )
        if len(items) >= int(source.get("limit", 3)):
            break
    return items


def extract_json(payload: str, source: dict, cfg: dict) -> list[dict]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("data") or data.get("providers") or []
    if not isinstance(data, list):
        return []
    keywords = cfg.get("keywords", [])
    items: list[dict] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        blob = " ".join(str(v) for v in row.values() if isinstance(v, (str, int, float)))
        if not any(k in blob for k in keywords):
            continue
        name = str(row.get("name") or row.get("title") or row.get("id") or "").strip()
        if not name:
            continue
        items.append(
            {
                "date": None,
                "title": f"{name} · 免费层",
                "summary": clean_text(blob)[:150],
                "source": source["name"],
                "url": str(row.get("url") or row.get("link") or source["url"]),
                "tag": "实时",
                "live": True,
            }
        )
        if len(items) >= int(source.get("limit", 3)):
            break
    return items


def extract_items(payload: str, kind: str, source: dict, cfg: dict) -> list[dict]:
    if kind == "markdown":
        return extract_markdown(payload, source, cfg)
    if kind == "openrouter":
        return extract_openrouter(payload, source)
    if kind == "json":
        return extract_json(payload, source, cfg)
    return extract_html(payload, source, cfg)



# --------------------------------------------------------------------------- #
# 合并 & 输出
# --------------------------------------------------------------------------- #
def build(offline: bool = False) -> dict:
    now = datetime.now(CST)
    today = now.strftime("%Y-%m-%d")
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    offers = seed.get("offers", [])
    news = list(seed.get("news", []))
    tips = seed.get("tips", [])

    cfg = json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.exists() else {}
    report = []
    live_items: list[dict] = []

    if not offline:
        for source in cfg.get("sources", []):
            print(f"[fetch] {source['name']} ...", flush=True)
            payload = None
            for candidate in [source["url"], *source.get("fallbacks", [])]:
                payload = http_get(candidate)
                if payload:
                    break
            if not payload:
                report.append({"name": source["name"], "url": source["url"], "ok": False, "items": 0})
                continue
            text = payload if source.get("kind") == "markdown" else payload
            got = extract_items(text, source.get("kind", "html"), source, cfg)
            for g in got:
                g["date"] = g.get("date") or today
            live_items.extend(got)
            report.append(
                {"name": source["name"], "url": source["url"], "ok": True, "items": len(got)}
            )
            print(f"        -> {len(got)} 条", flush=True)

    # 抓取全部失败时不要让资讯退化成基线：
    # 否则一次网络抖动 / 源站改版就会把线上已有的实时资讯冲掉。
    if not offline and not live_items and OUT_JSON.exists():
        try:
            prev = json.loads(OUT_JSON.read_text(encoding="utf-8")).get("news") or []
            if len(prev) > len(news):
                print(
                    f"  [warn] 本次抓取全部失败，沿用上次的 {len(prev)} 条资讯",
                    file=sys.stderr,
                )
                news = prev
        except Exception:  # noqa: BLE001 - 旧文件损坏时忽略，走基线
            pass

    # 去重：以标题前 12 字为指纹，实时条目排在前面
    fingerprints = set()
    merged: list[dict] = []
    for item in live_items + news:
        fp = re.sub(r"\W", "", item["title"])[:12]
        if fp in fingerprints:
            continue
        fingerprints.add(fp)
        merged.append(item)
    merged.sort(key=lambda x: x.get("date") or "", reverse=True)

    stats = {
        "total": len(offers),
        "official": sum(1 for o in offers if o.get("category") == "official"),
        "newbie": sum(1 for o in offers if o.get("category") == "newbie"),
        "limited": sum(1 for o in offers if o.get("category") == "limited"),
        "oss": sum(1 for o in offers if o.get("category") == "oss"),
        "live": len(live_items),
    }

    data = {
        "meta": {
            "title": seed.get("meta", {}).get("title", "FreeToken Radar"),
            "note": seed.get("meta", {}).get("note", ""),
            "updated": today,
            "generated_at": now.isoformat(timespec="seconds"),
            "generated_label": now.strftime("%Y-%m-%d %H:%M"),
            "offline": offline,
            "stats": stats,
            "sources": report,
        },
        "offers": offers,
        "news": merged[:24],
        "tips": tips,
    }
    return data


def write_outputs(data: dict) -> str:
    DATA.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    OUT_JSON.write_text(payload, encoding="utf-8")
    OUT_JS.write_text(
        "/* 自动生成，请勿手改。运行 crawler/fetch_offers.py 重新生成 */\n"
        "window.__FREE_TOKEN_DATA__ = " + payload + ";\n",
        encoding="utf-8",
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]


def main() -> None:
    ap = argparse.ArgumentParser(description="FreeToken Radar 数据抓取")
    ap.add_argument("--watch", action="store_true", help="循环抓取，实现页面实时更新")
    ap.add_argument("--interval", type=int, default=600, help="循环间隔秒数，默认 600")
    ap.add_argument("--offline", action="store_true", help="不联网，仅用基线数据重建")
    args = ap.parse_args()

    if not SEED.exists():
        sys.exit(f"缺少基线数据: {SEED}")

    if not args.watch:
        data = build(offline=args.offline)
        digest = write_outputs(data)
        s = data["meta"]["stats"]
        print(
            f"[ok] {s['total']} 个平台 / {len(data['news'])} 条资讯 "
            f"-> data/offers.json (hash {digest})"
        )
        return

    print(f"[watch] 每 {args.interval}s 抓取一次，Ctrl+C 退出")
    last = None
    while True:
        try:
            data = build(offline=args.offline)
            digest = write_outputs(data)
            if digest != last:
                print(f"[{datetime.now(CST):%H:%M:%S}] 更新 -> hash {digest}", flush=True)
                last = digest
            else:
                print(f"[{datetime.now(CST):%H:%M:%S}] 无变化", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {exc}", file=sys.stderr, flush=True)
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()
