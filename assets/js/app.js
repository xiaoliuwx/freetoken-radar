/* =========================================================
   FreeToken Radar · 交互脚本
   ---------------------------------------------------------
   1. 优先 fetch data/offers.json（实时，配合 crawler/serve.py）
   2. 失败则回退到 data/offers.js 内嵌快照（file:// 双击也能看）
   3. 每 60s 轮询一次，数据变化自动重绘并提示
   ========================================================= */
(function () {
  "use strict";

  const DATA_URL = "data/offers.json";
  const POLL_MS = 60 * 1000;
  const EMBEDDED = window.__FREE_TOKEN_DATA__ || { offers: [], news: [], tips: [], meta: {} };

  let DATA = EMBEDDED;
  let mode = "snapshot"; // live | snapshot
  let filter = "all";
  let keyword = "";
  let sortBy = "weight";
  let pollTimer = null;
  let countdownTimer = null;

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const CAT_LABEL = {
    official: "长期免费",
    newbie: "新人赠送",
    limited: "限时活动",
    oss: "开源自部署",
  };

  const ICONS = {
    shield: "i-shield",
    clock: "i-clock",
    alert: "i-alert",
    key: "i-key",
    gauge: "i-gauge",
    refresh: "i-refresh",
  };

  /* ---------------- 工具 ---------------- */
  function esc(str) {
    return String(str == null ? "" : str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function toast(msg) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2400);
  }

  function fmtDate(d) {
    if (!d) return "--";
    return String(d).slice(5).replace("-", "/");
  }

  /* ---------------- 额度拆分 ---------------- */
  function splitQuota(q) {
    const s = String(q || "").trim();
    const m = s.match(/^([¥$€]?\s?[\d,.]+\s?(?:万|亿|k|K|M)?)\s*(.*)$/);
    if (!m) return { num: "", unit: s };
    return { num: m[1].replace(/\s/g, ""), unit: m[2] };
  }

  function quotaValue(q) {
    const s = String(q || "");
    const m = s.match(/([\d,.]+)\s*(万|亿|k|K|M)?/);
    if (!m) return -1;
    let n = parseFloat(m[1].replace(/,/g, ""));
    if (isNaN(n)) return -1;
    const u = m[2];
    if (u === "万") n *= 1e4;
    else if (u === "亿") n *= 1e8;
    else if (u === "k" || u === "K") n *= 1e3;
    else if (u === "M") n *= 1e6;
    return n;
  }

  function deadlineText(d) {
    const end = new Date(d + "T23:59:59+08:00").getTime();
    const diff = end - Date.now();
    if (isNaN(end)) return "";
    if (diff <= 0) return "已结束";
    const days = Math.floor(diff / 86400000);
    const hours = Math.floor((diff % 86400000) / 3600000);
    if (days >= 1) return "剩 " + days + " 天";
    if (hours >= 1) return "剩 " + hours + " 小时";
    return "即将截止";
  }

  /* ---------------- 渲染：卡片 ---------------- */
  function cardHTML(o, i) {
    const q = splitQuota(o.quotaNum);
    const badge = CAT_LABEL[o.category] || "免费";
    const tags = (o.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
    const dl = o.deadline
      ? `<span class="deadline" data-deadline="${esc(o.deadline)}">${esc(deadlineText(o.deadline))}</span>`
      : `<span>核验 ${esc(fmtDate(o.verified))}</span>`;

    return `
      <article class="card glass" style="--c1:${esc(o.c1)};--c2:${esc(o.c2)}" data-id="${esc(o.id)}">
        <div class="card-top">
          <span class="mono">${esc(o.mono || o.name.slice(0, 2))}</span>
          <div class="card-id">
            <h3 title="${esc(o.name)}">${esc(o.name)}</h3>
            <span class="region">${esc(o.region || "—")}</span>
          </div>
          <span class="badge badge-${esc(o.category)}">${esc(badge)}</span>
        </div>
        <div class="quota">
          ${q.num ? `<span class="q-num">${esc(q.num)}</span><span class="q-unit">${esc(q.unit)}</span>`
                  : `<span class="q-num">${esc(q.unit)}</span>`}
          <span class="q-label">${esc(o.quota || "")}</span>
        </div>
        <p class="detail">${esc(o.detail)}</p>
        <div class="tags">${tags}</div>
        <div class="card-foot">
          ${dl}
          <a class="go" href="${esc(o.url)}" target="_blank" rel="noopener noreferrer">
            立即领取<svg><use href="#i-link"/></svg>
          </a>
        </div>
      </article>`;
  }

  function currentOffers() {
    const kw = keyword.trim().toLowerCase();
    let list = (DATA.offers || []).filter((o) => {
      if (filter !== "all" && o.category !== filter) return false;
      if (!kw) return true;
      const hay = [o.name, o.brand, o.detail, o.quota, o.quotaNum, o.region, (o.tags || []).join(" ")]
        .join(" ")
        .toLowerCase();
      return hay.indexOf(kw) !== -1;
    });

    list = list.slice().sort((a, b) => {
      if (sortBy === "quota") return quotaValue(b.quotaNum) - quotaValue(a.quotaNum);
      if (sortBy === "verified") return String(b.verified || "").localeCompare(String(a.verified || ""));
      return (b.weight || 0) - (a.weight || 0);
    });
    return list;
  }

  function renderOffers() {
    const grid = $("#offerGrid");
    if (!grid) return;
    const list = currentOffers();
    grid.innerHTML = list.map(cardHTML).join("");
    $("#resultCount").textContent = `共 ${list.length} 个平台`;
    $("#emptyState").hidden = list.length !== 0;

    // 进场动画
    const io = "IntersectionObserver" in window
      ? new IntersectionObserver((entries, obs) => {
          entries.forEach((e) => {
            if (e.isIntersecting) {
              const idx = Number(e.target.dataset.idx || 0);
              e.target.style.transitionDelay = Math.min(idx, 8) * 45 + "ms";
              e.target.classList.add("in");
              obs.unobserve(e.target);
            }
          });
        }, { rootMargin: "0px 0px -40px 0px" })
      : null;

    $$(".card", grid).forEach((el, i) => {
      el.dataset.idx = i;
      if (io) io.observe(el);
      else el.classList.add("in");
    });

    bindCardTilt();
  }

  /* 桌面端：卡片跟随光斑 */
  function bindCardTilt() {
    if (window.matchMedia("(hover: none)").matches) return;
    $$(".card").forEach((card) => {
      card.addEventListener("pointermove", (e) => {
        const r = card.getBoundingClientRect();
        card.style.setProperty("--mx", ((e.clientX - r.left) / r.width) * 100 + "%");
        card.style.setProperty("--my", ((e.clientY - r.top) / r.height) * 100 + "%");
      });
    });
  }

  /* ---------------- 渲染：资讯 ---------------- */
  let newsExpanded = false;
  const NEWS_PAGE = 10;

  function renderNews() {
    const box = $("#timeline");
    if (!box) return;
    const all = DATA.news || [];
    const list = newsExpanded ? all : all.slice(0, NEWS_PAGE);

    box.innerHTML = list.map((n) => {
      const tag = n.tag || "资讯";
      return `
        <li class="tl-item">
          <span class="tl-date">${esc(n.date || "--")}</span>
          <span class="tl-dot"></span>
          <div class="tl-body">
            <a class="title" href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">
              ${esc(n.title)}<svg><use href="#i-link"/></svg>
            </a>
            ${n.summary ? `<p>${esc(n.summary)}</p>` : ""}
            <div class="tl-meta">
              <span class="tl-tag t-${esc(tag)}">${esc(tag)}</span>
              <span>来源 · ${esc(n.source || "公开资讯")}</span>
              ${n.live ? "<span>· 本次抓取</span>" : ""}
            </div>
          </div>
        </li>`;
    }).join("");

    if (all.length > NEWS_PAGE) {
      const rest = all.length - NEWS_PAGE;
      box.insertAdjacentHTML("beforeend", `
        <li class="tl-more">
          <button type="button" id="newsMore">
            ${newsExpanded ? "收起" : `展开全部 ${all.length} 条（还有 ${rest} 条）`}
          </button>
        </li>`);
      $("#newsMore").addEventListener("click", () => {
        newsExpanded = !newsExpanded;
        renderNews();
      });
    }

    $("#statNews").textContent = all.length;
    $("#statNews").dataset.count = all.length;
  }

  /* ---------------- 渲染：避坑 ---------------- */
  function renderTips() {
    const box = $("#tipsGrid");
    if (!box) return;
    box.innerHTML = (DATA.tips || []).map((t) => `
      <div class="tip glass reveal">
        <span class="tip-ic"><svg><use href="#${ICONS[t.icon] || "i-spark"}"/></svg></span>
        <div>
          <h3>${esc(t.title)}</h3>
          <p>${esc(t.text)}</p>
        </div>
      </div>`).join("");
  }

  /* ---------------- 状态 ---------------- */
  function setStatus(state, meta) {
    mode = state;
    const dot = $("#liveDot");
    const label = $("#liveLabel");
    const statusText = $("#statusText");
    const statusMeta = $("#statusMeta");

    if (state === "live") {
      if (dot) dot.classList.remove("is-offline");
      if (label) label.textContent = "实时同步中";
      if (statusText) statusText.textContent = "实时数据";
    } else {
      if (dot) dot.classList.add("is-offline");
      if (label) label.textContent = "本地快照模式";
      if (statusText) statusText.textContent = "本地快照";
    }
    if (meta) {
      metaBase = meta;
      paintMeta();
    }
  }

  function updateMeta() {
    const m = DATA.meta || {};
    const stats = m.stats || {};
    const time = m.generated_label || m.updated || "--";

    $("#liveTime").textContent = time;
    $("#footTime").textContent = time;
    $("#footSources").textContent = (m.sources && m.sources.length)
      ? m.sources.filter((s) => s.ok).length + "/" + m.sources.length + " 个源"
      : "内置快照";
    $("#statOffer").textContent = stats.total || (DATA.offers || []).length;
    $("#statFree").textContent = stats.official || 0;
    $("#statTotal").textContent = stats.total || (DATA.offers || []).length;
    metaBase = "更新于 " + String(time).slice(5);
    paintMeta();
  }

  let metaBase = "";
  let nextPollAt = 0;

  function paintMeta() {
    const el = $("#statusMeta");
    if (!el || !metaBase) return;
    if (mode !== "live") {
      el.textContent = metaBase;
      return;
    }
    const left = Math.max(0, Math.round((nextPollAt - Date.now()) / 1000));
    el.textContent = metaBase + " · " + left + "s 后刷新";
  }

  function startMetaTicker() {
    setInterval(paintMeta, 1000);
  }

  function render() {
    renderOffers();
    renderNews();
    renderTips();
    updateMeta();
    startCountdown();
    revealInit();
  }

  /* ---------------- 截止时间倒计时 ---------------- */
  function startCountdown() {
    clearInterval(countdownTimer);
    const tick = () => {
      $$("[data-deadline]").forEach((el) => {
        el.textContent = deadlineText(el.dataset.deadline);
      });
    };
    tick();
    countdownTimer = setInterval(tick, 60000);
  }

  /* ---------------- 滚动进场 ---------------- */
  function revealInit() {
    const els = $$(".reveal:not(.in)");
    if (!("IntersectionObserver" in window)) {
      els.forEach((el) => el.classList.add("in"));
      return;
    }
    const io = new IntersectionObserver((entries, obs) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          e.target.classList.add("in");
          obs.unobserve(e.target);
        }
      });
    }, { rootMargin: "0px 0px -60px 0px" });
    els.forEach((el) => io.observe(el));
  }

  /* ---------------- 实时拉取 ---------------- */
  async function loadLive(silent) {
    const btn = $("#refreshBtn");
    if (btn) btn.classList.add("loading");
    try {
      const res = await fetch(DATA_URL + "?t=" + Date.now(), { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const json = await res.json();
      if (!json || !Array.isArray(json.offers)) throw new Error("bad payload");

      const prev = (DATA.meta && DATA.meta.generated_at) || "";
      const next = (json.meta && json.meta.generated_at) || "";
      const changed = prev !== next;

      DATA = json;
      nextPollAt = Date.now() + POLL_MS;
      setStatus("live", "更新于 " + String(json.meta.generated_label || "").slice(5));
      render();

      if (!silent) {
        toast(changed ? "已同步最新数据" : "数据已是最新");
      } else if (changed) {
        toast("检测到新数据，已自动更新");
      }
      return true;
    } catch (err) {
      if (mode !== "snapshot") {
        setStatus("snapshot", "本地快照 · 启动 start.bat 可实时");
      }
      if (!silent) toast("无法连接数据服务，已展示本地快照");
      return false;
    } finally {
      if (btn) btn.classList.remove("loading");
    }
  }

  function schedulePoll() {
    clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      if (document.visibilityState === "visible") loadLive(true);
    }, POLL_MS);
  }

  /* ---------------- 事件绑定 ---------------- */
  function bindEvents() {
    // 搜索
    const input = $("#searchInput");
    const clearBtn = $("#clearSearch");
    let timer = null;
    input.addEventListener("input", () => {
      clearBtn.hidden = !input.value;
      clearTimeout(timer);
      timer = setTimeout(() => {
        keyword = input.value;
        renderOffers();
      }, 140);
    });
    clearBtn.addEventListener("click", () => {
      input.value = "";
      keyword = "";
      clearBtn.hidden = true;
      renderOffers();
      input.focus();
    });

    // 分类
    $("#filterChips").addEventListener("click", (e) => {
      const btn = e.target.closest(".chip-btn");
      if (!btn) return;
      $$(".chip-btn").forEach((b) => {
        b.classList.toggle("active", b === btn);
        b.setAttribute("aria-selected", b === btn ? "true" : "false");
      });
      filter = btn.dataset.filter;
      renderOffers();
    });

    // 排序
    $("#sortSelect").addEventListener("change", (e) => {
      sortBy = e.target.value;
      renderOffers();
    });

    // 空状态重置
    $("#resetFilter").addEventListener("click", () => {
      input.value = "";
      keyword = "";
      filter = "all";
      clearBtn.hidden = true;
      $$(".chip-btn").forEach((b) => {
        b.classList.toggle("active", b.dataset.filter === "all");
        b.setAttribute("aria-selected", b.dataset.filter === "all" ? "true" : "false");
      });
      renderOffers();
    });

    // 刷新
    $("#refreshBtn").addEventListener("click", () => loadLive(false));

    // 复制
    document.addEventListener("click", (e) => {
      const btn = e.target.closest(".copy-btn");
      if (!btn) return;
      const text = btn.dataset.copy || "";
      const done = () => {
        btn.classList.add("done");
        toast("已复制到剪贴板");
        setTimeout(() => btn.classList.remove("done"), 1600);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
      } else {
        fallbackCopy(text, done);
      }
    });

    // 主题
    $("#themeBtn").addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      applyTheme(next);
      try { localStorage.setItem("ftr-theme", next); } catch (e) {}
    });

    // 移动端菜单
    const menuBtn = $("#menuBtn");
    const navLinks = $("#navLinks");
    menuBtn.addEventListener("click", () => {
      const open = navLinks.classList.toggle("open");
      menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
      menuBtn.innerHTML = `<svg><use href="#${open ? "i-close" : "i-menu"}"/></svg>`;
    });
    navLinks.addEventListener("click", (e) => {
      if (e.target.tagName === "A") {
        navLinks.classList.remove("open");
        menuBtn.setAttribute("aria-expanded", "false");
        menuBtn.innerHTML = `<svg><use href="#i-menu"/></svg>`;
      }
    });

    // 回到顶部
    const toTop = $("#toTop");
    window.addEventListener("scroll", () => {
      toTop.classList.toggle("show", window.scrollY > 600);
    }, { passive: true });
    toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

    // 快捷键
    document.addEventListener("keydown", (e) => {
      const tag = (document.activeElement || {}).tagName;
      if (e.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA") {
        e.preventDefault();
        input.focus();
        input.scrollIntoView({ block: "center", behavior: "smooth" });
      }
      if (e.key === "Escape" && document.activeElement === input) {
        input.blur();
      }
    });

    // 页面可见性
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") loadLive(true);
    });
  }

  function fallbackCopy(text, done) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { toast("复制失败，请手动选择"); }
    document.body.removeChild(ta);
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "light" ? "#eaeefb" : "#07080f");
  }

  /* ---------------- 启动 ---------------- */
  function init() {
    try {
      const saved = localStorage.getItem("ftr-theme");
      if (saved) applyTheme(saved);
    } catch (e) {}

    bindEvents();
    render();
    startMetaTicker();
    loadLive(true).then((ok) => {
      if (!ok) {
        setStatus("snapshot", "本地快照 · 启动 start.bat 可实时");
        updateMeta();
      }
    });
    schedulePoll();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
