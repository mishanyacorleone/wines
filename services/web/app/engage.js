/* Вовлечение: вопрос дня, челленджи, дневник дегустации, подарочная открытка,
 * призы (промокоды) и «Мой винный год». Сервер — сервис пользователей.
 *
 * Принцип: всё, что человек копит (записи, серия дней, призы), сохраняется
 * только с аккаунтом. Без него можно ответить на вопрос и заполнить запись —
 * на «Сохранить» страница предлагает войти и после входа сохраняет сама.
 *
 * Встраивается через события app.js / account.js: wine:card (карточка вина
 * открыта), wine:auth (вход/выход), wine:prizes (приз за скан); профиль зовёт
 * mountProfile. Без сервиса пользователей модуль ничего не показывает.
 */
(() => {
  "use strict";

  const W = window.Wine;
  if (!W || W.DEMO) return;
  const { $, esc, icon, img, srcAttr, toast, openSheet, closeSheet, openWine } = W;
  const ACC = () => window.WineAccount;
  const track = (type, props = {}) => document.dispatchEvent(new CustomEvent("wine:track", { detail: { type, props } }));
  // Предложение войти — с источником: дашборд считает, какой призыв приводит регистрации
  const askRegister = (source, then) => { track("auth_prompt", { source }); ACC().openAuth("register", then); };

  const QUIZ_KEY = "wine.quizAnswer"; // ответ без аккаунта: { day, choice } — сохраним после входа
  const PAGE = 10;
  // Подсказки «что почувствовал»: нажать проще, чем писать, — и это первые сведения о вкусе
  const TAGS = ["Ягоды", "Фрукты", "Цитрус", "Цветы", "Пряности", "Травы", "Мёд", "Дуб", "Свежее", "Кислотное",
    "Мягкое", "Танинное", "Лёгкое", "Насыщенное"];

  const st = { quiz: null, quizResult: null, challenges: [], diary: null, diaryKind: "tasted", year: null };

  const call = (method, url, body) => ACC().call(method, url, body);
  const plural = (n, a, b, c) => ACC().plural(n, a, b, c);
  const signedIn = () => ACC()?.signedIn();
  const store = {
    get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch (_) { return null; } },
    set(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch (_) { /* приватный режим */ } },
    del(key) { try { localStorage.removeItem(key); } catch (_) { /* приватный режим */ } },
  };
  const DATE = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long" });
  const fmtDay = (iso) => (iso ? DATE.format(new Date(`${iso}T12:00:00`)) : "");
  const stars = (n) => `<span class="stars" aria-label="Оценка ${n} из 5">${[1, 2, 3, 4, 5]
    .map((i) => icon("star", i <= n ? "star--on" : "")).join("")}</span>`;

  // ── Главная: вопрос дня и челленджи ──────────────────────────────────────
  async function loadHome() {
    const root = $("#home-engage");
    if (!root || !ACC()?.available()) return;
    const [quiz, challenges] = await Promise.all([
      call("GET", "/v1/quiz/today").catch(() => null),
      call("GET", "/v1/challenges").catch(() => []),
    ]);
    st.quiz = quiz;
    st.challenges = challenges || [];
    // без аккаунта: сегодняшний ответ — из браузера
    const saved = store.get(QUIZ_KEY);
    if (quiz && !quiz.signed_in && saved?.day === quiz.day) st.quizResult = saved.result;
    else st.quizResult = quiz?.answered ? { ...quiz.answered, streak: quiz.streak, best: quiz.best, saved: true } : null;
    renderHome();
  }

  function renderHome() {
    const root = $("#home-engage");
    if (!root) return;
    root.innerHTML = `${st.quiz ? quizHtml() : ""}${st.challenges.map(challengeHtml).join("")}`;
  }

  function quizHtml() {
    const q = st.quiz;
    const r = st.quizResult;
    const streak = r?.streak ?? q.streak;
    const options = q.options.map((text, i) => {
      let cls = "";
      if (r) cls = i === r.correct_index ? "quiz__opt--right" : i === r.choice ? "quiz__opt--wrong" : "quiz__opt--dim";
      return `<button class="quiz__opt ${cls}" type="button" data-action="quiz-answer" data-i="${i}" ${r ? "disabled" : ""}>${esc(text)}</button>`;
    }).join("");
    let after = "";
    if (r) {
      after = `
        <p class="quiz__verdict ${r.correct ? "quiz__verdict--ok" : ""}"><b>${r.correct ? "Верно!" : "Не угадали."}</b>${r.explanation ? ` ${esc(r.explanation)}` : ""}</p>
        ${r.wine_slug ? `<button class="link-btn" type="button" data-action="quiz-wine" data-slug="${esc(r.wine_slug)}">Открыть карточку вина</button>` : ""}
        ${r.saved
          ? `<p class="quiz__next">${icon("flame")}Серия: ${streak} ${plural(streak, "день", "дня", "дней")} подряд — новый вопрос завтра</p>`
          : `<div class="quiz__nudge">
               <p>Войдите — серия дней будет копиться, а за серии бывают призы</p>
               <button class="ui-button ui-button_primary ui-button_round" type="button" data-action="quiz-save">Сохранить серию</button>
             </div>`}`;
    }
    return `
      <section class="quiz info-card" aria-labelledby="quiz-title">
        <div class="quiz__head">
          <span class="round-icon">${icon("help")}</span>
          <h2 class="quiz__title" id="quiz-title">Вопрос дня</h2>
          ${streak ? `<span class="quiz__streak" title="Дней подряд">${icon("flame")}${streak}</span>` : ""}
        </div>
        <div class="quiz__body">
          ${q.image_url ? `<button class="quiz__img" type="button" data-action="quiz-zoom" aria-label="Увеличить фото бутылки">
            ${img(q.image_url, "", "Бутылка вина")}<span class="quiz__zoom">${icon("search")}</span></button>` : ""}
          <p class="quiz__text">${esc(q.text)}</p>
        </div>
        <div class="quiz__opts">${options}</div>
        ${after}
      </section>`;
  }

  async function answerQuiz(choice) {
    try {
      const r = await call("POST", "/v1/quiz/today/answer", { choice });
      st.quizResult = r;
      if (!r.saved) store.set(QUIZ_KEY, { day: st.quiz.day, choice, result: r });
      track("quiz", { correct: r.correct });
      renderHome();
      celebrate(r);
    } catch (err) { toast(err.message); }
  }

  // Ответ без аккаунта → после входа он засчитывается в серию
  async function saveQuizAfterAuth() {
    const saved = store.get(QUIZ_KEY);
    if (!saved || saved.day !== st.quiz?.day) return loadHome();
    store.del(QUIZ_KEY);
    try {
      const r = await call("POST", "/v1/quiz/today/answer", { choice: saved.choice });
      celebrate(r);
    } catch (_) { /* не критично */ }
    loadHome();
  }

  function challengeHtml(c) {
    const pct = Math.round((c.progress / Math.max(1, c.target)) * 100);
    return `
      <section class="challenge" aria-label="Челлендж">
        <p class="challenge__kicker">${icon("sparkles")}Челлендж до ${esc(fmtDay(c.ends_on))}</p>
        <h2 class="challenge__title">${esc(c.title)}</h2>
        ${c.description ? `<p class="challenge__desc">${esc(c.description)}</p>` : ""}
        ${signedIn()
          ? `<div class="challenge__bar" aria-label="${c.progress} из ${c.target}"><i style="width:${pct}%"></i></div>
             <p class="challenge__meta">${c.done ? `${icon("check")}Выполнено` : `${c.progress} из ${c.target}`}</p>`
          : `<button class="link-btn" type="button" data-action="eng-auth">Войдите, чтобы участвовать</button>`}
        ${c.reward ? `<p class="challenge__prize">${icon("gift")}Приз: ${esc(c.reward.title)}</p>` : ""}
        ${c.prize_code ? codeHtml(c.prize_code) : ""}
      </section>`;
  }

  const codeHtml = (code) => `
    <button class="promo-code" type="button" data-action="eng-copy" data-code="${esc(code)}" aria-label="Скопировать промокод">
      <span>${esc(code)}</span>${icon("copy")}</button>`;

  // ── Призы и достижения за действие ───────────────────────────────────────
  function celebrate(result) {
    if (result?.new_achievements?.length) ACC().celebrate(result.new_achievements);
    if (result?.new_prizes?.length) showPrizes(result.new_prizes);
  }

  function showPrizes(list) {
    track("prize", { count: list.length });
    openSheet(list.length > 1 ? "Ваши призы" : "Ваш приз", `
      <div class="prize-pop">
        <span class="prize-pop__icon">${icon("gift")}</span>
        ${list.map((p) => `
          <h3 class="prize-pop__title">${esc(p.title)}</h3>
          <p class="prize-pop__reason">${esc(p.reason)}</p>
          ${codeHtml(p.code)}
          ${p.description ? `<p class="sheet__hint">${esc(p.description)}</p>` : ""}`).join("")}
        <p class="sheet__hint">Промокод сохранён в профиле — «Мои призы»</p>
      </div>`);
  }

  // На http (стенд по IP) буфер обмена браузера недоступен — копируем через выделение текста
  async function copyCode(code, done = "Скопировано") {
    try {
      if (!window.isSecureContext || !navigator.clipboard) throw new Error("нет clipboard");
      await navigator.clipboard.writeText(code);
      toast(done);
    } catch (_) {
      const area = Object.assign(document.createElement("textarea"), { value: code, readOnly: true });
      area.style.cssText = "position:fixed;top:-1000px;opacity:0";
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
      area.remove();
      toast(ok ? done : code);
    }
  }

  // ── Карточка вина: «В дневник» и «Подарить» ──────────────────────────────
  function addWineActions(wine) {
    const hero = $("#screen-result .hero");
    if (!hero || !wine?.slug || !ACC()?.available()) return;
    hero.insertAdjacentHTML("afterend", `
      <div class="wine-actions">
        <button class="ui-button ui-button_elevated ui-button_round" type="button" data-action="diary-open" data-slug="${esc(wine.slug)}">${icon("book")}В дневник</button>
        <button class="ui-button ui-button_elevated ui-button_round" type="button" data-action="gift-open" data-slug="${esc(wine.slug)}">${icon("gift")}Подарить</button>
      </div>`);
  }

  // ── Дневник ──────────────────────────────────────────────────────────────
  let draft = null; // { wine, entry, body } — запись, ждущая входа

  function wineHead(w) {
    return `
      <div class="mini-wine">
        <span class="mini-wine__img">${w.image_url ? img(w.image_url, "", "") : icon("wine")}</span>
        <span><span class="mini-wine__title">${esc(w.title)}</span>
          <span class="mini-wine__meta">${esc([w.manufacturer, w.region].filter(Boolean).join(" · "))}</span></span>
      </div>`;
  }

  function openDiary(wine, entry = null) {
    const rating = entry?.rating || 0;
    const tags = new Set(entry?.tags || []);
    openSheet(entry ? "Запись в дневнике" : "В дневник дегустации", `
      ${wineHead(wine)}
      <form class="form" id="diary-form" data-slug="${esc(wine.slug)}" data-id="${entry?.id || ""}">
        <div class="field"><span class="field__label">Оценка</span>
          <div class="rate5" role="radiogroup" aria-label="Оценка от 1 до 5">
            ${[1, 2, 3, 4, 5].map((i) => `<button class="rate5__star ${i <= rating ? "rate5__star--on" : ""}" type="button"
              data-action="diary-star" data-v="${i}" role="radio" aria-checked="${i === rating}" aria-label="${i}">${icon("star")}</button>`).join("")}
          </div>
          <input type="hidden" name="rating" value="${rating || ""}"></div>
        <div class="field"><span class="field__label">Что почувствовали</span>
          <div class="chips">${TAGS.map((t) => `<button class="chip ${tags.has(t) ? "chip--on" : ""}" type="button"
            data-action="diary-tag" aria-pressed="${tags.has(t)}">${esc(t)}</button>`).join("")}</div></div>
        <label class="field"><span class="field__label">Заметка</span>
          <textarea class="field__input field__input--area" name="note" maxlength="2000" rows="2"
            placeholder="Например: яркая вишня, мягкое послевкусие">${esc(entry?.note || "")}</textarea></label>
        <label class="field"><span class="field__label">Где, с кем, с чем</span>
          <input class="field__input" name="occasion" maxlength="200" value="${esc(entry?.occasion || "")}"
            placeholder="Ужин с друзьями, к стейку"></label>
        <div class="form__btns">
          <button class="ui-button ui-button_primary ui-button_large" type="submit">Сохранить</button>
          ${entry
            ? `<button class="link-btn" type="button" data-action="diary-delete" data-id="${entry.id}">Удалить запись</button>`
            : `<button class="link-btn" type="button" data-action="diary-want">Хочу попробовать позже</button>`}
        </div>
        ${signedIn() ? "" : `<p class="form__note">Дневник хранится в профиле — на «Сохранить» предложим войти</p>`}
      </form>`);
    $("#sheet").dataset.wine = JSON.stringify(wine);
  }

  function diaryBody(form, kind = "tasted") {
    return {
      kind,
      rating: Number(form.rating.value) || null,
      tags: [...form.querySelectorAll(".chip--on")].map((b) => b.textContent.trim()),
      note: form.note.value.trim() || null,
      occasion: form.occasion.value.trim() || null,
    };
  }

  async function saveDiary(form, kind = "tasted") {
    const wine = JSON.parse($("#sheet").dataset.wine || "{}");
    const body = diaryBody(form, kind);
    const id = form.dataset.id;
    if (!signedIn()) {
      draft = { wine, body };
      askRegister("diary", () => { const d = draft; draft = null; if (d) sendDiary(d.wine, d.body); });
      return;
    }
    await sendDiary(wine, body, id);
  }

  async function sendDiary(wine, body, id = null) {
    try {
      const out = id ? await call("PUT", `/v1/me/diary/${id}`, body)
        : await call("POST", "/v1/me/diary", { ...body, slug: wine.slug });
      closeSheet();
      toast(body.kind === "want" ? "Добавили в «Хочу попробовать»" : "Записали в дневник");
      track("diary", { kind: body.kind, rating: body.rating || null });
      st.diary = null;
      refreshProfileDiary();
      celebrate(out);
    } catch (err) { toast(err.message); }
  }

  async function wineFor(slug) {
    const current = W.current?.();
    if (current?.slug === slug) return current;
    return W.request(`/v1/wines/${encodeURIComponent(slug)}`);
  }

  // ── Профиль: челленджи, призы, итоги года, дневник ───────────────────────
  async function mountProfile(top, diaryRoot) {
    if (!top) return;
    const [prizes, challenges, year] = await Promise.all([
      call("GET", "/v1/me/prizes").catch(() => []),
      call("GET", "/v1/challenges").catch(() => []),
      call("GET", "/v1/settings/year").catch(() => null),
    ]);
    st.year = year;
    const yearOn = year && (year.enabled || ACC().isAdmin());
    top.innerHTML = `
      ${yearOn ? `
        <button class="year-banner" type="button" data-action="year-open">
          ${icon("sparkles")}<span><b>Мой винный ${year.year}</b>
          <span>${year.enabled ? "Итоги года — вина, регионы, любимый сорт" : "Предпросмотр: пользователи пока не видят"}</span></span>
          ${icon("chevron")}
        </button>` : ""}
      ${challenges.length ? `<div class="profile-challenges">${challenges.map(challengeHtml).join("")}</div>` : ""}
      ${prizes.length ? `
        <section class="section" aria-labelledby="prizes-title">
          <div class="section__head"><h2 class="section__title" id="prizes-title">Мои призы</h2>
            <span class="section__count">${prizes.length}</span></div>
          <div class="prizes">${prizes.map((p) => `
            <div class="prize-row">
              <span class="round-icon">${icon("ticket")}</span>
              <span class="prize-row__text"><b>${esc(p.title)}</b><span>${esc(p.reason)}</span>
                ${p.description ? `<span>${esc(p.description)}</span>` : ""}</span>
              ${codeHtml(p.code)}
            </div>`).join("")}</div>
        </section>` : ""}`;
    if (diaryRoot) {
      diaryRoot.dataset.mounted = "1";
      st.diary = null;
      await loadDiary(diaryRoot);
    }
  }

  async function loadDiary(root = $("#profile-diary"), more = false) {
    if (!root) return;
    try {
      const offset = more ? st.diary.items.length : 0;
      const page = await call("GET", `/v1/me/diary?kind=${st.diaryKind}&limit=${PAGE}&offset=${offset}`);
      st.diary = more ? { ...page, items: [...st.diary.items, ...page.items] } : page;
      renderDiary(root);
    } catch (_) { root.innerHTML = ""; }
  }

  function renderDiary(root) {
    const d = st.diary;
    const tab = (kind, label, n) => `<button class="seg__btn ${st.diaryKind === kind ? "seg__btn--on" : ""}" type="button"
      role="tab" aria-selected="${st.diaryKind === kind}" data-action="diary-tab" data-kind="${kind}">${label}${n ? ` · ${n}` : ""}</button>`;
    root.innerHTML = `
      <section class="section" aria-labelledby="diary-title">
        <div class="section__head"><h2 class="section__title" id="diary-title">Дневник дегустации</h2></div>
        <div class="seg diary-tabs" role="tablist">${tab("tasted", "Попробовал", d.tasted)}${tab("want", "Хочу попробовать", d.want)}</div>
        <div class="history">
          ${d.items.length ? d.items.map(diaryRowHtml).join("") : `
            <div class="empty"><p>${st.diaryKind === "tasted"
              ? "Попробовали вино — откройте его карточку и нажмите «В дневник»: оценка, вкус, с чем пили"
              : "Сюда попадают вина «на потом» — и подарки по открыткам"}</p></div>`}
          ${d.items.length < d.total ? `<button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="diary-more">Показать ещё</button>` : ""}
        </div>
      </section>`;
  }

  function diaryRowHtml(n) {
    const w = n.wine || {};
    const meta = n.kind === "tasted"
      ? [n.tags.slice(0, 3).join(", "), n.occasion].filter(Boolean).join(" · ")
      : n.occasion || [w.manufacturer, w.region].filter(Boolean).join(" · ");
    return `
      <button class="scan-row diary-row" type="button" data-action="diary-edit" data-id="${n.id}">
        <span class="scan-row__img">${w.image_url ? img(w.image_url, "", "") : icon("wine")}</span>
        <span class="scan-row__text">
          <span class="scan-row__title">${esc(w.title || n.slug)}</span>
          ${n.kind === "tasted" && n.rating ? stars(n.rating) : ""}
          ${meta ? `<span class="scan-row__meta">${esc(meta)}</span>` : ""}
          <span class="scan-row__date">${esc(n.kind === "tasted" ? fmtDay(n.tasted_on) : "хочу попробовать")}</span>
        </span>
      </button>`;
  }

  function refreshProfileDiary() {
    const root = $("#profile-diary");
    if (root?.dataset.mounted && $("#screen-profile").classList.contains("screen--active")) loadDiary(root);
  }

  // ── Подарочная открытка ──────────────────────────────────────────────────
  function openGiftForm(wine) {
    openSheet("Подарить вино", `
      ${wineHead(wine)}
      <p class="sheet__hint">Получатель откроет открытку по ссылке: вино, как подать, к чему — и ваше пожелание</p>
      <form class="form" id="gift-form" data-slug="${esc(wine.slug)}">
        <label class="field"><span class="field__label">От кого</span>
          <input class="field__input" name="from_name" maxlength="40" value="${esc(ACC().userName() || "")}" placeholder="Ваше имя"></label>
        <label class="field"><span class="field__label">Пожелание</span>
          <textarea class="field__input field__input--area" name="message" maxlength="300" rows="3"
            placeholder="Попробуй это вино — мне кажется, тебе понравится!"></textarea></label>
        <button class="ui-button ui-button_primary ui-button_large" type="submit">${icon("gift")}Создать открытку</button>
      </form>`);
  }

  async function createGift(form) {
    try {
      const g = await call("POST", "/v1/gifts", {
        slug: form.dataset.slug, from_name: form.from_name.value.trim() || null, message: form.message.value.trim() || null,
      });
      const url = `${location.origin}${location.pathname}#gift/${g.token}`;
      const text = `${g.from_name} дарит вам вино «${g.wine.title}»`;
      track("share", { channel: "gift", kind: "gift" });
      openSheet("Открытка готова", `
        ${wineHead(g.wine)}
        <p class="gift-quote">«${esc(g.message)}»</p>
        <div class="gift-link"><span>${esc(url)}</span></div>
        <div class="form__btns">
          ${navigator.share ? `<button class="ui-button ui-button_primary ui-button_large" type="button" data-action="gift-native"
              data-url="${esc(url)}" data-text="${esc(text)}">${icon("share")}Отправить…</button>` : ""}
          <a class="ui-button ui-button_elevated ui-button_large" target="_blank" rel="noopener"
             href="https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}">Telegram</a>
          <a class="ui-button ui-button_elevated ui-button_large" target="_blank" rel="noopener"
             href="https://vk.com/share.php?url=${encodeURIComponent(url)}&title=${encodeURIComponent(text)}">ВКонтакте</a>
          <button class="link-btn" type="button" data-action="eng-copy" data-code="${esc(url)}">${icon("copy")}Скопировать ссылку</button>
        </div>`);
    } catch (err) { toast(err.message); }
  }

  async function openGiftLanding(token) {
    let g;
    try { g = await call("GET", `/v1/gifts/${encodeURIComponent(token)}`); } catch (err) { toast(err.message); return; }
    const w = g.wine;
    track("gift_open");
    openSheet("Вам подарили вино", `
      <div class="gift-card">
        <p class="gift-card__from">${icon("gift")}От: ${esc(g.from_name)}</p>
        <p class="gift-quote">«${esc(g.message)}»</p>
        <span class="gift-card__bottle">${w.image_url ? img(w.image_url, "", w.title) : icon("wine")}</span>
        <h3 class="gift-card__title">${esc(w.title)}</h3>
        <p class="gift-card__meta">${esc([w.manufacturer, w.region, w.category].filter(Boolean).join(" · "))}</p>
        <div class="form__btns">
          <button class="ui-button ui-button_primary ui-button_large" type="button" data-action="gift-save" data-token="${esc(g.token)}">
            ${icon("book")}Сохранить себе</button>
          <button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="gift-wine" data-slug="${esc(w.slug)}">
            Как подать и с чем пить</button>
        </div>
        <p class="sheet__hint">Сохраним в дневник «Хочу попробовать» — не забудете купить</p>
      </div>`);
    history.replaceState(null, "", location.pathname + location.search);
  }

  async function saveGift(token) {
    const save = async () => {
      try {
        await call("POST", `/v1/gifts/${encodeURIComponent(token)}/save`);
        closeSheet();
        toast("Сохранили в дневник — «Хочу попробовать»");
        track("gift_save");
      } catch (err) { toast(err.message); }
    };
    if (signedIn()) save();
    else askRegister("gift", save);
  }

  // ── Мой винный год ───────────────────────────────────────────────────────
  // Истории-слайды: коллаж бутылок, карта России, сорт года на фоне винограда,
  // лучшее вино, огонёк серии и значки, финальный «постер» с картинкой для соцсетей
  async function openYear() {
    let y;
    try { y = await call("GET", "/v1/me/year"); } catch (err) { toast(err.message); return; }
    st.yearData = y;
    track("year_open");
    // Картинки сцен — с сайта: пейзаж винной родины, карта, виноград сорта года, блюдо к лучшему вину
    const card = (w) => (w ? W.request(`/v1/wines/${encodeURIComponent(w.slug)}`).catch(() => null) : null);
    const [mapUrl, taste, best, regions] = await Promise.all([
      y.regions ? window.WineMap?.shareData?.({ pad: 0.15 }).then((m) => (m ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(nightMap(m.svg))}` : null)).catch(() => null) : null,
      card(y.taste_wine),
      card(y.best_wine),
      y.top_region ? W.request("/v1/regions").catch(() => []) : [],
    ]);
    const homeland = big(regions.find((r) => r.name === y.top_region)?.image);
    const dish = (best?.sommelier?.pairings || []).find((p) => p.image);
    const covers = (y.covers || []).slice(-5);
    const wines = (n) => plural(n, "вино", "вина", "вин");
    const bottle = (src, cls = "") => (src ? `<img class="${cls}" ${srcAttr(src, 900)} alt="">` : "");
    const slides = [
      `<div class="ys ys--intro ys--photo">
        ${photo(homeland)}
        <p class="ys__kicker">Ваш винный</p>
        <p class="ys__year">${y.year}</p>
        ${covers.length ? `<div class="ys__fan">${covers.map((u, i) => `<img ${srcAttr(u)} alt="" style="--i:${i - (covers.length - 1) / 2}">`).join("")}</div>` : ""}
        <p class="ys__big">${y.wines}<span> ${wines(y.wines)}</span></p>
        <p class="ys__small">${y.scans} ${plural(y.scans, "скан", "скана", "сканов")}${y.first_wine
          ? ` · первое вино года — «${esc(y.first_wine.title)}»` : ""}</p>
      </div>`,
      y.regions ? `<div class="ys ys--map">
        ${mapUrl ? `<img class="ys__mapbg" src="${mapUrl}" alt="Открытые регионы на карте России">` : ""}
        <p class="ys__kicker">Винная Россия</p>
        <p class="ys__big">${y.regions}<span> из ${y.regions_total || "?"}</span></p>
        <p class="ys__line">винных регионов открыто</p>
        ${y.top_region ? `<p class="ys__chip">Ваша винная родина — ${esc(y.top_region)}</p>
          <p class="ys__small">${y.top_region_wines} ${wines(y.top_region_wines)} оттуда${y.region_names.length > 1
            ? ` · ещё ${esc(y.region_names.slice(1, 4).join(", "))}` : ""}</p>` : ""}
      </div>` : "",
      y.top_grape || y.top_style ? `<div class="ys ys--taste ys--photo">
        ${photo(taste?.background_image)}
        <p class="ys__kicker">Ваш вкус</p>
        ${y.top_grape ? `<p class="ys__line">Сорт года</p><p class="ys__title">${esc(y.top_grape)}</p>` : ""}
        ${bottle(y.taste_wine?.image_url, "ys__bottle")}
        ${y.top_style ? `<p class="ys__chip">Ваш стиль — ${esc(y.top_style.toLowerCase())}</p>` : ""}
        ${y.top_manufacturer ? `<p class="ys__small">Винодельня года — ${esc(y.top_manufacturer)}</p>` : ""}
      </div>` : "",
      y.best_wine || y.likes ? `<div class="ys ys--best">
        ${y.best_wine?.image_url ? `<img class="ys__blur" ${srcAttr(y.best_wine.image_url)} alt="">` : ""}
        <p class="ys__kicker">${y.best_wine ? "Лучшее вино года" : "Понравилось"}</p>
        ${y.best_wine ? `<div class="ys__stage">
            <span class="ys__beam" aria-hidden="true"></span>
            ${dish ? `<img class="ys__plate" src="${esc(big(dish.image, 480))}" alt="" onerror="this.remove()">` : ""}
            ${bottle(y.best_wine.image_url, "ys__hero")}
            ${(y.top_tags || []).map((t, i) => `<span class="ys__note" style="--n:${i}">${esc(t)}</span>`).join("")}
          </div>
          <p class="ys__stars" aria-label="Оценка ${y.best_rating || 5} из 5">${[1, 2, 3, 4, 5].map((i) =>
            `<span class="${i <= (y.best_rating || 5) ? "on" : ""}" style="--s:${i}">★</span>`).join("")}</p>
          <p class="ys__title ys__title--sm">«${esc(y.best_wine.title)}»</p>
          <p class="ys__small">${[dish ? `С чем пить — ${esc(dish.dish.toLowerCase())}` : "",
            `${y.diary} ${plural(y.diary, "запись", "записи", "записей")} в дневнике`].filter(Boolean).join(" · ")}</p>`
          : `<p class="ys__big">${y.likes}<span> ${wines(y.likes)}</span></p><p class="ys__line">отмечено «нравится»</p>`}
      </div>` : "",
      y.quiz_days || y.badges?.length ? `<div class="ys ys--game">
        <span class="ys__bokeh" aria-hidden="true"></span>
        <p class="ys__kicker">${y.badges?.length ? "Ваши награды" : "Вопрос дня"}</p>
        ${y.badges?.length ? `<div class="ys__medals">${y.badges.slice(0, 5).map((b, i, list) => `<span title="${esc(b.title)}"
            style="--i:${i - (list.length - 1) / 2};--d:${0.25 + i * 0.15}s">${b.image_url
            ? `<img src="${esc(b.image_url)}" alt="${esc(b.title)}">` : icon("trophy")}</span>`).join("")}</div>
          <p class="ys__big">${y.badges.length}<span> ${plural(y.badges.length, "достижение", "достижения", "достижений")} за год</span></p>
          <p class="ys__small">Последнее — «${esc(y.badges[y.badges.length - 1].title)}»</p>` : ""}
        ${y.quiz_days ? `<div class="ys__streak ${y.badges?.length ? "" : "ys__streak--solo"}">
            <span class="ys__streak-flame">${icon("flame")}<b>${y.best_streak}</b></span>
            <span><b>${y.best_streak} ${plural(y.best_streak, "день", "дня", "дней")} подряд</b> — лучшая серия
              <br>${y.quiz_days} ${plural(y.quiz_days, "ответ", "ответа", "ответов")} на вопрос дня</span>
          </div>` : ""}
      </div>` : "",
      `<div class="ys ys--final ys--photo">
        ${homeland ? `<img class="ys__photo ys__photo--soft" src="${esc(homeland)}" alt="" onerror="this.remove()">` : ""}
        <div class="ys__confetti" aria-hidden="true">${Array.from({ length: 36 }, (_, i) =>
          `<i style="--x:${(i * 37) % 100};--d:${(i % 7) * 0.18}s;--c:${["#F8ECC9", "#D9A23A", "#FFFFFF", "#E58E95"][i % 4]}"></i>`).join("")}</div>
        <p class="ys__headline">Это был ваш<br>винный ${y.year}</p>
        <div class="ys__poster">
          <p class="ys__poster-kicker">Мой винный ${y.year}</p>
          ${covers.length ? `<div class="ys__poster-bottles">${covers.map((u) => `<img ${srcAttr(u)} alt="">`).join("")}</div>` : ""}
          <div class="ys__poster-grid">
            <span><b>${y.wines}</b>${wines(y.wines)}</span>
            <span><b>${y.regions}${y.regions_total ? `/${y.regions_total}` : ""}</b>регионов</span>
            <span><b>${esc(y.top_grape || "—")}</b>сорт года</span>
            ${y.best_streak
              ? `<span><b>${y.best_streak} ${plural(y.best_streak, "день", "дня", "дней")}</b>серия в «Вопросе дня»</span>`
              : `<span><b>${y.diary}</b>${plural(y.diary, "запись", "записи", "записей")} в дневнике</span>`}
          </div>
        </div>
        <div class="year__btns">
          <button class="ui-button ui-button_large ys__share" type="button" data-action="year-share">${icon("share")}Поделиться итогами</button>
          <button class="link-btn ys__closelink" type="button" data-action="year-close">Закрыть</button>
        </div>
      </div>`,
    ].filter(Boolean);
    const el = document.createElement("div");
    el.className = "year";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-label", `Мой винный ${y.year}`);
    el.innerHTML = `
      <div class="year__bars">${slides.map(() => "<i></i>").join("")}</div>
      <button class="year__close" type="button" data-action="year-close" aria-label="Закрыть">${icon("x")}</button>
      ${y.enabled ? "" : `<p class="year__preview">Предпросмотр — пользователи пока не видят</p>`}
      <button class="year__tap year__tap--prev" type="button" data-action="year-step" data-d="-1" aria-label="Назад"></button>
      <button class="year__tap year__tap--next" type="button" data-action="year-step" data-d="1" aria-label="Дальше"></button>
      ${slides.map((s, i) => `<div class="year__slide" data-i="${i}" ${i ? "hidden" : ""}>${s}</div>`).join("")}`;
    document.body.appendChild(el);
    document.body.style.overflow = "hidden";
    stepYear(0);
  }

  // Для фона — крупная версия картинки сайта: локальная копия /site-media/800/,
  // а без копии — ресайз-API сайта (…/str-api/88/88/… → нужный размер)
  const big = (url, size = 800) => (url ? url.replace(/^\/site-media\/\d+\//, "/site-media/800/")
    .replace(/\/str-api\/\d+\/\d+\//, `/str-api/${size}/${size}/`) : null);
  const photo = (url) => (url ? `<img class="ys__photo" src="${esc(url)}" alt="" onerror="this.remove()">` : "");

  // Карта для ночного фона: открытые регионы — золотом, остальные едва видны
  function nightMap(svg) {
    const colors = { "#EAE0CB": "rgba(255,255,255,.07)", "#C27B80": "rgba(255,255,255,.16)",
      "#D9BC86": "rgba(248,236,201,.28)", "#8F3D42": "#F2C36B", "#FFFFFF": "rgba(58,21,25,.85)" };
    return svg.replace(/"(#[0-9A-F]{6})"/gi, (m, c) => (colors[c.toUpperCase()] ? `"${colors[c.toUpperCase()]}"` : m));
  }

  function stepYear(delta) {
    const el = $(".year");
    if (!el) return;
    const slides = [...el.querySelectorAll(".year__slide")];
    const now = slides.findIndex((s) => !s.hidden);
    const next = Math.max(0, Math.min(slides.length - 1, now + delta));
    slides.forEach((s, i) => { s.hidden = i !== next; });
    el.querySelectorAll(".year__bars i").forEach((b, i) => b.classList.toggle("on", i <= next));
    el.querySelector(".year__tap--next").hidden = next === slides.length - 1;
    el.querySelector(".year__tap--prev").hidden = next === 0;
  }

  function closeYear() {
    $(".year")?.remove();
    document.body.style.overflow = "";
  }

  function shareYear() {
    const y = st.yearData;
    if (!y) return;
    track("share", { kind: "year", channel: "open" });
    // Картинка 1080×1350 и Telegram / ВК / «Сохранить» — как у карточки вина (share.js);
    // шторка открывается поверх итогов (у .year z-index ниже, чем у шторки)
    if (window.WineShare?.shareYear) window.WineShare.shareYear(y);
    else shareText(`Мой винный ${y.year}: ${y.wines} ${plural(y.wines, "вино", "вина", "вин")}. «Своё Вино»`);
  }

  async function shareText(text, url = location.origin + location.pathname) {
    if (navigator.share) {
      try { await navigator.share({ text, url }); return; } catch (_) { return; }
    }
    copyCode(`${text} ${url}`);
  }

  // ── События ──────────────────────────────────────────────────────────────
  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const a = el.dataset.action;
    switch (a) {
      case "quiz-answer": answerQuiz(Number(el.dataset.i)); break;
      case "quiz-save": askRegister("quiz", saveQuizAfterAuth); break;
      case "quiz-wine": openWine(el.dataset.slug); break;
      case "quiz-zoom":
        openSheet("Рассмотрите этикетку", `<div class="quiz-zoom">${img(st.quiz.image_url, "", "Бутылка вина", 900)}</div>`);
        break;
      case "eng-auth": askRegister("challenge", loadHome); break;
      case "eng-copy": copyCode(el.dataset.code); break;
      case "diary-open":
        try { openDiary(await wineFor(el.dataset.slug)); } catch (err) { toast(err.message); }
        break;
      case "diary-star": {
        const form = el.closest("form");
        const v = Number(el.dataset.v);
        const value = Number(form.rating.value) === v ? 0 : v; // повторное нажатие снимает оценку
        form.rating.value = value || "";
        form.querySelectorAll(".rate5__star").forEach((b, i) => {
          b.classList.toggle("rate5__star--on", i < value);
          b.setAttribute("aria-checked", String(i + 1 === value));
        });
        break;
      }
      case "diary-tag":
        el.classList.toggle("chip--on");
        el.setAttribute("aria-pressed", String(el.classList.contains("chip--on")));
        break;
      case "diary-want": saveDiary(el.closest("form"), "want"); break;
      case "diary-edit": {
        const n = st.diary?.items.find((x) => String(x.id) === el.dataset.id);
        if (n) openDiary(n.wine, n);
        break;
      }
      case "diary-delete":
        if (!confirm("Удалить запись из дневника?")) break;
        try { await call("DELETE", `/v1/me/diary/${el.dataset.id}`); closeSheet(); toast("Запись удалена"); loadDiary(); }
        catch (err) { toast(err.message); }
        break;
      case "diary-tab": st.diaryKind = el.dataset.kind; loadDiary(); break;
      case "diary-more": loadDiary($("#profile-diary"), true); break;
      case "gift-open":
        try { openGiftForm(await wineFor(el.dataset.slug)); } catch (err) { toast(err.message); }
        break;
      case "gift-native": shareText(el.dataset.text, el.dataset.url); break;
      case "gift-save": saveGift(el.dataset.token); break;
      case "gift-wine": closeSheet(); openWine(el.dataset.slug); break;
      case "year-open": openYear(); break;
      case "year-step": stepYear(Number(el.dataset.d)); break;
      case "year-close": closeYear(); break;
      case "year-share": shareYear(); break;
    }
  });

  document.addEventListener("submit", (e) => {
    if (e.target.id === "diary-form") { e.preventDefault(); saveDiary(e.target); }
    else if (e.target.id === "gift-form") { e.preventDefault(); createGift(e.target); }
  });

  document.addEventListener("keydown", (e) => {
    if (!$(".year")) return;
    if (e.key === "Escape") closeYear();
    else if (e.key === "ArrowRight") stepYear(1);
    else if (e.key === "ArrowLeft") stepYear(-1);
  });

  document.addEventListener("wine:card", (e) => addWineActions(e.detail.wine));
  document.addEventListener("wine:prizes", (e) => showPrizes(e.detail));
  document.addEventListener("wine:auth", () => { if (!store.get(QUIZ_KEY)) loadHome(); });

  function route() {
    const m = location.hash.match(/^#gift\/([\w-]+)$/);
    if (m) openGiftLanding(m[1]);
  }
  window.addEventListener("hashchange", route);

  // account.js узнаёт, есть ли сервис пользователей, асинхронно — ждём его
  (async function start(tries = 0) {
    if (!ACC()) return;
    if (!ACC().available()) {
      if (tries < 40) setTimeout(() => start(tries + 1), 250);
      return;
    }
    loadHome();
    route();
  })();

  window.WineEngage = { mountProfile, openYear };
})();
