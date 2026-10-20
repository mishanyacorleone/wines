/* Админка → «Каталог»: синхронизация с vino-svoe.ru (сервис catalog).
 *
 * Кнопка «Обновить каталог с сайта» — новые вина скачиваются, получают вектор
 * и сразу появляются в поиске, без перезапуска сервисов. Расписание («N раз
 * в минуту / час / день / неделю / месяц») задаёт админ здесь же. Пока идёт синхронизация, экран раз в 2 с
 * спрашивает статус и показывает текущий шаг.
 */
(() => {
  "use strict";

  const W = window.Wine;
  if (!W || W.DEMO) return;
  const { esc, icon, toast } = W;

  const view = { root: null, data: null, timer: null };

  async function api(method, url, body) {
    const init = { method };
    if (body) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    const res = await fetch(url, init);
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail; } catch (_) { /* не JSON */ }
      throw new Error(typeof detail === "string" && detail ? detail : `Ошибка сервера (${res.status})`);
    }
    return res.json();
  }

  async function mountAdmin(root) {
    view.root = root;
    root.innerHTML = `<div class="skeleton"></div>`;
    await load();
  }

  async function load() {
    clearTimeout(view.timer);
    if (!view.root?.isConnected) return; // админ ушёл с вкладки — опрос прекращается
    try {
      view.data = await api("GET", "/v1/admin/catalog/sync");
      render();
      if (view.data.running) view.timer = setTimeout(load, 2000);
    } catch (err) {
      view.root.innerHTML = `<p class="muted">Сервис каталога недоступен: ${esc(err.message)}</p>`;
    }
  }

  const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("ru-RU"));
  const WHEN = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  const fmtWhen = (iso) => (iso ? WHEN.format(new Date(iso)) : "—");
  // Как в catalog_service.sync.PERIODS; чаще раза в минуту сервис не даст
  const PERIODS = [["minute", "минуту", 60], ["hour", "час", 3600], ["day", "день", 86400],
                   ["week", "неделю", 7 * 86400], ["month", "месяц", 30 * 86400]];
  const timesWord = (n) => (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? "раза" : "раз");

  // «2 раза в день» → «каждые 12 ч»: админ сразу видит, как часто это на деле
  function everyText(times, per) {
    const seconds = (PERIODS.find(([p]) => p === per)?.[2] || 86400) / Math.max(times, 1);
    if (seconds < 60) return "чаще раза в минуту нельзя";
    const parts = [[86400, "дн"], [3600, "ч"], [60, "мин"]];
    let rest = Math.round(seconds / 60) * 60;
    const out = [];
    for (const [size, unit] of parts) {
      if (rest >= size) { out.push(`${Math.floor(rest / size)} ${unit}`); rest %= size; }
    }
    return `каждые ${out.slice(0, 2).join(" ")}`;
  }

  function render() {
    const d = view.data;
    const last = d.runs[0];
    const { times, per } = d.schedule;
    const off = d.schedule.auto ? "" : "disabled";
    view.root.innerHTML = `
      <div class="an-kpis" style="margin-top:24px">
        <div class="an-tile"><span class="an-tile__label">Вин в поиске</span><span class="an-tile__value">${fmt(d.indexed)}</span></div>
        <div class="an-tile"><span class="an-tile__label">Последнее обновление</span>
          <span class="an-tile__value cat-tile__text">${last ? esc(fmtWhen(last.finished_at || last.started_at)) : "ещё не было"}</span>
          ${last && last.status === "ok" ? `<span class="an-tile__label">+${fmt(last.indexed)} вин</span>` : ""}</div>
        <div class="an-tile"><span class="an-tile__label">Следующее автоматически</span>
          <span class="an-tile__value cat-tile__text">${d.schedule.auto ? esc(fmtWhen(d.next_run_at)) : "выключено"}</span></div>
      </div>

      <section class="info-card admin-form-card">
        <h2 class="admin-form__title">Обновить сейчас</h2>
        <p class="muted" style="margin:0">Сервис сверит каталог с vino-svoe.ru, скачает новые вина и добавит их в поиск —
          без перезапуска. Обычно — секунды на каждое новое вино.</p>
        ${d.running
          ? `<p class="cat-stage">${icon("clock")}${esc(d.stage || "Идёт синхронизация…")}</p>`
          : `<button class="ui-button ui-button_primary ui-button_large" type="button" data-action="cat-sync">${icon("download")}Обновить каталог с сайта</button>`}
      </section>

      <section class="info-card admin-form-card">
        <h2 class="admin-form__title">Расписание</h2>
        <form class="form" id="cat-schedule">
          <label class="field field--check"><input type="checkbox" name="auto" ${d.schedule.auto ? "checked" : ""}>
            <span>Обновлять автоматически</span></label>
          <div class="field"><span class="field__label">Как часто</span>
            <div class="cat-freq">
              <input class="field__input cat-freq__times" name="times" type="number" min="1" max="1000" step="1"
                     value="${times}" inputmode="numeric" aria-label="Сколько раз" ${off}>
              <span class="cat-freq__word" data-role="times-word">${timesWord(times)} в</span>
              <select class="field__input cat-freq__per" name="per" aria-label="За какой период" ${off}>
                ${PERIODS.map(([p, label]) => `<option value="${p}" ${p === per ? "selected" : ""}>${label}</option>`).join("")}
              </select>
            </div>
            <span class="field__hint" data-role="every">${esc(everyText(times, per))}</span></div>
          <div class="form__btns"><button class="ui-button ui-button_elevated ui-button_large" type="submit">Сохранить</button></div>
        </form>
      </section>

      <section class="section" aria-labelledby="cat-runs-title">
        <div class="section__head"><h2 class="section__title" id="cat-runs-title">История</h2></div>
        ${d.runs.length ? `<div class="admin-list">${d.runs.map(runHtml).join("")}</div>`
          : `<p class="muted">Синхронизаций ещё не было.</p>`}
      </section>`;
  }

  function runHtml(r) {
    const tag = r.status === "running" ? "идёт" : r.status === "ok" ? (r.failed ? "с ошибками" : "готово") : "ошибка";
    const details = [
      ...r.added.map((w) => `<li>${icon("plus")}${esc(w.title || w.slug)}${w.manufacturer ? ` — ${esc(w.manufacturer)}` : ""}</li>`),
      ...r.errors.map((e) => `<li class="cat-error">${icon("x")}${esc(e)}</li>`),
    ];
    return `
      <div class="admin-row ${r.status === "error" ? "admin-row--off" : ""}">
        <span class="admin-row__img">${icon(r.status === "error" ? "x" : r.indexed ? "plus" : "check")}</span>
        <span class="admin-row__text">
          <span class="admin-row__title">${esc(fmtWhen(r.started_at))} <span class="tag">${tag}</span></span>
          <span class="admin-row__meta">${esc(r.message || "—")}</span>
          <span class="admin-row__rule">${esc(r.trigger)} · ${fmt(r.seconds)} с</span>
          ${details.length ? `<details class="cat-details"><summary>Подробнее</summary><ul>${details.join("")}</ul></details>` : ""}
        </span>
      </div>`;
  }

  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action='cat-sync']");
    if (!el) return;
    el.disabled = true;
    try {
      view.data = await api("POST", "/v1/admin/catalog/sync");
      render();
      view.timer = setTimeout(load, 1000);
    } catch (err) {
      toast(err.message);
      el.disabled = false;
    }
  });

  function syncHint(form) {
    const times = Math.max(1, Math.floor(Number(form.times.value) || 1));
    form.querySelector("[data-role='times-word']").textContent = `${timesWord(times)} в`;
    form.querySelector("[data-role='every']").textContent = everyText(times, form.per.value);
  }

  document.addEventListener("change", (e) => {
    const form = e.target.closest("#cat-schedule");
    if (!form) return;
    if (e.target.name === "auto") form.times.disabled = form.per.disabled = !e.target.checked;
    syncHint(form);
  });

  document.addEventListener("input", (e) => {
    const form = e.target.closest("#cat-schedule");
    if (form && e.target.name === "times") syncHint(form);
  });

  document.addEventListener("submit", async (e) => {
    if (e.target.id !== "cat-schedule") return;
    e.preventDefault();
    const form = e.target;
    try {
      view.data = await api("PUT", "/v1/admin/catalog/sync/settings", {
        auto: form.auto.checked, times: Math.max(1, Math.floor(Number(form.times.value) || 1)), per: form.per.value,
      });
      render();
      toast(view.data.schedule.auto ? `Расписание сохранено: ${view.data.schedule.text}` : "Автообновление выключено");
    } catch (err) { toast(err.message); }
  });

  window.WineCatalogAdmin = { mountAdmin };
})();
