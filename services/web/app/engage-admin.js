/* Управление → «Задания и призы»: вопрос дня, челленджи, призы (промокоды),
 * «Мой винный год». Сервер — сервис пользователей (/v1/admin/quiz, …/challenges,
 * …/prizes, …/settings/year).
 *
 * Один экран — четыре вкладки; в каждой: форма сверху (новая запись или правка)
 * и список ниже. Нет своих вопросов на день — пользователи получают вопрос по
 * бутылке из каталога, это видно в блоке «Сегодня».
 */
(() => {
  "use strict";

  const W = window.Wine;
  if (!W || W.DEMO) return;
  const { esc, icon, toast } = W;
  const call = (m, u, b) => window.WineAccount.call(m, u, b);

  const KINDS = { wines: "Отсканировать вина", diary: "Записать в дневник", quiz: "Ответить на вопрос дня" };
  const FIELDS = { "": "любые вина", region: "регион", grape: "сорт", category: "категория (например, игристое)",
    manufacturer: "винодельня" };
  const TRIGGERS = { challenge: "только за челленджи", quiz_streak: "за серию «Вопроса дня»", achievement: "за достижение" };
  const view = { root: null, tab: "quiz", data: {}, editing: null };
  const DATE = new Intl.DateTimeFormat("ru", { day: "numeric", month: "short" });
  const fmtDay = (iso) => (iso ? DATE.format(new Date(`${iso}T12:00:00`)) : "");
  // Последний день месяца даты «ГГГГ-ММ-ДД» — без часовых поясов (toISOString сдвигал на день назад)
  const monthEnd = (iso) => {
    const [y, m] = iso.split("-").map(Number);
    return `${iso.slice(0, 8)}${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
  };
  const tomorrowIso = () => new Date(Date.now() + 27 * 3600e3).toISOString().slice(0, 10); // по Москве
  const todayIso = () => new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10); // по Москве, как на сервере

  async function mountAdmin(root) {
    view.root = root;
    view.editing = null;
    await load();
  }

  async function load() {
    view.root.innerHTML = `${tabsHtml()}<div class="skeleton"></div>`;
    try {
      const d = view.data;
      if (view.tab === "quiz") d.quiz = await call("GET", "/v1/admin/quiz");
      if (view.tab === "challenges") {
        [d.challenges, d.prizes, d.rules] = await Promise.all([
          call("GET", "/v1/admin/challenges"), call("GET", "/v1/admin/prizes"), call("GET", "/v1/admin/achievements/rules"),
        ]);
      }
      if (view.tab === "prizes") {
        [d.prizes, d.achievements] = await Promise.all([call("GET", "/v1/admin/prizes"), call("GET", "/v1/admin/achievements")]);
      }
      if (view.tab === "year") d.year = await call("GET", "/v1/settings/year");
      render();
    } catch (err) {
      view.root.innerHTML = `${tabsHtml()}<p class="muted">${esc(err.message)}</p>`;
    }
  }

  const tabsHtml = () => `
    <div class="seg eng-tabs" role="tablist" aria-label="Раздел">
      ${[["quiz", "Вопрос дня"], ["challenges", "Челленджи"], ["prizes", "Призы"], ["year", "Винный год"]].map(([k, l]) => `
        <button class="seg__btn ${view.tab === k ? "seg__btn--on" : ""}" type="button" role="tab" aria-selected="${view.tab === k}"
          data-action="eng-tab" data-tab="${k}">${l}</button>`).join("")}
    </div>`;

  function render() {
    const body = { quiz: quizHtml, challenges: challengesHtml, prizes: prizesHtml, year: yearHtml }[view.tab]();
    view.root.innerHTML = tabsHtml() + body;
    if (view.tab === "challenges") syncChallengeForm();
    if (view.tab === "prizes") syncPrizeForm();
  }

  const row = (title, meta, rule, id, off = false) => `
    <div class="admin-row ${off ? "admin-row--off" : ""}">
      <span class="admin-row__text">
        <span class="admin-row__title">${title}</span>
        ${meta ? `<span class="admin-row__meta">${meta}</span>` : ""}
        ${rule ? `<span class="admin-row__rule">${rule}</span>` : ""}
      </span>
      <span class="admin-row__btns">
        <button class="ui-button ui-button_transparent" type="button" data-action="eng-edit" data-id="${id}" aria-label="Изменить">${icon("pencil")}</button>
        <button class="ui-button ui-button_transparent" type="button" data-action="eng-del" data-id="${id}" aria-label="Удалить">${icon("trash")}</button>
      </span>
    </div>`;

  const formCard = (title, inner, editing) => `
    <section class="info-card admin-form-card">
      <h2 class="admin-form__title">${editing ? `Изменить «${esc(title)}»` : title}</h2>
      ${inner}
    </section>`;

  const buttons = (editing) => `
    <div class="form__btns">
      <button class="ui-button ui-button_primary ui-button_large" type="submit">${editing ? "Сохранить" : "Добавить"}</button>
      ${editing ? `<button class="link-btn" type="button" data-action="eng-cancel">Отменить правку</button>` : ""}
    </div>`;

  const checkbox = (name, on, label) => `
    <label class="field field--check"><input type="checkbox" name="${name}" ${on ? "checked" : ""}><span>${label}</span></label>`;

  // ── Вопрос дня ──
  function quizHtml() {
    const { questions, today } = view.data.quiz;
    const e = view.editing;
    const opts = e?.options || ["", "", "", ""];
    const own = today && !String(today.key || "").startsWith("auto:");
    return `
      <section class="info-card eng-today">
        <p class="eng-today__kicker">Сегодня, ${esc(fmtDay(todayIso()))} · ${own ? "свой вопрос" : "«Угадайте вино» — по бутылке из каталога"}</p>
        ${today ? `<p class="eng-today__text">${esc(today.text)}</p>
          <ol class="eng-today__opts">${today.options.map((o, i) => `<li class="${i === today.correct ? "on" : ""}">${esc(o)}</li>`).join("")}</ol>`
          : `<p class="muted">Вопрос недоступен — каталог не ответил</p>`}
      </section>
      ${formCard(e ? e.text : "Новый вопрос", `
        <form class="form" id="eng-quiz-form">
          <label class="field"><span class="field__label">Вопрос</span>
            <textarea class="field__input field__input--area" name="text" maxlength="300" rows="2" required
              placeholder="Кто вывел сорт Красностоп золотовский?">${esc(e?.text || "")}</textarea></label>
          <div class="field"><span class="field__label">Варианты — отметьте верный (2–4)</span>
            ${[0, 1, 2, 3].map((i) => `
              <label class="eng-opt"><input type="radio" name="correct" value="${i}" ${i === (e?.correct ?? 0) ? "checked" : ""}>
                <input class="field__input" name="opt${i}" maxlength="120" value="${esc(opts[i] || "")}" placeholder="Вариант ${i + 1}${i > 1 ? " (необязательно)" : ""}"></label>`).join("")}
          </div>
          <label class="field"><span class="field__label">Пояснение после ответа</span>
            <input class="field__input" name="explanation" maxlength="1000" value="${esc(e?.explanation || "")}" placeholder="Необязательно"></label>
          <label class="field"><span class="field__label">Дата показа</span>
            <input class="field__input" type="date" name="day" required value="${esc(e?.day || tomorrowIso())}">
            <span class="field__hint">В дни без своего вопроса выходит «Угадайте вино» — вопрос по бутылке из каталога</span></label>
          ${checkbox("is_active", e ? e.is_active : true, "Включён")}
          ${buttons(e)}
        </form>`, e)}
      <section class="section" aria-label="Вопросы">
        <div class="section__head"><h2 class="section__title">Свои вопросы</h2><span class="section__count">${questions.length}</span></div>
        ${questions.length ? `<div class="admin-list">${questions.map((q) => row(esc(q.text),
          `${q.day ? (q.shown ? `показан ${fmtDay(q.day)}` : `на ${fmtDay(q.day)}`) : "без даты — не выйдет"} · ${q.options.length} варианта`,
          `Ответ: ${esc(q.options[q.correct])}`, q.id, !q.is_active)).join("")}</div>`
          : `<p class="muted">Своих вопросов нет — каждый день выходит вопрос по бутылке из каталога: регион, сорт, цвет или сладость</p>`}
      </section>`;
  }

  async function submitQuiz(form) {
    const options = [0, 1, 2, 3].map((i) => form[`opt${i}`].value.trim());
    const filled = options.map((o, i) => [o, i]).filter(([o]) => o);
    const correctRaw = Number(form.querySelector("[name=correct]:checked")?.value ?? 0);
    const correct = filled.findIndex(([, i]) => i === correctRaw);
    if (correct < 0) { toast("Верный вариант не заполнен"); return; }
    await save("/v1/admin/quiz", {
      text: form.text.value.trim(), options: filled.map(([o]) => o), correct,
      explanation: form.explanation.value.trim() || null, day: form.day.value || null, is_active: form.is_active.checked,
    });
  }

  // ── Челленджи ──
  function challengesHtml() {
    const { challenges, prizes, rules } = view.data;
    const e = view.editing;
    const start = e?.starts_on || todayIso();
    const end = e?.ends_on || monthEnd(start);
    return `
      ${formCard(e ? e.title : "Новый челлендж", `
        <form class="form" id="eng-ch-form">
          <label class="field"><span class="field__label">Название</span>
            <input class="field__input" name="title" maxlength="80" required value="${esc(e?.title || "")}" placeholder="Октябрь — месяц Дона"></label>
          <label class="field"><span class="field__label">Описание</span>
            <input class="field__input" name="description" maxlength="500" value="${esc(e?.description || "")}"
              placeholder="Попробуйте три вина из Долины Дона"></label>
          <div class="form__row">
            <label class="field"><span class="field__label">С</span><input class="field__input" type="date" name="starts_on" required value="${start}"></label>
            <label class="field"><span class="field__label">По</span><input class="field__input" type="date" name="ends_on" required value="${end}"></label>
          </div>
          <div class="form__row">
            <label class="field"><span class="field__label">Что сделать</span>
              <select class="field__input" name="kind">${Object.entries(KINDS).map(([k, l]) =>
                `<option value="${k}" ${k === (e?.kind || "wines") ? "selected" : ""}>${l}</option>`).join("")}</select></label>
            <label class="field"><span class="field__label">Сколько раз</span>
              <input class="field__input" type="number" name="target" min="1" max="1000" required value="${e?.target || 3}"></label>
          </div>
          <div class="form__row" data-role="filter">
            <label class="field"><span class="field__label">Какие вина</span>
              <select class="field__input" name="filter_field">${Object.entries(FIELDS).map(([k, l]) =>
                `<option value="${k}" ${k === (e?.filter_field || "") ? "selected" : ""}>${l}</option>`).join("")}</select></label>
            <label class="field"><span class="field__label">Значение</span>
              <input class="field__input" name="filter_value" maxlength="120" list="eng-regions" value="${esc(e?.filter_value || "")}" placeholder="Долина Дона"></label>
          </div>
          <datalist id="eng-regions">${(rules?.regions || []).map((r) => `<option value="${esc(r)}">`).join("")}</datalist>
          <label class="field"><span class="field__label">Приз за выполнение</span>
            <select class="field__input" name="reward_pool_id"><option value="">без приза</option>
              ${prizes.map((p) => `<option value="${p.id}" ${p.id === e?.reward_pool_id ? "selected" : ""}>${esc(p.title)}</option>`).join("")}
            </select>${prizes.length ? "" : `<span class="field__hint">Призы добавляются во вкладке «Призы»</span>`}</label>
          ${checkbox("is_active", e ? e.is_active : true, "Включён")}
          ${checkbox("count_before", e?.count_before, "Засчитывать сделанное до публикации")}
          <span class="field__hint">Без галочки прогресс считается с момента, когда челлендж включили: приз — за новые действия</span>
          ${buttons(e)}
        </form>`, e)}
      <section class="section" aria-label="Челленджи">
        <div class="section__head"><h2 class="section__title">Челленджи</h2><span class="section__count">${challenges.length}</span></div>
        ${challenges.length ? `<div class="admin-list">${challenges.map((c) => row(esc(c.title),
          `${fmtDay(c.starts_on)} — ${fmtDay(c.ends_on)} · ${esc(c.condition)}`,
          c.reward ? `Приз: ${esc(c.reward.title)}` : "без приза", c.id, !c.is_active)).join("")}</div>`
          : `<p class="muted">Челленджей пока нет</p>`}
      </section>`;
  }

  function syncChallengeForm() {
    const form = document.getElementById("eng-ch-form");
    if (!form) return;
    const wines = form.kind.value === "wines";
    form.querySelector("[data-role=filter]").hidden = !wines;
    form.filter_value.disabled = !wines || !form.filter_field.value;
  }

  async function submitChallenge(form) {
    const wines = form.kind.value === "wines";
    await save("/v1/admin/challenges", {
      title: form.title.value.trim(), description: form.description.value.trim() || null,
      starts_on: form.starts_on.value, ends_on: form.ends_on.value, kind: form.kind.value, target: Number(form.target.value),
      filter_field: wines && form.filter_field.value ? form.filter_field.value : null,
      filter_value: wines && form.filter_field.value ? form.filter_value.value.trim() || null : null,
      reward_pool_id: form.reward_pool_id.value ? Number(form.reward_pool_id.value) : null, is_active: form.is_active.checked,
      count_before: form.count_before.checked,
    });
  }

  // ── Призы ──
  function prizesHtml() {
    const { prizes, achievements } = view.data;
    const e = view.editing;
    const trigger = e?.trigger || "challenge";
    return `
      ${formCard(e ? e.title : "Новый приз", `
        <form class="form" id="eng-prize-form">
          <label class="field"><span class="field__label">Название</span>
            <input class="field__input" name="title" maxlength="80" required value="${esc(e?.title || "")}" placeholder="Скидка 10% в ВинЛаб"></label>
          <label class="field"><span class="field__label">Условия для пользователя</span>
            <input class="field__input" name="description" maxlength="500" value="${esc(e?.description || "")}"
              placeholder="Действует до 31 декабря в магазинах ВинЛаб"></label>
          <div class="seg eng-codes" role="radiogroup" aria-label="Какие коды">
            <label class="seg__btn"><input type="radio" name="mode" value="list" ${e?.shared_code ? "" : "checked"}>Список кодов</label>
            <label class="seg__btn"><input type="radio" name="mode" value="shared" ${e?.shared_code ? "checked" : ""}>Один код на всех</label>
          </div>
          <label class="field" data-role="list"><span class="field__label">${e ? "Добавить коды" : "Коды"} — по одному в строке</span>
            <textarea class="field__input field__input--area" name="codes" rows="4" placeholder="WINE-7Q2K&#10;WINE-9F4D"></textarea>
            ${e && !e.shared_code ? `<span class="field__hint">Сейчас: осталось ${e.codes_left} из ${e.codes_total}</span>` : ""}</label>
          <label class="field" data-role="shared"><span class="field__label">Общий код</span>
            <input class="field__input" name="shared_code" maxlength="64" value="${esc(e?.shared_code || "")}" placeholder="VINO2026"></label>
          <div class="form__row">
            <label class="field"><span class="field__label">За что выдавать</span>
              <select class="field__input" name="trigger">${Object.entries(TRIGGERS).map(([k, l]) =>
                `<option value="${k}" ${k === trigger ? "selected" : ""}>${l}</option>`).join("")}</select></label>
            <label class="field" data-role="streak"><span class="field__label">Дней подряд</span>
              <input class="field__input" type="number" name="streak" min="1" max="1000"
                value="${trigger === "quiz_streak" ? e.trigger_value : 7}"></label>
            <label class="field" data-role="achievement"><span class="field__label">Достижение</span>
              <select class="field__input" name="achievement">${achievements.map((a) =>
                `<option value="${a.id}" ${trigger === "achievement" && a.id === e.trigger_value ? "selected" : ""}>${esc(a.title)}</option>`).join("")}</select></label>
          </div>
          ${checkbox("is_active", e ? e.is_active : true, "Выдавать")}
          ${buttons(e)}
        </form>`, e)}
      <section class="section" aria-label="Призы">
        <div class="section__head"><h2 class="section__title">Призы</h2><span class="section__count">${prizes.length}</span></div>
        ${prizes.length ? `<div class="admin-list">${prizes.map((p) => row(esc(p.title), prizeTrigger(p, achievements),
          p.shared_code ? `Общий код ${esc(p.shared_code)} · выдан ${p.issued} раз`
            : `Выдано ${p.issued} · осталось ${p.codes_left} из ${p.codes_total}${p.codes_left ? "" : " — коды закончились"}`,
          p.id, !p.is_active)).join("")}</div>`
          : `<p class="muted">Призов пока нет. Коды даёт партнёр (ВинЛаб, винодельня) — сюда их можно вставить списком</p>`}
      </section>`;
  }

  function prizeTrigger(p, achievements) {
    if (p.trigger === "quiz_streak") return `За серию «Вопроса дня»: ${p.trigger_value} дн. подряд`;
    if (p.trigger === "achievement") return `За достижение «${esc(achievements.find((a) => a.id === p.trigger_value)?.title || "?")}»`;
    return "За челленджи";
  }

  function syncPrizeForm() {
    const form = document.getElementById("eng-prize-form");
    if (!form) return;
    const shared = form.querySelector("[name=mode]:checked").value === "shared";
    form.querySelector("[data-role=list]").hidden = shared;
    form.querySelector("[data-role=shared]").hidden = !shared;
    form.querySelectorAll(".eng-codes .seg__btn").forEach((l) => l.classList.toggle("seg__btn--on", l.querySelector("input").checked));
    form.querySelector("[data-role=streak]").hidden = form.trigger.value !== "quiz_streak";
    form.querySelector("[data-role=achievement]").hidden = form.trigger.value !== "achievement";
  }

  async function submitPrize(form) {
    const shared = form.querySelector("[name=mode]:checked").value === "shared";
    const trigger = form.trigger.value;
    await save("/v1/admin/prizes", {
      title: form.title.value.trim(), description: form.description.value.trim() || null,
      shared_code: shared ? form.shared_code.value.trim() || null : null, codes: shared ? "" : form.codes.value,
      trigger, trigger_value: trigger === "quiz_streak" ? Number(form.streak.value)
        : trigger === "achievement" ? Number(form.achievement.value) || null : null,
      is_active: form.is_active.checked,
    });
  }

  // ── Винный год ──
  function yearHtml() {
    const y = view.data.year;
    return `
      <section class="info-card admin-form-card">
        <h2 class="admin-form__title">Мой винный год</h2>
        <p class="muted" style="margin:0">Итоги года для каждого пользователя: сколько вин, регионы, любимый сорт и винодельня,
          лучшее вино из дневника, серия вопроса дня. Механика сезонная — включайте, например, с 1 декабря по 15 января.
          Пока выключено, итоги видите только вы — для проверки.</p>
        <form class="form" id="eng-year-form">
          ${checkbox("enabled", y.enabled, "Показывать пользователям — баннер в профиле")}
          <label class="field"><span class="field__label">За какой год</span>
            <input class="field__input" type="number" name="year" min="2024" max="2100" value="${y.year}"></label>
          <div class="form__btns">
            <button class="ui-button ui-button_primary ui-button_large" type="submit">Сохранить</button>
            <button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="year-open">Посмотреть мои итоги</button>
          </div>
        </form>
      </section>`;
  }

  // ── Общее ──
  const URLS = { quiz: "/v1/admin/quiz", challenges: "/v1/admin/challenges", prizes: "/v1/admin/prizes" };
  const LISTS = { quiz: () => view.data.quiz.questions, challenges: () => view.data.challenges, prizes: () => view.data.prizes };

  async function save(url, body) {
    try {
      const id = view.editing?.id;
      await call(id ? "PUT" : "POST", id ? `${url}/${id}` : url, body);
      toast(id ? "Сохранено" : "Добавлено");
      view.editing = null;
      await load();
    } catch (err) { toast(err.message); }
  }

  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action]");
    if (!el || !view.root?.isConnected || !view.root.contains(el)) return;
    switch (el.dataset.action) {
      case "eng-tab": view.tab = el.dataset.tab; view.editing = null; load(); break;
      case "eng-edit":
        view.editing = LISTS[view.tab]().find((x) => String(x.id) === el.dataset.id) || null;
        render();
        view.root.querySelector(".admin-form-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
        break;
      case "eng-cancel": view.editing = null; render(); break;
      case "eng-del":
        if (!confirm("Удалить?")) break;
        try { await call("DELETE", `${URLS[view.tab]}/${el.dataset.id}`); toast("Удалено"); load(); }
        catch (err) { toast(err.message); }
        break;
    }
  });

  document.addEventListener("change", (e) => {
    const form = e.target.form;
    if (form?.id === "eng-ch-form") syncChallengeForm();
    if (form?.id === "eng-prize-form") syncPrizeForm();
  });

  document.addEventListener("submit", async (e) => {
    const handlers = {
      "eng-quiz-form": submitQuiz, "eng-ch-form": submitChallenge, "eng-prize-form": submitPrize,
      "eng-year-form": async (form) => {
        try {
          view.data.year = await call("PUT", "/v1/admin/settings/year",
            { enabled: form.enabled.checked, year: Number(form.year.value) });
          toast(view.data.year.enabled ? "Итоги года открыты пользователям" : "Итоги года скрыты");
        } catch (err) { toast(err.message); }
      },
    };
    const handler = handlers[e.target.id];
    if (!handler) return;
    e.preventDefault();
    handler(e.target);
  });

  window.WineEngageAdmin = { mountAdmin };
})();
