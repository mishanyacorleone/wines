/* Личный кабинет: вход, профиль, история сканов, оценки, достижения, админка.
 *
 * Работает поверх сканера (app.js) и сервиса пользователей за шлюзом.
 * Сканировать можно без аккаунта: кабинет только записывает сканы в историю
 * вошедшего и ненавязчиво предлагает войти остальным. Сканы без аккаунта
 * копятся в браузере и переносятся в историю при входе.
 * Нет сервиса пользователей (запуск без шлюза) или демо-режим — кабинет
 * просто не показывается.
 */
(() => {
  "use strict";

  const W = window.Wine;
  if (!W || W.DEMO) return;
  const { $, esc, icon, img, show, toast, openSheet, closeSheet, closeMenu, openWine } = W;

  // Событие для аналитики (analytics.js): модуль про неё ничего не знает
  const track = (type, props = {}) => document.dispatchEvent(new CustomEvent("wine:track", { detail: { type, props } }));

  const PENDING_KEY = "wine.pendingScans";
  const NUDGE_KEY = "wine.nudgeDismissed";
  const MAX_PENDING = 20;
  const PAGE = 10;  // история сканов — по 10, дальше «Показать ещё» (решение владельца 19.10)

  const acc = {
    available: false,
    me: null,          // ответ /v1/me: user, stats, achievements
    presets: [],       // готовые аватары
    scanId: null,      // последний записанный скан — его правит «Не то вино?»
    history: null,     // { items, total }
    admin: { rules: [], regions: [], list: [], editing: null, tab: "achievements" },
    pops: [],          // очередь поздравлений
  };

  // ── API ──────────────────────────────────────────────────────────────────
  const FIELD_ERRORS = {
    password: "Пароль — не короче 8 символов",
    login: "Введите почту или номер телефона",
    name: "Имя — от 1 до 40 символов",
    title: "Заполните название",
    description: "Заполните описание",
    threshold: "Порог — целое число от 1",
  };

  async function call(method, url, body) {
    const init = { method, headers: {} };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch (_) {
      throw Object.assign(new Error("Нет связи с сервером"), { status: 0 });
    }
    if (res.status === 204) return null;
    let data = null;
    try { data = await res.json(); } catch (_) { /* не JSON: шлюз отдал 502 */ }
    if (!res.ok) {
      throw Object.assign(new Error(errorText(data?.detail, res.status)), { status: res.status });
    }
    return data;
  }

  function errorText(detail, status) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      const field = detail[0].loc?.[detail[0].loc.length - 1];
      return FIELD_ERRORS[field] || "Проверьте введённые данные";
    }
    if (status >= 502) return "Сервис аккаунтов недоступен — попробуйте чуть позже";
    return `Ошибка сервера (${status})`;
  }

  // ── Хранилище браузера: сканы без аккаунта ───────────────────────────────
  // Может быть недоступно (приватный режим) — тогда просто не переносим
  const store = {
    get(key, fallback) {
      try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (_) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; }
    },
    remove(key) { try { localStorage.removeItem(key); } catch (_) { /* нет хранилища */ } },
  };
  const session = {
    get(key) { try { return sessionStorage.getItem(key); } catch (_) { return null; } },
    set(key, v) { try { sessionStorage.setItem(key, v); } catch (_) { /* нет хранилища */ } },
  };
  const pending = () => store.get(PENDING_KEY, []);

  // Уменьшенная копия фото: в историю идёт превью, а не кадр 1600 px
  async function shrink(file, side, quality) {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
      const scale = Math.min(1, side / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(bitmap.width * scale);
      canvas.height = Math.round(bitmap.height * scale);
      canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close?.();
      return canvas;
    } catch (_) {
      return null;
    }
  }
  const canvasBlob = (canvas, q) => new Promise((r) => canvas.toBlob(r, "image/jpeg", q));

  // ── Аватар ───────────────────────────────────────────────────────────────
  function avatarHtml(user, cls = "") {
    if (user?.avatar?.url) return `<img class="avatar ${cls}" src="${esc(user.avatar.url)}" alt="">`;
    const preset = acc.presets.find((p) => p.id === user?.avatar?.preset) || acc.presets[0];
    if (!preset) return `<span class="avatar ${cls}">${icon("user")}</span>`;
    return `<span class="avatar ${cls}" style="--avatar-bg:${esc(preset.background)}" aria-hidden="true">${esc(preset.emoji)}</span>`;
  }

  function updateHeader() {
    const btn = $("#header-profile");
    const mobile = $("#mobile-profile");
    btn.hidden = mobile.hidden = !acc.available;
    if (!acc.available) return;
    const user = acc.me?.user;
    btn.innerHTML = user ? avatarHtml(user, "avatar--sm") : icon("user");
    btn.setAttribute("aria-label", user ? `Профиль: ${user.name}` : "Войти");
    btn.title = user ? "Профиль" : "Войти";
    mobile.textContent = user ? "Мой профиль" : "Войти";
  }

  function setMe(data) {
    acc.me = data;
    updateHeader();
    celebrate(data?.new_achievements);
  }

  // ── Вход и регистрация ───────────────────────────────────────────────────
  let afterAuth = null;

  function openAuth(mode = "login", then = null) {
    afterAuth = then ?? afterAuth;
    const register = mode === "register";
    const waiting = pending().length;
    openSheet(register ? "Регистрация" : "Вход", `
      <div class="auth-tabs" role="tablist">
        <button class="auth-tabs__tab ${register ? "" : "auth-tabs__tab--on"}" type="button" role="tab"
                aria-selected="${!register}" data-action="acc-auth" data-mode="login">Вход</button>
        <button class="auth-tabs__tab ${register ? "auth-tabs__tab--on" : ""}" type="button" role="tab"
                aria-selected="${register}" data-action="acc-auth" data-mode="register">Регистрация</button>
      </div>
      ${waiting ? `<p class="sheet__hint">${icon("clock")}${waiting} ${plural(waiting, "скан", "скана", "сканов")} без аккаунта сохранятся в вашей истории</p>` : ""}
      <form class="form" id="acc-auth-form" data-mode="${register ? "register" : "login"}" novalidate>
        ${register ? field("name", "Как вас зовут", `<input class="field__input" name="name" maxlength="40" autocomplete="given-name" placeholder="Необязательно">`) : ""}
        ${field("login", "Телефон или почта", `<input class="field__input" name="login" required autocomplete="username" inputmode="email" placeholder="+7 912 345-67-89 или name@mail.ru">`)}
        ${field("password", "Пароль", `<input class="field__input" name="password" type="password" required minlength="${register ? 8 : 1}"
              autocomplete="${register ? "new-password" : "current-password"}" placeholder="${register ? "Не короче 8 символов" : ""}">`)}
        <p class="form__error" role="alert" hidden></p>
        <button class="ui-button ui-button_primary ui-button_large" type="submit">${register ? "Зарегистрироваться" : "Войти"}</button>
        <p class="form__note">Сканировать вина можно и без аккаунта — он нужен, чтобы сохранять историю, оценки и достижения</p>
      </form>`);
    $("#acc-auth-form input")?.focus();
  }

  const field = (name, label, control) => `
    <label class="field"><span class="field__label">${label}</span>${control}</label>`;

  async function submitAuth(form) {
    const mode = form.dataset.mode;
    const data = Object.fromEntries(new FormData(form));
    const error = $(".form__error", form);
    const button = $("button[type=submit]", form);
    error.hidden = true;
    button.disabled = true;
    try {
      const me = await call("POST", `/v1/auth/${mode}`, {
        login: data.login, password: data.password, ...(mode === "register" ? { name: data.name || null } : {}),
      });
      setMe(me);
      closeSheet();
      track(mode === "register" ? "register" : "login");
      toast(mode === "register" ? `Добро пожаловать, ${me.user.name}!` : `С возвращением, ${me.user.name}!`);
      await importPending();
      const then = afterAuth;
      afterAuth = null;
      document.dispatchEvent(new CustomEvent("wine:auth", { detail: { user: me.user } }));
      if (typeof then === "function") then();  // engage.js: сохранить запись дневника, серию, подарок
      else if (then === "profile") openProfile();
      else if ($("#screen-map")?.classList.contains("screen--active")) window.WineMap?.open({ push: false });
      else refreshSlot();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      button.disabled = false;
    }
  }

  async function logout() {
    await call("POST", "/v1/auth/logout").catch(() => null);
    acc.me = acc.history = acc.scanId = null;
    updateHeader();
    show("scan", { push: false });
    history.replaceState(null, "", location.pathname + location.search);
    toast("Вы вышли из аккаунта");
    document.dispatchEvent(new CustomEvent("wine:auth", { detail: { user: null } }));
  }

  async function deleteAccount() {
    if (!confirm("Удалить аккаунт вместе с историей сканов, оценками и достижениями? Это нельзя отменить.")) return;
    try {
      await call("DELETE", "/v1/me");
      acc.me = acc.history = acc.scanId = null;
      updateHeader();
      show("scan", { push: false });
      history.replaceState(null, "", location.pathname + location.search);
      toast("Аккаунт удалён");
    } catch (err) {
      toast(err.message);
    }
  }

  // ── Сканы: запись в историю или предложение войти ────────────────────────
  async function onScanned({ res, file }) {
    acc.scanId = null;
    if (!acc.available) return;
    const slug = res.status === "match" ? res.wine?.slug : null;
    if (acc.me) {
      await record(slug, file);
    } else {
      await keepPending(slug, file);
      refreshSlot();
    }
  }

  async function record(slug, file) {
    const form = new FormData();
    form.append("status", slug ? "match" : "not_found");
    if (slug) form.append("slug", slug);
    const canvas = file && await shrink(file, 480, 0.8);
    const blob = canvas && await canvasBlob(canvas, 0.8);
    if (blob) form.append("photo", blob, "scan.jpg");
    try {
      const out = await call("POST", "/v1/me/scans", form);
      acc.scanId = out.scan.id;
      acc.history = null;
      refreshSlot();
      celebrate(out.new_achievements, out.new_region);
      prizes(out.new_prizes);
    } catch (err) {
      if (err.status === 401) { acc.me = null; updateHeader(); await keepPending(slug, file); refreshSlot(); }
      // остальные сбои записи не мешают смотреть карточку вина
    }
  }

  async function keepPending(slug, file) {
    const canvas = file && await shrink(file, 320, 0.7);
    const photo = canvas ? canvas.toDataURL("image/jpeg", 0.7) : null;
    const list = [...pending(), { slug, status: slug ? "match" : "not_found", scanned_at: new Date().toISOString(), photo }];
    // Не влезло в хранилище с фото — сохраняем без фото, скан важнее превью
    if (!store.set(PENDING_KEY, list.slice(-MAX_PENDING))) {
      store.set(PENDING_KEY, list.slice(-MAX_PENDING).map((x) => ({ ...x, photo: null })));
    }
  }

  async function importPending() {
    const list = pending();
    if (!list.length || !acc.me) return;
    let moved = 0;
    const earned = [];
    const regions = [];
    for (const item of list) {
      const form = new FormData();
      form.append("status", item.slug ? "match" : "not_found");
      if (item.slug) form.append("slug", item.slug);
      if (item.scanned_at) form.append("scanned_at", item.scanned_at);
      if (item.photo) {
        const blob = await fetch(item.photo).then((r) => r.blob()).catch(() => null);
        if (blob) form.append("photo", blob, "scan.jpg");
      }
      try {
        const out = await call("POST", "/v1/me/scans", form);
        moved += 1;
        earned.push(...out.new_achievements);
        if (out.new_region) regions.push(out.new_region);
        prizes(out.new_prizes);
        acc.scanId = out.scan.id; // последний перенесённый — текущий скан на экране
      } catch (_) { /* вино сняли с каталога — пропускаем */ }
    }
    store.remove(PENDING_KEY);
    acc.history = null;
    if (moved) toast(`${moved} ${plural(moved, "скан перенесён", "скана перенесены", "сканов перенесены")} в историю`);
    regions.forEach((r) => celebrate([], r));
    celebrate(earned);
  }

  // Призы челленджей за скан — показывает engage.js
  const prizes = (list) => { if (list?.length) document.dispatchEvent(new CustomEvent("wine:prizes", { detail: list })); };

  async function onPicked({ slug }) {
    if (!acc.available) return;
    if (acc.me && acc.scanId) {
      try {
        const out = await call("PATCH", `/v1/me/scans/${acc.scanId}`, { slug });
        acc.history = null;
        celebrate(out.new_achievements, out.new_region);
        prizes(out.new_prizes);
      } catch (_) { /* не критично: скан останется с исходным ответом */ }
    } else if (!acc.me) {
      const list = pending();
      if (list.length) {
        Object.assign(list[list.length - 1], { slug, status: "match" });
        store.set(PENDING_KEY, list);
      }
    }
  }

  // Плашка под результатом: «сохранено в истории» или ненавязчивое «войдите»
  function refreshSlot() {
    const slot = $("#account-slot");
    if (!slot || !acc.available) return;
    if (acc.me) {
      slot.innerHTML = acc.scanId ? `
        <div class="saved-note">${icon("check")}<span>Сохранено в истории</span>
          <button class="link-btn" type="button" data-action="acc-profile">Мой профиль</button>
        </div>` : "";
      return;
    }
    if (session.get(NUDGE_KEY)) { slot.innerHTML = ""; return; }
    const count = pending().length;
    track("nudge_shown");
    slot.innerHTML = `
      <section class="nudge" aria-label="Сохранить историю сканов">
        <span class="round-icon">${icon("clock")}</span>
        <div class="nudge__text">
          <p class="nudge__title">Сохраните историю сканов</p>
          <p class="nudge__lead">Войдите — ${count > 1 ? `эти ${count} ${plural(count, "скан", "скана", "сканов")}` : "этот скан"} и следующие
            останутся в профиле, а за вина из разных регионов вы получите достижения</p>
          <div class="nudge__btns">
            <button class="ui-button ui-button_primary ui-button_round" type="button" data-action="acc-auth" data-mode="register">Зарегистрироваться</button>
            <button class="ui-button ui-button_elevated ui-button_round" type="button" data-action="acc-auth" data-mode="login">Войти</button>
          </div>
        </div>
        <button class="ui-button ui-button_transparent nudge__close" type="button" data-action="acc-nudge-close" aria-label="Не сейчас">${icon("x")}</button>
      </section>`;
  }

  // ── Профиль ──────────────────────────────────────────────────────────────
  async function openProfile({ push = true } = {}) {
    closeMenu();
    if (!acc.me) {
      // могли войти в другой вкладке
      try { setMe(await call("GET", "/v1/me")); } catch (_) { openAuth("login", "profile"); return; }
    }
    show("profile", { push });
    $("#screen-profile").innerHTML = `<div class="skeleton"></div>`;
    try {
      const [me, scans] = await Promise.all([call("GET", "/v1/me"), call("GET", `/v1/me/scans?limit=${PAGE}`)]);
      setMe(me);
      acc.history = scans;
      renderProfile();
    } catch (err) {
      if (err.status === 401) { acc.me = null; updateHeader(); show("scan", { push: false }); openAuth("login", "profile"); return; }
      $("#screen-profile").innerHTML = `<p class="muted">${esc(err.message)}</p>`;
    }
  }

  function renderProfile() {
    const { user, stats, achievements } = acc.me;
    const earned = achievements.filter((a) => a.earned).length;
    $("#screen-profile").innerHTML = `
      <button class="back-link" type="button" data-action="home">${icon("arrow-left")}К распознаванию вин</button>
      <div class="profile-head">
        <button class="profile-avatar" type="button" data-action="acc-avatar" aria-label="Сменить аватар">
          ${avatarHtml(user, "avatar--xl")}
          <span class="profile-avatar__edit">${icon("pencil")}</span>
        </button>
        <h1 class="title-block__title profile-name">
          <span>${esc(user.name)}</span>
          <button class="ui-button ui-button_transparent" type="button" data-action="acc-name" aria-label="Изменить имя">${icon("pencil")}</button>
        </h1>
        <p class="profile-login">${esc(user.login)}</p>
      </div>

      <div class="stat-row">
        ${statHtml(stats.scans, plural(stats.scans, "скан", "скана", "сканов"))}
        ${statHtml(stats.regions, `${plural(stats.regions, "регион", "региона", "регионов")}${stats.regions_total ? ` из ${stats.regions_total}` : ""}`)}
        ${statHtml(earned, `${plural(earned, "достижение", "достижения", "достижений")} из ${achievements.length}`)}
      </div>

      <div id="profile-taste"></div>
      <div id="profile-engage"></div>

      ${window.WineMap ? `
        <section class="section" aria-labelledby="pmap-title">
          <h2 class="section__title" id="pmap-title">Винная карта</h2>
          <div id="profile-map"><div class="skeleton"></div></div>
        </section>` : ""}

      <section class="section" aria-labelledby="achv-title">
        <div class="section__head">
          <h2 class="section__title" id="achv-title">Достижения</h2>
          <span class="section__count">${earned} из ${achievements.length}</span>
        </div>
        ${achievements.length
          ? `<div class="achv-grid">${achievements.map(achvHtml).join("")}</div>`
          : `<p class="muted">Скоро здесь появятся достижения</p>`}
      </section>

      <div id="profile-diary"></div>

      <section class="section" aria-labelledby="history-title">
        <div class="section__head">
          <h2 class="section__title" id="history-title">История сканов</h2>
          ${acc.history?.total ? `<span class="section__count">${acc.history.total}</span>` : ""}
        </div>
        <div class="history" id="history">${historyHtml()}</div>
      </section>

      <div class="stack profile-actions">
        ${user.role === "admin" ? `<button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="acc-admin">${icon("settings")}Управление</button>` : ""}
        <button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="acc-logout">${icon("logout")}Выйти</button>
        ${user.role === "admin" ? "" : `<button class="link-btn profile-delete" type="button" data-action="acc-delete">Удалить аккаунт</button>`}
      </div>`;
    window.WineTaste?.mountProfile($("#profile-taste"));
    window.WineEngage?.mountProfile($("#profile-engage"), $("#profile-diary"));
    window.WineMap?.previewHtml()
      .then((html) => { if ($("#profile-map")) $("#profile-map").innerHTML = html; })
      .catch(() => $("#profile-map")?.closest(".section")?.remove());
  }

  const statHtml = (value, label) => `
    <div class="hero-card stat"><p class="hero-card__value">${value}</p><p class="hero-card__label">${label}</p></div>`;

  function achvHtml(a) {
    const pct = Math.round((a.progress / Math.max(1, a.threshold)) * 100);
    return `
      <button class="achv ${a.earned ? "" : "achv--locked"}" type="button" data-action="acc-achv" data-id="${a.id}">
        <span class="achv__img">${a.image_url ? img(a.image_url, "", "") : icon("trophy")}
          ${a.earned ? "" : `<span class="achv__lock">${icon("lock")}</span>`}</span>
        <span class="achv__title">${esc(a.title)}</span>
        ${a.earned
          ? `<span class="achv__meta">${esc(fmtDate(a.earned_at, false))}</span>`
          : `<span class="achv__bar" aria-label="${a.progress} из ${a.threshold}"><i style="width:${pct}%"></i></span>
             <span class="achv__meta">${a.progress} из ${a.threshold}</span>`}
      </button>`;
  }

  function openAchievement(id) {
    const a = acc.me?.achievements.find((x) => String(x.id) === String(id));
    if (!a) return;
    openSheet(a.title, `
      <div class="achv-detail ${a.earned ? "" : "achv--locked"}">
        <span class="achv-detail__img">${a.image_url ? img(a.image_url, "", "") : icon("trophy")}</span>
        <p class="achv-detail__desc">${esc(a.description)}</p>
        <p class="achv-detail__status">${a.earned
          ? `${icon("check")}Получено ${esc(fmtDate(a.earned_at, false))}`
          : `Прогресс: ${a.progress} из ${a.threshold}`}</p>
        ${a.earned && window.WineShare ? shareAchievementBtn(a, "ui-button ui-button_elevated ui-button_round") : ""}
      </div>`);
    $("#sheet").classList.add("achv-sheet");
  }

  function historyHtml() {
    const items = acc.history?.items || [];
    if (!items.length) {
      return `
        <div class="empty">
          <p>Здесь появятся вина, которые вы отсканируете</p>
          <button class="ui-button ui-button_primary ui-button_round" type="button" data-action="camera">${icon("camera")}Сфотографировать этикетку</button>
        </div>`;
    }
    const more = items.length < acc.history.total
      ? `<button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="acc-more">Показать ещё</button>` : "";
    return items.map(scanRowHtml).join("") + more;
  }

  function scanRowHtml(s) {
    const w = s.wine;
    const pic = s.photo_url || w?.image_url;
    const meta = w ? [w.manufacturer, w.region].filter(Boolean).join(" · ") : "Вина нет в каталоге";
    const main = `
      <span class="scan-row__img">${pic ? img(pic, "", "") : icon("wine")}</span>
      <span class="scan-row__text">
        <span class="scan-row__title">${esc(w?.title || "Не распознано")}</span>
        <span class="scan-row__meta">${esc(meta)}</span>
        <span class="scan-row__date">${esc(fmtDate(s.scanned_at, true))}</span>
      </span>`;
    return `
      <div class="scan-row" data-scan="${s.id}">
        ${s.slug
          ? `<button class="scan-row__main" type="button" data-action="acc-scan-open" data-slug="${esc(s.slug)}">${main}</button>`
          : `<div class="scan-row__main scan-row__main--static">${main}</div>`}
        ${s.slug ? `
          <div class="scan-row__rate" role="group" aria-label="Оценка вина">
            <button class="rate-btn ${s.rating === 1 ? "rate-btn--on" : ""}" type="button" data-action="acc-rate" data-slug="${esc(s.slug)}"
                    data-value="1" aria-pressed="${s.rating === 1}" aria-label="Нравится">${icon("thumb-up")}</button>
            <button class="rate-btn ${s.rating === -1 ? "rate-btn--on rate-btn--down" : ""}" type="button" data-action="acc-rate" data-slug="${esc(s.slug)}"
                    data-value="-1" aria-pressed="${s.rating === -1}" aria-label="Не нравится">${icon("thumb-down")}</button>
          </div>` : ""}
      </div>`;
  }

  async function rate(slug, value) {
    // Повторное нажатие на ту же оценку снимает её
    const current = acc.history?.items.find((s) => s.slug === slug)?.rating || 0;
    const next = current === value ? 0 : value;
    try {
      const out = await call("PUT", `/v1/me/ratings/${encodeURIComponent(slug)}`, { value: next });
      acc.history.items.forEach((s) => { if (s.slug === slug) s.rating = next; });
      track("rate", { value: next });
      $("#history").innerHTML = historyHtml();
      celebrate(out.new_achievements);
      if (out.new_achievements?.length) refreshMeQuietly();
    } catch (err) {
      toast(err.message);
    }
  }

  async function loadMore() {
    try {
      const page = await call("GET", `/v1/me/scans?limit=${PAGE}&offset=${acc.history.items.length}`);
      acc.history = { items: [...acc.history.items, ...page.items], total: page.total };
      $("#history").innerHTML = historyHtml();
    } catch (err) {
      toast(err.message);
    }
  }

  async function refreshMeQuietly() {
    try {
      acc.me = await call("GET", "/v1/me");
      if ($("#screen-profile").classList.contains("screen--active")) renderProfile();
    } catch (_) { /* профиль обновится при следующем открытии */ }
  }

  // ── Имя и аватар ─────────────────────────────────────────────────────────
  function openNameEditor() {
    openSheet("Как вас зовут?", `
      <form class="form" id="acc-name-form" novalidate>
        ${field("name", "Имя", `<input class="field__input" name="name" maxlength="40" required value="${esc(acc.me.user.name)}" autocomplete="given-name">`)}
        <p class="form__error" role="alert" hidden></p>
        <button class="ui-button ui-button_primary ui-button_large" type="submit">Сохранить</button>
      </form>`);
    const input = $("#acc-name-form input");
    input.focus();
    input.select();
  }

  async function submitName(form) {
    const name = new FormData(form).get("name").trim();
    const error = $(".form__error", form);
    if (!name) { error.textContent = "Имя не может быть пустым"; error.hidden = false; return; }
    try {
      acc.me.user = await call("PATCH", "/v1/me", { name });
      closeSheet();
      updateHeader();
      renderProfile();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }

  function openAvatarPicker() {
    const current = acc.me.user.avatar;
    openSheet("Аватар", `
      <div class="avatar-grid">
        ${acc.presets.map((p) => `
          <button class="avatar-pick ${current.preset === p.id ? "avatar-pick--on" : ""}" type="button"
                  data-action="acc-preset" data-id="${esc(p.id)}" aria-pressed="${current.preset === p.id}">
            <span class="avatar avatar--lg" style="--avatar-bg:${esc(p.background)}" aria-hidden="true">${esc(p.emoji)}</span>
            <span class="avatar-pick__label">${esc(p.label)}</span>
          </button>`).join("")}
      </div>
      <label class="ui-button ui-button_elevated ui-button_large" for="acc-avatar-file">${icon("upload")}Загрузить своё фото</label>
      <input class="visually-hidden" type="file" id="acc-avatar-file" accept="image/*">`);
  }

  async function setPreset(id) {
    try {
      acc.me.user = await call("PATCH", "/v1/me", { avatar_preset: id });
      closeSheet();
      updateHeader();
      renderProfile();
    } catch (err) {
      toast(err.message);
    }
  }

  async function uploadAvatar(file) {
    if (!file) return;
    const canvas = await shrink(file, 800, 0.9);
    const blob = canvas ? await canvasBlob(canvas, 0.9) : file;
    const form = new FormData();
    form.append("image", blob, "avatar.jpg");
    try {
      acc.me.user = await call("PUT", "/v1/me/avatar", form);
      closeSheet();
      updateHeader();
      renderProfile();
    } catch (err) {
      toast(err.message);
    }
  }

  const shareAchievementBtn = (a, cls) => `
    <button class="${cls}" type="button" data-action="share-achievement" data-title="${esc(a.title)}"
            data-description="${esc(a.description)}" data-image="${esc(a.image_url || "")}">${icon("share")}Поделиться</button>`;

  // ── Поздравление с достижением ───────────────────────────────────────────
  // Очередь поздравлений: сначала новый регион на карте, затем достижения
  function celebrate(list, region = null) {
    const items = (list || []).map((a) => ({ type: "achievement", a }));
    items.forEach(({ a }) => track("achievement", { title: a.title }));
    if (region && window.WineMap) {
      const firstAchievement = acc.pops.findIndex((p) => p.type === "achievement");
      acc.pops.splice(firstAchievement < 0 ? acc.pops.length : firstAchievement, 0, { type: "region", name: region });
    }
    if (!items.length && !region) return;
    acc.pops.push(...items);
    if (!$(".achv-pop") && !acc.popOpening) nextPop();
  }

  async function nextPop() {
    const item = acc.pops.shift();
    if (!item) return;
    acc.popOpening = true;
    let body;
    if (item.type === "region") {
      try {
        body = `${await window.WineMap.popHtml(item.name)}
          <button class="ui-button ui-button_primary ui-button_large" type="button" data-action="acc-pop-close">Отлично!</button>
          <button class="link-btn" type="button" data-action="acc-pop-map">Открыть карту</button>`;
      } catch (_) {
        acc.popOpening = false;
        nextPop();
        return;
      }
    } else {
      const a = item.a;
      body = `
        <p class="achv-pop__kicker">Новое достижение</p>
        <span class="achv-pop__img">${a.image_url ? img(a.image_url, "", "") : icon("trophy")}</span>
        <h2 class="achv-pop__title">${esc(a.title)}</h2>
        <p class="achv-pop__desc">${esc(a.description)}</p>
        <button class="ui-button ui-button_primary ui-button_large" type="button" data-action="acc-pop-close">Отлично!</button>
        ${window.WineShare ? shareAchievementBtn(a, "link-btn") : ""}
        <button class="link-btn" type="button" data-action="acc-pop-profile">Все достижения</button>`;
    }
    const el = document.createElement("div");
    el.className = "achv-pop";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-label", item.type === "region" ? "Новый регион на карте" : "Новое достижение");
    el.innerHTML = `<div class="achv-pop__backdrop" data-action="acc-pop-close"></div><div class="achv-pop__card">${body}</div>`;
    document.body.appendChild(el);
    acc.popOpening = false;
    $(".achv-pop__card .ui-button", el).focus();
  }

  function closePop() {
    $(".achv-pop")?.remove();
    nextPop();
  }

  // ── Админка: достижения ──────────────────────────────────────────────────
  async function openAdmin({ push = true, tab = null } = {}) {
    if (acc.me?.user.role !== "admin") {
      toast("Раздел только для администратора");
      return;
    }
    if (tab) acc.admin.tab = tab;
    show("admin", { push });
    $("#screen-admin").innerHTML = `<div class="skeleton"></div>`;
    try {
      const [rules, list] = await Promise.all([
        call("GET", "/v1/admin/achievements/rules"), call("GET", "/v1/admin/achievements"),
      ]);
      Object.assign(acc.admin, { rules: rules.rules, regions: rules.regions, list, editing: null });
      renderAdmin();
    } catch (err) {
      $("#screen-admin").innerHTML = `<p class="muted">${esc(err.message)}</p>`;
    }
  }

  // Вкладки админки: достижения | регионы винной карты (map.js)
  const adminTabsHtml = () => window.WineMap ? `
    <div class="seg admin-tabs" role="tablist" aria-label="Раздел админки">
      <button class="seg__btn ${acc.admin.tab === "achievements" ? "seg__btn--on" : ""}" type="button" role="tab"
              aria-selected="${acc.admin.tab === "achievements"}" data-action="acc-admin-tab" data-tab="achievements">Достижения</button>
      <button class="seg__btn ${acc.admin.tab === "map" ? "seg__btn--on" : ""}" type="button" role="tab"
              aria-selected="${acc.admin.tab === "map"}" data-action="acc-admin-tab" data-tab="map">Регионы карты</button>
      ${window.WineShare ? `<button class="seg__btn ${acc.admin.tab === "share" ? "seg__btn--on" : ""}" type="button" role="tab"
              aria-selected="${acc.admin.tab === "share"}" data-action="acc-admin-tab" data-tab="share">Поделиться</button>` : ""}
      ${window.WineAnalytics ? `<button class="seg__btn ${acc.admin.tab === "analytics" ? "seg__btn--on" : ""}" type="button" role="tab"
              aria-selected="${acc.admin.tab === "analytics"}" data-action="acc-admin-tab" data-tab="analytics">Аналитика</button>` : ""}
      ${window.WineCatalogAdmin ? `<button class="seg__btn ${acc.admin.tab === "catalog" ? "seg__btn--on" : ""}" type="button" role="tab"
              aria-selected="${acc.admin.tab === "catalog"}" data-action="acc-admin-tab" data-tab="catalog">Каталог</button>` : ""}
      ${window.WineEngageAdmin ? `<button class="seg__btn ${acc.admin.tab === "engage" ? "seg__btn--on" : ""}" type="button" role="tab"
              aria-selected="${acc.admin.tab === "engage"}" data-action="acc-admin-tab" data-tab="engage">Задания и призы</button>` : ""}
    </div>` : "";

  function renderAdmin() {
    if (acc.admin.tab === "analytics" && window.WineAnalytics) {
      $("#screen-admin").innerHTML = `
        <button class="back-link" type="button" data-action="acc-profile">${icon("arrow-left")}К профилю</button>
        <div class="title-block">
          <h1 class="title-block__title">Аналитика</h1>
          <p class="title-block__lead">Как пользуются сканером: сканы, посетители, регистрации, шары и спрос на вина вне каталога</p>
          ${adminTabsHtml()}
        </div>
        <div id="admin-analytics"></div>`;
      document.body.classList.add("screen-wide");
      window.WineAnalytics.mountDashboard($("#admin-analytics"));
      return;
    }
    document.body.classList.remove("screen-wide");
    if (acc.admin.tab === "engage" && window.WineEngageAdmin) {
      $("#screen-admin").innerHTML = `
        <button class="back-link" type="button" data-action="acc-profile">${icon("arrow-left")}К профилю</button>
        <div class="title-block">
          <h1 class="title-block__title">Задания и призы</h1>
          <p class="title-block__lead">Вопрос дня, челленджи, промокоды и «Мой винный год» — то, ради чего возвращаются</p>
          ${adminTabsHtml()}
        </div>
        <div id="admin-engage"></div>`;
      window.WineEngageAdmin.mountAdmin($("#admin-engage"));
      return;
    }
    if (acc.admin.tab === "catalog" && window.WineCatalogAdmin) {
      $("#screen-admin").innerHTML = `
        <button class="back-link" type="button" data-action="acc-profile">${icon("arrow-left")}К профилю</button>
        <div class="title-block">
          <h1 class="title-block__title">Каталог вин</h1>
          <p class="title-block__lead">Новые вина с vino-svoe.ru попадают в поиск без перезапуска — по кнопке или по расписанию</p>
          ${adminTabsHtml()}
        </div>
        <div id="admin-catalog"></div>`;
      window.WineCatalogAdmin.mountAdmin($("#admin-catalog"));
      return;
    }
    if (acc.admin.tab === "share" && window.WineShare) {
      $("#screen-admin").innerHTML = `
        <button class="back-link" type="button" data-action="acc-profile">${icon("arrow-left")}К профилю</button>
        <div class="title-block">
          <h1 class="title-block__title">Поделиться</h1>
          <p class="title-block__lead">Текст, с которым пользователи делятся найденным вином</p>
          ${adminTabsHtml()}
        </div>
        <div id="admin-share"></div>`;
      window.WineShare.mountAdmin($("#admin-share"));
      return;
    }
    if (acc.admin.tab === "map" && window.WineMap) {
      $("#screen-admin").innerHTML = `
        <button class="back-link" type="button" data-action="acc-profile">${icon("arrow-left")}К профилю</button>
        <div class="title-block">
          <h1 class="title-block__title">Регионы карты</h1>
          <p class="title-block__lead">Вино встаёт на карту, если его регион совпал с названием региона карты. Остальные — здесь, их сопоставляет админ</p>
          ${adminTabsHtml()}
        </div>
        <div id="admin-map"></div>`;
      window.WineMap.mountAdmin($("#admin-map"));
      return;
    }
    const { list, rules } = acc.admin;
    const ruleOf = (type) => rules.find((r) => r.type === type);
    $("#screen-admin").innerHTML = `
      <button class="back-link" type="button" data-action="acc-profile">${icon("arrow-left")}К профилю</button>
      <div class="title-block">
        <h1 class="title-block__title">Достижения</h1>
        <p class="title-block__lead">Условие собирается из готовых правил. Новое достижение получат и те, кто уже выполнил условие, — при следующем входе в профиль</p>
        ${adminTabsHtml()}
      </div>
      <section class="info-card admin-form-card" id="admin-form-card">${adminFormHtml()}</section>
      <section class="section" aria-labelledby="admin-list-title">
        <div class="section__head">
          <h2 class="section__title" id="admin-list-title">Все достижения</h2>
          <span class="section__count">${list.length}</span>
        </div>
        <div class="admin-list">
          ${list.map((a) => `
            <div class="admin-row ${a.is_active ? "" : "admin-row--off"}">
              <span class="admin-row__img">${a.image_url ? img(a.image_url, "", "") : icon("trophy")}</span>
              <span class="admin-row__text">
                <span class="admin-row__title">${esc(a.title)}${a.is_active ? "" : ` <span class="tag">выключено</span>`}</span>
                <span class="admin-row__meta">${esc(a.description)}</span>
                <span class="admin-row__rule">${esc(ruleOf(a.rule_type)?.label || a.rule_type)} ≥ ${a.threshold}${a.rule_value ? ` · ${esc(a.rule_value)}` : ""}</span>
              </span>
              <span class="admin-row__btns">
                <button class="ui-button ui-button_transparent" type="button" data-action="acc-adm-edit" data-id="${a.id}" aria-label="Изменить">${icon("pencil")}</button>
                <button class="ui-button ui-button_transparent" type="button" data-action="acc-adm-delete" data-id="${a.id}" aria-label="Удалить">${icon("trash")}</button>
              </span>
            </div>`).join("")}
        </div>
      </section>`;
    syncRuleValue();
  }

  function adminFormHtml() {
    const { rules, regions, editing } = acc.admin;
    const a = editing || { title: "", description: "", rule_type: rules[0]?.type, threshold: 1, rule_value: "", sort_order: 100, is_active: true };
    return `
      <h2 class="admin-form__title">${editing ? `Изменить «${esc(editing.title)}»` : "Новое достижение"}</h2>
      <form class="form" id="acc-adm-form" novalidate>
        ${field("title", "Название", `<input class="field__input" name="title" maxlength="80" required value="${esc(a.title)}" placeholder="Игристое настроение">`)}
        ${field("description", "Краткое описание", `<textarea class="field__input field__input--area" name="description" maxlength="300" required
              rows="2" placeholder="Отсканируйте игристое вино">${esc(a.description)}</textarea>`)}
        <div class="form__row">
          ${field("rule_type", "Условие", `<select class="field__input" name="rule_type">
              ${rules.map((r) => `<option value="${esc(r.type)}" ${r.type === a.rule_type ? "selected" : ""}>${esc(r.label)}</option>`).join("")}
            </select>`)}
          ${field("threshold", "Не меньше",`<input class="field__input" name="threshold" type="number" min="1" step="1" required value="${a.threshold}">`)}
        </div>
        <label class="field" id="adm-value-field">
          <span class="field__label" id="adm-value-label">Значение</span>
          <input class="field__input" name="rule_value" list="adm-regions" maxlength="120" value="${esc(a.rule_value || "")}">
          <datalist id="adm-regions">${regions.map((r) => `<option value="${esc(r)}">`).join("")}</datalist>
        </label>
        <label class="field field--check"><input type="checkbox" name="is_active" ${a.is_active ? "checked" : ""}><span>Показывать пользователям</span></label>
        <div class="field">
          <span class="field__label">Картинка (PNG или JPEG, квадратная)</span>
          <div class="file-pick">
            <span class="file-pick__preview" id="adm-preview">${editing?.image_url ? img(editing.image_url, "", "") : icon("trophy")}</span>
            <label class="ui-button ui-button_elevated ui-button_round" for="adm-image">${icon("upload")}Выбрать файл</label>
            <input class="visually-hidden" type="file" id="adm-image" name="image" accept="image/png,image/jpeg,image/webp">
          </div>
        </div>
        <p class="form__error" role="alert" hidden></p>
        <div class="form__btns">
          <button class="ui-button ui-button_primary ui-button_large" type="submit">${editing ? "Сохранить" : `${icon("plus")}Добавить`}</button>
          ${editing ? `<button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="acc-adm-cancel">Отмена</button>` : ""}
        </div>
      </form>`;
  }

  // Поле «Значение» нужно только правилам вида «вина из региона X»
  function syncRuleValue() {
    const form = $("#acc-adm-form");
    if (!form) return;
    const rule = acc.admin.rules.find((r) => r.type === form.rule_type.value);
    const box = $("#adm-value-field");
    box.hidden = !rule?.needs_value;
    $("#adm-value-label").textContent = rule?.value_hint || "Значение";
    form.rule_value.toggleAttribute("required", !!rule?.needs_value);
    // подсказка регионов — только для правила «регион»
    form.rule_value.setAttribute("list", rule?.type === "region" ? "adm-regions" : "");
  }

  async function submitAdmin(form) {
    const error = $(".form__error", form);
    const data = new FormData(form);
    data.set("is_active", form.is_active.checked ? "true" : "false");
    const rule = acc.admin.rules.find((r) => r.type === form.rule_type.value);
    if (!rule?.needs_value) data.delete("rule_value");
    if (!data.get("image")?.size) data.delete("image");
    const editing = acc.admin.editing;
    try {
      await call(editing ? "PATCH" : "POST", editing ? `/v1/admin/achievements/${editing.id}` : "/v1/admin/achievements", data);
      acc.admin.list = await call("GET", "/v1/admin/achievements");
      acc.admin.editing = null;
      renderAdmin();
      toast(editing ? "Достижение сохранено" : "Достижение добавлено");
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }

  async function deleteAchievement(id) {
    const a = acc.admin.list.find((x) => String(x.id) === String(id));
    if (!a || !confirm(`Удалить достижение «${a.title}»? У пользователей оно тоже пропадёт. Скрыть, не удаляя, — снимите «Показывать пользователям».`)) return;
    try {
      await call("DELETE", `/v1/admin/achievements/${id}`);
      acc.admin.list = acc.admin.list.filter((x) => x !== a);
      if (acc.admin.editing?.id === a.id) acc.admin.editing = null;
      renderAdmin();
    } catch (err) {
      toast(err.message);
    }
  }

  // ── Утилиты ──────────────────────────────────────────────────────────────
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }

  const DATE = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long", year: "numeric" });
  const DATE_TIME = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  const fmtDate = (iso, withTime) => (iso ? (withTime ? DATE_TIME : DATE).format(new Date(iso)) : "");

  // ── Обработчики ──────────────────────────────────────────────────────────
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action^='acc-']");
    if (!el) return;
    switch (el.dataset.action) {
      case "acc-open":
        closeMenu();
        if (acc.me) openProfile(); else openAuth("login", "profile");
        break;
      case "acc-profile":
        closeSheet();
        openProfile();
        break;
      case "acc-auth":
        if (el.closest(".nudge")) track("nudge_click", { place: el.closest(".map-nudge") ? "map" : "scan" });
        openAuth(el.dataset.mode);
        break;
      case "acc-nudge-close":
        session.set(NUDGE_KEY, "1");
        $("#account-slot").innerHTML = "";
        break;
      case "acc-logout":
        logout();
        break;
      case "acc-delete":
        deleteAccount();
        break;
      case "acc-name":
        openNameEditor();
        break;
      case "acc-avatar":
        openAvatarPicker();
        break;
      case "acc-preset":
        setPreset(el.dataset.id);
        break;
      case "acc-achv":
        openAchievement(el.dataset.id);
        break;
      case "acc-scan-open":
        openWine(el.dataset.slug, { backTo: "profile" });
        break;
      case "acc-rate":
        rate(el.dataset.slug, Number(el.dataset.value));
        break;
      case "acc-more":
        loadMore();
        break;
      case "acc-pop-close":
        closePop();
        break;
      case "acc-pop-profile":
        acc.pops = [];
        closePop();
        openProfile();
        break;
      case "acc-pop-map":
        acc.pops = acc.pops.filter((p) => p.type === "achievement");
        $(".achv-pop")?.remove();
        window.WineMap?.open();
        nextPop();
        break;
      case "acc-admin":
        openAdmin();
        break;
      case "acc-admin-tab":
        acc.admin.tab = el.dataset.tab;
        renderAdmin();
        break;
      case "acc-admin-map":
        openAdmin({ tab: "map" });
        break;
      case "acc-adm-edit":
        acc.admin.editing = acc.admin.list.find((a) => String(a.id) === el.dataset.id) || null;
        $("#admin-form-card").innerHTML = adminFormHtml();
        syncRuleValue();
        $("#admin-form-card").scrollIntoView({ behavior: "smooth", block: "start" });
        break;
      case "acc-adm-cancel":
        acc.admin.editing = null;
        $("#admin-form-card").innerHTML = adminFormHtml();
        syncRuleValue();
        break;
      case "acc-adm-delete":
        deleteAchievement(el.dataset.id);
        break;
    }
  });

  document.addEventListener("submit", (e) => {
    const handlers = { "acc-auth-form": submitAuth, "acc-name-form": submitName, "acc-adm-form": submitAdmin };
    const handler = handlers[e.target.id];
    if (!handler) return;
    e.preventDefault();
    handler(e.target);
  });

  document.addEventListener("change", (e) => {
    if (e.target.id === "acc-avatar-file") {
      uploadAvatar(e.target.files?.[0]);
      e.target.value = "";
    } else if (e.target.name === "rule_type" && e.target.form?.id === "acc-adm-form") {
      syncRuleValue();
    } else if (e.target.id === "adm-image" && e.target.files?.[0]) {
      const url = URL.createObjectURL(e.target.files[0]);
      $("#adm-preview").innerHTML = `<img src="${url}" alt="">`;
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $(".achv-pop")) { e.stopImmediatePropagation(); closePop(); }
  }, true);

  // «Поделиться» в поздравлении — поздравление уступает место шторке
  document.addEventListener("click", (e) => {
    if (e.target.closest(".achv-pop [data-action='share-achievement']")) {
      acc.pops = acc.pops.filter((p) => p.type === "achievement");
      $(".achv-pop")?.remove();
    }
  }, true);

  document.addEventListener("wine:scanned", (e) => onScanned(e.detail));
  document.addEventListener("wine:picked", (e) => onPicked(e.detail));
  document.addEventListener("wine:route", (e) => {
    if (e.detail.screen === "admin") openAdmin({ push: false });
    else if (e.detail.screen === "profile") openProfile({ push: false });
  });

  // Для карты: показать админу подсказку «не сопоставлено»
  window.WineAccount = {
    isAdmin: () => acc.me?.user.role === "admin",
    userName: () => acc.me?.user.name || null,
    userId: () => acc.me?.user.id ?? null,
    // для engage.js: вход с продолжением, поздравления, запросы с понятными ошибками
    available: () => acc.available,
    signedIn: () => Boolean(acc.me),
    openAuth,
    celebrate,
    call,
    plural,
    fmtDate,
  };

  // ── Старт ────────────────────────────────────────────────────────────────
  (async () => {
    try {
      acc.presets = await call("GET", "/v1/avatars");
    } catch (_) {
      return; // сервиса пользователей нет — сканер работает без кабинета
    }
    acc.available = true;
    try { setMe(await call("GET", "/v1/me")); } catch (_) { acc.me = null; }
    updateHeader();
    const screen = location.hash.slice(1);
    if (screen === "profile") openProfile({ push: false });
    else if (screen === "admin") openAdmin({ push: false });
  })();
})();
