/* «Сомелье, который вас помнит»: над карточкой вина — понравится ли оно этому человеку и почему,
 * «вы уже пили это вино»; экран «Мой вкус» — цвета, сладость, сорта, винодельни (как жанры в Spotify).
 *
 * Считает сервис пользователей (/v1/me/taste/match) по профилю и оценкам: звёзды дневника и 👍/👎
 * истории. Сорта с фото и винодельни для выбора — api (/v1/taste/options). Профиль, как и дневник,
 * хранится только с аккаунтом: без него можно отметить вкус, на «Сохранить» — вход, потом сохранится само.
 */
(() => {
  "use strict";

  const W = window.Wine;
  if (!W || W.DEMO) return;
  const { $, esc, icon, toast, openSheet, closeSheet } = W;
  const ACC = () => window.WineAccount;
  const call = (method, url, body) => ACC().call(method, url, body);
  const signedIn = () => ACC()?.signedIn();
  const track = (type, props = {}) => document.dispatchEvent(new CustomEvent("wine:track", { detail: { type, props } }));
  const askRegister = (source, then) => { track("auth_prompt", { source }); ACC().openAuth("register", then); };

  // Процент («87%») или только словами («Скорее понравится») — решает владелец; ?taste=words — показать без процента
  const SHOW_PERCENT = new URLSearchParams(location.search).get("taste") !== "words";

  const COLORS = [
    { k: "red", t: "Красное", d: "ягоды, специи, танины", c: "#7A1F2B" },
    { k: "white", t: "Белое", d: "свежее, фрукты, цитрус", c: "#E9D8A6" },
    { k: "rose", t: "Розовое", d: "лёгкое, клубника", c: "#E8A0A8" },
    { k: "orange", t: "Оранжевое", d: "белое на кожице, пряное", c: "#D98B3A" },
    { k: "sparkling", t: "Игристое", d: "пузырьки, брют", c: "#F1E6C0", bubbles: true },
  ];
  const SUGARS = [
    { k: "dry", t: "Сухое", d: "без сладости — так пьют чаще всего" },
    { k: "semidry", t: "Полусухое", d: "едва заметная сладость" },
    { k: "semisweet", t: "Полусладкое", d: "мягкое, сладость чувствуется" },
    { k: "sweet", t: "Сладкое", d: "десертное" },
  ];
  // Подсказки для тех, кто не знает сортов: каким сорт обычно бывает (на сайте описаний сортов нет)
  const HINTS = {
    "Каберне Совиньон": "плотное, терпкое, чёрная смородина",
    "Шардоне": "округлое, яблоко, бывает с ванилью от дуба",
    "Пино Нуар": "лёгкое, вишня, нежные танины",
    "Мерло": "мягкое, слива, бархатное",
    "Рислинг": "свежее, кисловатое, цитрус",
    "Рислинг Рейнский": "свежее, кисловатое, цитрус",
    "Совиньон Блан": "яркое, травы, крыжовник",
    "Саперави": "густое, тёмное, ежевика",
    "Каберне Фран": "травянистое, малина, перец",
    "Ркацители": "свежее, яблоко, лёгкая горчинка",
    "Алиготе": "лёгкое, кислотное, цитрус",
    "Сира": "пряное, чёрный перец, тёмные ягоды",
    "Шираз": "пряное, чёрный перец, тёмные ягоды",
    "Красностоп Золотовский": "донское, насыщенное, вишня и специи",
    "Красностоп": "донское, насыщенное, вишня и специи",
    "Пино Блан": "нежное, груша, сливочное",
    "Пино Гри": "мягкое, груша, мёд",
    "Вионье": "ароматное, абрикос, цветы",
    "Мускат Белый": "ароматное, цветы, сладкий виноград",
    "Мускат": "ароматное, цветы, сладкий виноград",
    "Мускат Оттонель": "лёгкое, цветы, виноград",
    "Цимлянский черный": "донское, вишня, мягкие танины",
    "Кокур": "крымское, свежее, фрукты",
    "Кокур Белый": "крымское, свежее, фрукты",
    "Цитронный Магарача": "цитрус и цветы, очень ароматное",
    "Мальбек": "сочное, слива, фиалка",
    "Пти Вердо": "плотное, тёмное, фиалка",
    "Первенец Магарача": "крымское, свежее, фрукты",
    "Бастардо Магарачский": "крымское, тёмные ягоды, мягкое",
  };
  const GRAPES_SHOWN = 24;
  const STEPS = ["colors", "sugars", "grapes", "makers"];

  const st = { options: null, profile: null, draft: null, step: 0, grapeQuery: "", makerQuery: "", allGrapes: false };
  const DATE = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long" });
  const fmtDay = (iso) => (iso ? DATE.format(new Date(`${iso}T12:00:00`)) : "");
  const stars = (n) => `<span class="stars" aria-label="Оценка ${n} из 5">${[1, 2, 3, 4, 5]
    .map((i) => icon("star", i <= n ? "star--on" : "")).join("")}</span>`;
  const empty = () => ({ colors: [], sugars: [], grapes: [], manufacturers: [] });

  // ── Строка над карточкой ─────────────────────────────────────────────────
  async function onCard(wine) {
    // сразу под названием вина: первое, что видно после скана (на широком экране — в левой карточке)
    const title = $("#screen-result .hero__title");
    if (!title || !wine?.slug || !ACC()?.available()) return;
    $("#screen-result .taste-slot")?.remove();
    const slot = document.createElement("div");
    slot.className = "taste-slot";
    slot.dataset.slug = wine.slug;
    title.append(slot);
    if (!signedIn()) { slot.innerHTML = askHtml(); return; }
    try {
      const m = await call("GET", `/v1/me/taste/match?slug=${encodeURIComponent(wine.slug)}`);
      if (!slot.isConnected) return;
      st.match = m;
      // уже пил и оценил — звёзды в строке «Вы уже пили», второй строкой то же самое не повторяем
      const rated = m.tried?.kind === "tasted" && m.tried.rating;
      slot.innerHTML = triedHtml(m.tried) + (rated ? "" : lineHtml(m));
      if (m.state === "ready") track("taste", { action: "match", percent: m.percent });
    } catch (_) { slot.remove(); }
  }

  const askHtml = (title = "Понравится ли вам это вино?", text = "Расскажите о своём вкусе — и я начну подсказывать") => `
    <button class="taste-line taste-line--ask" type="button" data-action="taste-open">
      <span class="taste-line__icon">${icon("sparkles")}</span>
      <span class="taste-line__text"><b>${title}</b><span>${text}</span></span>
      ${icon("chevron")}
    </button>`;

  function lineHtml(m) {
    if (m.state === "need_data") return askHtml();
    if (m.state === "unknown") return askHtml("Пока не с чем сравнить", "Оцените похожие вина в дневнике или уточните свой вкус");
    return `
      <button class="taste-line taste-line--${m.tone}" type="button" data-action="taste-why">
        ${SHOW_PERCENT ? ringHtml(m.percent) : `<span class="taste-line__icon">${icon(m.tone === "bad" ? "thumb-down" : "thumb-up")}</span>`}
        <span class="taste-line__text"><b>${esc(m.verdict)}</b><span>${esc(m.reasons[0] || "")}</span></span>
        ${icon("chevron")}
      </button>`;
  }

  const ringHtml = (p, cls = "") => `<span class="taste-ring ${cls}" style="--p:${p}" role="img" aria-label="Совпадение ${p}%"><b>${p}%</b></span>`;

  function triedHtml(t) {
    if (!t) return "";
    let text;
    if (t.kind === "tasted") {
      const words = [t.note ? `«${esc(t.note)}»` : t.tags?.length ? esc(t.tags.join(", ").toLowerCase()) : ""].filter(Boolean);
      text = `<b>Вы уже пили это вино</b><span>${fmtDay(t.date)}${t.rating ? ` · ${stars(t.rating)}` : ""}${words.length ? ` · ${words[0]}` : ""}</span>`;
    } else if (t.kind === "want") {
      text = `<b>Вы хотели попробовать это вино</b><span>в дневнике с ${fmtDay(t.date)}</span>`;
    } else {
      text = `<b>Вы уже сканировали это вино</b><span>${fmtDay(t.date)}${t.like === 1 ? " · понравилось" : t.like === -1 ? " · не понравилось" : ""}</span>`;
    }
    return `<div class="taste-tried">${icon(t.kind === "scanned" ? "clock" : "book")}<span class="taste-line__text">${text}</span></div>`;
  }

  function openWhy() {
    const m = st.match;
    if (!m || m.state !== "ready") return;
    openSheet("Понравится ли вам", `
      <div class="taste-why">
        ${SHOW_PERCENT ? ringHtml(m.percent, "taste-ring--big") : ""}
        <p class="taste-why__verdict taste-why__verdict--${m.tone}">${esc(m.verdict)}</p>
        <ul class="taste-why__list">${m.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
        <p class="sheet__hint">${m.rated
          ? `Считаю по «Моему вкусу» и вашим оценкам: звёзды в дневнике и 👍/👎 в истории сканов (оценено вин: ${m.rated}).
             Чем больше оцениваете, тем точнее.`
          : "Пока считаю только по «Моему вкусу». Ставьте звёзды в дневнике и 👍/👎 в истории сканов — подсказки станут точнее."}</p>
        <button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="taste-open">${icon("sparkles")}Изменить «Мой вкус»</button>
      </div>`);
  }

  // ── Экран «Мой вкус» ─────────────────────────────────────────────────────
  async function openTaste() {
    closeSheet();
    track("taste", { action: "open" });
    st.step = 0;
    st.grapeQuery = st.makerQuery = "";
    st.allGrapes = false;
    st.profile = st.draft ? { ...st.draft } : empty();
    if (signedIn()) {
      try { st.profile = await call("GET", "/v1/me/taste"); } catch (_) { /* пустой профиль */ }
    }
    if (!st.options) {
      try { st.options = await W.request("/v1/taste/options"); } catch (_) { st.options = { grapes: [], manufacturers: [] }; }
    }
    document.body.insertAdjacentHTML("beforeend", `<div class="taste" role="dialog" aria-modal="true" aria-label="Мой вкус"></div>`);
    document.body.style.overflow = "hidden";
    renderTaste();
  }

  function closeTaste() {
    $(".taste")?.remove();
    document.body.style.overflow = "";
  }

  function renderTaste() {
    const root = $(".taste");
    if (!root) return;
    const step = STEPS[st.step];
    const last = st.step === STEPS.length - 1;
    const p = st.profile;
    const count = { colors: p.colors.length, sugars: p.sugars.length, grapes: p.grapes.length, makers: p.manufacturers.length }[step];
    root.innerHTML = `
      <header class="taste__top">
        <button class="taste__icon-btn" type="button" data-action="${st.step ? "taste-back" : "taste-close"}"
          aria-label="${st.step ? "Назад" : "Закрыть"}">${icon(st.step ? "arrow-left" : "x")}</button>
        <div class="taste__dots" aria-label="Шаг ${st.step + 1} из ${STEPS.length}">${STEPS.map((_, i) =>
          `<i class="${i <= st.step ? "on" : ""}"></i>`).join("")}</div>
        ${last ? `<span class="taste__skip"></span>` : `<button class="taste__skip" type="button" data-action="taste-next">Пропустить</button>`}
      </header>
      <div class="taste__body">${{ colors: colorsHtml, sugars: sugarsHtml, grapes: grapesHtml, makers: makersHtml }[step]()}</div>
      <footer class="taste__foot">
        <button class="ui-button ui-button_primary ui-button_large" type="button" data-action="${last ? "taste-save" : "taste-next"}">
          ${last ? "Готово" : count ? `Далее · выбрано ${count}` : "Далее"}</button>
        ${last && !signedIn() ? `<p class="form__note">Вкус хранится в профиле — на «Готово» предложим войти</p>` : ""}
      </footer>`;
    root.querySelector(".taste__body").scrollTop = 0;
  }

  const head = (title, sub) => `<h2 class="taste__title">${title}</h2><p class="taste__sub">${sub}</p>`;
  const tick = (on) => `<span class="taste-tick ${on ? "taste-tick--on" : ""}">${icon("check")}</span>`;

  function colorsHtml() {
    const on = new Set(st.profile.colors);
    return head("Какое вино вы любите?", "Можно несколько — потом всегда можно поменять") + `
      <div class="taste-colors">${COLORS.map((c) => `
        <button class="taste-color ${on.has(c.k) ? "taste-color--on" : ""}" type="button" data-action="taste-pick"
          data-field="colors" data-v="${c.k}" aria-pressed="${on.has(c.k)}" style="--c:${c.c}">
          <span class="taste-glass ${c.bubbles ? "taste-glass--bubbles" : ""}"><i></i></span>
          <b>${c.t}</b><span>${c.d}</span>${tick(on.has(c.k))}
        </button>`).join("")}</div>`;
  }

  function sugarsHtml() {
    const on = new Set(st.profile.sugars);
    return head("Сухое или послаще?", "Брют у игристых — это сухое") + `
      <div class="taste-sugars">${SUGARS.map((s, i) => `
        <button class="taste-sugar ${on.has(s.k) ? "taste-sugar--on" : ""}" type="button" data-action="taste-pick"
          data-field="sugars" data-v="${s.k}" aria-pressed="${on.has(s.k)}">
          <span class="taste-sugar__drops" aria-hidden="true">${i ? "<i></i>".repeat(i) : `<i class="off"></i>`}</span>
          <span class="taste-sugar__text"><b>${s.t}</b><span>${s.d}</span></span>${tick(on.has(s.k))}
        </button>`).join("")}</div>`;
  }

  function grapesHtml() {
    const on = new Set(st.profile.grapes);
    const q = st.grapeQuery.trim().toLowerCase();
    const all = st.options.grapes;
    let list = q ? all.filter((g) => g.name.toLowerCase().includes(q))
      : st.allGrapes ? all : all.slice(0, GRAPES_SHOWN);
    // выбранные, но не попавшие в список, — тоже видны
    if (!q) list = [...all.filter((g) => on.has(g.name) && !list.includes(g)), ...list];
    return head("Любимые сорта", "Не знаете сортов — не страшно: под каждым подсказка, каким он бывает") + `
      <label class="taste-search">${icon("search")}<input type="search" data-role="grape-q" placeholder="Найти сорт"
        value="${esc(st.grapeQuery)}" autocomplete="off"></label>
      <div class="taste-grapes">${list.map((g) => `
        <button class="taste-grape ${on.has(g.name) ? "taste-grape--on" : ""}" type="button" data-action="taste-pick"
          data-field="grapes" data-v="${esc(g.name)}" aria-pressed="${on.has(g.name)}">
          <span class="taste-grape__img">${g.image ? `<img src="${esc(g.image)}" alt="" loading="lazy" onerror="this.remove()">` : ""}${icon("grape")}${tick(on.has(g.name))}</span>
          <b>${esc(g.name)}</b>${HINTS[g.name] ? `<span>${HINTS[g.name]}</span>` : `<span>${g.wines} ${ACC().plural(g.wines, "вино", "вина", "вин")}</span>`}
        </button>`).join("") || `<p class="muted">Такого сорта в каталоге нет</p>`}</div>
      ${!q && !st.allGrapes && all.length > GRAPES_SHOWN
        ? `<button class="link-btn taste-more" type="button" data-action="taste-all-grapes">Все сорта — ${all.length}</button>` : ""}`;
  }

  function makersHtml() {
    const on = new Set(st.profile.manufacturers);
    const q = st.makerQuery.trim().toLowerCase();
    const all = st.options.manufacturers;
    const list = (q ? all.filter((m) => m.name.toLowerCase().includes(q)) : all.slice(0, 16)).filter((m) => !on.has(m.name));
    return head("Любимые винодельни", "Необязательно — если есть, вино от них отмечу") + `
      ${on.size ? `<div class="chips taste-chosen">${[...on].map((n) => `<button class="chip chip--on" type="button"
        data-action="taste-pick" data-field="manufacturers" data-v="${esc(n)}" aria-pressed="true">${esc(n)} ×</button>`).join("")}</div>` : ""}
      <label class="taste-search">${icon("search")}<input type="search" data-role="maker-q" placeholder="Найти винодельню"
        value="${esc(st.makerQuery)}" autocomplete="off"></label>
      <div class="chips">${list.slice(0, 30).map((m) => `<button class="chip" type="button" data-action="taste-pick"
        data-field="manufacturers" data-v="${esc(m.name)}" aria-pressed="false">${esc(m.name)}</button>`).join("")
        || `<p class="muted">Ничего не нашлось</p>`}</div>`;
  }

  function pick(el) {
    const field = el.dataset.field;
    const v = el.dataset.v;
    const list = st.profile[field];
    const i = list.indexOf(v);
    if (i >= 0) list.splice(i, 1); else list.push(v);
    // перерисовать, сохранив прокрутку и фокус поиска
    const body = $(".taste__body");
    const top = body?.scrollTop || 0;
    renderTaste();
    if ($(".taste__body")) $(".taste__body").scrollTop = top;
  }

  function body() {
    const p = st.profile;
    return { colors: p.colors, sugars: p.sugars, grapes: p.grapes, manufacturers: p.manufacturers };
  }

  async function saveTaste() {
    const data = body();
    if (!signedIn()) {
      st.draft = data;
      closeTaste();
      askRegister("taste", () => { const d = st.draft; st.draft = null; if (d) sendTaste(d); });
      return;
    }
    closeTaste();
    await sendTaste(data);
  }

  async function sendTaste(data) {
    try {
      st.profile = await call("PUT", "/v1/me/taste", data);
      track("taste", { action: "save" });
      toast("Запомнил ваш вкус");
      refreshAll();
    } catch (err) { toast(err.message); }
  }

  function refreshAll() {
    const wine = W.current?.();
    if (wine && $("#screen-result .taste-slot")) onCard(wine);
    if ($("#profile-taste")) mountProfile($("#profile-taste"));
  }

  // ── Профиль: «Мой вкус» ─────────────────────────────────────────────────
  async function mountProfile(root) {
    if (!root) return;
    let p;
    try { p = await call("GET", "/v1/me/taste"); } catch (_) { return; }
    const style = [
      ...SUGARS.filter((s) => p.sugars.includes(s.k)).map((s) => s.t.toLowerCase()),
      ...COLORS.filter((c) => p.colors.includes(c.k)).map((c) => c.t.toLowerCase()),
    ].join(", ");
    const parts = [style && style[0].toUpperCase() + style.slice(1), p.grapes.slice(0, 3).join(", ")].filter(Boolean);
    const summary = parts.length ? parts.join(" · ")
      : "Расскажите, какое вино вы любите, — буду подсказывать после скана";
    root.innerHTML = `
      <button class="taste-banner" type="button" data-action="taste-open">
        <span class="taste-banner__icon">${icon("sparkles")}</span>
        <span><b>Мой вкус</b><span>${esc(summary)}</span>
          ${p.rated ? `<span>Оценено вин: ${p.rated} — подсказки учитывают их</span>` : ""}</span>
        ${icon("chevron")}
      </button>`;
  }

  // ── Обработчики ─────────────────────────────────────────────────────────
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action^='taste-']");
    if (!el) return;
    switch (el.dataset.action) {
      case "taste-open": openTaste(); break;
      case "taste-why": openWhy(); break;
      case "taste-close": closeTaste(); break;
      case "taste-back": st.step = Math.max(0, st.step - 1); renderTaste(); break;
      case "taste-next": if (st.step < STEPS.length - 1) { st.step += 1; renderTaste(); } else saveTaste(); break;
      case "taste-save": saveTaste(); break;
      case "taste-pick": pick(el); break;
      case "taste-all-grapes": st.allGrapes = true; renderTaste(); break;
    }
  });

  document.addEventListener("input", (e) => {
    const role = e.target.dataset?.role;
    if (role !== "grape-q" && role !== "maker-q") return;
    st[role === "grape-q" ? "grapeQuery" : "makerQuery"] = e.target.value;
    const pos = e.target.selectionStart;
    renderTaste();
    const input = $(`.taste [data-role=${role}]`);
    input?.focus();
    input?.setSelectionRange(pos, pos);
  });

  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $(".taste")) closeTaste(); });
  document.addEventListener("wine:card", (e) => onCard(e.detail.wine));
  document.addEventListener("wine:auth", () => { if ($("#screen-result .taste-slot")) refreshAll(); });

  window.WineTaste = { mountProfile, open: openTaste };
})();
