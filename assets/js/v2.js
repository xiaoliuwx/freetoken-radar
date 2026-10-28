/* =========================================================
   FreeToken Radar v2 · 工作台逻辑
   状态：筛选 / 排序 / 视图 / 收藏 / 主题 / 实时轮询
   ========================================================= */
(function () {
  "use strict";

  const DATA_URL = "data/offers.json";
  const POLL_MS = 60000;
  const SAVED_KEY = "ftr-saved";
  const VIEW_KEY = "ftr-view";
  const THEME_KEY = "ftr-theme";

  let DATA = window.__FREE_TOKEN_DATA__ || { offers: [], news: [], tips: [], meta: {} };
  let mode = "snapshot";

  const state = {
    cat: "all",
    region: "all",
    q: "",
    sort: "weight",
    view: "grid",
    saved: new Set(),
  };

  const CAT_LABEL = {
    official: "长期免费",
    newbie: "新人赠送",
    limited: "限时活动",
    oss: "开源自部署",
  };

  const ICONS = { shield: "i-shield", clock: "i-clock", alert: "i-alert", key: "i-key", gauge: "i-gauge", refresh: "i-refresh" };

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const esc = (v) =>
    String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const fmtDate = (d) => (d ? String(d).slice(5).replace("-", "/") : "--");

  function toast(msg) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2200);
  }

  /* ---------------- 额度解析 ---------------- */
  function splitQuota(q) {
    const s = String(q || "").trim();
    const m = s.match(/^([¥$€]?\s?[\d,.]+\s?(?:万|亿|k|K|M)?)\s*(.*)$/);
    if (!m) return { num: "", unit: s, plain: true };
    const unit = m[2].trim();
    if (!unit) return { num: m[1].replace(/\s/g, ""), unit: "", plain: false };
    // 单位不像计量单位（如「-04 开放」「款长期免费」）时整串按文本展示，避免「大数字+怪尾巴」
    const cjk = (unit.match(/[\u4e00-\u9fff]/g) || []).length;
    if (cjk >= 4) return { num: "", unit: s, plain: true };
    if (!/^(万|亿|Token|token|tokens|次|元|天|个|款|人|月|年|%|\/|Credits|credits|Neurons|积分|美元|\$)/.test(unit)) {
      return { num: "", unit: s, plain: true };
    }
    return { num: m[1].replace(/\s/g, ""), unit, plain: false };
  }

  function quotaValue(q) {
    const m = String(q || "").match(/([\d,.]+)\s*(万|亿|k|K|M)?/);
    if (!m) return -1;
    let n = parseFloat(m[1].replace(/,/g, ""));
    if (isNaN(n)) return -1;
    if (m[2] === "万") n *= 1e4;
    else if (m[2] === "亿") n *= 1e8;
    else if (m[2] === "k" || m[2] === "K") n *= 1e3;
    else if (m[2] === "M") n *= 1e6;
    return n;
  }

  /* 品牌色块：保证白字/深字至少 4.5:1，必要时压暗底色 */
  function hex2rgb(h) {
    const c = String(h || "#6d6bf5").replace("#", "");
    const s = c.length === 3 ? c.split("").map((x) => x + x).join("") : c;
    return [parseInt(s.slice(0, 2), 16) || 109, parseInt(s.slice(2, 4), 16) || 107, parseInt(s.slice(4, 6), 16) || 245];
  }
  function relLum(rgb) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
  }
  const rgb2hex = (rgb) => "#" + rgb.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
  function tileOf(c1) {
    const rgb = hex2rgb(c1);
    const lum = relLum(rgb);
    const INK_L = 0.00402; // #0b0d12 的相对亮度
    const cw = 1.05 / (lum + 0.05);
    const cd = (lum + 0.05) / (INK_L + 0.05);
    if (cw >= 4.8) return { base: rgb2hex(rgb), ink: "#ffffff" };
    if (cd >= 4.8) return { base: rgb2hex(rgb), ink: "#0b0d12" };
    let k = 0.95, cur = rgb;
    while (k > 0.2) {
      cur = rgb.map((v) => v * k);
      if (1.05 / (relLum(cur) + 0.05) >= 4.9) break;
      k -= 0.05;
    }
    return { base: rgb2hex(cur), ink: "#ffffff" };
  }

  function daysLeft(d) {
    if (!d) return null;
    const end = new Date(d + "T23:59:59+08:00").getTime();
    if (isNaN(end)) return null;
    return Math.ceil((end - Date.now()) / 86400000);
  }

  function deadlineText(d) {
    const n = daysLeft(d);
    if (n === null) return "";
    if (n <= 0) return "已结束";
    if (n === 1) return "剩 1 天";
    if (n <= 45) return "剩 " + n + " 天";
    return "剩 " + Math.floor(n / 30) + " 个月";
  }

  /* ---------------- 数据筛选 ---------------- */
  function currentList() {
    const kw = state.q.trim().toLowerCase();
    let list = (DATA.offers || []).filter((o) => {
      if (state.cat === "saved") {
        if (!state.saved.has(o.id)) return false;
      } else if (state.cat !== "all" && o.category !== state.cat) {
        return false;
      }
      if (state.region !== "all" && o.region !== state.region) return false;
      if (!kw) return true;
      const hay = [o.name, o.brand, o.detail, o.quota, o.quotaNum, o.region, (o.tags || []).join(" ")]
        .join(" ").toLowerCase();
      return hay.indexOf(kw) !== -1;
    });

    const byDeadline = (a, b) => {
      const da = daysLeft(a.deadline), db = daysLeft(b.deadline);
      if (da === null && db === null) return 0;
      if (da === null) return 1;
      if (db === null) return -1;
      return da - db;
    };

    list = list.slice().sort((a, b) => {
      if (state.sort === "quota") return quotaValue(b.quotaNum) - quotaValue(a.quotaNum);
      if (state.sort === "verified") return String(b.verified || "").localeCompare(String(a.verified || ""));
      if (state.sort === "deadline") return byDeadline(a, b);
      if (state.sort === "name") return String(a.name).localeCompare(String(b.name), "zh");
      if (state.sort === "region") return String(a.region).localeCompare(String(b.region), "zh");
      return (b.weight || 0) - (a.weight || 0);
    });
    return list;
  }

  /* ---------------- 渲染：限时活动 ---------------- */
  function renderLimited() {
    const box = $("#limitedGrid");
    if (!box) return;
    const list = (DATA.offers || [])
      .filter((o) => o.deadline && (daysLeft(o.deadline) || 0) > 0)
      .sort((a, b) => daysLeft(a.deadline) - daysLeft(b.deadline))
      .slice(0, 3);

    box.innerHTML = list.map((o) => {
      const q = splitQuota(o.quotaNum);
      const left = daysLeft(o.deadline) || 0;
      const pct = Math.max(6, Math.min(100, Math.round((left / 30) * 100)));
      return `
        <a class="lim" href="${esc(o.url)}" target="_blank" rel="noopener noreferrer" style="--c1:${esc(o.c1)};--c2:${esc(o.c2)}">
          <div class="lim-top">
            <span class="mono" style="width:26px;height:26px;border-radius:8px;font-size:9px;--tile:${tileOf(o.c1).base};--mono-ink:${tileOf(o.c1).ink}">${esc(o.mono || "")}</span>
            <span class="lim-name">${esc(o.name)}</span>
            <span class="badge b-limited" style="margin-left:auto">限时</span>
          </div>
          <div class="lim-quota${q.plain ? " plain" : ""}">${esc(q.num || q.unit)}${q.num && q.unit ? `<small>${esc(q.unit)}</small>` : ""}</div>
          <div class="lim-bar"><i style="width:${pct}%"></i></div>
          <div class="lim-foot">
            <span class="lim-left">${esc(deadlineText(o.deadline))}</span>
            <span>· ${esc(o.deadline)}</span>
            <span class="lim-go">去领取<svg><use href="#i-link"/></svg></span>
          </div>
        </a>`;
    }).join("");

    $("#railLimited").textContent = (DATA.offers || []).filter((o) => o.deadline).length;
    if (!list.length) $("#limitedSub").textContent = "当前没有带截止时间的活动";
  }

  /* ---------------- 渲染：卡片 ---------------- */
  function cardHTML(o) {
    const q = splitQuota(o.quotaNum);
    const saved = state.saved.has(o.id);
    return `
      <article class="card" data-id="${esc(o.id)}" style="--c1:${esc(o.c1)};--c2:${esc(o.c2)};--tile:${tileOf(o.c1).base};--mono-ink:${tileOf(o.c1).ink}">
        <div class="c-top">
          <span class="mono">${esc(o.mono || "")}</span>
          <div class="c-id">
            <h3 title="${esc(o.name)}">${esc(o.name)}</h3>
            <span class="c-meta">${esc(o.region)} · 核验 ${esc(fmtDate(o.verified))}</span>
          </div>
          <span class="badge b-${esc(o.category)}">${esc(CAT_LABEL[o.category] || "免费")}</span>
        </div>
        <div class="quota">
          ${q.num ? `<span class="q-num">${esc(q.num)}</span><span class="q-unit">${esc(q.unit)}</span>`
                  : `<span class="q-num${q.plain ? " plain" : ""}">${esc(q.unit)}</span>`}
          <span class="q-label">${esc(o.quota || "")}</span>
        </div>
        <p class="detail">${esc(o.detail)}</p>
        <div class="tags">${(o.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
        <div class="c-foot">
          ${o.deadline ? `<span class="dl">${esc(deadlineText(o.deadline))}</span>` : `<span>长期有效</span>`}
          <span class="c-act">
            <button class="star ${saved ? "on" : ""}" data-star="${esc(o.id)}" type="button"
              aria-label="${saved ? "取消收藏" : "收藏"}" aria-pressed="${saved}">
              <svg><use href="#i-star"/></svg>
            </button>
            <a class="go" href="${esc(o.url)}" target="_blank" rel="noopener noreferrer">领取<svg><use href="#i-link"/></svg></a>
          </span>
        </div>
      </article>`;
  }

  function renderGrid(list) {
    const grid = $("#offerGrid");
    grid.innerHTML = list.map(cardHTML).join("");
    const io = "IntersectionObserver" in window
      ? new IntersectionObserver((es, ob) => {
          es.forEach((e) => {
            if (e.isIntersecting) {
              e.target.style.transitionDelay = Math.min(Number(e.target.dataset.i || 0), 8) * 35 + "ms";
              e.target.classList.add("in");
              ob.unobserve(e.target);
            }
          });
        }, { rootMargin: "0px 0px -30px 0px" })
      : null;
    $$(".card", grid).forEach((el, i) => {
      el.dataset.i = i;
      if (io) io.observe(el); else el.classList.add("in");
    });
  }

  /* ---------------- 渲染：表格 ---------------- */
  function renderTable(list) {
    const tb = $("#offerTbody");
    tb.innerHTML = list.map((o) => {
      const q = splitQuota(o.quotaNum);
      const saved = state.saved.has(o.id);
      return `
        <tr>
          <td class="c-star">
            <button class="star ${saved ? "on" : ""}" data-star="${esc(o.id)}" type="button"
              aria-label="${saved ? "取消收藏" : "收藏"}" aria-pressed="${saved}"><svg><use href="#i-star"/></svg></button>
          </td>
          <td>
            <span class="t-name">
              <span class="mono" style="--c1:${esc(o.c1)};--c2:${esc(o.c2)};--tile:${tileOf(o.c1).base};--mono-ink:${tileOf(o.c1).ink}">${esc(o.mono || "")}</span>
              <b>${esc(o.name)}</b>
            </span>
          </td>
          <td>${esc(o.region)}</td>
          <td><span class="t-quota">${esc(q.num || q.unit)}</span><small>${esc(q.num ? q.unit : "")}</small></td>
          <td><span class="badge b-${esc(o.category)}">${esc(CAT_LABEL[o.category] || "免费")}</span></td>
          <td>${o.deadline ? `<span class="t-dl">${esc(deadlineText(o.deadline))}</span>` : `<span class="t-none">—</span>`}</td>
          <td class="c-act">
            <a class="go" href="${esc(o.url)}" target="_blank" rel="noopener noreferrer">领取<svg><use href="#i-link"/></svg></a>
          </td>
        </tr>`;
    }).join("");

    $$("#offerTable th[data-sort]").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === state.sort);
    });
  }

  /* ---------------- 渲染：结果区 ---------------- */
  function renderOffers() {
    const list = currentList();
    $("#resultCount").textContent = `共 ${list.length} 个平台`;
    $("#offersSub").textContent = "官方入口直达 · 星标可收藏";

    const grid = $("#offerGrid");
    const tableWrap = $("#tableWrap");
    if (state.view === "table") {
      grid.hidden = true;
      tableWrap.hidden = false;
      renderTable(list);
    } else {
      tableWrap.hidden = true;
      grid.hidden = false;
      renderGrid(list);
    }

    $("#emptyState").hidden = list.length !== 0;
    if (!list.length) {
      $("#emptyText").textContent = state.cat === "saved" ? "清单还是空的，点卡片右上角星标收藏" : "没有匹配的平台，换个关键词试试";
    }

    // 已选条件
    const tags = [];
    if (state.cat !== "all") tags.push([state.cat === "saved" ? "我的清单" : CAT_LABEL[state.cat], "cat"]);
    if (state.region !== "all") tags.push([state.region, "region"]);
    if (state.q.trim()) tags.push(["“" + state.q.trim() + "”", "q"]);
    const af = $("#activeFilters");
    af.hidden = tags.length === 0;
    af.innerHTML = tags.map(([label, key]) =>
      `<span class="af-tag">${esc(label)}<button data-clear="${key}" aria-label="移除筛选">✕</button></span>`).join("");
  }

  /* ---------------- 渲染：资讯 ---------------- */
  let newsOpen = false;
  const NEWS_PAGE = 12;

  function renderNews() {
    const box = $("#newsList");
    const all = DATA.news || [];
    const list = newsOpen ? all : all.slice(0, NEWS_PAGE);

    box.innerHTML = list.map((n) => `
      <div class="news-row">
        <span class="news-date">${esc(n.date || "--")}</span>
        <div class="news-main">
          <a class="news-title" href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">
            ${esc(n.title)}<svg><use href="#i-link"/></svg>
          </a>
          ${n.summary ? `<p class="news-sum">${esc(n.summary)}</p>` : ""}
          <div class="news-meta">
            <span class="news-tag t-${esc(n.tag || "")}">${esc(n.tag || "资讯")}</span>
            <span>${esc(n.source || "公开资讯")}</span>
            ${n.live ? "<span>· 本次抓取</span>" : ""}
          </div>
        </div>
      </div>`).join("");

    if (all.length > NEWS_PAGE) {
      box.insertAdjacentHTML("beforeend",
        `<div class="news-more"><button type="button" id="newsMore">${newsOpen ? "收起" : `展开全部 ${all.length} 条`}</button></div>`);
      $("#newsMore").addEventListener("click", () => {
        newsOpen = !newsOpen;
        renderNews();
      });
    }
    $("#railNews").textContent = all.length;
  }

  /* ---------------- 渲染：避坑 ---------------- */
  function renderTips() {
    $("#tipsGrid").innerHTML = (DATA.tips || []).map((t) => `
      <div class="tip">
        <span class="tip-ic"><svg><use href="#${ICONS[t.icon] || "i-spark"}"/></svg></span>
        <div><h3>${esc(t.title)}</h3><p>${esc(t.text)}</p></div>
      </div>`).join("");
  }

  /* ---------------- 元信息 / 状态 ---------------- */
  function updateMeta() {
    const m = DATA.meta || {};
    const time = m.generated_label || m.updated || "--";
    $("#liveTime").textContent = time;
    $("#footTime").textContent = time;
    $("#footSources").textContent = (m.sources && m.sources.length)
      ? m.sources.filter((s) => s.ok).length + "/" + m.sources.length + " 个源"
      : "内置快照";
    $("#railOffers").textContent = (DATA.offers || []).length;
    const hc = $("#headCount"), ht = $("#headTime");
    if (hc) hc.textContent = (DATA.offers || []).length;
    if (ht) ht.textContent = String(time).replace(/^20\d\d-/, "");
  }

  function setStatus(ok) {
    mode = ok ? "live" : "snapshot";
    const dot = $("#liveDot");
    if (dot) dot.classList.toggle("off", !ok);
    $("#liveLabel").textContent = ok ? "实时同步" : "本地快照";
  }

  function updateSaved() {
    $("#railSaved").textContent = state.saved.size;
    $("#savedBtn").setAttribute("aria-pressed", state.cat === "saved" ? "true" : "false");
    try { localStorage.setItem(SAVED_KEY, JSON.stringify([...state.saved])); } catch (e) {}
  }

  function renderAll() {
    renderLimited();
    renderOffers();
    renderNews();
    renderTips();
    updateMeta();
    updateSaved();
    startCountdown();
    observeCards();
  }

  /* ---------------- 倒计时 ---------------- */
  let tickTimer = null;
  function startCountdown() {
    clearInterval(tickTimer);
    tickTimer = setInterval(() => {
      $$(".dl").forEach((el) => {
        const row = el.closest("[data-id]");
        if (!row) return;
        const o = (DATA.offers || []).find((x) => x.id === row.dataset.id);
        if (o && o.deadline) el.textContent = deadlineText(o.deadline);
      });
      $$(".lim-left").forEach((el) => {
        const card = el.closest(".lim");
        if (!card) return;
        const href = card.getAttribute("href");
        const o = (DATA.offers || []).find((x) => x.url === href && x.deadline);
        if (o) el.textContent = deadlineText(o.deadline);
      });
    }, 60000);
  }

  function observeCards() {
    const els = $$(".sec-head, .tip");
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((es, ob) => {
      es.forEach((e) => { if (e.isIntersecting) { ob.unobserve(e.target); } });
    }, { threshold: 0.1 });
    els.forEach((el) => io.observe(el));
  }

  /* ---------------- 滚动联动 ---------------- */
  function initSpy() {
    const ids = ["limited", "offers", "news", "tips"];
    const links = $$("[data-spy]");
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        const id = e.target.id;
        links.forEach((l) => l.classList.toggle("active", l.dataset.spy === id));
        $$(".tabbar a").forEach((a) => a.classList.toggle("on", a.dataset.spy === id));
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    ids.forEach((id) => { const el = document.getElementById(id); if (el) io.observe(el); });
  }

  /* ---------------- 交互 ---------------- */
  function setCat(cat) {
    state.cat = cat;
    $$("[data-cat]").forEach((b) => {
      const on = b.dataset.cat === cat;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    updateSaved();
    renderOffers();
  }

  function setRegion(r) {
    state.region = r;
    $$("[data-region]").forEach((b) => {
      const on = b.dataset.region === r;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    renderOffers();
  }

  function setSort(s) {
    state.sort = s;
    const sel = $("#sortSelect");
    if (sel) sel.value = s;
    $$("#sortChips .chip").forEach((b) => b.classList.toggle("on", b.dataset.sort === s));
    renderOffers();
  }

  function setView(v) {
    state.view = v;
    $$("#viewToggle .seg-btn").forEach((b) => {
      const on = b.dataset.view === v;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    try { localStorage.setItem(VIEW_KEY, v); } catch (e) {}
    renderOffers();
  }

  function toggleStar(id) {
    if (state.saved.has(id)) state.saved.delete(id);
    else state.saved.add(id);
    updateSaved();
    const o = (DATA.offers || []).find((x) => x.id === id);
    toast(state.saved.has(id) ? "已加入我的清单" : "已移出清单");
    if (state.cat === "saved") renderOffers();
    else {
      $$(`[data-star="${id}"]`).forEach((b) => {
        const on = state.saved.has(id);
        b.classList.toggle("on", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }
    if (o && state.saved.has(id)) updateSaved();
  }

  function openSheet() {
    const s = $("#filterSheet"), m = $("#sheetMask");
    s.hidden = false; m.hidden = false;
    requestAnimationFrame(() => { s.classList.add("show"); m.classList.add("show"); });
  }
  function closeSheet() {
    const s = $("#filterSheet"), m = $("#sheetMask");
    s.classList.remove("show"); m.classList.remove("show");
    setTimeout(() => { s.hidden = true; m.hidden = true; }, 300);
  }

  function openRail() {
    $("#rail").classList.add("open");
    $("#sheetMask").hidden = false;
    requestAnimationFrame(() => $("#sheetMask").classList.add("show"));
  }
  function closeRail() {
    $("#rail").classList.remove("open");
    $("#sheetMask").classList.remove("show");
    setTimeout(() => { $("#sheetMask").hidden = true; }, 260);
  }

  function bind() {
    // 搜索
    const input = $("#searchInput");
    const clearBtn = $("#clearSearch");
    let t = null;
    input.addEventListener("input", () => {
      clearBtn.hidden = !input.value;
      clearTimeout(t);
      t = setTimeout(() => { state.q = input.value; renderOffers(); }, 120);
    });
    clearBtn.addEventListener("click", () => {
      input.value = ""; state.q = ""; clearBtn.hidden = true; renderOffers(); input.focus();
    });

    // 分类 / 地区
    document.addEventListener("click", (e) => {
      const cat = e.target.closest("[data-cat]");
      if (cat) { setCat(cat.dataset.cat); return; }
      const reg = e.target.closest("[data-region]");
      if (reg) { setRegion(reg.dataset.region); return; }
      const sortChip = e.target.closest("#sortChips [data-sort]");
      if (sortChip) { setSort(sortChip.dataset.sort); return; }
      const view = e.target.closest("#viewToggle .seg-btn");
      if (view) { setView(view.dataset.view); return; }
      const star = e.target.closest("[data-star]");
      if (star) { e.preventDefault(); toggleStar(star.dataset.star); return; }
      const clear = e.target.closest("[data-clear]");
      if (clear) {
        const k = clear.dataset.clear;
        if (k === "cat") setCat("all");
        if (k === "region") setRegion("all");
        if (k === "q") { input.value = ""; state.q = ""; clearBtn.hidden = true; renderOffers(); }
        return;
      }
      const th = e.target.closest("#offerTable th[data-sort]");
      if (th) {
        const key = th.dataset.sort;
        state.sort = key;
        if (state.sortDir === key + ":desc") { state.sortDir = key + ":asc"; }
        else { state.sortDir = key + ":desc"; }
        renderOffers();
        $$("#offerTable th[data-sort]").forEach((h) => {
          h.classList.toggle("sorted", h.dataset.sort === key);
          h.classList.toggle("desc", h.dataset.sort === key && state.sortDir === key + ":desc");
        });
        return;
      }
    });

    $("#sortSelect").addEventListener("change", (e) => setSort(e.target.value));

    $("#savedBtn").addEventListener("click", () => {
      setCat(state.cat === "saved" ? "all" : "saved");
      document.getElementById("offers").scrollIntoView({ behavior: "smooth", block: "start" });
      if (state.cat === "saved") toast(state.saved.size ? `清单里有 ${state.saved.size} 个平台` : "清单还是空的");
      closeRail();
    });

    $("#resetFilter").addEventListener("click", () => {
      input.value = ""; state.q = ""; clearBtn.hidden = true;
      setCat("all"); setRegion("all");
    });

    // 视图 / 主题
    $("#themeBtn").addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      applyTheme(next);
      try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
    });

    // 移动端抽屉 / 筛选面板
    $("#railToggle").addEventListener("click", openRail);
    $("#filterBtn").addEventListener("click", openSheet);
    $("#sheetClose").addEventListener("click", closeSheet);
    $("#sheetDone").addEventListener("click", closeSheet);
    $("#sheetReset").addEventListener("click", () => { setCat("all"); setRegion("all"); setSort("weight"); });
    $("#sheetMask").addEventListener("click", () => { closeSheet(); closeRail(); });

    // 刷新
    $("#refreshBtn").addEventListener("click", () => loadLive(false));

    // 全部限时
    $("#allLimited").addEventListener("click", (e) => {
      e.preventDefault();
      setCat("limited");
      setSort("deadline");
      document.getElementById("offers").scrollIntoView({ behavior: "smooth", block: "start" });
    });

    // 键盘
    document.addEventListener("keydown", (e) => {
      const tag = (document.activeElement || {}).tagName;
      if (e.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA") {
        e.preventDefault();
        input.focus();
        input.select();
      }
      if (e.key === "Escape") {
        if (!$("#filterSheet").hidden) closeSheet();
        else if ($("#rail").classList.contains("open")) closeRail();
        else if (document.activeElement === input) input.blur();
      }
    });

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") loadLive(true);
    });
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "light" ? "#eef1f8" : "#080910");
  }

  /* ---------------- 实时数据 ---------------- */
  async function loadLive(silent) {
    const btn = $("#refreshBtn");
    if (btn) btn.classList.add("loading");
    try {
      const res = await fetch(DATA_URL + "?t=" + Date.now(), { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const json = await res.json();
      if (!json || !Array.isArray(json.offers)) throw new Error("bad payload");
      const prev = (DATA.meta && DATA.meta.generated_at) || "";
      const changed = prev !== (json.meta && json.meta.generated_at);
      DATA = json;
      setStatus(true);
      renderAll();
      if (!silent) toast(changed ? "已同步最新数据" : "数据已是最新");
      else if (changed) toast("检测到新数据，已自动更新");
      return true;
    } catch (err) {
      setStatus(false);
      if (!silent) toast("数据服务未启动，展示本地快照");
      return false;
    } finally {
      if (btn) btn.classList.remove("loading");
    }
  }

  /* ---------------- 启动 ---------------- */
  function init() {
    try {
      const th = localStorage.getItem(THEME_KEY);
      if (th) applyTheme(th);
      const saved = JSON.parse(localStorage.getItem(SAVED_KEY) || "[]");
      if (Array.isArray(saved)) state.saved = new Set(saved);
      const v = localStorage.getItem(VIEW_KEY);
      if (v === "table" || v === "grid") state.view = v;
    } catch (e) {}

    bind();
    initSpy();
    if (state.view === "table") setView("table"); else renderAll();
    setStatus(false);
    loadLive(true);
    setInterval(() => {
      if (document.visibilityState === "visible") loadLive(true);
    }, POLL_MS);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
