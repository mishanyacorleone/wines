/* Сканер вина: фото → /v1/scan → карточка с советами сомелье или «не найдено» + топ-5.
 *
 * Без сборки и фреймворков: страницу отдаёт тот же FastAPI, что и API.
 * Разметка карточки повторяет мобильную карточку вина vino-svoe.ru.
 * ?demo=match | ?demo=not_found — работа на заготовленных ответах из demo/,
 * чтобы вёрстку можно было смотреть без GPU и поднятого сервиса.
 */
(() => {
  "use strict";

  // Фото с телефона — 3024×4032 и 2–5 МБ. Сервису больше 1600 px не нужно
  // (SigLIP смотрит 512, VLM — ~0,5 Мп), а декодирование полного
  // кадра было самым долгим шагом на сервере. Сжимаем на клиенте.
  const MAX_SIDE = 1600;
  const JPEG_QUALITY = 0.9;

  const params = new URLSearchParams(location.search);
  const DEMO = params.get("demo");

  const $ = (sel, root = document) => root.querySelector(sel);
  const screens = ["scan", "loading", "result"];
  const state = { photoUrl: null, scan: null, current: null };

  // ── API ──────────────────────────────────────────────────────────────────
  const api = {
    async scan(blob) {
      if (DEMO) return demo(`scan-${DEMO === "not_found" ? "not_found" : "match"}.json`, 900);
      const form = new FormData();
      form.append("image", blob, "photo.jpg");
      return request("/v1/scan", { method: "POST", body: form });
    },
    wine(slug) {
      if (DEMO) return demo(`wine-${slug}.json`).catch(() => demo("wine.json"));
      return request(`/v1/wines/${encodeURIComponent(slug)}`);
    },
    dishes() {
      if (DEMO) return demo("dishes.json");
      return request("/v1/pairing/dishes");
    },
    pairing(dish, { style, exclude } = {}) {
      if (DEMO) return demo("pairing.json");
      const q = new URLSearchParams({ dish, limit: "10" });
      if (style) q.set("style", style);
      if (exclude) q.set("exclude", exclude);
      return request(`/v1/pairing?${q}`);
    },
    async search(q) {
      if (DEMO) {
        demoCatalog ??= await demo("catalog.json", 0);
        return searchLocal(demoCatalog, q);
      }
      return request(`/v1/catalog/search?${new URLSearchParams({ q, limit: "12" })}`);
    },
    feedback(body) {
      if (DEMO) return Promise.resolve({ ok: true });
      return request("/v1/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).catch(() => null); // отзыв не должен ломать сценарий
    },
  };

  async function request(url, init) {
    const res = await fetch(url, init);
    // Страницу открыли простым статическим сервером (http.server), без API:
    // честно говорим, что распознавать некому, а не «ошибка 501»
    if ([404, 405, 501].includes(res.status) && url.startsWith("/v1/")) {
      throw new Error("Сервис распознавания не запущен. Запустите python -m search_api и откройте http://<хост>:8080/app/");
    }
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail; } catch (_) { /* не JSON */ }
      throw new Error(detail || `Ошибка сервера (${res.status})`);
    }
    return res.json();
  }

  async function demo(file, delay = 250) {
    await new Promise((r) => setTimeout(r, delay));
    const res = await fetch(`demo/${file}`);
    if (!res.ok) throw new Error("нет демо-данных");
    return res.json();
  }

  // Демо-поиск в браузере — то же правило, что Sommelier.search на сервере:
  // все слова запроса — префиксы слов карточки, выше совпадения в названии
  let demoCatalog = null;
  const norm = (s) => String(s ?? "").toLowerCase().replaceAll("ё", "е");
  const splitWords = (s) => norm(s).split(/[\s,.;:«»"'()\-]+/).filter(Boolean);
  function searchLocal(rows, q) {
    const words = splitWords(q);
    if (!words.length) return [];
    return rows.map((w) => {
      const tokens = splitWords([w.title, w.manufacturer, w.region, w.category, ...(w.grapes || [])].join(" "));
      if (!words.every((x) => tokens.some((t) => t.startsWith(x)))) return null;
      const title = splitWords(w.title);
      return { w, s: words.filter((x) => title.some((t) => t.startsWith(x))).length, r: w.public_rating || 0 };
    }).filter(Boolean).sort((a, b) => b.s - a.s || b.r - a.r).slice(0, 12).map((x) => x.w);
  }

  // ── Утилиты ──────────────────────────────────────────────────────────────
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (name, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const img = (src, cls, alt = "") => (src ? `<img class="${cls}" loading="lazy" src="${esc(src)}" alt="${esc(alt)}">` : "");

  const fmtRating = (r, digits = 2) => (r ? Number(r).toFixed(digits) : "");
  const fmtAlcohol = (a) => (a == null ? "—" : `${String(a).replace(".", ",").replace(/,0$/, "")}%`);
  const fmtTemp = (w) => {
    const s = w.sommelier?.serving;
    if (s?.temperature_min != null) {
      return s.temperature_min === s.temperature_max ? `${s.temperature_min}°C` : `${s.temperature_min}–${s.temperature_max}°C`;
    }
    return w.temperature ? `${w.temperature.replace("-", "–")}°C` : "—";
  };

  // Цвет плашки «Категория и цвет» — как цветной квадрат на сайте
  const SWATCH = {
    red: "linear-gradient(135deg,#B4434D,#6E1F2A)", white: "linear-gradient(135deg,#F6E7A8,#E4C46A)",
    orange: "linear-gradient(135deg,#F6B27A,#E07B3C)", rose: "linear-gradient(135deg,#F7C6C4,#E58E95)",
    sparkling: "linear-gradient(135deg,#FBF1C9,#E8D48E)", sweet: "linear-gradient(135deg,#E9A86B,#A8542C)",
  };

  function show(name, { push = true } = {}) {
    screens.forEach((s) => $(`#screen-${s}`).classList.toggle("screen--active", s === name));
    window.scrollTo(0, 0);
    // «Назад» на Android возвращает к сканеру, а не уводит со страницы
    if (push && name === "result") history.pushState({ screen: name }, "");
  }

  let toastTimer;
  function toast(text) {
    const el = $("#toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
  }

  // Уменьшает фото и применяет EXIF-поворот (createImageBitmap делает это сам)
  async function downscale(file) {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(bitmap.width * scale);
      canvas.height = Math.round(bitmap.height * scale);
      canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close?.();
      const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", JPEG_QUALITY));
      return blob || file;
    } catch (_) {
      return file; // старый браузер или HEIC — пусть сервер разбирается сам
    }
  }

  // ── Камера ───────────────────────────────────────────────────────────────
  // На ноутбуке <input capture> игнорируется и открывает выбор файла, поэтому
  // там камера открывается прямо в странице через getUserMedia. На телефоне
  // лучше системная камера (автофокус, вспышка) — её открывает <input capture>.
  // getUserMedia работает только на https или localhost; иначе — выбор файла.
  const camera = { stream: null, devices: [], index: 0 };
  const isPhone = () => matchMedia("(pointer: coarse)").matches && /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);

  async function openCamera() {
    if (isPhone() || !navigator.mediaDevices?.getUserMedia) {
      if (!isPhone() && !window.isSecureContext) {
        toast("Камера в браузере работает только по https или localhost — выберите фото");
      }
      $("#input-camera").click();
      return;
    }
    $("#camera").hidden = false;
    document.body.style.overflow = "hidden";
    try {
      await startStream();
      const all = await navigator.mediaDevices.enumerateDevices();
      camera.devices = all.filter((d) => d.kind === "videoinput");
      // кнопка остаётся в разметке, чтобы спуск не съезжал с центра
      $("#camera-switch").classList.toggle("camera__side--off", camera.devices.length < 2);
      $(".camera__shutter").focus();
    } catch (err) {
      closeCamera();
      const denied = err?.name === "NotAllowedError" || err?.name === "SecurityError";
      toast(denied ? "Нет доступа к камере — разрешите его в браузере или выберите фото"
                   : "Камера не найдена — выберите фото из галереи");
      $("#input-gallery").click();
    }
  }

  async function startStream() {
    stopStream();
    const device = camera.devices[camera.index];
    camera.stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: device
        ? { deviceId: { exact: device.deviceId }, width: { ideal: 1920 }, height: { ideal: 1080 } }
        : { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    });
    const video = $("#camera-video");
    video.srcObject = camera.stream;
    await video.play().catch(() => {});
  }

  function stopStream() {
    camera.stream?.getTracks().forEach((t) => t.stop());
    camera.stream = null;
  }

  function closeCamera() {
    stopStream();
    $("#camera-video").srcObject = null;
    $("#camera").hidden = true;
    document.body.style.overflow = "";
  }

  function shoot() {
    const video = $("#camera-video");
    if (!video.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      closeCamera();
      if (blob) onPhoto(blob);
    }, "image/jpeg", JPEG_QUALITY);
  }

  // ── Поиск (лупа в шапке) ─────────────────────────────────────────────────
  let searchTimer = 0;
  let searchSeq = 0;
  function openSearch() {
    closeMenu();
    $("#search").hidden = false;
    document.body.style.overflow = "hidden";
    $("#search-input").focus();
  }
  function closeSearch() {
    if ($("#search").hidden) return;
    $("#search").hidden = true;
    document.body.style.overflow = "";
  }
  async function runSearch(q) {
    const seq = ++searchSeq;
    const box = $("#search-results");
    q = q.trim();
    if (q.length < 2) { box.innerHTML = ""; return; }
    const siteLink = `
      <a class="search-modal__site" href="https://vino-svoe.ru/search-result?substring=${encodeURIComponent(q)}" target="_blank" rel="noopener">
        <span>Искать «${esc(q)}» по всему сайту: статьи, винодельни, события</span>${icon("external")}
      </a>`;
    try {
      const wines = await api.search(q);
      if (seq !== searchSeq) return; // пришёл ответ на устаревший запрос
      box.innerHTML = (wines.length
        ? wines.map((w) => altRowHtml(w, "open")).join("")
        : `<p class="search-modal__empty">В каталоге вин ничего не нашлось</p>`) + siteLink;
    } catch (err) {
      if (seq === searchSeq) box.innerHTML = `<p class="search-modal__empty">${esc(err.message)}</p>${siteLink}`;
    }
  }

  // ── Меню разделов (мобильная версия) ─────────────────────────────────────
  const desktop = matchMedia("(min-width: 1200px)");
  function toggleMenu(open = !$("#header").classList.contains("core-header--open")) {
    $("#header").classList.toggle("core-header--open", open);
    $(".core-header__action-button").setAttribute("aria-expanded", String(open));
    document.body.classList.toggle("menu-open", open);
  }
  const closeMenu = () => toggleMenu(false);
  desktop.addEventListener("change", closeMenu);

  // ── Сканирование ─────────────────────────────────────────────────────────
  const STEPS = ["Сравниваем с каталогом…", "Читаем этикетку…", "Сверяем цвет и сорт…", "Почти готово…"];

  async function onPhoto(file) {
    if (!file) return;
    if (state.photoUrl) URL.revokeObjectURL(state.photoUrl);
    state.photoUrl = URL.createObjectURL(file);
    $("#loading-img").src = state.photoUrl;
    show("loading", { push: false });

    let step = 0;
    $("#loading-step").textContent = STEPS[0];
    const timer = setInterval(() => {
      step = Math.min(step + 1, STEPS.length - 1);
      $("#loading-step").textContent = STEPS[step];
    }, 700);

    try {
      const blob = await downscale(file);
      const res = await api.scan(blob);
      state.scan = res;
      if (res.status === "match" && res.wine) renderMatch(res);
      else renderNotFound(res);
      show("result");
    } catch (err) {
      show("scan", { push: false });
      toast(err.message || "Не удалось распознать фото. Попробуйте ещё раз");
    } finally {
      clearInterval(timer);
    }
  }

  // ── Карточка вина (как мобильная карточка на сайте) ──────────────────────
  function renderMatch(res) {
    const wine = res.wine;
    state.current = wine;
    $("#screen-result").innerHTML = `
      ${cardHtml(wine, `<span class="status-chip">${icon("check")}${esc(res.message)}</span>`)}
      <section class="ask-card" id="ask">
        <h2 class="ask-card__title">Это ваше вино?</h2>
        <div class="ask-card__btns">
          <button class="ui-button ui-button_primary" type="button" data-action="fb-yes">Да, это оно</button>
          <button class="ui-button ui-button_elevated" type="button" data-action="not-this">Нет, другое</button>
        </div>
      </section>
      ${sommelierHtml(wine)}
      ${tailHtml(wine)}`;
    $("#screen-result").dataset.alts = JSON.stringify(res.alternatives || []);
    bindCard();
  }

  function renderPicked(wine) {
    state.current = wine;
    const back = state.scan
      ? `<button class="back-link" type="button" data-action="back-to-alts">${icon("arrow-left")}К результату</button>`
      : "";
    $("#screen-result").innerHTML = `${back}${cardHtml(wine)}${sommelierHtml(wine)}${tailHtml(wine)}`;
    bindCard();
    window.scrollTo(0, 0);
  }

  function cardHtml(w, statusHtml = "") {
    const style = w.sommelier?.style?.key || "white";
    const colorLine = [w.category, w.color].filter(Boolean);
    return `
      <div class="title-block">
        ${statusHtml}
        <h1 class="title-block__title">${esc(w.title)}</h1>
        ${w.manufacturer ? `<a class="title-block__maker" href="${esc(w.url)}" target="_blank" rel="noopener">${esc(w.manufacturer)}</a>` : ""}
        ${w.public_rating ? `
          <a class="rating-chip" href="${esc(w.url)}" target="_blank" rel="noopener">
            ${icon("wine")}Народный рейтинг ${fmtRating(w.public_rating, 1).replace(/\.0$/, "")}${icon("chevron", "i--chevron")}
          </a>` : ""}
      </div>

      <div class="bottle">
        ${w.image_url ? `<img src="${esc(w.image_url)}" alt="Вино ${esc(w.category || "")} ${esc(w.title)}">` : `<div class="bottle__empty">${icon("wine")}</div>`}
      </div>

      <section class="info-card">
        ${w.region ? detailHtml(w.region_image ? img(w.region_image, "detail__image", "") : iconThumb("pin"), "Регион", esc(w.region)) : ""}
        ${w.grapes?.length ? detailHtml(w.grape_image ? img(w.grape_image, "detail__image") : iconThumb("grape"), "Сорт винограда", esc(w.grapes.join(", "))) : ""}
        ${colorLine.length ? detailHtml(`<span class="detail__image detail__image--swatch" style="--swatch:${SWATCH[style]}"></span>`,
          "Категория и цвет", colorLine.map(esc).join("<br>")) : ""}
      </section>

      ${w.background_image ? `<div class="visual">${img(w.background_image, "", "Виноград")}</div>` : ""}

      <div class="hero-cards">
        <div class="hero-card">
          <span class="round-icon">${icon("thermometer")}</span>
          <p class="hero-card__label">Температура<br>подачи</p>
          <p class="hero-card__value">${esc(fmtTemp(w))}</p>
        </div>
        <div class="hero-card">
          <span class="round-icon">${icon("percent")}</span>
          <p class="hero-card__label">Крепость<br>вина</p>
          <p class="hero-card__value">${esc(fmtAlcohol(w.alcohol))}</p>
        </div>
      </div>

      ${w.sommelier?.pairings?.length ? `
        <section class="dishes-card" aria-label="Сочетание с блюдами">
          <div class="dishes-card__left">
            <span class="round-icon">${icon("utensils")}</span>
            <p class="dishes-card__label">Сочетание с блюдами</p>
          </div>
          <div class="dishes-card__slider">${w.sommelier.pairings.map((p) => dishItemHtml(p, "dish")).join("")}</div>
        </section>` : ""}`;
  }

  const iconThumb = (name) => `<span class="detail__image detail__image--icon">${icon(name)}</span>`;
  const detailHtml = (thumb, label, valueHtml) => `
    <div class="detail">${thumb}<div><p class="detail__label">${label}</p><p class="detail__value">${valueHtml}</p></div></div>`;

  function dishItemHtml(d, action) {
    const pic = d.image
      ? img(d.image, "wine-dish-item__image", d.dish)
      : `<span class="wine-dish-item__image wine-dish-item__emoji" aria-hidden="true">${esc(d.icon)}</span>`;
    return `
      <button class="wine-dish-item" type="button" data-action="${action}" data-dish="${esc(d.dish)}">
        ${pic}<span class="wine-dish-item__name">${esc(d.dish)}</span>
      </button>`;
  }

  function sommelierHtml(w) {
    const s = w.sommelier;
    if (!s) return "";
    const sv = s.serving || {};
    const level = s.style?.body_level;
    const scale = level
      ? `<span class="body-scale" aria-label="Тело: ${esc(s.style.body)}">${[1, 2, 3].map((i) => `<i class="${i <= level ? "on" : ""}"></i>`).join("")}</span>`
      : "";
    const advice = [
      ["thermometer", `Подача${sv.temperature ? ` при ${sv.temperature}` : ""}`, sv.tip],
      ["glass", "Бокал", sv.glass],
      ["wind", "Аэрация", sv.aeration],
      ["scale", `Тело: ${s.style?.body || ""}${scale}`, s.alcohol?.note],
    ].filter(([, , text]) => text);

    return `
      <section class="section" aria-labelledby="som-title">
        <h2 class="section__title" id="som-title">Совет сомелье</h2>
        <p class="section__lead">${esc(s.summary)}</p>
        <div class="info-card">
          ${advice.map(([ic, label, text]) => detailHtml(iconThumb(ic), label, esc(text))).join("")}
        </div>
        ${s.pairings?.length ? `
          <div class="info-card">
            ${s.pairings.map((p) => `
              <button class="pairing" type="button" data-action="dish" data-dish="${esc(p.dish)}">
                ${p.image ? img(p.image, "pairing__image", p.dish) : `<span class="pairing__image wine-dish-item__emoji">${esc(p.icon)}</span>`}
                <span><span class="pairing__dish">${esc(p.dish)}</span>${p.tip ? `<span class="pairing__tip">${esc(p.tip)}</span>` : ""}</span>
                ${icon("chevron")}
              </button>`).join("")}
          </div>` : ""}
      </section>`;
  }

  function tailHtml(w) {
    return `
      ${w.description ? `
        <section class="section description" id="description">
          <p class="description__text">${esc(w.description)}</p>
          <button class="link-btn" type="button" data-action="more">Читать полностью</button>
        </section>` : ""}
      <div class="stack">
        <a class="ui-button ui-button_primary ui-button_large" href="${esc(w.url)}" target="_blank" rel="noopener">Открыть на сайте ${icon("external")}</a>
        <button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="camera">${icon("camera")}Сфотографировать другое вино</button>
      </div>
      ${w.similar?.length ? `
        <section class="section" aria-labelledby="similar-title">
          <h2 class="section__title" id="similar-title">Похожие вина</h2>
          <div class="carousel">${w.similar.map((x) => wineItemHtml(x, "open")).join("")}</div>
        </section>` : ""}`;
  }

  // Карточка вина в подборке (.wine-item_s)
  function wineItemHtml(w, action, badge = "") {
    const chip = badge
      ? `<span class="wine-item-rating wine-item-rating--accent">${esc(badge)}</span>`
      : (w.public_rating ? `<span class="wine-item-rating">${icon("wine")}${fmtRating(w.public_rating)}</span>` : "");
    return `
      <button class="wine-item" type="button" data-action="${action}" data-slug="${esc(w.slug)}">
        ${chip}
        <span class="wine-item__img">${img(w.image_url, "", w.title)}</span>
        <span>
          <span class="wine-item__title">${esc(w.title)}</span>
          <span class="wine-item__maker">${esc(w.manufacturer)}</span>
        </span>
        ${w.reasons?.length ? `<span class="reasons">${w.reasons.map((r) => `<span class="reason">${esc(r)}</span>`).join("")}</span>` : ""}
      </button>`;
  }

  function altRowHtml(w, action) {
    const meta = [w.manufacturer, w.category].filter(Boolean).join(" · ");
    return `
      <button class="alt-row" type="button" data-action="${action}" data-slug="${esc(w.slug)}">
        <span class="alt-row__img">${img(w.image_url, "", w.title)}</span>
        <span><span class="alt-row__title">${esc(w.title)}</span><span class="alt-row__meta">${esc(meta)}</span></span>
        ${icon("chevron")}
      </button>`;
  }

  function bindCard() {
    const desc = $("#description");
    if (!desc) return;
    const text = $(".description__text", desc);
    // кнопка нужна, только если текст действительно обрезан
    requestAnimationFrame(() => {
      if (text.scrollHeight <= text.clientHeight + 2) $(".link-btn", desc).hidden = true;
    });
  }

  // ── «Не найдено» ─────────────────────────────────────────────────────────
  // [заголовок, подводка, заголовок списка]
  const ANALOGS = ["Данное вино отсутствует в каталоге", "Но вот какие аналоги вы можете найти в каталоге «Своё Вино»:", "Аналоги"];
  const NOT_FOUND = {
    not_verified: ANALOGS,
    vlm_none: ANALOGS,
    low_similarity: ANALOGS,
    ambiguous: ["Уточните, какое это вино", "Нашли несколько очень похожих вин — выберите своё:", "Возможно, это оно"],
    empty: ["Не удалось распознать", "Попробуйте переснять: этикетка крупно, без бликов.", "Возможно, это оно"],
  };

  function renderNotFound(res) {
    const alts = res.alternatives || [];
    // Пустой top1 — сервис решил, что вина с фото нет в каталоге (то же, что
    // {"slug": null} в /v1/eval/predict); иначе текст — по причине отказа
    const texts = res.top1 === null && res.reason !== "empty" ? ANALOGS : NOT_FOUND[res.reason];
    const [title, lead, altsTitle] = texts || [ANALOGS[0], res.message, ANALOGS[2]];
    $("#screen-result").innerHTML = `
      <div class="title-block">
        <span class="status-chip">${icon("search-x")}Нет точного совпадения</span>
        <h1 class="title-block__title">${esc(title)}</h1>
      </div>
      ${state.photoUrl ? `<img class="photo-thumb" src="${state.photoUrl}" alt="Ваше фото">` : ""}
      <p class="notfound-lead">${esc(lead)}</p>
      ${alts.length ? `
        <section class="section" style="padding-top:32px" aria-labelledby="alts-title">
          <h2 class="section__title" id="alts-title">${esc(altsTitle)}</h2>
          <div class="grid-2">${alts.map((w, i) => wineItemHtml(w, "pick", i === 0 ? "Больше всего похоже" : "")).join("")}</div>
        </section>` : ""}
      <div class="stack">
        <button class="ui-button ui-button_primary ui-button_large" type="button" data-action="camera">${icon("camera")}Переснять</button>
        ${alts.length ? `<button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="none-match">Моего вина здесь нет</button>` : ""}
      </div>`;
    $("#screen-result").dataset.alts = JSON.stringify(alts);
  }

  // ── Шторка ───────────────────────────────────────────────────────────────
  let lastFocus = null;
  function openSheet(title, html) {
    lastFocus = document.activeElement;
    $("#sheet-title").textContent = title;
    $("#sheet-body").innerHTML = html;
    $("#sheet").hidden = false;
    document.body.style.overflow = "hidden";
    $("#sheet .sheet__close").focus();
  }
  function closeSheet() {
    if ($("#sheet").hidden) return;
    $("#sheet").hidden = true;
    document.body.style.overflow = "";
    lastFocus?.focus?.();
  }

  async function openDish(dish, { style, exclude } = {}) {
    openSheet(`Вина к блюду «${dish}»`, `<div class="skeleton"></div>`);
    try {
      const res = await api.pairing(dish, { style, exclude });
      $("#sheet-body").innerHTML = (style ? `<p class="sheet__hint">В том же стиле, что и ваше вино</p>` : "")
        + (res.wines.length ? res.wines.map((w) => altRowHtml(w, "open")).join("") : `<p class="muted">Подходящих вин не нашлось.</p>`);
    } catch (err) {
      $("#sheet-body").innerHTML = `<p class="muted">${esc(err.message)}</p>`;
    }
  }

  async function openWine(slug, { fromAlternative = false } = {}) {
    closeSheet();
    closeSearch();
    const predicted = state.scan?.wine?.slug || state.scan?.alternatives?.[0]?.slug || null;
    if (fromAlternative) {
      api.feedback({ action: "picked_alternative", predicted_slug: predicted, chosen_slug: slug,
        status: state.scan?.status, reason: state.scan?.reason });
    }
    try {
      renderPicked(await api.wine(slug));
      show("result");
    } catch (err) {
      toast(err.message);
    }
  }

  // ── Обработчики ──────────────────────────────────────────────────────────
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const alts = () => JSON.parse($("#screen-result").dataset.alts || "[]");

    switch (el.dataset.action) {
      case "home":
        e.preventDefault();
        closeSheet();
        show("scan", { push: false });
        break;
      case "camera":
        closeSheet();
        closeSearch();
        closeMenu();
        openCamera();
        break;
      case "camera-shoot":
        shoot();
        break;
      case "camera-close":
        closeCamera();
        break;
      case "camera-gallery":
        closeCamera();
        $("#input-gallery").click();
        break;
      case "camera-switch":
        camera.index = (camera.index + 1) % camera.devices.length;
        startStream().catch(() => toast("Не удалось переключить камеру"));
        break;
      case "search":
        openSearch();
        break;
      case "search-close":
        closeSearch();
        break;
      case "menu":
        // на широком экране эта кнопка — поиск, как на сайте
        if (desktop.matches) openSearch();
        else toggleMenu();
        break;
      case "close-sheet":
        closeSheet();
        break;
      case "fb-yes":
        api.feedback({ action: "confirmed", predicted_slug: state.current?.slug, chosen_slug: state.current?.slug,
          status: state.scan?.status, reason: state.scan?.reason });
        $("#ask").innerHTML = `<h2 class="ask-card__title">Спасибо!</h2><p class="ask-card__done">Это помогает нам распознавать точнее.</p>`;
        break;
      case "not-this":
        openSheet("Не то вино?", `<p class="sheet__hint">Выберите своё из похожих:</p>
          ${alts().map((w) => altRowHtml(w, "pick")).join("")}
          <button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="none-match">Моего вина здесь нет</button>`);
        break;
      case "pick":
        openWine(el.dataset.slug, { fromAlternative: true });
        break;
      case "open":
        openWine(el.dataset.slug);
        break;
      case "back-to-alts":
        if (state.scan?.status === "match") renderMatch(state.scan);
        else renderNotFound(state.scan || {});
        window.scrollTo(0, 0);
        break;
      case "none-match":
        api.feedback({ action: "none_match", predicted_slug: state.scan?.wine?.slug || state.scan?.alternatives?.[0]?.slug,
          status: state.scan?.status, reason: state.scan?.reason });
        closeSheet();
        toast("Спасибо! Передадим, что этого вина не хватает в каталоге");
        break;
      case "dish":
        openDish(el.dataset.dish, { style: state.current?.sommelier?.style?.key, exclude: state.current?.slug });
        break;
      case "dish-home":
        openDish(el.dataset.dish);
        break;
      case "more": {
        const box = $("#description");
        box.classList.toggle("description--open");
        el.textContent = box.classList.contains("description--open") ? "Свернуть" : "Читать полностью";
        break;
      }
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("#camera").hidden) closeCamera();
    else if (!$("#search").hidden) closeSearch();
    else if ($("#header").classList.contains("core-header--open")) closeMenu();
    else closeSheet();
  });

  $("#search-input").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(e.target.value), 200);
  });
  $("#search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    clearTimeout(searchTimer);
    runSearch($("#search-input").value);
  });

  window.addEventListener("popstate", () => {
    closeSheet();
    closeSearch();
    closeCamera();
    show("scan", { push: false });
  });

  ["#input-camera", "#input-gallery"].forEach((sel) => {
    $(sel).addEventListener("change", (e) => {
      closeSheet();
      onPhoto(e.target.files?.[0]);
      e.target.value = ""; // повторный выбор того же файла тоже должен срабатывать
    });
  });

  // Блюда на главной: «цифровой сомелье» работает и без фото
  api.dishes().then((dishes) => {
    $("#dish-grid").innerHTML = dishes.slice(0, 14).map((d) => dishItemHtml(d, "dish-home")).join("");
  }).catch(() => { $("#home-dishes").hidden = true; });

  if (DEMO) {
    $("#demo-banner").hidden = false;
    document.body.classList.add("demo");
  }

  // Демо: сразу показать экран результата, без выбора файла
  if (DEMO && params.has("autorun")) {
    const canvas = document.createElement("canvas");
    canvas.width = 30; canvas.height = 40;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#E9CFA0";
    ctx.fillRect(0, 0, 30, 40);
    canvas.toBlob((b) => onPhoto(b), "image/jpeg");
  }
})();
