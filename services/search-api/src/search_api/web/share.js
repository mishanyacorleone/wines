/* «Поделиться» и «Сохранить картинку»: вино, достижение, винная карта.
 *
 * Картинка 1080×1350 рисуется в браузере (canvas) — без сервера и внешних
 * сервисов; QR-код ведёт на страницу вина на vino-svoe.ru (vendor/qrcode.js, MIT).
 * Telegram и ВКонтакте по ссылке принимают только адрес и текст, поэтому:
 *   — кнопки Telegram и ВК делятся ссылкой на страницу вина с текстом;
 *   — «Сохранить» — картинку можно прикрепить к сообщению или сторис вручную.
 * Системное меню «Отправить…» (Web Share с файлом) убрано 18.10: у владельца
 * картинка через него не доходила; версия с ним — backups/share-v1-2026-10-18/.
 * Ссылки ведут на vino-svoe.ru, а не на этот стенд: у получателя стенд не откроется.
 */
(() => {
  "use strict";

  const W = window.Wine;
  if (!W || W.DEMO) return;
  const { $, esc, icon, openSheet, toast } = W;

  // Событие для аналитики (analytics.js): модуль про неё ничего не знает
  const track = (type, props = {}) => document.dispatchEvent(new CustomEvent("wine:track", { detail: { type, props } }));

  const SITE = "https://vino-svoe.ru/";
  const SIZE = { w: 1080, h: 1350 };
  const C = {
    paper: "#FEFDFA", cream: "#FDF9ED", gold: "#F8ECC9", wine: "#8F3D42", wineDark: "#7B3528",
    ink: "#2C2A28", ink2: "#423F3C", muted: "#857E79", line: "#EBE9E9",
  };
  const SERIF = '"Playfair Display", Georgia, serif';
  const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  // ── Рисование ────────────────────────────────────────────────────────────
  async function canvas() {
    // Шрифт сайта должен успеть загрузиться, иначе canvas нарисует запасной
    try {
      await Promise.all([document.fonts.load(`600 64px ${SERIF}`), document.fonts.load(`500 40px ${SERIF}`)]);
    } catch (_) { /* останется Georgia */ }
    const el = document.createElement("canvas");
    el.width = SIZE.w;
    el.height = SIZE.h;
    const ctx = el.getContext("2d");
    ctx.fillStyle = C.paper;
    ctx.fillRect(0, 0, SIZE.w, SIZE.h);
    return { el, ctx };
  }

  function roundRect(ctx, x, y, w, h, r, fill) {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    ctx.fillStyle = fill;
    ctx.fill();
  }

  // Перенос по словам; лишнее — многоточием в последней строке
  function lines(ctx, text, maxWidth, maxLines) {
    const words = String(text || "").split(/\s+/).filter(Boolean);
    const out = [];
    let line = "";
    for (const word of words) {
      const test = line ? `${line} ${word}` : word;
      if (ctx.measureText(test).width <= maxWidth || !line) {
        line = test;
      } else {
        out.push(line);
        line = word;
        if (out.length === maxLines) break;
      }
    }
    if (out.length < maxLines && line) out.push(line);
    if (out.length === maxLines && words.join(" ") !== out.join(" ")) {
      let last = out[maxLines - 1];
      while (last && ctx.measureText(`${last}…`).width > maxWidth) last = last.slice(0, -1);
      out[maxLines - 1] = `${last.replace(/[\s,.;:—-]+$/, "")}…`;
    }
    return out;
  }

  function text(ctx, value, x, y, { font, color = C.ink, align = "left", maxWidth, maxLines = 1, lineHeight }) {
    ctx.font = font;
    ctx.fillStyle = color;
    ctx.textAlign = align;
    ctx.textBaseline = "alphabetic";
    const rows = maxWidth ? lines(ctx, value, maxWidth, maxLines) : [String(value)];
    rows.forEach((row, i) => ctx.fillText(row, x, y + i * (lineHeight || 0)));
    return y + (rows.length - 1) * (lineHeight || 0);
  }

  function loadImage(src) {
    return new Promise((resolve) => {
      if (!src) return resolve(null);
      const image = new Image();
      image.decoding = "async";
      // Фото с другого домена (ресайз-API сайта) без CORS «отравило» бы canvas —
      // сохранить картинку стало бы нельзя. С crossOrigin такое фото просто не
      // загрузится, и карточка нарисуется без него
      if (new URL(src, location.href).origin !== location.origin) image.crossOrigin = "anonymous";
      image.onload = () => resolve(image);
      image.onerror = () => resolve(null); // картинка с чужого домена или нет сети — рисуем без неё
      image.src = src;
    });
  }

  function drawContain(ctx, image, x, y, w, h) {
    const k = Math.min(w / image.width, h / image.height);
    const dw = image.width * k, dh = image.height * k;
    ctx.drawImage(image, x + (w - dw) / 2, y + (h - dh), dw, dh);
  }

  function qr(ctx, url, x, y, size) {
    if (typeof qrcode !== "function") return;
    const code = qrcode(0, "M");
    code.addData(url);
    code.make();
    const n = code.getModuleCount();
    const quiet = 2;
    const cell = size / (n + quiet * 2);
    roundRect(ctx, x, y, size, size, 18, "#FFFFFF");
    ctx.fillStyle = C.ink;
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        if (code.isDark(r, c)) ctx.fillRect(x + (c + quiet) * cell, y + (r + quiet) * cell, Math.ceil(cell), Math.ceil(cell));
      }
    }
  }

  // Шапка и подвал — общие для всех картинок
  function frame(ctx, chip) {
    roundRect(ctx, 40, 40, SIZE.w - 80, SIZE.h - 80, 56, C.cream);
    text(ctx, "Своё Вино", 96, 136, { font: `600 46px ${SERIF}`, color: C.wine });
    text(ctx, "от Россельхозбанка", 98, 172, { font: `400 22px ${SANS}`, color: C.muted });
    if (chip) {
      ctx.font = `600 26px ${SANS}`;
      const w = ctx.measureText(chip).width + 48;
      roundRect(ctx, SIZE.w - 96 - w, 96, w, 56, 28, C.gold);
      text(ctx, chip, SIZE.w - 96 - w / 2, 133, { font: `600 26px ${SANS}`, color: C.wine, align: "center" });
    }
  }

  function footer(ctx, url, caption) {
    const y = SIZE.h - 248;
    ctx.fillStyle = C.line;
    ctx.fillRect(96, y - 32, SIZE.w - 192, 2);
    qr(ctx, url, SIZE.w - 96 - 168, y, 168);
    text(ctx, caption, 96, y + 58, { font: `400 28px ${SANS}`, color: C.ink2, maxWidth: 660, maxLines: 2, lineHeight: 38 });
    text(ctx, "vino-svoe.ru", 96, y + 150, { font: `700 40px ${SANS}`, color: C.wine });
  }

  // ── Вино ────────────────────────────────────────────────────────────────
  async function drawWine(wine) {
    const { el, ctx } = await canvas();
    frame(ctx, "Найдено по фото");
    const bottle = await loadImage(wine.image_url);
    // бутылка слева, с мягкой тенью
    const bx = 96, by = 220, bw = 330, bh = 720;
    roundRect(ctx, bx, by, bw, bh, 40, "#FFFFFF");
    if (bottle) {
      ctx.save();
      ctx.shadowColor = "rgba(44, 42, 40, .22)";
      ctx.shadowBlur = 36;
      ctx.shadowOffsetY = 18;
      drawContain(ctx, bottle, bx + 30, by + 40, bw - 60, bh - 70);
      ctx.restore();
    }
    // справа — название и факты
    const x = 480, maxW = SIZE.w - 96 - x;
    let y = 262;
    text(ctx, "МОЁ ВИНО", x, y, { font: `700 24px ${SANS}`, color: C.wine });
    y = text(ctx, wine.title, x, y + 74, { font: `600 62px ${SERIF}`, maxWidth: maxW, maxLines: 3, lineHeight: 74 });
    if (wine.manufacturer) y = text(ctx, wine.manufacturer, x, y + 56, { font: `400 32px ${SANS}`, color: C.wine, maxWidth: maxW, maxLines: 2, lineHeight: 40 });
    const s = wine.sommelier || {};
    const temp = s.serving?.temperature || (wine.temperature ? `${String(wine.temperature).replace("-", "–")} °C` : null);
    const facts = [
      ["Регион", wine.region],
      ["Категория", wine.category],
      ["Подача", temp],
      ["Крепость", wine.alcohol != null ? `${String(wine.alcohol).replace(".", ",").replace(/,0$/, "")}%` : null],
    ].filter(([, v]) => v);
    y += 40;
    for (const [label, value] of facts) {
      text(ctx, label, x, y + 36, { font: `400 24px ${SANS}`, color: C.muted });
      y = text(ctx, value, x, y + 74, { font: `500 32px ${SANS}`, color: C.ink, maxWidth: maxW, maxLines: 1 });
      y += 14;
    }
    // совет сомелье — во всю ширину, под бутылкой
    const dishes = (s.pairings || []).map((p) => p.dish).slice(0, 3).join(", ");
    const tip = dishes ? `К блюдам: ${dishes}` : s.summary;
    if (tip) {
      roundRect(ctx, 96, 970, SIZE.w - 192, 72, 36, C.gold);
      text(ctx, tip, 132, 1016, { font: `500 28px ${SANS}`, color: C.wineDark, maxWidth: SIZE.w - 264, maxLines: 1 });
    }
    footer(ctx, wine.url || SITE, "Наведите камеру на QR — карточка вина на сайте «Своё Вино»");
    return el;
  }

  // ── Достижение ──────────────────────────────────────────────────────────
  async function drawAchievement(a) {
    const { el, ctx } = await canvas();
    frame(ctx, "Достижение");
    const cx = SIZE.w / 2;
    // мягкий ореол под значком
    const glow = ctx.createRadialGradient(cx, 520, 40, cx, 520, 360);
    glow.addColorStop(0, "rgba(248, 236, 201, 1)");
    glow.addColorStop(1, "rgba(253, 249, 237, 0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 160, SIZE.w, 720);
    const badge = await loadImage(a.image);
    if (badge) {
      ctx.save();
      ctx.shadowColor = "rgba(108, 85, 15, .28)";
      ctx.shadowBlur = 40;
      ctx.shadowOffsetY = 20;
      ctx.drawImage(badge, cx - 230, 290, 460, 460);
      ctx.restore();
    }
    text(ctx, "НОВОЕ ДОСТИЖЕНИЕ", cx, 250, { font: `700 26px ${SANS}`, color: C.wine, align: "center" });
    let y = text(ctx, a.title, cx, 840, { font: `700 68px ${SANS}`, align: "center", maxWidth: 860, maxLines: 2, lineHeight: 80 });
    y = text(ctx, a.description, cx, y + 64, { font: `400 36px ${SANS}`, color: C.ink2, align: "center", maxWidth: 820, maxLines: 2, lineHeight: 48 });
    if (a.user) text(ctx, a.user, cx, y + 70, { font: `600 30px ${SANS}`, color: C.wine, align: "center", maxWidth: 820 });
    footer(ctx, SITE, "Сканируйте вина по фото и собирайте достижения в «Своё Вино»");
    return el;
  }

  // ── Винная карта ────────────────────────────────────────────────────────
  async function drawMap(m) {
    const { el, ctx } = await canvas();
    frame(ctx, "Винная карта");
    text(ctx, "Моя винная карта России", 96, 286, { font: `600 54px ${SERIF}` });
    text(ctx, `Открыто ${m.opened} из ${m.total} ${plural(m.total, "региона", "регионов", "регионов")}`, 96, 350, { font: `500 36px ${SANS}`, color: C.wine });
    roundRect(ctx, 96, 396, SIZE.w - 192, 560, 36, "#F4EFE4");
    const map = await loadImage(URL.createObjectURL(new Blob([m.svg], { type: "image/svg+xml" })));
    if (map) {
      const k = Math.min((SIZE.w - 232) / map.width, 520 / map.height);
      ctx.drawImage(map, (SIZE.w - map.width * k) / 2, 416 + (520 - map.height * k) / 2, map.width * k, map.height * k);
    }
    // открытые регионы — плашками
    let x = 96, y = 990;
    ctx.font = `600 26px ${SANS}`;
    for (const name of m.names.slice(0, 8)) {
      const w = ctx.measureText(name).width + 40;
      if (x + w > SIZE.w - 96) { x = 96; y += 60; }
      if (y > 1060) break;
      roundRect(ctx, x, y, w, 48, 24, C.wine);
      text(ctx, name, x + w / 2, y + 33, { font: `600 26px ${SANS}`, color: "#FFFFFF", align: "center" });
      x += w + 12;
    }
    footer(ctx, SITE, "Сканируйте вина по фото — регионы откроются на вашей карте");
    return el;
  }

  // ── Текст «Поделиться вином»: шаблон с масками, его меняет админ ─────────
  const DEFAULT_TEMPLATE = "Моё вино: «{название}», {бренд} — нашлось по фото в «Своё Вино»";
  const MASKS = [
    ["название", "Название вина"], ["сорт", "Сорт винограда"], ["сахар", "Сухое, полусладкое…"],
    ["цвет", "Красное, белое…"], ["бренд", "Винодельня"], ["регион", "Регион"], ["ссылка", "Страница вина на сайте"],
  ];
  const SUGAR = ["экстра брют", "брют натюр", "брют", "полусухое", "полусладкое", "сухое", "сладкое", "десертное", "ликёрное", "ликерное"];
  const COLOR = /(красное|белое|розовое|оранжевое|рыжее)/;
  let template = { text: null, at: 0 };

  async function wineTemplate() {
    if (template.text && Date.now() - template.at < 60_000) return template.text;
    try {
      const res = await fetch("/v1/settings/share");
      if (res.ok) template = { text: (await res.json()).wine_template, at: Date.now() };
    } catch (_) { /* нет сервиса пользователей — шаблон по умолчанию */ }
    return template.text || DEFAULT_TEMPLATE;
  }

  function maskValues(wine) {
    const category = String(wine.category || "").toLowerCase().replaceAll("ё", "е");
    const grapes = wine.grapes || [];
    return {
      "название": wine.title || "",
      "сорт": grapes.length > 2 ? `${grapes.slice(0, 2).join(", ")} и другие` : grapes.join(" и "),
      "сахар": SUGAR.find((s) => category.includes(s.replaceAll("ё", "е"))) || "",
      "цвет": category.match(COLOR)?.[1] || "",
      "бренд": wine.manufacturer || "",
      "регион": wine.region || "",
      "ссылка": wine.url || SITE,
    };
  }

  // Подстановка; пустая маска убирается вместе с осиротевшей запятой и пробелами
  function fill(text, wine) {
    const values = maskValues(wine);
    return text
      .replace(/\{([а-яё]+)\}/gi, (m, key) => (key.toLowerCase() in values ? values[key.toLowerCase()] : m))
      .replace(/«\s*»/g, "")
      .replace(/\(\s*\)/g, "")
      .replace(/[ \t]+([,.!?;:])/g, "$1")
      .replace(/,(\s*)(?=[,.!?;:—)]|$)/gm, "$1")
      .replace(/[ \t]{2,}/g, " ")
      .replace(/ +\n/g, "\n")
      .trim();
  }

  // ── Шторка «Поделиться» ─────────────────────────────────────────────────
  let current = null; // { file, url, text, objectUrl }

  async function open({ title, linkTitle, draw, url, message, filename, kind, linkInText = false }) {
    openSheet(title, `
      <div class="share-preview"><div class="skeleton share-preview__wait"></div></div>
      <div class="share-actions" id="share-actions" hidden>
        <button class="ui-button ui-button_primary ui-button_large share-native" type="button" data-action="share-native" hidden>${icon("share")}Отправить…</button>
        <div class="share-row">
          <button class="share-btn share-btn--tg" type="button" data-action="share-tg">${TG}Telegram</button>
          <button class="share-btn share-btn--vk" type="button" data-action="share-vk">${VK}ВКонтакте</button>
          <button class="share-btn" type="button" data-action="share-save">${icon("download")}Сохранить</button>
        </div>
        <p class="share-hint" id="share-hint"></p>
      </div>`);
    $("#sheet").classList.add("share-sheet");
    try {
      const el = await draw();
      const blob = await new Promise((r) => el.toBlob(r, "image/png"));
      if (current?.objectUrl) URL.revokeObjectURL(current.objectUrl);
      const file = new File([blob], filename, { type: "image/png" });
      current = { file, url, text: message, linkTitle, kind, linkInText, objectUrl: URL.createObjectURL(blob) };
      if (!$(".share-preview")) return; // шторку уже закрыли
      $(".share-preview").innerHTML = `<img src="${current.objectUrl}" alt="Картинка для публикации">`;
      // Системное меню «Поделиться» с файлом (iPhone, Android, Chrome на Windows/macOS).
      // Браузер даёт его только на https или localhost — на http-адресе кнопки нет
      const native = !!navigator.canShare?.({ files: [file] });
      $(".share-native").hidden = !native;
      $("#share-hint").textContent = native
        ? "«Отправить…» передаёт картинку и текст в любое приложение: сообщения, Telegram, ВКонтакте, почту, AirDrop"
        : "В Telegram текст встанет в сообщение сам, во ВКонтакте — скопируется: вставьте его в «Ваше сообщение». Картинку сохраните и прикрепите к сообщению или сторис";
      $("#share-actions").hidden = false;
      // Как в приложениях: меню сразу по «Поделиться». Браузер разрешает его
      // только вскоре после нажатия — если картинка рисовалась дольше, остаётся кнопка
      if (native && navigator.userActivation?.isActive) shareNative();
    } catch (err) {
      if ($(".share-preview")) $(".share-preview").innerHTML = `<p class="muted">Не удалось подготовить картинку: ${esc(err.message)}</p>`;
    }
  }

  async function shareWine() {
    const wine = W.current?.();
    if (!wine) return;
    const text = await wineTemplate();
    open({
      title: "Поделиться вином",
      linkTitle: `${wine.title}${wine.manufacturer ? ` — ${wine.manufacturer}` : ""}`,
      kind: "wine",
      draw: () => drawWine(wine),
      url: wine.url || SITE,
      message: fill(text, wine),
      // ссылка уже в тексте — второй раз её не добавляем
      linkInText: /\{ссылка\}/i.test(text),
      filename: `${wine.slug || "wine"}.png`,
    });
  }

  function shareAchievement(el) {
    const a = { title: el.dataset.title, description: el.dataset.description, image: el.dataset.image,
      user: window.WineAccount?.userName?.() };
    open({
      title: "Поделиться достижением",
      linkTitle: `Достижение «${a.title}» в «Своё Вино»`,
      kind: "achievement",
      draw: () => drawAchievement(a),
      url: SITE,
      message: `Новое достижение в «Своё Вино»: «${a.title}». ${a.description}`,
      filename: "dostizhenie.png",
    });
  }

  async function shareMap() {
    const m = await window.WineMap?.shareData?.();
    if (!m) return;
    open({
      title: "Поделиться картой",
      linkTitle: "Моя винная карта России — «Своё Вино»",
      kind: "map",
      draw: () => drawMap(m),
      url: SITE,
      message: `Моя винная карта России: открыто ${m.opened} из ${m.total} регионов в «Своё Вино»`,
      filename: "vinnaya-karta.png",
    });
  }

  // Копирование в буфер: Clipboard API есть только на https и localhost; на адресе
  // стенда по http работает старый способ через скрытое поле (в обработчике клика)
  function copyText(text) {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).catch(() => copyFallback(text));
      return true;
    }
    return copyFallback(text);
  }

  function copyFallback(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
    area.remove();
    return ok;
  }

  async function shareNative() {
    if (!current) return;
    track("share", { kind: current.kind, channel: "native" });
    // Ссылку — в текст, а не в поле url: с url часть приложений берёт только ссылку и теряет картинку
    const text = current.linkInText ? current.text : `${current.text}\n${current.url}`;
    try {
      await navigator.share({ files: [current.file], text });
    } catch (err) {
      if (err?.name !== "AbortError") toast("Не удалось открыть меню — сохраните картинку");
    }
  }

  function popup(href) {
    window.open(href, "_blank", "noopener,width=640,height=560");
  }

  function save() {
    const a = document.createElement("a");
    a.href = current.objectUrl;
    a.download = current.file.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast("Картинка сохранена");
  }

  // Значки соцсетей — упрощённые, монохромные (цвет задаёт кнопка)
  const TG = '<svg class="i i--fill" viewBox="0 0 24 24" aria-hidden="true"><path d="M21.4 4.1 2.9 11.3c-1.3.5-1.3 1.2-.2 1.5l4.7 1.5 1.8 5.6c.2.6.1.8.7.8.5 0 .7-.2 1-.5l2.3-2.2 4.7 3.5c.9.5 1.5.2 1.7-.8l3.1-14.6c.3-1.3-.5-1.9-1.3-1.6Zm-3 3.4-8.7 7.9-.3 3.6-1.6-5 9.6-6.1c.5-.3.9-.1.6.1Z"/></svg>';
  const VK = '<svg class="i i--fill" viewBox="0 0 24 24" aria-hidden="true"><path d="M12.8 17.3C6.6 17.3 3 13 2.9 6.2h3.1c.1 5 2.3 7.1 4 7.5V6.2h2.9v4.3c1.7-.2 3.5-2.1 4.1-4.3h2.9c-.5 2.6-2.4 4.5-3.8 5.3 1.4.6 3.6 2.3 4.4 5.2h-3.2c-.7-2.1-2.3-3.7-4.4-3.9v3.9h-.1Z"/></svg>';

  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action^='share-']");
    if (!el) return;
    switch (el.dataset.action) {
      case "share-wine": shareWine(); break;
      case "share-achievement": shareAchievement(el); break;
      case "share-map": shareMap(); break;
      case "share-native": shareNative(); break;
      case "share-save": if (current) { save(); track("share", { kind: current.kind, channel: "save" }); } break;
      case "share-tg":
        if (current) track("share", { kind: current.kind, channel: "tg" });
        if (current) popup(`https://t.me/share/url?url=${encodeURIComponent(current.url)}&text=${encodeURIComponent(textWithoutLink())}`);
        break;
      case "share-vk":
        // У ВК нет параметра для поля «Ваше сообщение»/комментария: `title` — это
        // заголовок карточки ссылки. Поэтому текст кладём в буфер, а заголовком
        // делаем название вина; пользователю — подсказка «вставьте Ctrl+V»
        if (current) {
          track("share", { kind: current.kind, channel: "vk" });
          const copied = copyText(textWithoutLink());
          popup(`https://vk.com/share.php?url=${encodeURIComponent(current.url)}&title=${encodeURIComponent(current.linkTitle || "Своё Вино")}&noparse=true`);
          toast(copied ? "Текст скопирован — вставьте его в «Ваше сообщение» во ВКонтакте (Ctrl+V)" : "Скопируйте текст вручную: браузер не дал доступ к буферу");
        }
        break;
      case "share-mask": insertMask(el.dataset.mask); break;
      case "share-adm-reset":
        $("#share-tpl").value = admin.defaultText;
        preview();
        break;
    }
  });

  const textWithoutLink = () => (current.linkInText ? current.text.split(current.url).join("").replace(/[:\s—-]+$/, "").trim() : current.text);

  // ── Админка: текст «Поделиться вином» ────────────────────────────────────
  // Пример для предпросмотра: последнее открытое вино, иначе — вино-образец
  const SAMPLE = {
    title: "Пино Нуар", manufacturer: "А. Гордиенко & М. Николаев", category: "Красное сухое", region: "Кубань",
    grapes: ["Пино Нуар"], url: "https://vino-svoe.ru/wines/a-gordienko-m-nikolaev-pino-nuar-krasnoe-suhoe-135",
  };
  const admin = { defaultText: DEFAULT_TEMPLATE };

  async function mountAdmin(root) {
    root.innerHTML = `<div class="skeleton"></div>`;
    let settings = { wine_template: DEFAULT_TEMPLATE, default_wine_template: DEFAULT_TEMPLATE };
    try {
      const res = await fetch("/v1/settings/share");
      if (res.ok) settings = await res.json();
    } catch (_) { /* покажем шаблон по умолчанию */ }
    admin.defaultText = settings.default_wine_template || DEFAULT_TEMPLATE;
    root.innerHTML = `
      <section class="info-card share-adm">
        <h2 class="admin-form__title">Текст «Поделиться вином»</h2>
        <p class="map-adm__lead">Уходит в Telegram и ВКонтакте вместе со ссылкой на страницу вина. Нажмите на маску — она встанет туда, где курсор. Пустая маска (например, у вина без сорта) убирается вместе с лишней запятой.</p>
        <form class="form" id="share-adm-form" novalidate>
          <div class="share-masks">${MASKS.map(([key, hint]) => `
            <button class="share-mask" type="button" data-action="share-mask" data-mask="${key}" title="${esc(hint)}">{${key}}</button>`).join("")}
          </div>
          <label class="field"><span class="field__label">Шаблон</span>
            <textarea class="field__input field__input--area share-tpl" id="share-tpl" name="wine_template" maxlength="600" rows="4">${esc(settings.wine_template)}</textarea></label>
          <div class="share-adm__preview">
            <span class="field__label">Так увидит получатель — на примере «<span id="share-sample-title"></span>»</span>
            <p class="share-adm__text" id="share-preview-text"></p>
          </div>
          <p class="form__error" role="alert" hidden></p>
          <div class="form__btns form__btns--row">
            <button class="ui-button ui-button_primary ui-button_large" type="submit">Сохранить</button>
            <button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="share-adm-reset">Вернуть исходный</button>
          </div>
        </form>
      </section>`;
    $("#share-tpl").addEventListener("input", preview);
    preview();
  }

  function preview() {
    const wine = W.current?.() || SAMPLE;
    $("#share-sample-title").textContent = wine.title;
    const text = $("#share-tpl").value;
    const out = fill(text, wine);
    $("#share-preview-text").textContent = /\{ссылка\}/i.test(text) ? out : `${out}\n${wine.url || SITE}`;
  }

  function insertMask(key) {
    const area = $("#share-tpl");
    if (!area) return;
    const token = `{${key}}`;
    const [a, b] = [area.selectionStart ?? area.value.length, area.selectionEnd ?? area.value.length];
    area.value = area.value.slice(0, a) + token + area.value.slice(b);
    area.focus();
    area.setSelectionRange(a + token.length, a + token.length);
    preview();
  }

  document.addEventListener("submit", async (e) => {
    if (e.target.id !== "share-adm-form") return;
    e.preventDefault();
    const form = e.target;
    const error = $(".form__error", form);
    error.hidden = true;
    try {
      const res = await fetch("/v1/admin/settings/share", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wine_template: $("#share-tpl").value }),
      });
      if (!res.ok) {
        let detail = "";
        try { detail = (await res.json()).detail; } catch (_) { /* не JSON */ }
        throw new Error(typeof detail === "string" && detail ? detail : `Ошибка сервера (${res.status})`);
      }
      template = { text: (await res.json()).wine_template, at: Date.now() };
      toast("Текст сохранён");
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });

  window.WineShare = { drawWine, drawAchievement, drawMap, mountAdmin, fill };
})();
