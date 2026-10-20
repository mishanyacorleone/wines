/* «От бутылки — на винодельню» (A11): на карточке вина — где его сделали и можно ли туда приехать;
 * экран винодельни — фото, терруар, люди, туры с записью, вина из каталога, соседи; «Хочу съездить»
 * копит список поездок в профиле (только с аккаунтом, как дневник); метки виноделен — на винной карте.
 *
 * Данные — api (/v1/wineries, снимок страниц vino-svoe.ru/wineries и /tours), список поездок —
 * сервис пользователей (/v1/me/trips).
 */
(() => {
  "use strict";

  const W = window.Wine;
  if (!W || W.DEMO) return;
  const { $, esc, icon, img, toast } = W;
  const ACC = () => window.WineAccount;
  const signedIn = () => ACC()?.signedIn?.();
  const call = (method, url, body) => ACC().call(method, url, body);
  const track = (type, props = {}) => document.dispatchEvent(new CustomEvent("wine:track", { detail: { type, props } }));
  const askRegister = (source, then) => { track("auth_prompt", { source }); ACC().openAuth("register", then); };

  const st = { info: null, trips: null };
  const rub = (n) => `${Math.round(n).toLocaleString("ru-RU")} ₽`;
  const plural = (n, one, few, many) => {
    const a = Math.abs(n) % 100, b = a % 10;
    return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many;
  };
  const DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

  function tourMeta(t) {
    const days = [...new Set((t.schedule || []).map((s) => s.day))].sort((a, b) => DAYS.indexOf(a) - DAYS.indexOf(b));
    const time = t.schedule?.length === 1 && t.schedule[0].from ? ` ${t.schedule[0].from}` : "";
    return [
      days.length ? days.join(", ") + time : t.individual ? "по договорённости" : "",
      t.duration || "",
      t.price ? rub(t.price) : "",
    ].filter(Boolean).join(" · ");
  }

  // ── Карточка вина ────────────────────────────────────────────────────────
  function onCard(wine) {
    const b = wine?.winery;
    const screen = $("#screen-result");
    if (!b || !screen || $("#winery-sec", screen)) return;
    const chips = [
      b.tours ? `${icon("route")}${b.tours} ${plural(b.tours, "тур", "тура", "туров")}${b.price_from ? ` · от ${rub(b.price_from)}` : ""}` : "",
      b.terrain ? `${icon("sun")}${esc(b.terrain)}` : "",
    ].filter(Boolean);
    const html = `
      <section class="section winery-sec" id="winery-sec" aria-labelledby="winery-sec-title">
        <h2 class="section__title" id="winery-sec-title">Где рождается это вино</h2>
        <button class="winery-card" type="button" data-action="winery-open" data-slug="${esc(b.slug)}">
          <span class="winery-card__photo">${b.image ? img(b.image, "", b.name) : icon("grape")}</span>
          <span class="winery-card__body">
            <span class="winery-card__kicker">${icon("pin")}${esc([b.place, b.region].filter(Boolean).join(" · "))}</span>
            <span class="winery-card__name">${esc(b.name)}</span>
            ${b.lead ? `<span class="winery-card__lead">${esc(b.lead)}</span>` : ""}
            ${chips.length ? `<span class="winery-card__chips">${chips.map((c) => `<span>${c}</span>`).join("")}</span>` : ""}
          </span>
        </button>
        ${b.tour ? `
          <button class="winery-tour-row" type="button" data-action="winery-open" data-slug="${esc(b.slug)}" data-focus="tours">
            <span class="winery-tour-row__img">${b.tour.image ? img(b.tour.image, "", "") : icon("route")}</span>
            <span><span class="winery-tour-row__kicker">Можно приехать</span>
              <span class="winery-tour-row__title">${esc(b.tour.title)}</span>
              <span class="winery-tour-row__meta">${esc(tourMeta(b.tour))}</span></span>
            ${icon("chevron")}
          </button>` : `
          <button class="link-btn winery-sec__more" type="button" data-action="winery-open" data-slug="${esc(b.slug)}">
            О винодельне и терруаре ${icon("chevron")}</button>`}
      </section>`;
    const anchor = $("[aria-labelledby=som-title]", screen) || $(".description", screen);
    if (anchor) anchor.insertAdjacentHTML("afterend", html);
    else $(".stack", screen)?.insertAdjacentHTML("beforebegin", html);
  }

  // ── Экран винодельни ─────────────────────────────────────────────────────
  async function open(slug, { focus } = {}) {
    W.closeSheet?.();
    close();
    document.body.insertAdjacentHTML("beforeend", `
      <div class="winery" role="dialog" aria-modal="true" aria-label="Винодельня">
        <button class="winery__close" type="button" data-action="winery-close" aria-label="Закрыть">${icon("x")}</button>
        <div class="winery__scroll"><div class="skeleton winery__wait"></div></div>
      </div>`);
    document.body.style.overflow = "hidden";
    track("winery", { action: "open", slug });
    try {
      const [info] = await Promise.all([W.request(`/v1/wineries/${encodeURIComponent(slug)}`), loadTrips()]);
      if (!$(".winery")) return; // успели закрыть
      st.info = info;
      render();
      if (focus) $(`.winery #winery-${focus}`)?.scrollIntoView({ block: "start" });
    } catch (err) {
      const box = $(".winery__scroll");
      if (box) box.innerHTML = `<p class="muted winery__error">Не удалось открыть винодельню: ${esc(err.message)}</p>`;
    }
  }

  function close() {
    if (!$(".winery")) return;
    $(".winery").remove();
    document.body.style.overflow = "";
  }

  const wanted = (slug) => !!st.trips?.some((t) => t.winery === slug);

  function render() {
    const w = st.info;
    const root = $(".winery__scroll");
    if (!w || !root) return;
    const facts = [
      w.region && [icon("pin"), w.region],
      w.square && [icon("grape"), `${Number(w.square).toLocaleString("ru-RU")} га виноградников`],
      w.climate && [icon("sun"), `климат ${w.climate}`],
      w.terrain && [icon("image"), w.terrain],
    ].filter(Boolean);
    const gallery = w.gallery.length ? w.gallery : w.logo ? [w.logo] : [];
    root.innerHTML = `
      <header class="winery__hero">
        <div class="winery__gallery">${gallery.map((g, i) => `<img src="${esc(g)}" alt="${i ? "" : esc(w.name)}" loading="${i ? "lazy" : "eager"}" onerror="this.remove()">`).join("")}</div>
        ${gallery.length > 1 ? `<div class="winery__dots" aria-hidden="true">${gallery.map((_, i) => `<i class="${i ? "" : "on"}"></i>`).join("")}</div>` : ""}
        <div class="winery__head">
          ${w.logo && w.gallery.length ? `<span class="winery__logo">${img(w.logo, "", "")}</span>` : ""}
          <p class="winery__kicker">${esc(w.place || w.region || "Винодельня")}</p>
          <h2 class="winery__name">${esc(w.name)}</h2>
        </div>
      </header>
      <div class="winery__body">
        ${facts.length ? `<div class="winery__facts">${facts.map(([ic, t]) => `<span>${ic}${esc(t)}</span>`).join("")}</div>` : ""}
        <div class="winery__actions">
          ${tripButton(w)}
          ${w.point ? `<button class="ui-button ui-button_elevated ui-button_round" type="button" data-action="winery-map" data-slug="${esc(w.slug)}">${icon("pin")}На карте</button>` : ""}
          ${w.site ? `<a class="ui-button ui-button_elevated ui-button_round" href="${esc(w.site)}" target="_blank" rel="noopener" data-track="site">Сайт ${icon("external")}</a>` : ""}
        </div>
        ${w.description ? `<p class="winery__lead">${esc(w.description)}</p>` : ""}
        ${toursHtml(w)}
        ${textBlock("О винодельне", w.about)}
        ${textBlock("Терруар", w.terroir)}
        ${peopleHtml(w)}
        ${textBlock("История", w.history)}
        ${w.wines.length ? `
          <section class="winery__sec">
            <h3 class="winery__title">Вина винодельни <span>${w.wines_total} в каталоге</span></h3>
            <div class="winery__wines">${w.wines.map(wineHtml).join("")}</div>
          </section>` : ""}
        ${w.nearby.length ? `
          <section class="winery__sec">
            <h3 class="winery__title">Рядом</h3>
            <div class="winery__chips">${w.nearby.map(pinChip).join("")}</div>
          </section>` : ""}
        <a class="ui-button ui-button_elevated ui-button_large winery__site" href="${esc(w.url)}" target="_blank" rel="noopener">
          Винодельня на vino-svoe.ru ${icon("external")}</a>
      </div>`;
    bindGallery(root);
  }

  function tripButton(w) {
    const on = wanted(w.slug);
    return `<button class="ui-button ui-button_round ${on ? "ui-button_elevated winery__want--on" : "ui-button_primary"}" type="button"
      data-action="winery-want" data-slug="${esc(w.slug)}" aria-pressed="${on}">${icon(on ? "check" : "bookmark")}${on ? "В моих поездках" : "Хочу съездить"}</button>`;
  }

  function toursHtml(w) {
    if (!w.tours.length) {
      return `
        <section class="winery__sec" id="winery-tours">
          <h3 class="winery__title">Туры</h3>
          <p class="winery__note">На сайте пока нет туров на эту винодельню${w.region_tours.length ? ` — вот куда можно съездить в регионе «${esc(w.region)}»` : ""}.</p>
          ${w.region_tours.length ? `<div class="winery__list">${w.region_tours.map(pinRow).join("")}</div>` : ""}
        </section>`;
    }
    return `
      <section class="winery__sec" id="winery-tours">
        <h3 class="winery__title">Туры и дегустации <span>${w.tours.length}</span></h3>
        <div class="winery__tours">${w.tours.map((t) => `
          <article class="tour-card">
            ${t.image ? `<div class="tour-card__img"><img src="${esc(t.image)}" alt="" loading="lazy" onerror="this.parentNode.remove()">
              ${t.kind ? `<span class="tour-card__kind">${esc(t.kind)}</span>` : ""}</div>` : ""}
            <div class="tour-card__body">
              <h4 class="tour-card__title">${esc(t.title)}</h4>
              ${t.short ? `<p class="tour-card__short">${esc(t.short)}</p>` : ""}
              <ul class="tour-card__meta">
                ${t.duration ? `<li>${icon("clock")}${esc(t.duration)}</li>` : ""}
                ${t.price ? `<li>${icon("ticket")}${rub(t.price)} с человека</li>` : ""}
                ${scheduleLine(t)}
                ${t.group?.[1] ? `<li>${icon("users")}${t.group[0] && t.group[0] > 1 ? `от ${t.group[0]} ` : ""}до ${t.group[1]} человек</li>` : ""}
                ${t.address ? `<li>${icon("pin")}${esc(t.address)}</li>` : ""}
              </ul>
              <div class="tour-card__btns">
                <a class="ui-button ui-button_primary ui-button_round" href="${esc(t.link || t.url)}" target="_blank" rel="noopener"
                  data-track="tour" data-tour="${esc(t.slug)}">Записаться ${icon("external")}</a>
                ${t.link ? `<a class="link-btn" href="${esc(t.url)}" target="_blank" rel="noopener" data-track="tour" data-tour="${esc(t.slug)}">Подробнее на сайте</a>` : ""}
              </div>
            </div>
          </article>`).join("")}</div>
      </section>`;
  }

  function scheduleLine(t) {
    if (!t.schedule?.length) return t.individual ? `<li>${icon("calendar")}Индивидуально, по договорённости</li>` : "";
    const slots = t.schedule.slice(0, 6).map((s) => `${s.day}${s.from ? ` ${s.from}` : ""}`).join(", ");
    return `<li>${icon("calendar")}${esc(slots)}${t.schedule.length > 6 ? "…" : ""}</li>`;
  }

  function textBlock(title, text) {
    if (!text) return "";
    const paras = text.split("\n").map((p) => `<p>${esc(p)}</p>`).join("");
    const long = text.length > 320;
    return `
      <section class="winery__sec">
        <h3 class="winery__title">${title}</h3>
        <div class="winery__text ${long ? "winery__text--clamp" : ""}">${paras}</div>
        ${long ? `<button class="link-btn" type="button" data-action="winery-more">Читать полностью</button>` : ""}
      </section>`;
  }

  function peopleHtml(w) {
    if (!w.people.length) return "";
    return `
      <section class="winery__sec">
        <h3 class="winery__title">Люди</h3>
        <div class="winery__people">${w.people.map((p) => `
          <div class="winery-person">
            <span class="winery-person__img">${p.image ? img(p.image, "", p.name) : icon("user")}</span>
            <span><b>${esc(p.name)}</b>${p.role ? `<span>${esc(p.role)}</span>` : ""}</span>
          </div>`).join("")}</div>
      </section>`;
  }

  const wineHtml = (x) => `
    <button class="winery-wine" type="button" data-action="open" data-slug="${esc(x.slug)}">
      <span class="winery-wine__img">${x.image_url ? img(x.image_url, "", x.title) : icon("wine")}</span>
      <span class="winery-wine__title">${esc(x.title)}</span>
      <span class="winery-wine__meta">${esc(x.category || "")}</span>
    </button>`;

  const pinChip = (p) => `
    <button class="winery-chip" type="button" data-action="winery-open" data-slug="${esc(p.slug)}">
      ${esc(p.name)}${p.tours ? `<span>${icon("route")}${p.tours}</span>` : ""}</button>`;

  const pinRow = (p) => `
    <button class="alt-row" type="button" data-action="winery-open" data-slug="${esc(p.slug)}">
      <span class="alt-row__img">${p.image || p.logo ? img(p.image || p.logo, "", "") : icon("grape")}</span>
      <span><span class="alt-row__title">${esc(p.name)}</span>
        <span class="alt-row__meta">${p.tours} ${plural(p.tours, "тур", "тура", "туров")}${p.wines ? ` · ${p.wines} ${plural(p.wines, "вино", "вина", "вин")} в каталоге` : ""}</span></span>
      ${icon("chevron")}
    </button>`;

  function bindGallery(root) {
    const g = $(".winery__gallery", root);
    const dots = root.querySelectorAll(".winery__dots i");
    if (!g || !dots.length) return;
    g.addEventListener("scroll", () => {
      const i = Math.round(g.scrollLeft / g.clientWidth);
      dots.forEach((d, k) => d.classList.toggle("on", k === i));
    }, { passive: true });
  }

  // ── «Хочу съездить» ──────────────────────────────────────────────────────
  async function loadTrips() {
    if (!signedIn()) { st.trips = []; return st.trips; }
    try { st.trips = await call("GET", "/v1/me/trips"); } catch (_) { st.trips = []; }
    return st.trips;
  }

  async function toggleWant(slug) {
    const w = st.info?.slug === slug ? st.info : null;
    if (!w) return;
    if (!signedIn()) {
      askRegister("trip", async () => { await loadTrips(); if (!wanted(slug)) await toggleWant(slug); });
      return;
    }
    try {
      if (wanted(slug)) {
        await call("DELETE", `/v1/me/trips/${encodeURIComponent(slug)}`);
        st.trips = st.trips.filter((t) => t.winery !== slug);
        toast("Убрали из поездок");
      } else {
        const trip = await call("PUT", `/v1/me/trips/${encodeURIComponent(slug)}`,
          { name: w.name, region: w.region, image: w.gallery[0] || w.logo || null });
        st.trips = [trip, ...st.trips];
        track("winery", { action: "want", slug });
        toast("Добавили в «Хочу съездить» — список в профиле");
      }
    } catch (err) {
      toast(`Не получилось: ${err.message}`);
      return;
    }
    const btn = $(`.winery [data-action=winery-want]`);
    if (btn) btn.outerHTML = tripButton(w);
    const prof = $("#profile-trips");
    if (prof) mountProfile(prof);
  }

  async function mountProfile(root) {
    if (!root) return;
    const trips = await loadTrips();
    if (!trips.length) { root.innerHTML = ""; return; }
    root.innerHTML = `
      <section class="trips">
        <h3 class="trips__title">${icon("route")}Хочу съездить <span>${trips.length}</span></h3>
        <div class="trips__list">${trips.map((t) => `
          <button class="trip-card" type="button" data-action="winery-open" data-slug="${esc(t.winery)}">
            <span class="trip-card__img">${t.image ? img(t.image, "", "") : icon("grape")}</span>
            <span class="trip-card__name">${esc(t.name)}</span>
            ${t.region ? `<span class="trip-card__region">${esc(t.region)}</span>` : ""}
          </button>`).join("")}</div>
      </section>`;
  }

  // ── Обработчики ─────────────────────────────────────────────────────────
  document.addEventListener("click", (e) => {
    const tracked = e.target.closest(".winery [data-track]");
    if (tracked) track("winery", { action: tracked.dataset.track, slug: st.info?.slug, tour: tracked.dataset.tour });
    // вино винодельни открывает обработчик страницы (data-action=open) — экран винодельни закрываем
    if (e.target.closest(".winery [data-action=open]")) { close(); return; }
    const el = e.target.closest("[data-action^='winery-']");
    if (!el) return;
    switch (el.dataset.action) {
      case "winery-open": open(el.dataset.slug, { focus: el.dataset.focus }); break;
      case "winery-close": close(); break;
      case "winery-want": toggleWant(el.dataset.slug); break;
      case "winery-map": close(); window.WineMap?.focusWinery?.(el.dataset.slug); break;
      case "winery-more": {
        el.previousElementSibling?.classList.remove("winery__text--clamp");
        el.remove();
        break;
      }
    }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $(".winery")) close(); });
  document.addEventListener("wine:card", (e) => onCard(e.detail.wine));
  document.addEventListener("wine:auth", () => { st.trips = null; });

  window.WineWinery = { open, close, mountProfile };
})();
