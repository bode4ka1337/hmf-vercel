/* HMF web client: Search / History / Settings / Rule34 + Details and Reader.
   Vanilla JS, no external libraries. The server keeps search sessions and
   settings; this file keeps only the API contract and UI state. */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const tg = window.Telegram && window.Telegram.WebApp;

  // Mirrors core.models.LANGUAGES (~70 codes).
  const LANGUAGES = [
    ["ru", "Russian"], ["en", "English"], ["uk", "Українська"], ["ja", "日本語"],
    ["ko", "한국어"], ["zh", "中文"], ["zh-hk", "中文 (HK)"], ["pl", "Polski"],
    ["es", "Español"], ["es-la", "Español (Latam)"], ["pt-br", "Português (BR)"],
    ["pt", "Português"], ["fr", "Français"], ["de", "Deutsch"], ["it", "Italiano"],
    ["tr", "Türkçe"], ["vi", "Tiếng Việt"], ["id", "Indonesia"], ["th", "ไทย"],
    ["ar", "العربية"], ["hi", "हिन्दी"], ["cs", "Čeština"], ["hu", "Magyar"],
    ["ro", "Română"], ["nl", "Nederlands"], ["sv", "Svenska"], ["fi", "Suomi"],
    ["bg", "Български"], ["el", "Ελληνικά"], ["fa", "فارسی"], ["he", "עברית"],
    ["ca", "Català"], ["tl", "Tagalog"], ["ms", "Bahasa Melayu"], ["bn", "বাংলা"],
    ["ta", "தமிழ்"], ["te", "తెలుగు"], ["mr", "मराठी"], ["ur", "اردو"],
    ["be", "Беларуская"], ["sr", "Српски"], ["hr", "Hrvatski"], ["sk", "Slovenčina"],
    ["sl", "Slovenščina"], ["lt", "Lietuvių"], ["lv", "Latviešu"], ["et", "Eesti"],
    ["da", "Dansk"], ["no", "Norsk"], ["is", "Íslenska"], ["ga", "Gaeilge"],
    ["cy", "Cymraeg"], ["eu", "Euskara"], ["gl", "Galego"], ["af", "Afrikaans"],
    ["sw", "Kiswahili"], ["am", "አማርኛ"], ["km", "ខ្មែរ"], ["lo", "ລາວ"],
    ["my", "မြန်မာ"], ["ne", "नेपाली"], ["si", "සිංහල"], ["ka", "ქართული"],
    ["hy", "Հայերեն"], ["az", "Azərbaycan"], ["kk", "Қазақша"], ["uz", "O'zbekcha"],
    ["mn", "Монгол"],
  ];

  const TAG_GROUPS = [
    ["genre", "Genres"], ["theme", "Themes"],
    ["format", "Format"], ["content", "Labels"],
  ];
  const SIMILAR_GROUPS = ["genre", "theme", "content"];

  // ------------------------------------------------------------------ utils

  const node = (tag, className, text) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  };

  const button = (label, className, onClick) => {
    const el = node("button", className, label);
    el.type = "button";
    if (onClick) el.addEventListener("click", onClick);
    return el;
  };

  const coverSrc = (url) => (url ? "/api/cover?url=" + encodeURIComponent(url) : "");
  const mediaSrc = (url) => (url ? "/api/media?url=" + encodeURIComponent(url) : "");

  const openUrl = (url) => {
    if (!url) return;
    if (tg && typeof tg.openUrl === "function") {
      try { tg.openUrl(url); return; } catch (e) { /* fall through */ }
    }
    window.open(url, "_blank", "noopener");
  };

  const copyText = async (text) => {
    if (!text) return;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const tmp = node("textarea");
        tmp.value = text;
        document.body.appendChild(tmp);
        tmp.select();
        document.execCommand("copy");
        tmp.remove();
      }
      showToast("Link copied");
    } catch (e) {
      showToast("Could not copy the link");
    }
  };

  let toastTimer = null;
  const showToast = (message, duration = 4200) => {
    const el = $("toast");
    if (!el || !message) return;
    el.textContent = String(message);
    el.classList.remove("hidden");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), duration);
  };

  const setStatus = (text) => {
    const value = text || "";
    const el = $("status");
    if (el) el.textContent = value;
    const bar = $("appStatusBar");
    if (bar) bar.textContent = value || "Ready";
  };
  const setStatusBottom = (text) => { const el = $("statusBottom"); if (el) el.textContent = text || ""; };
  const sourceLabel = (item) => {
    if (!item) return "";
    const src = state.sources.find((s) => s.key === item.source || s.key === item.source_key);
    return item.source_name || item.source || (src && (src.name || src.url)) || "";
  };
  const languageLabel = (code) =>
    (LANGUAGES.find((pair) => pair[0] === code) || [code, String(code || "").toUpperCase()])[1];

  const getClientId = () => {
    let id = "";
    try { id = localStorage.getItem("hmf_client_id") || ""; } catch (e) { /* noop */ }
    if (!id) {
      try { id = crypto.randomUUID(); } catch (e) { /* noop */ }
      if (!id) id = "web-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
      try { localStorage.setItem("hmf_client_id", id); } catch (e) { /* noop */ }
    }
    return id;
  };

  // Some actions return {error} with HTTP 200; their handlers display it.
  const api = async (body) => {
    let resp;
    try {
      resp = await fetch("/api/app", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      throw new Error("Network error: " + (e && e.message ? e.message : "request failed"));
    }
    let data;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (!data || typeof data !== "object") {
      throw new Error(resp.ok ? "Invalid server response" : "HTTP " + resp.status);
    }
    if (data.error && !resp.ok) throw new Error(String(data.error));
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return data;
  };

  // Auth responses need their status/body intact (bad password is a normal UI
  // message, not a thrown request error), so they use a separate small helper.
  const authApi = async (action, extra = {}) => {
    let resp;
    try {
      resp = await fetch("/api/app", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...extra }),
      });
    } catch (e) {
      return { ok: false, status: 0, data: { error: "Network error: " + (e?.message || "request failed") } };
    }
    let data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    const result = data && typeof data === "object" ? data : {};
    return { ok: resp.ok && !result.error, status: resp.status, data: result };
  };

  // Kept from the earlier client; also canonicalises "full colored" → "full color".
  const normalizeTagText = (value) =>
    String(value || "")
      .toLowerCase()
      .replace(/[-_]+/g, " ")
      .replace(/[^0-9a-zа-яёіїєґ ]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\bfull colored\b/g, "full color");

  const tagTextMatches = (candidate, needle) => {
    if (!needle) return true;
    const haystack = normalizeTagText(candidate);
    const query = normalizeTagText(needle);
    if (!query) return true;
    if (!haystack) return false;
    if (haystack.includes(query)) return true;
    const hayTokens = haystack.split(" ").filter(Boolean);
    return query.split(" ").filter(Boolean).every((need) =>
      hayTokens.some(
        (token) =>
          token === need ||
          (need.length >= 3 && token.startsWith(need)) ||
          (token.length >= 3 && need.startsWith(token))
      )
    );
  };

  const bindLongPress = (el, action) => {
    let timer = null;
    let start = null;
    let moved = false;
    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };
    const reset = () => { clear(); start = null; };

    el.addEventListener("pointerdown", (e) => {
      if (e.button && e.button !== 0) return;
      clear();
      moved = false;
      delete el.dataset.noClick;
      start = { x: e.clientX, y: e.clientY };
      timer = setTimeout(() => {
        timer = null;
        if (moved) return;
        el.dataset.longPressed = "1";
        action(e);
      }, 500);
    }, { passive: true });

    // Any deliberate drag is a scroll, not a long press or a tap.
    el.addEventListener("pointermove", (e) => {
      if (!start || moved) return;
      const dx = e.clientX - start.x;
      const dy = e.clientY - start.y;
      if (Math.abs(dx) > 9 || Math.abs(dy) > 9) {
        moved = true;
        clear();
        el.dataset.noClick = "1";
      }
    }, { passive: true });

    // Touch scrollers often fire touchmove/pointercancel instead of pointermove.
    el.addEventListener("touchmove", () => {
      if (!timer) return;
      moved = true;
      clear();
      el.dataset.noClick = "1";
    }, { passive: true });

    ["pointerup", "pointerleave", "pointercancel"].forEach((type) =>
      el.addEventListener(type, () => {
        if (moved || type === "pointercancel") el.dataset.noClick = "1";
        reset();
      }, { passive: true })
    );

    el.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      clear();
      el.dataset.longPressed = "1";
      action(e);
    });
  };

  const hideContextMenu = () => {
    const menu = $("contextMenu");
    if (menu) menu.classList.add("hidden");
  };

  const showContextMenu = (x, y, entries) => {
    const menu = $("contextMenu");
    if (!menu || !entries.length) return;
    menu.innerHTML = "";
    for (const entry of entries) {
      const item = button(entry.label, "", () => { hideContextMenu(); entry.action(); });
      item.setAttribute("role", "menuitem");
      menu.appendChild(item);
    }
    menu.classList.remove("hidden");
    const width = menu.offsetWidth || 200;
    const height = menu.offsetHeight || 100;
    menu.style.left = Math.max(8, Math.min(x, window.innerWidth - width - 8)) + "px";
    menu.style.top = Math.max(8, Math.min(y, window.innerHeight - height - 8)) + "px";
  };

  // ------------------------------------------------------------------ state

  const state = {
    sources: [],
    enabledSources: [],
    userId: 0,
    auth: { authenticated: false, username: "", user_id: "", error: "" },
    admin: { authenticated: false, accounts: [], error: "" },
    view: "search",
    lastTab: "search",

    tags: [],
    excluded: [],
    favoriteTags: [],
    allTags: [],
    tagMode: "AND",
    languages: [],
    sort: "relevance",
    block_gay: true,
    hide_read_in_search: false,
    interface_language: "en",
    historyCount: 0,
    readKeys: new Set(),

    items: [],
    hasMore: false,
    found: 0,
    loadingMore: false,
    searchGen: 0,
    searchPayload: null,
    authorFilter: "",

    history: [],
    details: null,
    detailsGen: 0,

    reader: {
      open: false,
      detail: null,
      chapters: [],
      index: -1,
      urls: [],
      saver: false,
      gen: 0,
      page: 0,
    },

    rule34_user_id: "",
    rule34_api_key: "",
    rule34_ai_generated: true,
    rule34_videos: true,
    rule34_images: true,
    r34: {
      query: "", items: [], pid: 0, hasMore: false,
      loading: false, viewerIndex: -1, gen: 0,
    },
  };

  // --------------------------------------------------------------- settings

  // The radio row and the legacy select share one state value.
  const syncTagModeUI = () => {
    if ($("tagMode")) $("tagMode").value = state.tagMode;
    document.querySelectorAll('input[name="tagModeRadio"]').forEach((radio) => {
      radio.checked = radio.value === state.tagMode;
    });
  };

  const applySettings = (data) => {
    if (!data) return;
    state.block_gay = data.block_gay !== false;
    state.hide_read_in_search = !!data.hide_read_in_search;
    state.interface_language = data.interface_language || "en";
    state.tags = (data.tags || []).map(String);
    state.excluded = (data.excluded || []).map(String);
    state.favoriteTags = (data.favorite_tags || []).map(String);
    state.tagMode = data.tag_mode === "OR" ? "OR" : "AND";
    state.languages = (data.languages || []).map(String);
    state.sort = data.sort || "relevance";
    state.rule34_user_id = data.rule34_user_id || "";
    state.rule34_api_key = data.rule34_api_key || "";
    state.rule34_ai_generated = data.rule34_ai_generated !== false;
    state.rule34_videos = data.rule34_videos !== false;
    state.rule34_images = data.rule34_images !== false;
    if (typeof data.history_count === "number") state.historyCount = data.history_count;
    if (Array.isArray(data.sources) && data.sources.length && state.sources.length) {
      state.enabledSources = data.sources.filter((key) =>
        state.sources.some((source) => source.key === key)
      );
    }
    syncTagModeUI();
    if ($("sortSelect")) $("sortSelect").value = state.sort;
    if ($("hideReadToggle")) $("hideReadToggle").checked = state.hide_read_in_search;
  };

  const saveSettings = async () => {
    const res = await api({
      action: "settings",
      user_id: state.userId,
      sources: state.enabledSources,
      block_gay: state.block_gay,
      hide_read_in_search: state.hide_read_in_search,
      tags: state.tags,
      excluded: state.excluded,
      favorite_tags: state.favoriteTags,
      tag_mode: state.tagMode,
      languages: state.languages,
      sort: state.sort,
      rule34_user_id: state.rule34_user_id,
      rule34_api_key: state.rule34_api_key,
      rule34_ai_generated: state.rule34_ai_generated,
      rule34_videos: state.rule34_videos,
      rule34_images: state.rule34_images,
      interface_language: state.interface_language,
    });
    if (res && !res.error) applySettings(res);
    return res;
  };

  // ------------------------------------------------------------------ views

  const VIEWS = {
    search: "Search",
    history: "History",
    settings: "Settings",
    rule34: "Rule34",
    details: "Details",
  };

  const closeFilterSheet = () => {
    $("filterPanel")?.classList.remove("open");
    $("filterOverlay")?.classList.add("hidden");
  };

  const switchView = (name) => {
    if (VIEWS[name] && name !== "details") state.lastTab = name;
    state.view = name;
    document.body.dataset.view = name;
    if (name === "details") {
      const win = $("detailsSection");
      if (win) { win.style.left = ""; win.style.top = ""; win.style.transform = ""; }
    }
    const sections = {
      search: "searchSection", history: "historySection",
      settings: "settingsSection", rule34: "rule34Section",
      details: "detailsSection",
    };
    Object.entries(sections).forEach(([view, id]) => {
      $(id)?.classList.toggle("hidden", view !== name);
    });
    document.querySelectorAll(".tabBtn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === name);
    });
    const header = $("screenTitle");
    const subtitle = $("screenSubtitle");
    if (header) header.textContent = VIEWS[name] || "HMF";
    if (subtitle) {
      subtitle.textContent = name === "details"
        ? (state.details?.title || "Title details")
        : name === "search" ? "Manga finder" : "HMF web client";
    }
    if (name === "details") {
      const winTitle = $("detailsWinTitle");
      if (winTitle) winTitle.textContent = state.details?.title || "Title details";
    }
    $("backBtn")?.classList.toggle("hidden", name !== "details");
    $("filtersToggle")?.classList.toggle("hidden", name !== "search");
    closeFilterSheet();
    hideContextMenu();
    if (name === "history") refreshHistory();
    if (name === "settings") renderSettings();
    if (name === "rule34") renderRule34();
  };

  // ----------------------------------------------------------------- search

  const buildSearchPayload = () => ({
    action: "search",
    user_id: state.userId,
    sources: state.enabledSources.length ? state.enabledSources : undefined,
    title: ($("titleInput")?.value || "").trim(),
    author: state.authorFilter || "",
    tags: state.tags,
    excluded: state.excluded,
    block_gay: state.block_gay,
    tag_mode: state.tagMode,
    languages: state.languages,
    sort: state.sort,
    limit: 20,
  });

  const doSearch = async () => {
    const gen = ++state.searchGen;
    const payload = buildSearchPayload();
    state.searchPayload = payload;
    setStatus("Searching…");
    setStatusBottom("");
    try {
      const res = await api(payload);
      if (gen !== state.searchGen) return;
      if (res.error) { setStatus(res.error); return; }
      state.items = res.items || [];
      state.hasMore = !!res.has_more;
      state.found = typeof res.total === "number"
        ? res.total : state.items.length + (state.hasMore ? 1 : 0);
      renderResults();
      const problems = Object.entries(res.states || {})
        .filter(([, info]) => info && info.error)
        .map(([key, info]) => `${key}: ${info.error}`);
      if (problems.length) setStatus(problems.join(" · "));
      else setStatus(state.items.length ? "" : "Nothing found. Try other filters.");
    } catch (e) {
      if (gen === state.searchGen) setStatus("Search failed: " + (e.message || e));
    }
  };

  const loadMore = async () => {
    if (state.view !== "search" || state.loadingMore || !state.hasMore) return;
    const payload = state.searchPayload || buildSearchPayload();
    const gen = state.searchGen;
    state.loadingMore = true;
    setStatusBottom("Loading more…");
    $("loadMoreBtn")?.classList.add("hidden");
    try {
      const res = await api(payload); // same payload: the server keeps the session
      if (gen !== state.searchGen) return;
      if (res.error) { setStatusBottom(res.error); return; }
      const seen = new Set(state.items.map((item) => item.key));
      const fresh = (res.items || []).filter((item) => !seen.has(item.key));
      state.items = state.items.concat(fresh);
      state.hasMore = !!res.has_more;
      if (typeof res.total === "number") state.found = res.total;
      renderResults();
    } catch (e) {
      if (gen === state.searchGen) setStatusBottom("Could not load more: " + (e.message || e));
    } finally {
      state.loadingMore = false;
      if (gen === state.searchGen) $("loadMoreBtn")?.classList.toggle("hidden", !state.hasMore);
    }
  };

  const resetFilters = () => {
    $("titleInput").value = "";
    state.authorFilter = "";
    state.tags = [];
    state.excluded = [];
    state.tagMode = "AND";
    state.languages = [];
    state.sort = "relevance";
    $("tagFilter").value = "";
    renderAuthorChip();
    renderTagList();
    renderChips();
    renderLangPanel();
    updateLangToggle();
    syncTagModeUI();
    if ($("sortSelect")) $("sortSelect").value = state.sort;
    saveSettings().catch((e) => showToast("Could not save filters: " + e.message));
  };

  const renderAuthorChip = () => {
    const wrap = $("authorChipWrap");
    const chip = $("authorChip");
    if (!wrap || !chip) return;
    wrap.classList.toggle("hidden", !state.authorFilter);
    chip.textContent = state.authorFilter ? `Author: ${state.authorFilter}  ✕` : "";
  };

  const searchByAuthor = (item) => {
    const author = String(item?.author_label || "").split(",")[0].trim();
    if (!author) { showToast("Author is unknown"); return; }
    $("titleInput").value = "";
    state.authorFilter = author;
    renderAuthorChip();
    switchView("search");
    doSearch();
  };

  const tagIdsFromNames = (names) => {
    const result = [];
    for (const name of names || []) {
      const found = state.allTags.find((tag) =>
        tagTextMatches(tag.name, name) || tagTextMatches(tag.id, name)
      );
      const id = found ? found.id : String(name || "");
      if (id && !result.includes(id)) result.push(id);
    }
    return result;
  };

  const searchSimilar = (item) => {
    let tags = (item?.tags || [])
      .filter((tag) => SIMILAR_GROUPS.includes(tag.group))
      .map((tag) => tag.id)
      .filter(Boolean);
    if (!tags.length) tags = tagIdsFromNames(item?.tag_names || []).slice(0, 14);
    $("titleInput").value = "";
    state.authorFilter = "";
    state.excluded = [];
    state.tagMode = "OR";
    if (tags.length) {
      state.tags = tags.slice(0, 14);
    } else {
      state.tags = [];
      $("titleInput").value = item?.title || "";
    }
    renderAuthorChip();
    renderTagList();
    renderChips();
    syncTagModeUI();
    saveSettings().catch(() => {});
    switchView("search");
    doSearch();
  };

  const searchByTag = (tag) => {
    if (!tag) return;
    const id = tag.id || tagIdsFromNames([tag.name])[0];
    state.tags = id ? [id] : [];
    state.excluded = [];
    state.authorFilter = "";
    $("titleInput").value = "";
    if (!id) $("titleInput").value = tag.name || "";
    renderAuthorChip();
    renderTagList();
    renderChips();
    saveSettings().catch(() => {});
    switchView("search");
    doSearch();
  };

  // ------------------------------------------------------------ tag browser

  const groupedTags = () => {
    const favorites = new Set(state.favoriteTags);
    const needle = normalizeTagText($("tagFilter")?.value || "");
    const groupFilter = $("tagGroup")?.value || "";
    const found = (state.allTags || []).filter((tag) =>
      !needle || tagTextMatches(tag.name, needle) || tagTextMatches(tag.id, needle)
    );
    const matches = groupFilter
      ? found.filter((tag) => (tag.group || "theme") === groupFilter)
      : found;
    const fav = matches.filter((tag) => favorites.has(tag.id));
    const groups = TAG_GROUPS.map(([key, title]) => [
      title,
      matches.filter((tag) => !favorites.has(tag.id) && (tag.group || "theme") === key),
    ]);
    const known = new Set(TAG_GROUPS.map(([key]) => key));
    const other = matches.filter((tag) => !favorites.has(tag.id) && !known.has(tag.group));
    if (other.length) groups.push(["Other", other]);
    return { fav, groups, matches };
  };

  const makeTagRow = (tag) => {
    const selected = state.tags.includes(tag.id);
    const excluded = state.excluded.includes(tag.id);
    const favorite = state.favoriteTags.includes(tag.id);
    const row = node("div", "tagRow" + (selected ? " tagActive" : "") + (excluded ? " tagExcluded" : ""));
    const label = button(tag.name || tag.id, "tagLabel", () => {
      if (row.dataset.longPressed) { delete row.dataset.longPressed; return; }
      if (row.dataset.noClick) { delete row.dataset.noClick; return; }
      if (excluded) {
        state.excluded = state.excluded.filter((id) => id !== tag.id);
        if (!state.tags.includes(tag.id)) state.tags.push(tag.id);
      } else if (selected) {
        state.tags = state.tags.filter((id) => id !== tag.id);
      } else {
        state.tags.push(tag.id);
      }
      saveSettings().catch(() => {});
      renderTagList();
      renderChips();
    });
    const star = button(favorite ? "★" : "☆", "tagStar" + (favorite ? " tagStarActive" : ""), (e) => {
      e.stopPropagation();
      if (row.dataset.longPressed) { delete row.dataset.longPressed; return; }
      if (row.dataset.noClick) { delete row.dataset.noClick; return; }
      if (favorite) state.favoriteTags = state.favoriteTags.filter((id) => id !== tag.id);
      else state.favoriteTags.push(tag.id);
      saveSettings().catch(() => {});
      renderTagList();
    });
    star.title = favorite ? "Remove from favorites" : "Add to favorites";
    bindLongPress(row, (e) => {
      showContextMenu(e.clientX || 100, e.clientY || 100, [
        {
          label: selected ? "Remove inclusion" : "Include tag",
          action: () => {
            state.excluded = state.excluded.filter((id) => id !== tag.id);
            state.tags = selected ? state.tags.filter((id) => id !== tag.id) : state.tags.concat(tag.id);
            saveSettings().catch(() => {});
            renderTagList(); renderChips();
          },
        },
        {
          label: favorite ? "Unfavorite" : "Favorite",
          action: () => {
            state.favoriteTags = favorite
              ? state.favoriteTags.filter((id) => id !== tag.id)
              : state.favoriteTags.concat(tag.id);
            saveSettings().catch(() => {});
            renderTagList();
          },
        },
        {
          label: excluded ? "Remove exclusion" : "Exclude tag",
          action: () => {
            state.tags = state.tags.filter((id) => id !== tag.id);
            state.excluded = excluded
              ? state.excluded.filter((id) => id !== tag.id)
              : state.excluded.concat(tag.id);
            saveSettings().catch(() => {});
            renderTagList(); renderChips();
          },
        },
      ]);
    });
    row.appendChild(label);
    row.appendChild(star);
    return row;
  };

  const renderTagList = () => {
    const list = $("tagList");
    if (!list) return;
    list.innerHTML = "";
    const { fav, groups, matches } = groupedTags();
    const addGroup = (title, tags) => {
      if (!tags.length) return;
      list.appendChild(node("div", "tagGroupTitle", `${title} · ${tags.length}`));
      for (const tag of tags) list.appendChild(makeTagRow(tag));
    };
    addGroup("★ Favorites", fav);
    groups.forEach(([title, tags]) => addGroup(title, tags));
    if (!matches.length) list.appendChild(node("div", "status", "No tags match the filter."));
    const count = $("selectedCount");
    if (count) count.textContent = `${state.tags.length + state.excluded.length} selected`;
  };

  const renderChips = () => {
    const box = $("selectedTags");
    const excludedBox = $("excludedTags");
    if (!box && !excludedBox) return;
    if (box) box.innerHTML = "";
    if (excludedBox) excludedBox.innerHTML = "";
    const excludedCount = $("excludedCount");
    if (excludedCount) excludedCount.textContent = String(state.excluded.length);
    const badge = $("filterBadge");
    if (badge) {
      const total = state.tags.length + state.excluded.length;
      badge.textContent = String(total);
      badge.classList.toggle("hidden", total === 0);
    }
    const ids = state.tags.concat(state.excluded.filter((id) => !state.tags.includes(id)));
    for (const id of ids) {
      const tag = state.allTags.find((t) => String(t.id) === String(id));
      const excluded = state.excluded.includes(id);
      const chip = button(tag ? (tag.name || tag.id) : id, "chip" + (excluded ? " chipExcluded" : ""), async () => {
        if (excluded) {
          state.excluded = state.excluded.filter((item) => item !== id);
          state.tags = state.tags.filter((item) => item !== id);
        } else {
          state.tags = state.tags.filter((item) => item !== id);
          state.excluded.push(id);
        }
        try { await saveSettings(); } catch (e) { /* noop */ }
        renderChips(); renderTagList();
      });
      chip.title = excluded ? "Click to remove exclusion" : "Click to exclude";
      (excluded ? excludedBox || box : box || excludedBox).appendChild(chip);
    }
  };

  const updateLangToggle = () => {
    const btn = $("langToggle");
    if (!btn) return;
    if (!state.languages.length) {
      btn.textContent = "All languages";
      btn.classList.remove("langActive");
      return;
    }
    const names = state.languages.map(languageLabel);
    btn.textContent = names.length <= 2 ? names.join(", ") : `${names.length} languages`;
    btn.classList.add("langActive");
  };

  const renderLangPanel = () => {
    const panel = $("langPanel");
    if (!panel) return;
    panel.innerHTML = "";
    for (const [code, name] of LANGUAGES) {
      const row = node("label", "langRow");
      const cb = node("input");
      cb.type = "checkbox";
      cb.checked = state.languages.includes(code);
      cb.addEventListener("change", async () => {
        state.languages = cb.checked
          ? state.languages.concat(code).filter((v, i, arr) => arr.indexOf(v) === i)
          : state.languages.filter((item) => item !== code);
        try { await saveSettings(); } catch (e) { /* noop */ }
        updateLangToggle();
      });
      row.appendChild(cb);
      row.appendChild(document.createTextNode(name));
      panel.appendChild(row);
    }
  };

  // ----------------------------------------------------------------- cards

  const markRead = async (item) => {
    if (!item) return;
    const key = item.key || `${item.source || ""}:${item.id || ""}`;
    state.readKeys.add(key);
    try {
      const res = await api({ action: "history_add", user_id: state.userId, item });
      if (res && typeof res.history_count === "number") state.historyCount = res.history_count;
      renderResults();
    } catch (e) {
      // Reading history is best effort.
    }
  };

  const cardContextMenu = (item, x, y) => {
    showContextMenu(x, y, [
      { label: "Open details", action: () => openDetails(item) },
      { label: "Open on site", action: () => { markRead(item); openUrl(item.url); } },
      { label: "Copy link", action: () => copyText(item.url) },
      { label: "Similar", action: () => searchSimilar(item) },
      { label: "Same author", action: () => searchByAuthor(item) },
      { label: "Mark as read", action: () => markRead(item) },
    ]);
  };

  const makeCard = (item, options = {}) => {
    const { r34 = false } = options;
    const card = node("div", "card" + (r34 ? " r34Card" : ""));
    const cardKey = item.key || `${item.source || ""}:${item.id || ""}`;
    card.dataset.read = !r34 && state.readKeys.has(cardKey) ? "true" : "false";
    const coverWrap = node("div", "cardCoverWrap");
    const img = node("img", "cover");
    img.src = coverSrc(r34 ? (item.preview_url || item.file_url) : item.cover_url);
    img.alt = r34 ? (item.tags || "Rule34") : (item.title || "Cover");
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    coverWrap.appendChild(img);
    if (r34 && (item.media_type === "video" || item.media_type === "gif")) {
      coverWrap.appendChild(node("div", "r34Badge", item.media_type === "video" ? "VIDEO" : "GIF"));
    } else if (!r34) {
      if (item.source_name || item.source) {
        coverWrap.appendChild(node("div", "badge", sourceLabel(item)));
      }
      if (state.readKeys.has(cardKey)) coverWrap.appendChild(node("div", "badgeRead", "READ"));
    }
    card.appendChild(coverWrap);

    if (!r34) {
      const body = node("div", "cardBody");
      body.appendChild(node("div", "cardTitle", item.title || "Untitled"));
      body.appendChild(node("div", "cardSource", item.author_label || sourceLabel(item)));
      const badges = node("div", "cardBadges");
      const parts = [];
      if (item.page_count) parts.push(`${item.page_count} p.`);
      if (item.year) parts.push(String(item.year));
      if (item.content_rating) parts.push(String(item.content_rating));
      if ((item.languages || []).length) parts.push(item.languages.slice(0, 2).map(languageLabel).join(", "));
      parts.forEach((text) => badges.appendChild(node("span", "", text)));
      if (parts.length) body.appendChild(badges);
      card.appendChild(body);
      const actions = node("div", "cardActions");
      actions.appendChild(button("Open", "btn small", (e) => {
        e.stopPropagation(); openDetails(item);
      }));
      actions.appendChild(button("On site", "btn small ghost", (e) => {
        e.stopPropagation(); markRead(item); openUrl(item.url);
      }));
      card.appendChild(actions);
    }

    return card;
  };

  const renderResults = () => {
    const grid = $("grid");
    if (!grid) return;
    grid.innerHTML = "";
    for (const item of state.items) {
      const card = makeCard(item);
      card.addEventListener("click", () => {
        if (card.dataset.longPressed) { delete card.dataset.longPressed; return; }
        if (card.dataset.noClick) { delete card.dataset.noClick; return; }
        openDetails(item);
      });
      bindLongPress(card, (e) => cardContextMenu(item, e.clientX || 100, e.clientY || 100));
      grid.appendChild(card);
    }
    const shown = state.items.length;
    const found = state.found || shown;
    const foundText = `${found}${state.hasMore ? "+" : ""}`;
    const counter = $("foundCounter");
    if (counter) counter.textContent = shown ? `Found ${foundText} · Shown ${shown}` : "";
    $("loadMoreBtn")?.classList.toggle("hidden", !state.hasMore);
    if (!shown && !state.hasMore) setStatusBottom("");
  };

  // ---------------------------------------------------------------- history

  const formatReadAt = (value) => {
    if (!value) return "";
    const date = typeof value === "number"
      ? new Date(value < 1e12 ? value * 1000 : value)
      : new Date(value);
    return isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  };

  const refreshHistory = async () => {
    const list = $("historyList");
    if (list) list.innerHTML = "";
    try {
      const res = await api({ action: "history", user_id: state.userId });
      if (res.error) { $("historyStatus").textContent = res.error; return; }
      state.history = res.history || [];
      state.readKeys = new Set(state.history.map((item) => item.key));
      renderHistory();
    } catch (e) {
      $("historyStatus").textContent = "Could not load history: " + (e.message || e);
    }
  };

  const renderHistory = () => {
    const list = $("historyList");
    if (!list) return;
    list.innerHTML = "";
    if (!state.history.length) {
      list.appendChild(node("div", "status", "History is empty. Open a title and it will appear here."));
      return;
    }
    for (const item of state.history) {
      const row = node("div", "historyRow");
      const main = node("div", "historyMain");
      const img = node("img");
      img.src = coverSrc(item.cover_url);
      img.alt = item.title || "";
      img.loading = "lazy";
      const meta = node("div", "historyMeta");
      meta.appendChild(node("div", "historyTitle", item.title || "Untitled"));
      const sub = [
        sourceLabel(item),
        (item.languages || []).map(languageLabel).join(", "),
        item.author_label,
        formatReadAt(item.read_at),
      ].filter(Boolean).join(" · ");
      meta.appendChild(node("div", "historySub", sub));
      main.appendChild(img);
      main.appendChild(meta);
      main.addEventListener("click", () => openDetails(item));
      row.appendChild(main);
      const actions = node("div", "historyActions");
      actions.appendChild(button("Details", "btn small", () => openDetails(item)));
      actions.appendChild(button("Same author", "btn small", () => searchByAuthor(item)));
      actions.appendChild(button("Similar", "btn small", () => searchSimilar(item)));
      actions.appendChild(button("On site", "btn small", () => openUrl(item.url)));
      actions.appendChild(button("Remove", "btn small danger", async () => {
        try {
          await api({ action: "history_remove", user_id: state.userId, key: item.key });
          await refreshHistory();
        } catch (e) { showToast("Could not remove the entry"); }
      }));
      row.appendChild(actions);
      list.appendChild(row);
    }
  };

  // ---------------------------------------------------------------- details

  const openDetails = async (item) => {
    if (!item) return;
    await markRead(item);
    const gen = ++state.detailsGen;
    state.details = null;
    switchView("details");
    const winTitle = $("detailsWinTitle");
    if (winTitle) winTitle.textContent = item.title || "Title details";
    const body = $("detailsBody");
    body.innerHTML = "";
    body.appendChild(node("div", "status", "Loading details…"));
    try {
      const res = await api({
        action: "details",
        user_id: state.userId,
        source: item.source,
        id: item.id,
        title: item.title,
      });
      if (gen !== state.detailsGen) return;
      // `details` can include a soft error (chapters/statistics failed) while
      // still carrying usable metadata — show those details and warn once.
      if (res.error && !res.title) {
        body.innerHTML = "";
        body.appendChild(node("div", "errorBox", res.error));
        return;
      }
      state.details = res;
      renderDetails(res);
      if (res.error) showToast(String(res.error).slice(0, 180));
    } catch (e) {
      if (gen !== state.detailsGen) return;
      body.innerHTML = "";
      body.appendChild(node("div", "errorBox", "Could not load details: " + (e.message || e)));
    }
  };

  const renderDescription = (text) => {
    const wrap = node("div", "description");
    if (!text) { wrap.appendChild(node("p", "muted", "Description is missing.")); return wrap; }
    const escaped = String(text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>')
      .replace(/\n{2,}/g, "</p><p>")
      .replace(/\n/g, "<br>");
    wrap.innerHTML = "<p>" + escaped + "</p>";
    return wrap;
  };

  const statsText = (stats) => {
    if (!stats) return "";
    const parts = [];
    if (stats.rating) parts.push(`★ ${Number(stats.rating).toFixed(2)}`);
    if (stats.rating_count) parts.push(`${stats.rating_count} ratings`);
    if (stats.follows) parts.push(`${stats.follows} follows`);
    if (stats.comments) parts.push(`${stats.comments} comments`);
    return parts.join(" · ");
  };

  const chapterColumns = (detail) => {
    const gallery = detail.gallery || (detail.chapters || []).some((ch) => ch.gallery);
    return gallery
      ? ["Gallery", "Volume", "Title", "Language", "Pages", "Source"]
      : ["Chapter", "Volume", "Title", "Language", "Pages", "Group"];
  };

  const renderChapterRows = (detail, filter) => {
    const list = $("chapterRows");
    if (!list) return;
    list.innerHTML = "";
    const columns = chapterColumns(detail);
    const head = $("chapterHead");
    if (head) {
      head.innerHTML = "";
      columns.forEach((name) => head.appendChild(node("span", "", name)));
    }
    const chapters = (detail.chapters || []).filter((ch) => !filter || ch.language === filter);
    if (!chapters.length) {
      list.appendChild(node("div", "chapterEmpty", "No chapters for this language."));
      return;
    }
    chapters.forEach((ch) => {
      const values = [
        ch.label || ch.title || "chapter",
        ch.volume || "—",
        ch.title || "—",
        ch.language ? languageLabel(ch.language) : "—",
        ch.page_total || ch.pages || "—",
        ch.group || (ch.external_url ? "external" : "—"),
      ];
      const row = button("", "chapterRow", () => {
        if (ch.external_url) { openUrl(ch.external_url); return; }
        if (ch.downloadable === false && !(ch.page_total || ch.pages)) {
          showToast("This chapter has no readable pages");
          return;
        }
        // The row index is relative to the filtered list; the reader works
        // with the full chapter list, so resolve the real index first.
        const idx = (detail.chapters || []).findIndex(
          (item) => item === ch || (ch.id && item.id === ch.id)
        );
        openReader(idx >= 0 ? idx : 0);
      });
      values.forEach((value, index) => {
        const cls = ["chapterLabel", "chapterVolume", "chapterTitle", "chapterLang",
          "chapterPages", "chapterGroup"][index];
        const cell = node("span", cls, value);
        if (index === 0 && ch.external_url) cell.classList.add("chapterExternal");
        row.appendChild(cell);
      });
      list.appendChild(row);
    });
  };

  const renderDetails = (detail) => {
    const body = $("detailsBody");
    const winTitle = $("detailsWinTitle");
    if (winTitle) winTitle.textContent = detail.title || "Title details";
    body.innerHTML = "";
    if (detail.error) body.appendChild(node("div", "errorBox", detail.error));

    const head = node("div", "detailsHead");
    const cover = node("img", "detailsCover");
    cover.src = coverSrc(detail.cover_url);
    cover.alt = detail.title || "";
    head.appendChild(cover);

    const info = node("div", "detailsInfo");
    info.appendChild(node("h1", "", detail.title || "Untitled"));
    if ((detail.alt_titles || []).length) {
      info.appendChild(node("div", "altTitles", detail.alt_titles.slice(0, 3).join(" · ")));
    }
    const badges = node("div", "badgeRow");
    const badgeData = [
      detail.source_name || detail.source, detail.content_rating, detail.status,
      detail.year, detail.page_count ? `${detail.page_count} pages` : null,
      detail.demographic,
      (detail.languages || []).slice(0, 3).map(languageLabel).join(", "),
    ].filter(Boolean);
    badgeData.forEach((value, i) => badges.appendChild(node("span", "badge" + (i === 0 ? " primary" : ""), value)));
    if (badgeData.length) info.appendChild(badges);

    const creators = [
      (detail.authors || []).length ? "Author: " + detail.authors.join(", ") : "",
      (detail.artists || []).length ? "Artist: " + detail.artists.join(", ") : "",
    ].filter(Boolean).join(" · ");
    if (creators) info.appendChild(node("div", "muted", creators));
    const stats = statsText(detail.statistics);
    if (stats) info.appendChild(node("div", "muted", stats));

    // Keep the tag cloud inside the top information block.
    if ((detail.tags || []).length) {
      const panel = node("div", "detailPanel tagsPanel");
      panel.appendChild(node("h3", "", "Tags"));
      const cloud = node("div", "tagCloud");
      for (const tag of detail.tags) {
        cloud.appendChild(button(tag.name || tag.id, "", () => searchByTag(tag)));
      }
      panel.appendChild(cloud);
      info.appendChild(panel);
    }

    const actions = node("div", "detailsActions");
    actions.appendChild(button("Open on site", "btn small primary", () => {
      markRead(detail); openUrl(detail.url);
    }));
    if (detail.author_label || (detail.authors || []).length) {
      actions.appendChild(button("Same author", "btn small", () => searchByAuthor(detail)));
    }
    actions.appendChild(button("Similar", "btn small", () => searchSimilar(detail)));
    actions.appendChild(button("Copy link", "btn small", () => copyText(detail.url)));
    info.appendChild(actions);
    head.appendChild(info);
    body.appendChild(head);

    const descPanel = node("div", "detailPanel");
    descPanel.appendChild(node("h3", "", "Description"));
    descPanel.appendChild(renderDescription(detail.description));
    body.appendChild(descPanel);

    const chapterPanel = node("div", "detailPanel");
    const gallery = detail.gallery || (detail.chapters || []).some((ch) => ch.gallery);
    const toolbar = node("div", "chapterToolbar");
    toolbar.appendChild(node("h3", "", (gallery ? "Gallery" : "Chapters") +
      (detail.chapters ? ` · ${detail.chapters.length}` : "")));
    const languages = [...new Set((detail.chapters || []).map((ch) => ch.language).filter(Boolean))];
    if (languages.length > 1 && !gallery) {
      const select = node("select", "select");
      select.appendChild(node("option", "", "All languages")).value = "";
      for (const code of languages) {
        select.appendChild(node("option", "", languageLabel(code))).value = code;
      }
      select.addEventListener("change", () => renderChapterRows(detail, select.value));
      toolbar.appendChild(select);
    }
    chapterPanel.appendChild(toolbar);
    const headRow = node("div", "chapterHead");
    headRow.id = "chapterHead";
    chapterPanel.appendChild(headRow);
    const rows = node("div", "chapterList");
    rows.id = "chapterRows";
    chapterPanel.appendChild(rows);
    body.appendChild(chapterPanel);
    renderChapterRows(detail, "");
  };

  // ----------------------------------------------------------------- reader

  const closeReader = () => {
    state.reader.open = false;
    state.reader.gen++;
    $("readerLayer")?.classList.add("hidden");
  };

  const updateReaderCounter = () => {
    const stage = $("readerStage");
    const images = $("readerStrip")?.querySelectorAll("img") || [];
    if (!images.length) return;
    const top = stage ? stage.scrollTop : 0;
    let active = 0;
    images.forEach((img, index) => {
      if (img.offsetTop - 10 <= top + 20) active = index;
    });
    state.reader.page = active;
    const counter = $("readerCounter");
    if (counter) counter.textContent = `Page ${active + 1} / ${images.length}`;
  };

  const scrollToPage = (index) => {
    const images = $("readerStrip")?.querySelectorAll("img") || [];
    if (!images.length) return;
    const target = Math.max(0, Math.min(index, images.length - 1));
    images[target].scrollIntoView({ behavior: "smooth", block: "start" });
    state.reader.page = target;
    const counter = $("readerCounter");
    if (counter) counter.textContent = `Page ${target + 1} / ${images.length}`;
  };

  const renderReaderChapters = () => {
    const select = $("readerChapter");
    if (!select) return;
    select.innerHTML = "";
    (state.reader.chapters || []).forEach((ch, index) => {
      select.appendChild(node("option", "", ch.label || ch.title || `Chapter ${index + 1}`)).value = String(index);
    });
    select.value = String(state.reader.index);
  };

  const loadReaderPages = async () => {
    const reader = state.reader;
    const chapter = reader.chapters[reader.index];
    if (!chapter || !reader.detail) return;
    const gen = ++reader.gen;
    const strip = $("readerStrip");
    strip.innerHTML = "";
    $("readerStatus").textContent = "Loading pages…";
    renderReaderChapters();
    try {
      const res = await api({
        action: "pages",
        user_id: state.userId,
        source: reader.detail.source,
        id: reader.detail.id,
        title: reader.detail.title,
        chapter_id: chapter.id,
        data_saver: reader.saver,
      });
      if (gen !== reader.gen) return;
      if (res.error) {
        $("readerStatus").textContent = res.error;
        return;
      }
      reader.urls = res.urls || [];
      if (!reader.urls.length) {
        $("readerStatus").textContent = "No pages available for this chapter.";
        if (chapter.external_url) {
          $("readerStatus").appendChild(document.createTextNode(" "));
          $("readerStatus").appendChild(button("Open on site", "btn small", () => openUrl(chapter.external_url)));
        }
        return;
      }
      $("readerStatus").textContent = "";
      reader.urls.forEach((url, index) => {
        const img = node("img", "", `Page ${index + 1}`);
        img.src = mediaSrc(url);
        img.alt = `Page ${index + 1} / ${reader.urls.length}`;
        img.loading = index < 3 ? "eager" : "lazy";
        strip.appendChild(img);
      });
      scrollToPage(0);
    } catch (e) {
      if (gen === reader.gen) $("readerStatus").textContent = "Could not load pages: " + (e.message || e);
    }
  };

  const openReader = (chapterIndex) => {
    const detail = state.details;
    if (!detail) return;
    const reader = state.reader;
    reader.open = true;
    reader.detail = detail;
    reader.chapters = detail.chapters || [];
    reader.saver = false;
    reader.page = 0;
    reader.index = chapterIndex >= 0 ? chapterIndex : 0;
    if ($("readerDataSaver")) $("readerDataSaver").checked = false;
    $("readerTitle").textContent = detail.title || "";
    $("readerLayer").classList.remove("hidden");
    $("readerStage").scrollTop = 0;
    loadReaderPages();
  };

  const stepReader = (delta) => {
    const images = $("readerStrip")?.querySelectorAll("img") || [];
    if (images.length) scrollToPage(state.reader.page + delta);
  };

  // ---------------------------------------------------------------- history→read

  // --------------------------------------------------------------- rule34

  const r34SetStatus = (text) => { const el = $("r34Status"); if (el) el.textContent = text || ""; };
  const r34SetStatusBottom = (text) => { const el = $("r34StatusBottom"); if (el) el.textContent = text || ""; };

  const renderRule34 = () => {
    const grid = $("r34Grid");
    if (!grid) return;
    grid.innerHTML = "";
    for (const item of state.r34.items) {
      const card = makeCard(item, { r34: true });
      card.addEventListener("click", () => openRule34Viewer(item));
      grid.appendChild(card);
    }
    if (!state.r34.items.length) {
      r34SetStatusBottom(state.r34.query ? "Nothing found." : "Enter tags and press Find.");
    } else {
      r34SetStatusBottom(state.r34.hasMore
        ? `${state.r34.items.length} shown · scroll for more`
        : `${state.r34.items.length} results`);
    }
  };

  const doRule34Search = async (opts = {}) => {
    const append = !!opts.append;
    const q = ($("r34Tags")?.value || "").trim();
    if (!append) {
      state.r34.gen++;
      state.r34.query = q;
      state.r34.items = [];
      state.r34.pid = 0;
      state.r34.hasMore = false;
      renderRule34();
    }
    if (!q) { r34SetStatus("Enter tags to search."); return; }
    if (state.r34.loading) return;
    const gen = state.r34.gen;
    state.r34.loading = true;
    if (!append) r34SetStatus("Searching…"); else r34SetStatusBottom("Loading…");
    try {
      const res = await api({
        action: "rule34_search",
        user_id: state.userId,
        tags: q,
        pid: append ? state.r34.pid : 0,
        limit: 40,
        rule34_user_id: state.rule34_user_id,
        rule34_api_key: state.rule34_api_key,
        rule34_ai_generated: state.rule34_ai_generated,
        rule34_videos: state.rule34_videos,
        rule34_images: state.rule34_images,
      });
      if (gen !== state.r34.gen) return;
      if (res.error) {
        if (!append) r34SetStatus(res.error); else r34SetStatusBottom(res.error);
        state.r34.hasMore = false;
        return;
      }
      const items = res.items || [];
      state.r34.items = append ? state.r34.items.concat(items) : items;
      state.r34.pid = typeof res.next_pid === "number"
        ? res.next_pid : (append ? state.r34.pid + 1 : 1);
      state.r34.hasMore = !!res.has_more;
      if (!append) r34SetStatus(items.length ? "" : "Nothing found.");
      renderRule34();
    } catch (e) {
      if (gen === state.r34.gen) {
        const msg = e && e.message ? e.message : "Search failed.";
        if (!append) r34SetStatus(msg); else r34SetStatusBottom(msg);
      }
    } finally {
      state.r34.loading = false;
    }
  };

  const loadMoreRule34 = () => {
    if (state.view === "rule34" && state.r34.hasMore && !state.r34.loading) {
      doRule34Search({ append: true });
    }
  };

  const showRule34Viewer = (index) => {
    const items = state.r34.items || [];
    if (index < 0 || index >= items.length) return;
    state.r34.viewerIndex = index;
    const item = items[index];
    const viewer = $("r34Viewer");
    const body = $("r34ViewerBody");
    if (!viewer || !body) return;
    body.innerHTML = "";
    const src = mediaSrc(item.file_url || item.sample_url || item.preview_url);
    if (item.media_type === "video") {
      const video = node("video");
      video.src = src;
      video.controls = true;
      video.autoplay = true;
      video.loop = true;
      video.playsInline = true;
      body.appendChild(video);
    } else {
      const img = node("img", "", item.tags || "Rule34");
      img.src = src;
      body.appendChild(img);
    }
    $("r34ViewerOpen").href = item.file_url || item.sample_url || item.preview_url || "#";
    $("r34ViewerIndex").textContent = `${index + 1} / ${items.length}`;
    $("r34Prev").disabled = index <= 0;
    $("r34Next").disabled = index >= items.length - 1 && !state.r34.hasMore;
    viewer.classList.remove("hidden");
    viewer.setAttribute("aria-hidden", "false");
  };

  const openRule34Viewer = (item) => {
    const items = state.r34.items || [];
    let index = items.findIndex((entry) => String(entry.id) === String(item.id));
    if (index < 0) index = 0;
    showRule34Viewer(index);
  };

  const stepRule34Viewer = async (delta) => {
    const items = state.r34.items || [];
    let index = state.r34.viewerIndex + delta;
    if (index >= items.length && state.r34.hasMore && !state.r34.loading) {
      await doRule34Search({ append: true });
    }
    if (index < 0) index = 0;
    if (index >= items.length) index = items.length - 1;
    if (index >= 0) showRule34Viewer(index);
  };

  const closeRule34Viewer = () => {
    const viewer = $("r34Viewer");
    if (!viewer) return;
    const video = $("r34ViewerBody")?.querySelector("video");
    if (video) { try { video.pause(); video.removeAttribute("src"); video.load(); } catch (e) { /* noop */ } }
    if ($("r34ViewerBody")) $("r34ViewerBody").innerHTML = "";
    state.r34.viewerIndex = -1;
    viewer.classList.add("hidden");
    viewer.setAttribute("aria-hidden", "true");
  };

  let r34SuggestTimer = null;
  const updateRule34Suggest = async () => {
    const input = $("r34Tags");
    const box = $("r34Suggest");
    if (!input || !box) return;
    const parts = (input.value || "").split(/\s+/);
    const last = (parts[parts.length - 1] || "").trim();
    if (last.length < 2) { box.innerHTML = ""; return; }
    try {
      const res = await api({ action: "rule34_autocomplete", q: last, user_id: state.userId });
      if (res.error) { box.innerHTML = ""; return; }
      box.innerHTML = "";
      for (const item of (res.items || []).slice(0, 12)) {
        const suggestion = item.value || item.label || last;
        box.appendChild(button(item.label || suggestion, "r34SugBtn", () => {
          parts[parts.length - 1] = suggestion;
          input.value = parts.filter(Boolean).join(" ") + " ";
          box.innerHTML = "";
          input.focus();
        }));
      }
    } catch (e) { box.innerHTML = ""; }
  };

  const scheduleRule34Suggest = () => {
    if (r34SuggestTimer) clearTimeout(r34SuggestTimer);
    r34SuggestTimer = setTimeout(updateRule34Suggest, 250);
  };

  const updateRule34FiltersToggle = () => {
    const btn = $("r34FiltersToggle");
    if (!btn) return;
    const parts = [];
    if (state.rule34_ai_generated) parts.push("AI");
    if (state.rule34_videos) parts.push("Videos");
    if (state.rule34_images) parts.push("Images");
    btn.textContent = "Filters: " + (parts.length ? parts.join(", ") : "none");
    btn.classList.toggle("langActive", parts.length > 0);
  };

  const renderRule34FiltersPanel = () => {
    const panel = $("r34FiltersPanel");
    if (!panel) return;
    panel.innerHTML = "";
    const opts = [
      ["rule34_ai_generated", "AI generated"],
      ["rule34_videos", "Videos"],
      ["rule34_images", "Images"],
    ];
    for (const [key, labelText] of opts) {
      const row = node("label", "langRow");
      const cb = node("input");
      cb.type = "checkbox";
      cb.checked = !!state[key];
      cb.addEventListener("change", async () => {
        state[key] = cb.checked;
        try { await saveSettings(); } catch (e) { /* noop */ }
        updateRule34FiltersToggle();
      });
      row.appendChild(cb);
      row.appendChild(document.createTextNode(labelText));
      panel.appendChild(row);
    }
  };

  // --------------------------------------------------------------- settings

  const settingsZone = (titleText) => {
    const zone = node("div", "settingsZone");
    zone.appendChild(node("div", "settingsHeader", titleText));
    return zone;
  };

  const toggleRow = (labelText, checked, onChange) => {
    const row = node("label", "settingRow");
    const cb = node("input", "sourceCb");
    cb.type = "checkbox";
    cb.checked = checked;
    cb.addEventListener("change", () => onChange(cb.checked));
    row.appendChild(cb);
    row.appendChild(node("span", "", labelText));
    return row;
  };

  const checkSites = async (statusEl, listEl) => {
    statusEl.textContent = "Checking sites…";
    listEl.innerHTML = "";
    try {
      const res = await api({ action: "check_sites" });
      if (res.error) { statusEl.textContent = res.error; return; }
      const sites = res.sites || [];
      statusEl.textContent = `${sites.filter((site) => site.ok).length} of ${sites.length} sites available`;
      for (const site of sites) {
        listEl.appendChild(node("div", "checkRow " + (site.ok ? "checkOk" : "checkBad"),
          `${site.url || site.name || site.key} — ${site.ok ? "Available" : "Unavailable"}` +
          (site.detail ? ` · ${site.detail}` : "")));
      }
    } catch (e) {
      statusEl.textContent = "Site check failed: " + (e.message || e);
    }
  };

  const localUserId = () => {
    const tgUser = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
    return tgUser ? tgUser.id : getClientId();
  };

  const applyAuth = (data) => {
    state.auth = {
      authenticated: !!data.authenticated,
      username: data.username || "",
      user_id: data.user_id || "",
      is_admin: !!data.is_admin,
      error: data.error || "",
    };
    if (state.auth.authenticated) state.userId = state.auth.user_id;
  };

  const refreshAuth = async () => {
    const res = await authApi("me");
    applyAuth(res.data);
    return res;
  };

  const showLoginLayer = (message = "") => {
    const layer = $("loginLayer");
    if (!layer) return;
    layer.classList.remove("hidden");
    const error = $("loginError");
    if (error) error.textContent = message || "";
    const input = $("loginUsername");
    if (input) setTimeout(() => input.focus(), 0);
  };

  const hideLoginLayer = () => {
    $("loginLayer")?.classList.add("hidden");
    const password = $("loginPassword");
    if (password) password.value = "";
    const error = $("loginError");
    if (error) error.textContent = "";
  };

  const afterLogin = async (announce = true) => {
    hideLoginLayer();
    await refreshAdmin();
    await loadUserData();
    renderSettings();
    switchView("search");
    if (announce) {
      showToast(state.auth.is_admin ? "Signed in as admin." : "Signed in.");
    }
  };

  const doLogin = async () => {
    const username = $("loginUsername")?.value.trim() || "";
    const password = $("loginPassword")?.value || "";
    const error = $("loginError");
    const button = $("loginSubmit");
    if (!username || !password) {
      if (error) error.textContent = "Enter a username and password.";
      return;
    }
    if (button) button.disabled = true;
    if (error) error.textContent = "Signing in…";
    const res = await authApi("login", { username, password });
    if (!res.ok || !res.data.ok) {
      if (error) error.textContent = res.data.error || "Invalid username or password.";
      if (button) button.disabled = false;
      return;
    }
    applyAuth({ ...res.data, authenticated: true });
    await afterLogin();
    if (button) button.disabled = false;
  };

  const doLogout = async () => {
    await authApi("logout");
    state.admin.authenticated = false;
    state.admin.accounts = [];
    applyAuth({ authenticated: false });
    state.userId = localUserId();
    showLoginLayer();
  };

  const loadAdminAccounts = async () => {
    const res = await authApi("admin_accounts");
    if (Array.isArray(res.data.accounts)) state.admin.accounts = res.data.accounts;
    if (res.data.error) state.admin.error = res.data.error;
    return res;
  };

  const refreshAdmin = async () => {
    state.admin.authenticated = !!state.auth.is_admin;
    state.admin.error = "";
    if (state.admin.authenticated) await loadAdminAccounts();
  };

  const doAdminAction = async (action, extra = {}) => {
    const res = await authApi(action, extra);
    if (!res.ok || !res.data.ok) {
      state.admin.error = res.data.error || "Admin action failed.";
    } else {
      state.admin.error = "";
      if (res.data.new_password) {
        showToast("New password: " + res.data.new_password, 12000);
      } else if (action === "admin_create_account") {
        showToast("Account created: " + (res.data.username || ""));
      } else if (action === "admin_delete_account") {
        showToast("Account deleted.");
      } else {
        showToast("Done.");
      }
      await loadAdminAccounts();
    }
    renderSettings();
    return res;
  };

  const renderAccountZone = (box) => {
    const zone = settingsZone("Account");
    if (!state.auth.authenticated) {
      zone.appendChild(node("div", "settingInfo", "Not signed in."));
      box.appendChild(zone);
      return;
    }
    zone.appendChild(node("div", "settingInfo",
      `Signed in as ${state.auth.username}` + (state.auth.is_admin ? " · Administrator" : "")));
    zone.appendChild(button("Sign out", "settingsBtn danger", () => { doLogout(); }));
    box.appendChild(zone);
  };

  const renderAdminZone = (box) => {
    if (!state.auth.is_admin) return;
    const zone = settingsZone("Admin");
    zone.appendChild(node("div", "settingInfo",
      "Create accounts for users. Passwords are stored as PBKDF2 hashes, not in plain text."));
    if (state.admin.error) zone.appendChild(node("div", "status", state.admin.error));

    const newUser = node("input", "titleInput");
    newUser.placeholder = "New username";
    newUser.autocomplete = "off";
    const newPass = node("input", "titleInput");
    newPass.type = "password";
    newPass.placeholder = "Password (at least 8 characters)";
    newPass.autocomplete = "new-password";

    const addAccount = async () => {
      const username = newUser.value.trim();
      const password = newPass.value;
      if (!username || !password) {
        state.admin.error = "Enter a username and password.";
        renderSettings();
        return;
      }
      newUser.value = "";
      newPass.value = "";
      await doAdminAction("admin_create_account", { username, password });
    };
    newPass.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); addAccount(); }
    });

    const addRow = node("div", "addAccountRow");
    addRow.appendChild(newUser);
    addRow.appendChild(newPass);
    addRow.appendChild(button("Add account", "settingsBtn", addAccount));
    zone.appendChild(addRow);

    const list = node("div", "scrollList");
    if (!state.admin.accounts.length) {
      list.appendChild(node("div", "settingInfo", "No user accounts yet."));
    }
    for (const account of state.admin.accounts) {
      const row = node("div", "settingRow");
      const meta = node("div", "sourceMeta");
      meta.appendChild(node("div", "",
        account.username + (account.is_admin ? " · ADMIN" : "")));
      meta.appendChild(node("div", "settingInfo",
        `ID ${account.user_id} · history ${account.history_count} · tags ${account.tags_count} · sources ${account.sources_count}`));
      row.appendChild(meta);
      if (account.is_admin) {
        row.appendChild(node("div", "settingInfo", "Built-in"));
      } else {
        row.appendChild(button("Reset pwd", "settingsBtn small", async () => {
          const generated = confirm(
            `Generate a new random password for ${account.username}?\n\nOK = generate, Cancel = enter your own.`
          );
          let password = "";
          if (!generated) {
            password = prompt(`New password for ${account.username} (at least 8 characters):`) || "";
            if (!password) return;
          }
          await doAdminAction("admin_reset_password", { username: account.username, new_password: password });
        }));
        row.appendChild(button("Delete", "settingsBtn danger small", async () => {
          if (!confirm(`Delete account ${account.username}? Its history and settings will be cleared.`)) return;
          if (!confirm("Are you sure? This cannot be undone.")) return;
          await doAdminAction("admin_delete_account", { username: account.username });
        }));
      }
      list.appendChild(row);
    }
    zone.appendChild(list);
    zone.appendChild(button("Refresh accounts", "settingsBtn", async () => {
      await loadAdminAccounts();
      renderSettings();
    }));
    box.appendChild(zone);
  };

  const renderSettings = () => {
    const box = $("settingsBody");
    if (!box) return;
    box.innerHTML = "";

    renderAccountZone(box);
    renderAdminZone(box);

    // General
    const general = settingsZone("General");
    general.appendChild(toggleRow("Hide read titles in search", state.hide_read_in_search, async (checked) => {
      state.hide_read_in_search = checked;
      $("hideReadToggle").checked = checked;
      try { await saveSettings(); } catch (e) { showToast("Could not save settings"); }
    }));
    general.appendChild(toggleRow("Block gay / yaoi content", state.block_gay, async (checked) => {
      state.block_gay = checked;
      try { await saveSettings(); } catch (e) { showToast("Could not save settings"); }
    }));
    box.appendChild(general);

    // Sources
    const sourcesZone = settingsZone("Sources");
    const selectAll = button("Select all sources", "settingsBtn", async () => {
      const allSelected = state.sources.length && state.enabledSources.length === state.sources.length;
      state.enabledSources = allSelected ? [] : state.sources.map((source) => source.key);
      renderSettings();
      try { await saveSettings(); } catch (e) { showToast("Could not save sources"); }
    });
    sourcesZone.appendChild(selectAll);
    const sourceList = node("div", "scrollList");
    for (const source of state.sources) {
      sourceList.appendChild(toggleRow(source.name || source.key,
        state.enabledSources.includes(source.key), async (checked) => {
          state.enabledSources = checked
            ? state.enabledSources.concat(source.key)
            : state.enabledSources.filter((key) => key !== source.key);
          try { await saveSettings(); } catch (e) { showToast("Could not save sources"); }
        }));
    }
    sourcesZone.appendChild(sourceList);
    box.appendChild(sourcesZone);

    // Local / server data
    const dataZone = settingsZone("Local / server data");
    dataZone.appendChild(node("div", "settingRow settingInfo", `History entries: ${state.historyCount}`));
    dataZone.appendChild(button("Clear history", "settingsBtn danger", async () => {
      if (!confirm("Clear all history?")) return;
      try {
        const res = await api({ action: "history_clear", user_id: state.userId });
        state.historyCount = res.history_count || 0;
        state.history = [];
        state.readKeys.clear();
        renderSettings();
      } catch (e) { showToast("Could not clear history"); }
    }));
    dataZone.appendChild(button("Reset settings", "settingsBtn danger", async () => {
      if (!confirm("Reset search settings to defaults?")) return;
      try {
        const res = await api({ action: "reset_settings", user_id: state.userId });
        if (!res.error) { applySettings(res); renderAllSettingsUI(); renderSettings(); }
      } catch (e) { showToast("Could not reset settings"); }
    }));
    dataZone.appendChild(button("Reset everything (history + settings)", "settingsBtn danger", async () => {
      if (!confirm("Reset everything? History and settings will be removed.")) return;
      if (!confirm("Are you sure? This cannot be undone.")) return;
      try {
        const res = await api({ action: "reset_all", user_id: state.userId });
        if (!res.error) {
          applySettings(res);
          state.history = [];
          state.readKeys.clear();
          renderAllSettingsUI();
          renderSettings();
        } else showToast(res.error);
      } catch (e) { showToast("Could not reset everything"); }
    }));
    box.appendChild(dataZone);

    // Rule34 credentials
    const r34Zone = settingsZone("Rule34 credentials & filters");
    r34Zone.appendChild(node("div", "settingInfo",
      "Use your own rule34.xxx User ID and API Key. The deployment can also provide them through environment variables."));
    const uidInput = node("input", "titleInput");
    uidInput.type = "text";
    uidInput.placeholder = "Rule34 User ID";
    uidInput.value = state.rule34_user_id || "";
    r34Zone.appendChild(uidInput);
    const keyInput = node("input", "titleInput");
    keyInput.type = "password";
    keyInput.placeholder = "Rule34 API Key";
    keyInput.value = state.rule34_api_key || "";
    r34Zone.appendChild(keyInput);
    const r34Status = node("div", "status");
    r34Zone.appendChild(button("Save Rule34 credentials", "settingsBtn", async () => {
      state.rule34_user_id = uidInput.value.trim();
      state.rule34_api_key = keyInput.value.trim();
      try {
        const res = await saveSettings();
        r34Status.textContent = res.error ? res.error : "Saved.";
      } catch (e) { r34Status.textContent = "Save failed: " + (e.message || e); }
    }));
    r34Zone.appendChild(r34Status);
    r34Zone.appendChild(toggleRow("Allow AI generated", state.rule34_ai_generated, async (checked) => {
      state.rule34_ai_generated = checked;
      updateRule34FiltersToggle(); renderRule34FiltersPanel();
      try { await saveSettings(); } catch (e) { /* noop */ }
    }));
    r34Zone.appendChild(toggleRow("Allow videos", state.rule34_videos, async (checked) => {
      state.rule34_videos = checked;
      updateRule34FiltersToggle(); renderRule34FiltersPanel();
      try { await saveSettings(); } catch (e) { /* noop */ }
    }));
    r34Zone.appendChild(toggleRow("Allow images", state.rule34_images, async (checked) => {
      state.rule34_images = checked;
      updateRule34FiltersToggle(); renderRule34FiltersPanel();
      try { await saveSettings(); } catch (e) { /* noop */ }
    }));
    box.appendChild(r34Zone);

    // Maintenance
    const maintenance = settingsZone("Maintenance");
    const checkBtn = button("Check sites", "settingsBtn");
    const checkStatus = node("div", "status");
    const checkList = node("div", "checkList");
    checkBtn.addEventListener("click", () => checkSites(checkStatus, checkList));
    maintenance.appendChild(checkBtn);
    maintenance.appendChild(checkStatus);
    maintenance.appendChild(checkList);
    box.appendChild(maintenance);
  };

  const renderAllSettingsUI = () => {
    renderTagList(); renderChips(); renderLangPanel(); updateLangToggle();
    renderRule34FiltersPanel(); updateRule34FiltersToggle();
    renderAuthorChip();
    if ($("hideReadToggle")) $("hideReadToggle").checked = state.hide_read_in_search;
  };

  const loadUserData = async () => {
    try {
      const res = await api({ action: "sources", user_id: state.userId });
      if (!res.error) state.sources = res.sources || [];
    } catch (e) { showToast("Could not load the source list"); }
    state.enabledSources = state.sources.map((source) => source.key);

    try {
      const saved = await api({ action: "settings", user_id: state.userId });
      if (!saved.error) applySettings(saved);
    } catch (e) { showToast("Could not load settings"); }
    if (!state.enabledSources.length) state.enabledSources = state.sources.map((source) => source.key);

    try {
      const res = await api({ action: "tags", user_id: state.userId });
      if (!res.error) state.allTags = res.tags || [];
    } catch (e) { showToast("Could not load tags"); }
    const tagStatus = $("tagStatus");
    if (tagStatus) {
      if (state.allTags.length) tagStatus.classList.add("hidden");
      else tagStatus.textContent = "No tags available.";
    }

    // Preload read markers for result cards without switching to History.
    try {
      const res = await api({ action: "history", user_id: state.userId });
      if (!res.error) {
        state.history = res.history || [];
        state.readKeys = new Set(state.history.map((item) => item.key));
      }
    } catch (e) { /* history markers are optional */ }

    renderAllSettingsUI();
    renderResults();
  };

  // ------------------------------------------------------------------- init

  const init = async () => {
    $("loginForm")?.addEventListener("submit", (e) => {
      e.preventDefault();
      doLogin();
    });

    if (tg) {
      try { tg.ready(); } catch (e) { /* noop */ }
      try { tg.expand(); } catch (e) { /* noop */ }
    }
    state.userId = localUserId();
    const authResult = await refreshAuth();
    if (state.auth.authenticated) {
      await afterLogin(false);
    } else {
      showLoginLayer(authResult.data.error || "");
      switchView("search");
    }
    $("findBtn")?.addEventListener("click", () => { closeFilterSheet(); doSearch(); });
    $("findTopBtn")?.addEventListener("click", doSearch);
    $("randomBtn")?.addEventListener("click", async () => {
      setStatus("Looking for a random title…");
      try {
        const res = await api({
          action: "random", user_id: state.userId,
          sources: state.enabledSources.length ? state.enabledSources : undefined,
        });
        if (res.error) { setStatus(res.error); return; }
        if (!res.item) { setStatus("No random title found."); return; }
        await markRead(res.item);
        openDetails(res.item);
      } catch (e) { setStatus("Random search failed: " + (e.message || e)); }
    });
    $("titleInput")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { state.authorFilter = ""; renderAuthorChip(); doSearch(); }
    });
    $("authorChip")?.addEventListener("click", async () => {
      state.authorFilter = "";
      renderAuthorChip();
      try { await saveSettings(); } catch (e) { /* noop */ }
    });
    $("tagFilter")?.addEventListener("input", renderTagList);
    $("tagGroup")?.addEventListener("change", renderTagList);
    document.querySelectorAll('input[name="tagModeRadio"]').forEach((radio) => {
      radio.addEventListener("change", async () => {
        if (!radio.checked) return;
        state.tagMode = radio.value;
        syncTagModeUI();
        try { await saveSettings(); } catch (err) { /* noop */ }
      });
    });
    $("tagMode")?.addEventListener("change", async (e) => {
      state.tagMode = e.target.value;
      syncTagModeUI();
      try { await saveSettings(); } catch (err) { /* noop */ }
    });
    $("sortSelect")?.addEventListener("change", async (e) => {
      state.sort = e.target.value;
      try { await saveSettings(); } catch (err) { /* noop */ }
    });
    $("langToggle")?.addEventListener("click", () => $("langPanel")?.classList.toggle("hidden"));
    $("resetFiltersBtn")?.addEventListener("click", resetFilters);
    $("hideReadToggle")?.addEventListener("change", async (e) => {
      state.hide_read_in_search = e.target.checked;
      try { await saveSettings(); } catch (err) { /* noop */ }
    });
    $("filtersToggle")?.addEventListener("click", () => {
      $("filterPanel")?.classList.add("open");
      $("filterOverlay")?.classList.remove("hidden");
    });
    $("filtersClose")?.addEventListener("click", closeFilterSheet);
    $("filterOverlay")?.addEventListener("click", closeFilterSheet);
    $("loadMoreBtn")?.addEventListener("click", loadMore);

    document.querySelectorAll(".tabBtn").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.view));
    });
    $("backBtn")?.addEventListener("click", () => switchView(state.lastTab));
    $("detailsClose")?.addEventListener("click", () => switchView(state.lastTab));

    // Non-modal details window: drag by its title bar (context menus keep clientX/Y).
    const detailsWin = $("detailsSection");
    const detailsBar = $("detailsWinBar");
    let detailsDrag = null;
    detailsBar?.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || (e.target && e.target.closest("button")) || !detailsWin) return;
      const rect = detailsWin.getBoundingClientRect();
      detailsDrag = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
      detailsWin.style.left = rect.left + "px";
      detailsWin.style.top = rect.top + "px";
      detailsWin.style.transform = "none";
      try { detailsBar.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
    });
    detailsBar?.addEventListener("pointermove", (e) => {
      if (!detailsDrag || !detailsWin) return;
      const left = Math.max(0, Math.min(e.clientX - detailsDrag.dx, window.innerWidth - 120));
      const top = Math.max(0, Math.min(e.clientY - detailsDrag.dy, window.innerHeight - 32));
      detailsWin.style.left = left + "px";
      detailsWin.style.top = top + "px";
    });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach((type) => {
      detailsBar?.addEventListener(type, () => { detailsDrag = null; });
    });

    $("historyRefresh")?.addEventListener("click", refreshHistory);

    if ("IntersectionObserver" in window) {
      const searchObserver = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
      });
      if ($("sentinel")) searchObserver.observe($("sentinel"));
      const r34Observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMoreRule34();
      });
      if ($("r34Sentinel")) r34Observer.observe($("r34Sentinel"));
    }

    // Rule34
    $("r34FindBtn")?.addEventListener("click", () => doRule34Search());
    $("r34Tags")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") doRule34Search();
    });
    $("r34Tags")?.addEventListener("input", scheduleRule34Suggest);
    $("r34FiltersToggle")?.addEventListener("click", () => $("r34FiltersPanel")?.classList.toggle("hidden"));
    $("r34ViewerClose")?.addEventListener("click", closeRule34Viewer);
    $("r34Viewer")?.addEventListener("click", (e) => {
      if (e.target === $("r34Viewer")) closeRule34Viewer();
    });
    $("r34Prev")?.addEventListener("click", () => stepRule34Viewer(-1));
    $("r34Next")?.addEventListener("click", () => stepRule34Viewer(1));

    // Reader
    $("readerBack")?.addEventListener("click", closeReader);
    $("readerChapter")?.addEventListener("change", (e) => {
      state.reader.index = Number(e.target.value) || 0;
      state.reader.page = 0;
      $("readerStage").scrollTop = 0;
      loadReaderPages();
    });
    $("readerDataSaver")?.addEventListener("change", (e) => {
      state.reader.saver = !!e.target.checked;
      loadReaderPages();
    });
    $("readerPrev")?.addEventListener("click", () => stepReader(-1));
    $("readerNext")?.addEventListener("click", () => stepReader(1));
    $("readerStage")?.addEventListener("scroll", updateReaderCounter, { passive: true });
    let touchStart = null;
    $("readerStage")?.addEventListener("touchstart", (e) => {
      const touch = e.touches[0];
      if (touch) touchStart = { x: touch.clientX, y: touch.clientY };
    }, { passive: true });
    $("readerStage")?.addEventListener("touchend", (e) => {
      if (!touchStart || !e.changedTouches[0]) return;
      const dx = e.changedTouches[0].clientX - touchStart.x;
      const dy = e.changedTouches[0].clientY - touchStart.y;
      touchStart = null;
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) stepReader(dx < 0 ? 1 : -1);
    }, { passive: true });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (state.reader.open) closeReader();
        else if (!$("r34Viewer").classList.contains("hidden")) closeRule34Viewer();
        else if (state.view === "details") switchView(state.lastTab);
        else closeFilterSheet();
        return;
      }
      if (state.reader.open) {
        if (e.key === "ArrowLeft") { e.preventDefault(); stepReader(-1); }
        if (e.key === "ArrowRight") { e.preventDefault(); stepReader(1); }
        return;
      }
      if (!$("r34Viewer").classList.contains("hidden")) {
        if (e.key === "ArrowLeft") stepRule34Viewer(-1);
        if (e.key === "ArrowRight") stepRule34Viewer(1);
      }
    });
    document.addEventListener("click", (e) => {
      const menu = $("contextMenu");
      if (menu && !menu.contains(e.target)) hideContextMenu();
    });
    window.addEventListener("scroll", hideContextMenu, { passive: true });
    $("filterPanel")?.addEventListener("scroll", hideContextMenu, { passive: true });
  };

  document.addEventListener("DOMContentLoaded", () => { init(); });

  window.HMF = { state, api, switchView, doSearch, loadMore, openDetails, openReader, saveSettings };
})();
