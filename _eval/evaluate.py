#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeToken Radar · UI 打分器
--------------------------------
用真实浏览器（Edge/Chromium）跑一组可复现的检查，把「功能性 / 交互细节 /
视觉样式 / 跟手性 / 可访问性 / 信息架构」量化成 0-100 分，并给出失败项清单。

用法：
    python _eval/evaluate.py              # 跑一轮，输出报告
    python _eval/evaluate.py --round 3    # 标记轮次
    python _eval/evaluate.py --headed     # 开有头浏览器调试

产物：_eval/report.json、_eval/scorecard.md
"""

from __future__ import annotations

import argparse
import functools
import json
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "_eval"
RUBRIC = json.loads((EVAL / "rubric.json").read_text(encoding="utf-8"))

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}


# --------------------------------------------------------------------------- #
# 本地静态服务
# --------------------------------------------------------------------------- #
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(port: int = 0):
    handler = functools.partial(Quiet, directory=str(ROOT))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# --------------------------------------------------------------------------- #
# 页面内测量脚本
# --------------------------------------------------------------------------- #
JS_CONTRAST = r"""
() => {
  const parse = (c) => { const m = c.match(/[\d.]+/g); return m ? m.map(Number) : null; };
  const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); const hi = Math.max(l1, l2), lo = Math.min(l1, l2); return (hi + 0.05) / (lo + 0.05); };
  const bodyBg = parse(getComputedStyle(document.body).backgroundColor) || [8, 9, 16];
  const base = bodyBg.length > 3 ? [bodyBg[0], bodyBg[1], bodyBg[2]] : bodyBg;

  function bgOf(el) {
    const stack = [];
    let n = el;
    while (n && n !== document.documentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && (c.length < 4 || c[3] > 0)) stack.push(c);
      n = n.parentElement;
    }
    let out = base.slice();
    for (let i = stack.length - 1; i >= 0; i--) {
      const l = stack[i]; const a = l.length > 3 ? l[3] : 1;
      out = [out[0] * (1 - a) + l[0] * a, out[1] * (1 - a) + l[1] * a, out[2] * (1 - a) + l[2] * a];
    }
    return out;
  }

  const nodes = Array.from(document.querySelectorAll('p,span,h3,a,td,button,li,dt,dd'))
    .filter((el) => {
      const t = (el.textContent || '').trim();
      if (t.length < 2) return false;
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) return false;
      if (getComputedStyle(el).visibility === 'hidden') return false;
      // 只取"叶子"文本节点，避免父容器重复计数
      return !Array.from(el.children).some((c) => (c.textContent || '').trim().length > 1);
    })
    .slice(0, 140);

  let fail = 0, checked = 0; const samples = [];
  for (const el of nodes) {
    const cs = getComputedStyle(el);
    const fg = parse(cs.color);
    if (!fg) continue;
    const size = parseFloat(cs.fontSize);
    const bold = parseInt(cs.fontWeight, 10) >= 600;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    const r = ratio([fg[0], fg[1], fg[2]], bgOf(el));
    checked++;
    if (r < need) {
      fail++;
      if (samples.length < 6) samples.push({ text: (el.textContent || '').trim().slice(0, 22), ratio: +r.toFixed(2), need });
    }
  }
  return { checked, fail, passRate: checked ? (checked - fail) / checked : 1, samples };
}
"""

JS_TEXT_SIZES = r"""
() => {
  const els = Array.from(document.querySelectorAll('p,span,td,li,a,button,h3'));
  let small = 0, total = 0;
  for (const el of els) {
    const t = (el.textContent || '').trim();
    if (t.length < 2) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    total++;
    if (parseFloat(getComputedStyle(el).fontSize) < 10.5) small++;
  }
  return { total, small, rate: total ? small / total : 0 };
}
"""

JS_A11Y = r"""
() => {
  const els = Array.from(document.querySelectorAll('a,button,input,select,[role="tab"]'));
  let missing = 0; const bad = [];
  for (const el of els) {
    const name = (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || el.value || '').trim();
    if (!name) { missing++; if (bad.length < 5) bad.push(el.tagName + '.' + (el.className || '').toString().slice(0, 24)); }
  }
  return {
    total: els.length,
    missing,
    named: els.length - missing,
    hasLang: !!document.documentElement.lang,
    hasTitle: document.title.length > 6,
    hasDesc: !!document.querySelector('meta[name="description"]'),
    liveRegion: !!document.querySelector('[aria-live]'),
    reducedMotion: Array.from(document.styleSheets).some((s) => {
      try { return Array.from(s.cssRules).some((r) => (r.conditionText || '').includes('prefers-reduced-motion')); }
      catch (e) { return false; }
    }),
  };
}
"""

JS_TAP = r"""
() => {
  const els = Array.from(document.querySelectorAll('a,button,select,input[type="search"]'));
  let small = 0, total = 0; const bad = [];
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (getComputedStyle(el).display === 'none') continue;
    total++;
    if (r.height < 40 || r.width < 40) {
      small++;
      if (bad.length < 8) bad.push((el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 14) + ` ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
  }
  return { total, small, rate: total ? (total - small) / total : 1, bad };
}
"""

JS_SCROLL = r"""
async () => {
  const frames = [];
  let last = performance.now();
  let stop = false;
  const loop = (t) => { frames.push(t - last); last = t; if (!stop) requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
  const max = document.body.scrollHeight - innerHeight;
  for (let y = 0; y < max; y += 90) {
    window.scrollTo(0, y);
    await new Promise((r) => setTimeout(r, 16));
    if (frames.length > 400) break;
  }
  stop = true;
  window.scrollTo(0, 0);
  const f = frames.slice(3).sort((a, b) => a - b);
  const p = (q) => f[Math.min(f.length - 1, Math.floor(f.length * q))] || 0;
  return { count: f.length, p50: p(0.5), p95: p(0.95), jankRate: f.filter((x) => x > 32).length / (f.length || 1) };
}
"""


# --------------------------------------------------------------------------- #
# 检查工具
# --------------------------------------------------------------------------- #
def check(cid, label, passed, value="", detail=""):
    return {"id": cid, "label": label, "pass": bool(passed), "value": value, "detail": detail}


def score_of(checks):
    if not checks:
        return 0.0
    return round(sum(1 for c in checks if c["pass"]) / len(checks) * 100, 1)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    httpd, port = serve(args.port)
    url = f"http://127.0.0.1:{port}/index.html"
    print(f"[eval] round {args.round} -> {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=not args.headed)
        ctx = browser.new_context(viewport=DESKTOP, device_scale_factor=1)
        page = ctx.new_page()
        console_errors: list[str] = []
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        page.add_init_script("""
            window.__paint = {};
            try {
              new PerformanceObserver((l) => {
                for (const e of l.getEntries()) window.__paint[e.name] = e.startTime;
              }).observe({ type: 'paint', buffered: true });
            } catch (err) {}
        """)
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(1200)

        # ---------- 采集 ----------
        metrics: dict = {}
        cards = page.eval_on_selector_all(".card", "els => els.map(e => ({ id: e.dataset.id, h: e.getBoundingClientRect().height }))")
        metrics["card_count"] = len(cards)
        metrics["card_height_min"] = round(min([c["h"] for c in cards], default=0), 1)

        links = page.eval_on_selector_all(".card .go", "els => els.map(e => e.getAttribute('href') || '')")
        metrics["card_links_ok"] = sum(1 for h in links if h.startswith("http"))

        fcp = page.evaluate("() => { const e = performance.getEntriesByType('paint').find(x => x.name === 'first-contentful-paint'); return (e && e.start) || (window.__paint && window.__paint['first-contentful-paint']) || 0; }") or 0
        dcl = page.evaluate("() => { const n = performance.getEntriesByType('navigation')[0]; return n ? n.domContentLoadedEventEnd : 0; }") or 0
        metrics["fcp"] = round(fcp, 0)
        metrics["dcl"] = round(dcl, 0)
        metrics["dom_nodes"] = page.evaluate("() => document.querySelectorAll('*').length")

        assets = page.evaluate("""() => performance.getEntriesByType('resource')
            .filter(r => /\\.(css|js)$/.test(r.name))
            .reduce((s, r) => s + (r.transferSize || r.encodedBodySize || 0), 0)""")
        metrics["asset_kb"] = round(assets / 1024, 1)

        metrics["contrast"] = page.evaluate(JS_CONTRAST)
        metrics["text_sizes"] = page.evaluate(JS_TEXT_SIZES)
        metrics["a11y"] = page.evaluate(JS_A11Y)

        overflow = {}
        for name, vp in (("1440", DESKTOP), ("768", {"width": 768, "height": 900}), ("390", MOBILE)):
            page.set_viewport_size(vp)
            page.wait_for_timeout(320)
            overflow[name] = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        metrics["overflow"] = overflow
        page.set_viewport_size(DESKTOP)
        page.wait_for_timeout(300)

        # 交互延迟：点分类 -> 网格重绘
        latency = page.evaluate("""() => new Promise((res) => {
            const grid = document.querySelector('#offerGrid');
            const t0 = performance.now();
            const mo = new MutationObserver(() => { mo.disconnect(); res(performance.now() - t0); });
            mo.observe(grid, { childList: true });
            document.querySelector('[data-cat="limited"]').click();
        })""")
        metrics["interaction_ms"] = round(latency, 1)

        # 滚动流畅度（先预热一帧，排除换视口后的重排抖动）
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(600)
        page.evaluate(JS_SCROLL)
        page.wait_for_timeout(300)
        metrics["scroll"] = page.evaluate(JS_SCROLL)
        page.wait_for_timeout(300)

        # ---------- 功能性断言 ----------
        func = []
        func.append(check("cards", "卡片渲染数量 ≥ 20", metrics["card_count"] >= 20, f'{metrics["card_count"]} 张'))
        func.append(check("links", "卡片均指向官方 http 链接", metrics["card_links_ok"] == metrics["card_count"] and metrics["card_count"] > 0,
                          f'{metrics["card_links_ok"]}/{metrics["card_count"]}'))

        page.fill("#searchInput", "智谱")
        page.wait_for_timeout(320)
        n_search = page.eval_on_selector_all(".card", "e => e.length")
        func.append(check("search", "搜索能过滤结果", 0 < n_search < metrics["card_count"], f"命中 {n_search} 个"))
        page.click("#clearSearch")
        page.wait_for_timeout(280)

        page.click('#filterChips [data-cat="limited"]')
        page.wait_for_timeout(320)
        n_limited = page.eval_on_selector_all(".card", "e => e.length")
        func.append(check("cat", "分类筛选生效", 0 < n_limited < metrics["card_count"], f"限时 {n_limited} 个"))
        page.click('#filterChips [data-cat="all"]')
        page.wait_for_timeout(280)

        page.click('#regionSeg [data-region="国内"]')
        page.wait_for_timeout(320)
        n_region = page.eval_on_selector_all(".card", "e => e.length")
        func.append(check("region", "地区筛选生效", 0 < n_region < metrics["card_count"], f"国内 {n_region} 个"))
        page.click('#regionSeg [data-region="all"]')
        page.wait_for_timeout(280)

        page.select_option("#sortSelect", "quota")
        page.wait_for_timeout(320)
        first = page.eval_on_selector(".card .q-num", "e => e.textContent.trim()")
        func.append(check("sort", "排序可切换且生效", bool(first), f"首位额度 {first}"))
        page.select_option("#sortSelect", "weight")
        page.wait_for_timeout(280)

        page.click('[data-view="table"]')
        page.wait_for_timeout(360)
        rows = page.eval_on_selector_all("#offerTbody tr", "e => e.length")
        func.append(check("view", "表格视图与卡片数据一致", rows == metrics["card_count"], f"{rows} 行"))
        page.click('[data-view="grid"]')
        page.wait_for_timeout(300)

        # 收藏
        page.click(".card .star")
        page.wait_for_timeout(260)
        saved_n = page.inner_text("#railSaved")
        func.append(check("saved", "收藏写入清单", saved_n.strip() == "1", f"清单 {saved_n} 项"))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        saved_after = page.inner_text("#railSaved")
        func.append(check("saved_persist", "刷新后收藏保留", saved_after.strip() == "1", f"清单 {saved_after} 项"))
        page.click(".card .star")
        page.wait_for_timeout(200)

        news_n = page.eval_on_selector_all(".news-row", "e => e.length")
        func.append(check("news", "资讯列表渲染 ≥ 8 条", news_n >= 8, f"{news_n} 条"))

        page.click("#newsMore") if page.query_selector("#newsMore") else None
        page.wait_for_timeout(300)
        news_all = page.eval_on_selector_all(".news-row", "e => e.length")
        has_more = page.query_selector("#newsMore") is not None
        func.append(check("news_more", "资讯可展开更多", (news_all > news_n) or (not has_more and news_n >= 8),
                          f"{news_n} → {news_all}" + ("" if has_more else "（已全部展示）")))

        lim_n = page.eval_on_selector_all(".lim", "e => e.length")
        func.append(check("limited", "限时活动区有内容", lim_n >= 1, f"{lim_n} 张"))

        # 表格表头排序
        page.click('[data-view="table"]')
        page.wait_for_timeout(320)
        page.select_option("#sortSelect", "weight")
        page.wait_for_timeout(300)
        first_row = page.eval_on_selector("#offerTbody tr b", "e => e.textContent.trim()")
        page.click('#offerTable th[data-sort="quota"]')
        page.wait_for_timeout(360)
        first_row2 = page.eval_on_selector("#offerTbody tr b", "e => e.textContent.trim()")
        func.append(check("th_sort", "表格表头点击排序生效", first_row != first_row2, f"{first_row} → {first_row2}"))
        page.click('[data-view="grid"]')
        page.wait_for_timeout(300)

        # 收藏筛选
        page.click(".card .star")
        page.wait_for_timeout(260)
        page.click("#savedBtn")
        page.wait_for_timeout(400)
        saved_cards = page.eval_on_selector_all(".card", "e => e.length")
        func.append(check("saved_filter", "「我的清单」只显示收藏项", saved_cards == 1, f"{saved_cards} 张"))
        page.click("#savedBtn")
        page.wait_for_timeout(300)

        # 主题切换
        bg_before = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        page.click("#themeBtn")
        page.wait_for_timeout(400)
        bg_after = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        func.append(check("theme", "深浅主题可切换且生效", bg_before != bg_after, f"{bg_before} → {bg_after}"))
        light_contrast = page.evaluate(JS_CONTRAST)
        metrics["contrast_light"] = light_contrast
        page.click("#themeBtn")
        page.wait_for_timeout(350)

        # ---------- 交互细节断言 ----------
        inter = []
        inter.append(check("latency", "点击到重绘 < 120ms", metrics["interaction_ms"] < 120, f'{metrics["interaction_ms"]}ms'))

        page.set_viewport_size(MOBILE)
        page.wait_for_timeout(400)
        tap = page.evaluate(JS_TAP)
        metrics["tap"] = tap
        inter.append(check("tap", "移动端点击热区 ≥ 40px（≥90%）", tap["rate"] >= 0.9, f'{round(tap["rate"]*100)}%', ", ".join(tap["bad"])))

        page.click("#filterBtn")
        page.wait_for_timeout(420)
        sheet_open = page.is_visible("#filterSheet")
        inter.append(check("sheet", "移动端筛选面板可打开", sheet_open))
        page.keyboard.press("Escape")
        page.wait_for_timeout(420)
        inter.append(check("esc", "Esc 可关闭浮层", not page.is_visible("#filterSheet")))

        page.keyboard.press("/")
        page.wait_for_timeout(220)
        focused = page.evaluate("() => document.activeElement && document.activeElement.id")
        inter.append(check("hotkey", "/ 快捷键聚焦搜索", focused == "searchInput", focused or "-"))
        page.keyboard.press("Escape")

        page.fill("#searchInput", "zzzzzz")
        page.wait_for_timeout(320)
        empty_ok = page.is_visible("#emptyState")
        inter.append(check("empty", "空状态提示", empty_ok))
        page.click("#resetFilter")
        page.wait_for_timeout(320)
        inter.append(check("reset", "空状态可一键重置", not page.is_visible("#emptyState")))

        page.set_viewport_size(DESKTOP)
        page.wait_for_timeout(400)
        page.click('#filterChips [data-cat="official"]')
        page.wait_for_timeout(320)
        af_ok = page.is_visible("#activeFilters")
        inter.append(check("af", "已选条件可视化", af_ok))
        page.click('#filterChips [data-cat="all"]')
        page.wait_for_timeout(260)
        page.evaluate("() => document.querySelector('#news').scrollIntoView()")
        page.wait_for_timeout(700)
        spy = page.evaluate("() => document.querySelector('.rail-link.active') ? document.querySelector('.rail-link.active').dataset.spy : ''")
        inter.append(check("spy", "滚动联动导航高亮", spy in ("news", "tips", "offers"), spy or "-"))

        # 响应式互斥：桌面不该出现移动端专属控件，反之亦然
        page.set_viewport_size(DESKTOP)
        page.wait_for_timeout(400)
        desk_leak = page.evaluate("""() => {
            const vis = (sel) => { const e = document.querySelector(sel); if (!e) return false;
                const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
                return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0 && r.left > -5000; };
            return { railToggle: vis('#railToggle'), tabbar: vis('.tabbar') };
        }""")
        inter.append(check("desktop_exclusive", "桌面端不出现移动端专属控件",
                           not desk_leak["railToggle"] and not desk_leak["tabbar"], str(desk_leak)))
        page.set_viewport_size(MOBILE)
        page.wait_for_timeout(400)
        mob_leak = page.evaluate("""() => {
            const vis = (sel) => { const e = document.querySelector(sel); if (!e) return false;
                const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
                return cs.display !== 'none' && r.width > 0 && r.height > 0 && r.right > 0; };
            return { rail: vis('#rail'), viewToggle: vis('#viewToggle'), sort: vis('.select-wrap') };
        }""")
        inter.append(check("mobile_exclusive", "移动端不出现桌面专属控件",
                           not mob_leak["rail"] and not mob_leak["viewToggle"] and not mob_leak["sort"], str(mob_leak)))
        page.set_viewport_size(DESKTOP)
        page.wait_for_timeout(350)

        # 键盘可达：Tab 能聚焦到可交互元素且有可见焦点样式
        page.evaluate("() => window.scrollTo(0, 0)")
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        page.wait_for_timeout(160)
        focus_info = page.evaluate("""() => {
            const el = document.activeElement;
            if (!el || el === document.body) return { ok: false };
            const cs = getComputedStyle(el);
            const ring = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
            return { ok: true, tag: el.tagName, ring, label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 12) };
        }""")
        inter.append(check("tab_focus", "键盘 Tab 可达且有焦点环", focus_info.get("ok") and focus_info.get("ring"), str(focus_info.get("tag", "")) + " " + str(focus_info.get("label", ""))))

        # 移动端底部标签栏
        page.set_viewport_size(MOBILE)
        page.wait_for_timeout(400)
        tab_visible = page.is_visible(".tabbar")
        tab_n = page.eval_on_selector_all(".tabbar a", "e => e.length")
        inter.append(check("tabbar", "移动端底部标签栏可用", tab_visible and tab_n >= 4, f"{tab_n} 个入口"))
        page.click('.tabbar a[data-spy="news"]')
        page.wait_for_timeout(700)
        scrolled = page.evaluate("() => window.scrollY")
        inter.append(check("tab_jump", "底部标签可跳转分区", scrolled > 100, f"scrollY={round(scrolled)}"))
        page.set_viewport_size(DESKTOP)
        page.wait_for_timeout(320)

        # ---------- 视觉断言 ----------
        vis = []
        c = metrics["contrast"]
        vis.append(check("contrast", "文本对比度达 WCAG AA（≥95%）", c["passRate"] >= 0.95,
                         f'{round(c["passRate"]*100)}%（{c["fail"]}/{c["checked"]} 不达标）',
                         "; ".join(f'{s["text"]}={s["ratio"]}' for s in c["samples"])))
        vis.append(check("overflow", "三档宽度无横向溢出",
                         all(v <= 0 for v in metrics["overflow"].values()),
                         json.dumps(metrics["overflow"], ensure_ascii=False)))
        ts = metrics["text_sizes"]
        vis.append(check("font", "无过小字号（<10.5px ≤2%）", ts["rate"] <= 0.02, f'{round(ts["rate"]*100)}%'))
        vis.append(check("card_h", "卡片高度不过矮（≥180px）", metrics["card_height_min"] >= 180, f'{metrics["card_height_min"]}px'))
        vis.append(check("no_error", "无 JS 运行时错误", len(console_errors) == 0, f"{len(console_errors)} 个", "; ".join(console_errors[:3])))
        cl = metrics.get("contrast_light") or {"passRate": 0, "checked": 0}
        vis.append(check("contrast_light", "浅色主题对比度达 AA（≥95%）", cl["passRate"] >= 0.95,
                         f'{round(cl["passRate"]*100)}%（{cl.get("fail", 0)}/{cl.get("checked", 0)} 不达标）'))
        glass_n = page.evaluate("""() => Array.from(document.querySelectorAll('.glass, .card, .tip, .lim'))
            .filter(e => (getComputedStyle(e).backdropFilter || '').includes('blur')).length""")
        vis.append(check("glass", "液态玻璃材质保留（≥6 处 blur）", glass_n >= 6, f"{glass_n} 处"))
        radii = page.evaluate("""() => {
            const set = new Set();
            document.querySelectorAll('.card').forEach(e => set.add(getComputedStyle(e).borderTopLeftRadius));
            return Array.from(set);
        }""")
        vis.append(check("radius", "卡片圆角统一", len(radii) == 1, str(radii)))
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(350)
        first_screen = page.evaluate("""() => Array.from(document.querySelectorAll('.card'))
            .filter(e => { const r = e.getBoundingClientRect(); return r.top < innerHeight && r.bottom > 0; }).length""")
        vis.append(check("density", "首屏可见卡片 ≥ 3 张", first_screen >= 3, f"{first_screen} 张"))

        # ---------- 跟手性断言 ----------
        perf = []
        perf.append(check("fcp", "FCP < 1200ms", 0 < metrics["fcp"] < 1200, f'{metrics["fcp"]}ms' + ("" if metrics["fcp"] else "（未采集到）")))
        perf.append(check("dcl", "DOMContentLoaded < 900ms", metrics["dcl"] < 900, f'{metrics["dcl"]}ms'))
        sc = metrics["scroll"]
        perf.append(check("scroll_p95", "滚动帧 p95 ≤ 34ms", sc["p95"] <= 34, f'{round(sc["p95"],1)}ms'))
        perf.append(check("jank", "掉帧率 ≤ 12%", sc["jankRate"] <= 0.12, f'{round(sc["jankRate"]*100)}%'))
        perf.append(check("nodes", "DOM 节点 < 3000", metrics["dom_nodes"] < 3000, f'{metrics["dom_nodes"]}'))
        perf.append(check("assets", "CSS+JS < 260KB", metrics["asset_kb"] < 260, f'{metrics["asset_kb"]}KB'))

        page.evaluate("() => window.scrollTo(0, 0)")
        rerenders = page.evaluate("""() => new Promise((res) => {
            const grid = document.querySelector('#offerGrid');
            const times = [];
            let i = 0;
            const cats = ['limited', 'official', 'all', 'newbie', 'all'];
            const step = () => {
                if (i >= cats.length) return res(times);
                const t0 = performance.now();
                const mo = new MutationObserver(() => {
                    mo.disconnect();
                    times.push(performance.now() - t0);
                    i++; step();
                });
                mo.observe(grid, { childList: true });
                document.querySelector('#filterChips [data-cat="' + cats[i] + '"]').click();
            };
            step();
        })""")
        avg = sum(rerenders) / max(len(rerenders), 1)
        metrics["rerender_ms"] = round(avg, 1)
        perf.append(check("rerender", "二次渲染均值 < 80ms", avg < 80, f"{round(avg,1)}ms"))

        # ---------- 可访问性 ----------
        a = metrics["a11y"]
        acc = []
        acc.append(check("names", "可交互元素均有可访问名称（≥98%）", (a["named"] / max(a["total"], 1)) >= 0.98,
                         f'{a["named"]}/{a["total"]}'))
        acc.append(check("lang", "声明 lang", a["hasLang"]))
        acc.append(check("title", "有标题与描述", a["hasTitle"] and a["hasDesc"]))
        acc.append(check("live", "Toast 使用 aria-live", a["liveRegion"]))
        acc.append(check("rm", "支持 prefers-reduced-motion", a["reducedMotion"]))
        aria_ok = page.evaluate("""() => {
            const groups = ['#filterChips', '#regionSeg', '#viewToggle'];
            return groups.every(sel => {
                const g = document.querySelector(sel);
                if (!g) return true;
                return g.querySelectorAll('[aria-selected="true"]').length === 1;
            });
        }""")
        acc.append(check("aria_state", "单选组 aria-selected 唯一", aria_ok))

        # ---------- 信息架构 ----------
        cont = []
        rich = page.eval_on_selector_all(".card", """els => els.filter(e =>
            e.querySelector('.q-num') && e.querySelector('.detail') &&
            e.querySelector('.badge') && e.querySelector('.go')).length""")
        cont.append(check("card_fields", "卡片信息完整（额度/说明/类型/入口）", rich == metrics["card_count"], f"{rich}/{metrics['card_count']}"))
        cont.append(check("nav", "分区导航存在", page.eval_on_selector_all(".rail-link", "e => e.length") >= 4))
        cont.append(check("compare", "具备对比视图（表格）", page.query_selector("#offerTable") is not None))
        cont.append(check("saved_ia", "具备收藏工作流", page.query_selector("#savedBtn") is not None))
        cont.append(check("disclaimer", "有数据来源与免责声明", page.query_selector(".foot-brand p") is not None))
        meta_rate = page.evaluate("""() => {
            const cards = Array.from(document.querySelectorAll('.card'));
            const ok = cards.filter(c => /核验 \\d{2}\\/\\d{2}/.test(c.querySelector('.c-meta').textContent)).length;
            return cards.length ? ok / cards.length : 0;
        }""")
        cont.append(check("meta_rate", "卡片均标注核验日期", meta_rate >= 0.99, f"{round(meta_rate*100)}%"))
        src_rate = page.evaluate("""() => {
            const rows = Array.from(document.querySelectorAll('.news-row'));
            const ok = rows.filter(r => r.querySelector('.news-meta').textContent.includes('·') || r.querySelector('.news-meta span:nth-child(2)')).length;
            return rows.length ? ok / rows.length : 0;
        }""")
        cont.append(check("src_rate", "资讯均标注来源", src_rate >= 0.99, f"{round(src_rate*100)}%"))
        cont.append(check("shortcut", "界面上有快捷键提示", page.query_selector(".s-kbd") is not None))

        browser.close()

    groups = {
        "functional": func, "interaction": inter, "visual": vis,
        "performance": perf, "a11y": acc, "content": cont,
    }

    total = 0.0
    dims = {}
    for d in RUBRIC["dimensions"]:
        s = score_of(groups[d["key"]])
        dims[d["key"]] = {
            "name": d["name"], "weight": d["weight"], "score": s,
            "checks": groups[d["key"]],
        }
        total += s * d["weight"] / 100

    total = round(total, 1)
    weakest = min(dims.items(), key=lambda kv: kv[1]["score"])
    passed = total >= RUBRIC["threshold"] and all(v["score"] >= RUBRIC["minDimension"] for v in dims.values())

    report = {
        "round": args.round,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "url": url,
        "total": total,
        "threshold": RUBRIC["threshold"],
        "passed": passed,
        "weakest": {"key": weakest[0], "name": weakest[1]["name"], "score": weakest[1]["score"]},
        "dimensions": dims,
        "metrics": metrics,
    }
    (EVAL / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台 & markdown
    lines = [f"# 第 {args.round} 轮 · UI 打分报告", "",
             f"- 时间：{report['time']}", f"- 总分：**{total} / 100**（阈值 {RUBRIC['threshold']}）",
             f"- 结论：{'✅ 达标' if passed else '❌ 未达标'}",
             f"- 最弱维度：{weakest[1]['name']} {weakest[1]['score']}", "", "| 维度 | 权重 | 得分 |", "| --- | --- | --- |"]
    for k, v in dims.items():
        lines.append(f"| {v['name']} | {v['weight']} | {v['score']} |")
    lines.append("")
    for k, v in dims.items():
        bad = [c for c in v["checks"] if not c["pass"]]
        lines.append(f"## {v['name']} · {v['score']}")
        if bad:
            for c in bad:
                lines.append(f"- ❌ {c['label']}（{c['value']}）{('· ' + c['detail']) if c['detail'] else ''}")
        else:
            lines.append("- ✅ 全部通过")
        lines.append("")
    (EVAL / "scorecard.md").write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "=" * 58)
    for k, v in dims.items():
        bar = "█" * int(v["score"] / 5)
        print(f"{v['name']:<6} {v['score']:>5}  {bar}")
    print("-" * 58)
    print(f"总分 {total} / 100   阈值 {RUBRIC['threshold']}   {'达标 ✅' if passed else '未达标 ❌'}")
    print(f"最弱：{weakest[1]['name']} {weakest[1]['score']}")
    print("=" * 58)
    for k, v in dims.items():
        for c in v["checks"]:
            if not c["pass"]:
                print(f"  ✗ [{v['name']}] {c['label']}  ->  {c['value']}  {c['detail']}")
    print()

    httpd.shutdown()


if __name__ == "__main__":
    main()
