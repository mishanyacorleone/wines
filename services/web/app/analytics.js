/* Аналитика: сбор событий веб-сканера и дашборд для РСХБ (админка → «Аналитика»).
 *
 * Сбор. У посетителя — случайный id в браузере (localStorage): по нему считаются
 * уникальные и вернувшиеся без входа. Модули страницы сообщают о действиях
 * событием `wine:track` ({ type, props }) и про аналитику ничего не знают; скан —
 * по `wine:scanned` из app.js. Отправка не мешает странице: ошибки молча пропускаются.
 * Дашборд. Формы и цвета — по навыку dataviz: ряд плиток, сканы по дням — столбцы
 * из двух частей (палитра проверена валидатором на кремовом фоне), остальное —
 * горизонтальные полосы одного цвета; у столбцов подсказка и таблица.
 */
(() => {
  "use strict";

  const W = window.Wine;
  if (!W || W.DEMO) return;
  const { $, esc, icon, toast } = W;

  // ── Сбор событий ─────────────────────────────────────────────────────────
  const ANON_KEY = "wine.anonId";
  function anonId() {
    try {
      let id = localStorage.getItem(ANON_KEY);
      if (!id) {
        id = crypto.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
        localStorage.setItem(ANON_KEY, id);
      }
      return id;
    } catch (_) {
      return (anonId.fallback ??= `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`);
    }
  }

  function send(type, props = {}) {
    const body = JSON.stringify({ type, anon_id: anonId(), user_id: window.WineAccount?.userId?.() ?? null, props });
    fetch("/v1/events", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true })
      .catch(() => null);
  }

  document.addEventListener("wine:track", (e) => send(e.detail.type, e.detail.props || {}));
  document.addEventListener("wine:scanned", (e) => {
    const { res } = e.detail;
    const wine = res.status === "match" ? res.wine : null;
    send("scan", {
      status: wine ? "match" : "not_found",
      slug: wine?.slug, title: wine?.title, region: wine?.region, manufacturer: wine?.manufacturer,
      ms: res.timings?.total_ms != null ? Math.round(res.timings.total_ms) : null,
    });
  });
  send("page_view", { referrer: document.referrer ? new URL(document.referrer).hostname : null });

  // ── Дашборд ──────────────────────────────────────────────────────────────
  const C = { found: "#8F3D42", notFound: "#B07A25", bar: "#8F3D42", grid: "#E6E1D6", surface: "#FDF9ED" };
  const NAMES = { native: "Меню «Поделиться»", tg: "Telegram", vk: "ВКонтакте", save: "Сохранили картинку", wine: "Вино", achievement: "Достижение", map: "Винная карта", gift: "Открытка" };
  const dash = { root: null, days: 30, demo: true, data: null, ro: null };

  async function mountDashboard(root) {
    dash.root = root;
    root.innerHTML = `<div class="skeleton"></div>`;
    await load();
  }

  async function api(method, url) {
    const res = await fetch(url, { method });
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail; } catch (_) { /* не JSON */ }
      throw new Error(typeof detail === "string" && detail ? detail : `Ошибка сервера (${res.status})`);
    }
    return res.json();
  }

  async function load() {
    // Перезагрузка держит прежнюю картинку полупрозрачной — без прыжков и мигания
    dash.root.querySelector(".an-body")?.classList.add("an-body--loading");
    try {
      dash.data = await api("GET", `/v1/admin/analytics?days=${dash.days}&demo=${dash.demo}`);
      render();
    } catch (err) {
      dash.root.innerHTML = `<p class="muted">Аналитика недоступна: ${esc(err.message)}</p>`;
    }
  }

  const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("ru-RU"));
  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }

  function render() {
    const d = dash.data;
    const k = d.kpi;
    dash.root.innerHTML = `
      <div class="an-filters">
        <div class="seg" role="tablist" aria-label="Период">
          ${[7, 30, 90].map((n) => `<button class="seg__btn ${dash.days === n ? "seg__btn--on" : ""}" type="button" role="tab"
            aria-selected="${dash.days === n}" data-action="an-days" data-days="${n}">${n} дней</button>`).join("")}
        </div>
        <label class="field--check an-demo-toggle"><input type="checkbox" id="an-demo" ${dash.demo ? "checked" : ""}><span>С демо-данными</span></label>
        <span class="an-filters__spacer"></span>
        <a class="ui-button ui-button_primary ui-button_round" href="/v1/admin/analytics/export.xlsx?days=${dash.days}&demo=${dash.demo}"
           download>${icon("download")}Скачать Excel</a>
        <button class="ui-button ui-button_elevated ui-button_round" type="button" data-action="an-demo-make">Сгенерировать демо-данные</button>
        ${d.has_demo ? `<button class="ui-button ui-button_transparent an-demo-del" type="button" data-action="an-demo-delete">Удалить демо</button>` : ""}
      </div>
      ${d.has_demo ? `<p class="an-demo-note">${icon("tag")}В отчёте демо-данные: сгенерированы для показа дашборда, настоящие события — вместе с ними. Снимите «С демо-данными», чтобы увидеть только настоящие</p>` : ""}
      ${!k.scans && !k.visitors ? `<p class="an-empty">За ${d.period.days} ${plural(d.period.days, "день", "дня", "дней")} событий нет. Отсканируйте вино на этом стенде или сгенерируйте демо-данные</p>` : ""}

      <div class="an-body">
        <section class="an-kpis" aria-label="Ключевые показатели">
          ${tile("Сканов", fmt(k.scans), `${fmt(k.found)} найдено · ${fmt(k.not_found)} нет в каталоге`, true)}
          ${tile("Посетителей", fmt(k.visitors), `${pct(k.returning_share)} вернулись в другой день`)}
          ${tile("С аккаунтом", fmt(k.visitors_with_account), `регистраций за период: ${fmt(k.registrations)}`)}
          ${tile("Конверсия в аккаунт", pct(k.conversion), "сканировали без входа → зарегистрировались")}
          ${tile("Нет в каталоге", pct(k.not_found_share), "спрос на вина, которых нет на сайте")}
          ${tile("Поделились", fmt(k.shares), "картинки и ссылки в соцсети")}
        </section>

        <section class="an-card">
          <div class="an-card__head">
            <h3 class="an-card__title">Сканы по дням</h3>
            <ul class="an-legend">
              <li><i style="background:${C.found}"></i>Найдено в каталоге</li>
              <li><i style="background:${C.notFound}"></i>Нет в каталоге</li>
            </ul>
          </div>
          <div class="an-chart" id="an-daily"></div>
          <details class="an-table">
            <summary>Таблица</summary>
            <table><thead><tr><th>Дата</th><th>Найдено</th><th>Нет в каталоге</th></tr></thead>
              <tbody>${d.daily.map((r) => `<tr><td>${esc(fmtDate(r.date))}</td><td>${r.found}</td><td>${r.not_found}</td></tr>`).join("")}</tbody></table>
          </details>
        </section>

        <div class="an-grid">
          <section class="an-card">
            <h3 class="an-card__title">Воронка регистрации</h3>
            <p class="an-card__lead">Посетители, которые сканировали без аккаунта</p>
            ${bars(d.funnel.map((s) => ({ label: s.step, value: s.value })), { share: true })}
          </section>
          <section class="an-card">
            <h3 class="an-card__title">Регионы в сканах</h3>
            ${bars(d.top_regions.map((r) => ({ label: r.name, value: r.count })))}
          </section>
          <section class="an-card">
            <h3 class="an-card__title">Чаще всего сканируют</h3>
            ${bars(d.top_wines.map((w) => ({ label: w.title, value: w.count })))}
          </section>
          <section class="an-card">
            <h3 class="an-card__title">Винодельни</h3>
            ${bars(d.top_manufacturers.map((m) => ({ label: m.name, value: m.count })))}
          </section>
          <section class="an-card">
            <h3 class="an-card__title">Поделились</h3>
            ${bars(d.share_channels.map((c) => ({ label: NAMES[c.name] || c.name, value: c.count })))}
            <p class="an-card__lead an-card__lead--sub">Чем делились</p>
            ${bars(d.share_kinds.map((c) => ({ label: NAMES[c.name] || c.name, value: c.count })))}
          </section>
          <section class="an-card">
            <h3 class="an-card__title">Винная карта</h3>
            <p class="an-card__lead">Открывали карту: <b>${fmt(d.map.opens)}</b> раз. Какие регионы смотрели:</p>
            ${bars(d.map.regions.map((r) => ({ label: r.name, value: r.count })))}
          </section>
        </div>

        ${engagementHtml(d.engagement)}

        <section class="an-card">
          <h3 class="an-card__title">Откуда сканируют</h3>
          <p class="an-card__lead">Сети провайдеров — адрес без последнего числа: видно сеть, а не человека.${d.local_share > 0.5 ? " Большая часть сканов — из локальной сети (стенд)." : ""} Город по адресу — подключением базы GeoIP</p>
          ${d.ips.length ? `<table class="an-ips"><thead><tr><th>Сеть</th><th>Сканов</th><th>Посетителей</th></tr></thead>
            <tbody>${d.ips.map((r) => `<tr><td>${esc(r.network)}</td><td>${fmt(r.scans)}</td><td>${fmt(r.visitors)}</td></tr>`).join("")}</tbody></table>`
            : `<p class="muted">Пока нет сканов</p>`}
        </section>
      </div>`;
    $("#an-demo").addEventListener("change", (e) => { dash.demo = e.target.checked; load(); });
    drawDaily();
    dash.ro?.disconnect();
    dash.ro = new ResizeObserver(() => drawDaily());
    dash.ro.observe($("#an-daily"));
  }

  function tile(label, value, sub, hero = false) {
    return `
      <div class="an-tile ${hero ? "an-tile--hero" : ""}">
        <p class="an-tile__label">${esc(label)}</p>
        <p class="an-tile__value">${esc(value)}</p>
        <p class="an-tile__sub">${esc(sub)}</p>
      </div>`;
  }

  // Горизонтальные полосы одного цвета: подпись над полосой, значение — у конца
  // Вовлечение: вопрос дня, дневник, открытки, призы — и какой призыв войти приводит регистрации
  function engagementHtml(g) {
    if (!g) return ""; // старый сервис аналитики — блока нет
    const q = g.quiz, dr = g.diary, gf = g.gifts;
    const sources = g.registration_sources || [];
    return `
      <h2 class="an-section">Вовлечение</h2>
      <section class="an-kpis an-kpis--4" aria-label="Вовлечение">
        ${tile("Вопрос дня", fmt(q.answers), `${fmt(q.players)} ${plural(q.players, "игрок", "игрока", "игроков")} · верно ${pct(q.correct_share)}`)}
        ${tile("Дневник дегустации", fmt(dr.tasted), `записей «попробовал» · «хочу попробовать»: ${fmt(dr.want)}`)}
        ${tile("Открытки", fmt(gf.created), `открыли ${fmt(gf.opened)} · сохранили себе ${fmt(gf.saved)}`)}
        ${tile("Призы выданы", fmt(g.prizes), `итоги года открывали: ${fmt(g.year_opens)}`)}
      </section>
      <section class="an-card">
        <h3 class="an-card__title">Откуда регистрации</h3>
        <p class="an-card__lead">Какой призыв войти был последним перед регистрацией. В скобках — сколько посетителей
          его увидели и какая доля из них зарегистрировалась</p>
        ${bars(sources.map((s) => ({ label: `${s.name}${s.prompted ? ` (${fmt(s.prompted)} → ${pct(s.conversion)})` : ""}`, value: s.registrations })))}
      </section>`;
  }

  function bars(items, { share = false } = {}) {
    if (!items.length) return `<p class="muted">Нет данных за период</p>`;
    const max = Math.max(...items.map((i) => i.value), 1);
    const first = items[0].value || 1;
    return `<ul class="an-bars">${items.map((i) => `
      <li class="an-bar">
        <span class="an-bar__label">${esc(i.label)}</span>
        <span class="an-bar__row">
          <span class="an-bar__track"><i style="width:${Math.max(1.5, (i.value / max) * 100)}%"></i></span>
          <span class="an-bar__value">${fmt(i.value)}${share ? ` · ${Math.round((i.value / first) * 100)}%` : ""}</span>
        </span>
      </li>`).join("")}</ul>`;
  }

  // Сканы по дням: столбцы из двух частей, зазор 2 px, скругление только у вершины
  function drawDaily() {
    const box = $("#an-daily");
    if (!box) return;
    const rows = dash.data.daily;
    const width = Math.max(280, box.clientWidth);
    const height = 220;
    const pad = { l: 36, r: 8, t: 12, b: 26 };
    const max = Math.max(...rows.map((r) => r.found + r.not_found), 1);
    const nice = niceMax(max);
    const band = (width - pad.l - pad.r) / rows.length;
    const barW = Math.min(24, Math.max(3, band * 0.68));
    const y = (v) => pad.t + (height - pad.t - pad.b) * (1 - v / nice);
    const ticks = [0, nice / 2, nice].map((v) => Math.round(v));
    const labelEvery = Math.ceil(rows.length / Math.max(2, Math.floor((width - pad.l) / 64)));
    let svg = ticks.map((t) => `
      <line x1="${pad.l}" x2="${width - pad.r}" y1="${y(t)}" y2="${y(t)}" stroke="${C.grid}" stroke-width="1"/>
      <text class="an-axis" x="${pad.l - 6}" y="${y(t) + 4}" text-anchor="end">${compact(t)}</text>`).join("");
    rows.forEach((r, i) => {
      const x = pad.l + band * i + (band - barW) / 2;
      const total = r.found + r.not_found;
      const yFound = y(r.found);
      const yTotal = y(total);
      const gap = r.found && r.not_found ? 2 : 0;
      if (r.found) svg += rect(x, yFound, barW, y(0) - yFound, C.found, !r.not_found);
      if (r.not_found) svg += rect(x, yTotal, barW, Math.max(1, yFound - yTotal - gap), C.notFound, true);
      svg += `<rect class="an-hit" x="${pad.l + band * i}" y="${pad.t}" width="${band}" height="${height - pad.t - pad.b}"
        data-i="${i}" tabindex="0" aria-label="${esc(fmtDate(r.date))}: найдено ${r.found}, нет в каталоге ${r.not_found}"/>`;
      if (i % labelEvery === 0) {
        svg += `<text class="an-axis" x="${pad.l + band * i + band / 2}" y="${height - 6}" text-anchor="middle">${esc(fmtDate(r.date, true))}</text>`;
      }
    });
    box.innerHTML = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Сканы по дням">${svg}</svg>
      <div class="an-tip" hidden></div>`;
    const tip = $(".an-tip", box);
    const show = (el) => {
      const r = rows[Number(el.dataset.i)];
      box.querySelectorAll(".an-hit--on").forEach((h) => h.classList.remove("an-hit--on"));
      el.classList.add("an-hit--on");
      tip.innerHTML = "";
      const title = document.createElement("b");
      title.textContent = fmtDate(r.date);
      tip.append(title);
      for (const [name, value, color] of [["Найдено", r.found, C.found], ["Нет в каталоге", r.not_found, C.notFound]]) {
        const row = document.createElement("span");
        row.innerHTML = `<i style="background:${color}"></i>`;
        const strong = document.createElement("strong");
        strong.textContent = value;
        row.append(strong, document.createTextNode(` ${name}`));
        tip.append(row);
      }
      tip.hidden = false;
      const bx = Number(el.getAttribute("x")) + Number(el.getAttribute("width")) / 2;
      tip.style.left = `${Math.min(Math.max(bx - tip.offsetWidth / 2, 0), width - tip.offsetWidth)}px`;
    };
    box.querySelectorAll(".an-hit").forEach((el) => {
      el.addEventListener("pointerenter", () => show(el));
      el.addEventListener("focus", () => show(el));
    });
    box.querySelector("svg").addEventListener("pointerleave", () => {
      tip.hidden = true;
      box.querySelectorAll(".an-hit--on").forEach((h) => h.classList.remove("an-hit--on"));
    });
  }

  // Столбец со скруглёнными верхними углами 4 px, низ — прямой (у оси)
  function rect(x, y, w, h, fill, roundTop) {
    if (h <= 0) return "";
    const r = roundTop ? Math.min(4, w / 2, h) : 0;
    return `<path d="M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z" fill="${fill}"/>`;
  }

  function niceMax(v) {
    const step = 10 ** Math.floor(Math.log10(v));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * step >= v) return m * step;
    return 10 * step;
  }
  const compact = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(".0", "")}K` : String(n));
  const DAY = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long" });
  const DAY_SHORT = new Intl.DateTimeFormat("ru", { day: "numeric", month: "short" });
  const fmtDate = (iso, short = false) => (short ? DAY_SHORT : DAY).format(new Date(`${iso}T12:00:00`));

  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action^='an-']");
    if (!el) return;
    switch (el.dataset.action) {
      case "an-days":
        dash.days = Number(el.dataset.days);
        load();
        break;
      case "an-demo-make":
        el.disabled = true;
        try {
          const out = await api("POST", `/v1/admin/analytics/demo?days=${Math.min(dash.days, 90)}`);
          toast(`Демо-данные: ${fmt(out.created)} событий`);
          dash.demo = true;
          await load();
        } catch (err) { toast(err.message); el.disabled = false; }
        break;
      case "an-demo-delete":
        try {
          const out = await api("DELETE", "/v1/admin/analytics/demo");
          toast(`Удалено демо-событий: ${fmt(out.deleted)}`);
          await load();
        } catch (err) { toast(err.message); }
        break;
    }
  });

  window.WineAnalytics = { mountDashboard, track: send };
})();
