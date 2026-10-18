/* Винная карта России: все субъекты, винные регионы выделены, открытые — закрашены.
 *
 * Контуры субъектов — russia-map.json (общий вид) и russia-map-detail.json
 * (подгружается при приближении), tools/region-map/build_map.py.
 * Какие субъекты входят в винный регион — данные, их ведёт админ:
 *   /v1/map/zones      — регионы карты (Кубань → Краснодарский край) и сопоставления;
 *   /v1/regions        — регионы каталога с числом вин (сервис распознавания);
 *   /v1/me/regions     — открытые пользователем (без входа — без прогресса).
 * region вина совпал с названием региона карты — вино на карте; не совпал —
 * регион попадает админу в «Не сопоставлено» (админка → «Регионы карты»).
 */
(() => {
  "use strict";

  const W = window.Wine;
  if (!W || W.DEMO) return;
  const { $, esc, icon, img, show, toast, openSheet, closeSheet, closeMenu, openWine } = W;

  // Событие для аналитики (analytics.js): модуль про неё ничего не знает
  const track = (type, props = {}) => document.dispatchEvent(new CustomEvent("wine:track", { detail: { type, props } }));

  const data = { geo: null, detail: null, regions: null, zones: [], aliases: [], progress: null };
  const derived = { zoneOf: () => null, zones: new Map(), subjectZone: new Map(), unmatched: [], bbox: new Map() };

  // ── Данные ───────────────────────────────────────────────────────────────
  async function getJson(url, init) {
    const res = await fetch(url, init);
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail; } catch (_) { /* не JSON */ }
      throw Object.assign(new Error(typeof detail === "string" && detail ? detail : `HTTP ${res.status}`), { status: res.status });
    }
    return res.status === 204 ? null : res.json();
  }

  async function ensure({ fresh = false } = {}) {
    if (!data.geo) data.geo = await getJson("russia-map.json");
    if (!data.regions || fresh) data.regions = await getJson("/v1/regions");
    if (!data.zonesLoaded || fresh) {
      const out = await getJson("/v1/map/zones");
      data.zones = out.zones;
      data.aliases = out.aliases;
      data.zonesLoaded = true;
    }
    derive();
    return data;
  }

  async function loadProgress() {
    try {
      const out = await getJson("/v1/me/regions");
      data.progress = new Map(out.regions.map((r) => [r.name, r]));
    } catch (_) {
      data.progress = null; // не вошёл или нет сервиса пользователей
    }
  }

  const norm = (s) => String(s ?? "").trim().toLowerCase().replaceAll("ё", "е").replace(/\s*[—–-]\s*/g, "-").replace(/\s+/g, " ");

  // Регионы каталога → регионы карты; что занимает каждый регион; что не сопоставлено
  function derive() {
    const byName = new Map(data.zones.map((z) => [norm(z.name), z.name]));
    const byAlias = new Map(data.aliases.map((a) => [norm(a.catalog_region), a.zone]));
    derived.zoneOf = (raw) => byName.get(norm(raw)) || byAlias.get(norm(raw)) || null;
    derived.zones = new Map(data.zones.map((z) => [z.name, { ...z, catalog: [], wines: 0, manufacturers: 0, grapes: [], image: null }]));
    derived.unmatched = [];
    for (const r of data.regions || []) {
      const zoneName = derived.zoneOf(r.name);
      const zone = zoneName && derived.zones.get(zoneName);
      if (!zone) { derived.unmatched.push(r); continue; }
      zone.catalog.push(r);
      zone.wines += r.wines;
      zone.manufacturers += r.manufacturers;
      zone.grapes = [...new Set([...zone.grapes, ...r.grapes])].slice(0, 4);
      // фото — у региона каталога с тем же названием, иначе у первого
      if (r.image && (!zone.image || norm(r.name) === norm(zone.name))) zone.image = r.image;
    }
    derived.subjectZone = new Map();
    for (const zone of derived.zones.values()) zone.subjects.forEach((id) => derived.subjectZone.set(id, zone.name));
  }

  // Винные регионы на карте — только те, где есть вина
  const wineZones = () => [...derived.zones.values()].filter((z) => z.wines > 0).sort((a, b) => b.wines - a.wines);
  const subjectsById = () => (data.subjectsById ??= new Map(data.geo.subjects.map((s) => [s.id, s])));

  function zoneState(zoneName) {
    const zone = derived.zones.get(zoneName);
    if (!zone || zone.wines === 0) return "plain";
    if (!data.progress) return "neutral";
    return data.progress.has(zoneName) ? "opened" : "closed";
  }
  const openedCount = () => wineZones().filter((z) => zoneState(z.name) === "opened").length;

  // Рамка субъекта — по координатам контура, для «показать регион»
  function bboxOf(ids) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const id of ids) {
      if (!derived.bbox.has(id)) {
        const nums = (subjectsById().get(id)?.d || "").match(/-?\d+(\.\d+)?/g)?.map(Number) || [];
        let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
        for (let i = 0; i + 1 < nums.length; i += 2) {
          a = Math.min(a, nums[i]); c = Math.max(c, nums[i]);
          b = Math.min(b, nums[i + 1]); d = Math.max(d, nums[i + 1]);
        }
        derived.bbox.set(id, [a, b, c, d]);
      }
      const [a, b, c, d] = derived.bbox.get(id);
      x0 = Math.min(x0, a); y0 = Math.min(y0, b); x1 = Math.max(x1, c); y1 = Math.max(y1, d);
    }
    return Number.isFinite(x0) ? [x0, y0, x1, y1] : null;
  }

  // Винные регионы европейской части — для кнопки «К винным регионам» и мини-карт
  function wineWestBox() {
    const ids = wineZones().flatMap((z) => z.subjects).filter((id) => (subjectsById().get(id)?.label[0] ?? 999) < 420);
    return bboxOf(ids.length ? ids : wineZones().flatMap((z) => z.subjects));
  }

  function base() {
    const [, , w, h] = data.geo.viewBox.split(" ").map(Number);
    return { x: 0, y: 0, w, h };
  }

  const baseRatio = () => base().h / base().w;

  // Рамка с запасом в пропорциях окна (ratio = высота / ширина): регион целиком, не слишком крупно
  function fitBox(box, { pad = 0.35, minW = 140, ratio = baseRatio() } = {}) {
    const b = base();
    if (!box) return clamp(b, ratio);
    const [x0, y0, x1, y1] = box;
    let w = Math.max(minW, (x1 - x0) * (1 + 2 * pad));
    w = Math.max(w, (y1 - y0) * (1 + 2 * pad) / ratio);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    return clamp({ x: cx - w / 2, y: cy - w * ratio / 2, w }, ratio);
  }

  // Не дальше границ страны: не мельче всей России и не крупнее ×14. Если окно
  // выше карты (телефон, страна целиком) — страна по центру по вертикали
  function clamp(vb, ratio = baseRatio()) {
    const b = base();
    const w = Math.min(Math.max(vb.w, b.w / 14), b.w);
    const h = w * ratio;
    const y = h >= b.h ? (b.h - h) / 2 : Math.min(Math.max(vb.y, 0), b.h - h);
    return { x: Math.min(Math.max(vb.x, 0), b.w - w), y, w, h };
  }

  // ── SVG ──────────────────────────────────────────────────────────────────
  // mode: view — карта с регионами; pick — админ отмечает субъекты региона; mini — картинка
  function svgHtml({ mode = "view", vb = base(), highlight = null, picked = null, editing = null } = {}) {
    const paths = data.geo.subjects.map((s) => {
      const zoneName = derived.subjectZone.get(s.id);
      let cls;
      if (mode === "pick") {
        cls = picked?.has(s.id) ? "picked" : zoneName && zoneName !== editing ? "taken" : "plain";
      } else if (highlight) {
        cls = zoneName === highlight ? "highlight" : zoneState(zoneName) === "opened" ? "opened" : "plain";
      } else {
        cls = zoneState(zoneName);
      }
      const d = data.detail?.[s.id] || s.d;
      const interactive = mode !== "mini" && (mode === "pick" || cls !== "plain");
      const a11y = interactive
        ? ` tabindex="0" role="button" aria-label="${esc(s.name)}${zoneName ? `, ${esc(zoneName)}` : ""}"` : "";
      return `<path class="wmap__subject wmap__subject--${cls}" data-id="${s.id}" d="${d}"${a11y}/>`;
    }).join("");
    const labels = mode === "mini" ? "" : data.geo.subjects.map((s) => {
      const zoneName = derived.subjectZone.get(s.id);
      const wine = mode === "pick" ? picked?.has(s.id) : zoneState(zoneName) !== "plain";
      return `<text class="wmap__label ${wine ? "wmap__label--wine" : ""}" data-id="${s.id}" x="${s.label[0]}" y="${s.label[1]}">${esc(s.short)}</text>`;
    }).join("");
    return `
      <svg class="wmap wmap--${mode}" viewBox="${vb.x} ${vb.y} ${vb.w} ${vb.h}" ${mode === "mini" ? 'aria-hidden="true"' : 'role="img" aria-label="Карта России"'}>
        <g class="wmap__subjects">${paths}</g><g class="wmap__labels">${labels}</g>
      </svg>`;
  }

  // ── Интерактивная карта: приближение, перемещение, подписи, подсказка ────
  class MapView {
    constructor(root, opts) {
      this.root = root;
      this.opts = opts; // { mode, fit: [x0,y0,x1,y1] | null, fitPad, fitMinW, onPick, onZone, picked, editing }
      this.vb = base();
      this.pointers = new Map();
      this.render();
      // окно карты задаёт пропорции области просмотра (на телефоне — почти квадрат)
      this.ratio = this.measure();
      this.vb = opts.fit ? fitBox(opts.fit, { pad: opts.fitPad ?? 0.1, minW: opts.fitMinW ?? 140, ratio: this.ratio }) : clamp(base(), this.ratio);
      this.apply();
      this.resize = new ResizeObserver(() => {
        const ratio = this.measure();
        if (Math.abs(ratio - this.ratio) < 0.01) return;
        this.ratio = ratio;
        this.vb = clamp({ ...this.vb, y: this.vb.y + (this.vb.h - this.vb.w * ratio) / 2 }, ratio);
        this.apply();
      });
      this.resize.observe($(".wmap-wrap", this.root));
    }

    measure() {
      const r = $(".wmap-wrap", this.root).getBoundingClientRect();
      return r.width ? r.height / r.width : baseRatio();
    }

    fit(box, opts = {}) { return fitBox(box, { ...opts, ratio: this.ratio }); }

    render() {
      this.root.innerHTML = `
        <div class="wmap-wrap">
          ${svgHtml({ mode: this.opts.mode, vb: this.vb, picked: this.opts.picked, editing: this.opts.editing })}
          <div class="wmap-ctrl" role="group" aria-label="Масштаб карты">
            <button type="button" class="wmap-ctrl__btn" data-zoom="in" aria-label="Приблизить">${icon("plus")}</button>
            <button type="button" class="wmap-ctrl__btn" data-zoom="out" aria-label="Отдалить"><svg class="i" aria-hidden="true"><path d="M5 12h14"/></svg></button>
          </div>
          <div class="wmap-presets">
            <button type="button" class="wmap-preset" data-preset="all">Вся Россия</button>
            <button type="button" class="wmap-preset" data-preset="wine">${this.opts.mode === "pick" ? "Юг и центр" : "К винным регионам"}</button>
          </div>
          <div class="wmap-tip" hidden></div>
        </div>`;
      this.svg = $("svg", this.root);
      this.tip = $(".wmap-tip", this.root);
      this.bind();
      this.apply();
    }

    // Подписи — постоянного экранного размера; видны, если субъекту хватает места
    apply() {
      this.svg.setAttribute("viewBox", `${this.vb.x} ${this.vb.y} ${this.vb.w} ${this.vb.h}`);
      cancelAnimationFrame(this.raf);
      this.raf = requestAnimationFrame(() => this.updateLabels());
      if (this.zoom() >= 2 && !data.detail && !data.detailLoading) this.loadDetail();
    }

    zoom() { return base().w / this.vb.w; }

    updateLabels() {
      const width = this.svg.getBoundingClientRect().width || 1;
      const pxPerUnit = width / this.vb.w;
      const font = 12 / pxPerUnit;
      this.svg.style.setProperty("--label-size", `${font}px`);
      for (const text of this.svg.querySelectorAll(".wmap__label")) {
        const s = subjectsById().get(text.dataset.id);
        const room = Math.sqrt(s.area) * pxPerUnit;               // «ширина» субъекта на экране
        const need = s.short.length * 6.6;                        // ширина подписи при 12 px
        const wine = text.classList.contains("wmap__label--wine");
        text.classList.toggle("wmap__label--on", room > need * (wine ? 0.5 : 0.95));
      }
    }

    async loadDetail() {
      data.detailLoading = true;
      try {
        data.detail = (await getJson("russia-map-detail.json")).subjects;
        for (const path of this.svg.querySelectorAll(".wmap__subject")) {
          const d = data.detail[path.dataset.id];
          if (d) path.setAttribute("d", d);
        }
      } catch (_) { /* остаются упрощённые контуры */ }
      data.detailLoading = false;
    }

    // Точка экрана → координаты карты (SVG растянут по ширине, пропорции сохранены)
    toMap(clientX, clientY) {
      const r = this.svg.getBoundingClientRect();
      return { x: this.vb.x + (clientX - r.left) / r.width * this.vb.w, y: this.vb.y + (clientY - r.top) / r.height * this.vb.h };
    }

    zoomAt(factor, clientX, clientY) {
      const r = this.svg.getBoundingClientRect();
      const p = clientX == null ? { x: this.vb.x + this.vb.w / 2, y: this.vb.y + this.vb.h / 2 } : this.toMap(clientX, clientY);
      const fx = clientX == null ? 0.5 : (clientX - r.left) / r.width;
      const fy = clientY == null ? 0.5 : (clientY - r.top) / r.height;
      const w = this.vb.w / factor;
      const h = w * this.ratio;
      this.vb = clamp({ x: p.x - fx * w, y: p.y - fy * h, w, h }, this.ratio);
      this.apply();
    }

    animateTo(target) {
      const from = { ...this.vb };
      const to = clamp(target, this.ratio);
      const start = performance.now();
      const step = (t) => {
        const k = Math.min(1, (t - start) / 350);
        const e = 1 - (1 - k) ** 3;
        this.vb = { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, w: from.w + (to.w - from.w) * e, h: from.h + (to.h - from.h) * e };
        this.apply();
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }

    bind() {
      const svg = this.svg;
      svg.addEventListener("wheel", (e) => {
        e.preventDefault();
        this.zoomAt(Math.exp(-e.deltaY * 0.0022), e.clientX, e.clientY);
      }, { passive: false });
      svg.addEventListener("dblclick", (e) => { e.preventDefault(); this.zoomAt(2, e.clientX, e.clientY); });

      // Указатель захватываем, только когда карту начали тащить: после захвата
      // браузер отдаёт клик самому SVG, и субъект под курсором терялся —
      // не открывалась шторка региона и не отмечался субъект в редакторе
      svg.addEventListener("pointerdown", (e) => {
        this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        this.downPath = e.target.closest?.(".wmap__subject") || null;
        this.moved = 0;
        if (this.pointers.size === 2) this.pinch = this.pinchState();
      });
      svg.addEventListener("pointermove", (e) => {
        const prev = this.pointers.get(e.pointerId);
        if (!prev) { this.hover(e); return; }
        const r = svg.getBoundingClientRect();
        if (this.pointers.size === 2 && this.pinch) {
          this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
          const now = this.pinchState();
          this.zoomAt(now.dist / this.pinch.dist, now.cx, now.cy);
          this.pinch = now;
          this.moved += 10;
          return;
        }
        const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
        this.moved += Math.abs(dx) + Math.abs(dy);
        this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (this.moved > 4) {
          if (!svg.hasPointerCapture(e.pointerId)) svg.setPointerCapture(e.pointerId);
          svg.classList.add("wmap--dragging");
          this.tip.hidden = true;
          this.vb = clamp({ ...this.vb, x: this.vb.x - dx / r.width * this.vb.w, y: this.vb.y - dy / r.height * this.vb.h }, this.ratio);
          this.apply();
        }
      });
      const up = (e) => {
        this.pointers.delete(e.pointerId);
        if (this.pointers.size < 2) this.pinch = null;
        svg.classList.remove("wmap--dragging");
      };
      svg.addEventListener("pointerup", up);
      svg.addEventListener("pointercancel", up);
      svg.addEventListener("pointerleave", () => { if (!this.pointers.size) this.tip.hidden = true; });

      // Клик — только если карту не тащили
      svg.addEventListener("click", (e) => {
        if (this.moved > 4) return;
        const path = e.target.closest(".wmap__subject") || this.downPath;
        if (path) this.activate(path, e);
      });
      svg.addEventListener("keydown", (e) => {
        const path = e.target.closest?.(".wmap__subject");
        if (path && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); this.activate(path); }
      });

      this.root.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => {
        const target = { ...this.vb };
        const factor = b.dataset.zoom === "in" ? 1.8 : 1 / 1.8;
        target.w = this.vb.w / factor;
        target.h = target.w * this.ratio;
        target.x = this.vb.x + (this.vb.w - target.w) / 2;
        target.y = this.vb.y + (this.vb.h - target.h) / 2;
        this.animateTo(target);
      }));
      this.root.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
        this.animateTo(b.dataset.preset === "all" ? clamp(base(), this.ratio) : this.fit(wineWestBox(), { pad: 0.08 }));
      }));
    }

    pinchState() {
      const [a, b] = [...this.pointers.values()];
      return { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
    }

    hover(e) {
      const path = e.target.closest?.(".wmap__subject");
      if (!path || e.pointerType === "touch") { this.tip.hidden = true; return; }
      this.showTip(path, e.clientX, e.clientY);
    }

    showTip(path, clientX, clientY) {
      const s = subjectsById().get(path.dataset.id);
      const zoneName = derived.subjectZone.get(s.id);
      const zone = zoneName && derived.zones.get(zoneName);
      let html = `<b>${esc(s.name)}</b>`;
      if (this.opts.mode === "pick") {
        html += zoneName && zoneName !== this.opts.editing ? `<span>в регионе «${esc(zoneName)}»</span>`
          : this.opts.picked?.has(s.id) ? `<span>отмечен</span>` : "";
      } else if (zone && zone.wines > 0) {
        const st = zoneState(zoneName);
        html += `<span class="wmap-tip__zone">${esc(zoneName)} · ${zone.wines} ${plural(zone.wines, "вино", "вина", "вин")}</span>`
          + (st === "opened" ? `<span class="wmap-tip__ok">${icon("check")}открыт вами</span>` : st === "closed" ? `<span>ещё не открыт</span>` : "");
      }
      this.tip.innerHTML = html;
      this.tip.hidden = false;
      const wrap = this.root.querySelector(".wmap-wrap").getBoundingClientRect();
      const x = Math.min(clientX - wrap.left + 14, wrap.width - this.tip.offsetWidth - 8);
      const y = Math.max(8, clientY - wrap.top - this.tip.offsetHeight - 12);
      this.tip.style.transform = `translate(${Math.max(8, x)}px, ${y}px)`;
    }

    activate(path, e) {
      const id = path.dataset.id;
      if (this.opts.mode === "pick") {
        const zoneName = derived.subjectZone.get(id);
        if (zoneName && zoneName !== this.opts.editing) {
          toast(`Субъект уже в регионе «${zoneName}» — сначала уберите его оттуда`);
          return;
        }
        this.opts.onPick?.(id);
        const on = this.opts.picked.has(id);
        path.setAttribute("class", `wmap__subject wmap__subject--${on ? "picked" : "plain"}`);
        this.svg.querySelector(`.wmap__label[data-id="${id}"]`)?.classList.toggle("wmap__label--wine", on);
        this.updateLabels();
        return;
      }
      const zoneName = derived.subjectZone.get(id);
      if (zoneName && zoneState(zoneName) !== "plain") {
        this.opts.onZone?.(zoneName);
      } else if (e) {
        this.showTip(path, e.clientX, e.clientY); // на телефоне — название по тапу
        clearTimeout(this.tipTimer);
        this.tipTimer = setTimeout(() => { this.tip.hidden = true; }, 2200);
      }
    }
  }

  // ── Экран карты ──────────────────────────────────────────────────────────
  let view = null;

  async function open({ push = true } = {}) {
    closeMenu();
    show("map", { push });
    track("map_open");
    const screen = $("#screen-map");
    screen.innerHTML = `<div class="skeleton"></div>`;
    try {
      await ensure({ fresh: true });
      await loadProgress();
      render();
    } catch (err) {
      screen.innerHTML = `<p class="muted">Карта сейчас недоступна: ${esc(err.message)}</p>`;
    }
  }

  function render() {
    const zones = wineZones();
    const n = zones.length;
    const opened = openedCount();
    const next = data.progress ? zones.find((z) => zoneState(z.name) === "closed") : null;
    const isAdmin = window.WineAccount?.isAdmin?.();
    const progress = data.progress
      ? `
        <section class="map-progress">
          <div class="map-progress__row">
            <p class="map-progress__title">Открыто <b>${opened}</b> из ${n} ${plural(n, "региона", "регионов", "регионов")}</p>
            <span class="map-progress__pct">${Math.round((opened / Math.max(1, n)) * 100)}%</span>
          </div>
          <span class="achv__bar map-progress__bar"><i style="width:${(opened / Math.max(1, n)) * 100}%"></i></span>
          ${window.WineShare && opened ? `<button class="ui-button ui-button_elevated ui-button_round map-progress__share" type="button" data-action="share-map">${icon("share")}Поделиться картой</button>` : ""}
          <p class="map-progress__hint">${next
            ? `Следующий шаг — <button class="link-btn" type="button" data-action="map-zone" data-zone="${esc(next.name)}">${esc(next.name)}</button>: ${next.wines} ${plural(next.wines, "вино", "вина", "вин")} в каталоге`
            : n ? "Вы открыли все винные регионы России — браво!" : ""}</p>
        </section>`
      : `
        <section class="nudge map-nudge">
          <span class="round-icon">${icon("pin")}</span>
          <div class="nudge__text">
            <p class="nudge__title">Открывайте регионы</p>
            <p class="nudge__lead">Войдите — и каждое отсканированное вино откроет свой регион на карте, а за путешествия по винной России вы получите достижения</p>
            <div class="nudge__btns">
              <button class="ui-button ui-button_primary ui-button_round" type="button" data-action="acc-auth" data-mode="register">Зарегистрироваться</button>
              <button class="ui-button ui-button_elevated ui-button_round" type="button" data-action="acc-auth" data-mode="login">Войти</button>
            </div>
          </div>
        </section>`;

    $("#screen-map").innerHTML = `
      <button class="back-link" type="button" data-action="home">${icon("arrow-left")}К распознаванию вин</button>
      <div class="title-block">
        <h1 class="title-block__title">Винная карта России</h1>
        <p class="title-block__lead">${n} винных ${plural(n, "регион", "региона", "регионов")} каталога «Своё Вино». Сканируйте вина — регионы будут открываться на карте</p>
      </div>
      ${progress}
      ${isAdmin && derived.unmatched.length ? `
        <button class="map-admin-note" type="button" data-action="acc-admin-map">
          ${icon("pin")}<span>Не сопоставлено с картой: ${derived.unmatched.length} ${plural(derived.unmatched.length, "регион", "региона", "регионов")} каталога — сопоставить</span>${icon("chevron")}
        </button>` : ""}
      <section class="map-card">
        <div class="map-card__bar">
          <p class="map-card__hint">Колесо или щипок — масштаб, перетаскивание — перемещение</p>
          <ul class="map-legend" aria-label="Обозначения">
            ${data.progress
              ? `<li><i class="map-legend__dot map-legend__dot--opened"></i>открыт</li><li><i class="map-legend__dot map-legend__dot--closed"></i>ещё нет</li>`
              : `<li><i class="map-legend__dot map-legend__dot--neutral"></i>винный регион</li>`}
          </ul>
        </div>
        <div id="map-view"></div>
      </section>

      <section class="section" aria-labelledby="regions-title">
        <h2 class="section__title" id="regions-title">Регионы</h2>
        <div class="region-list">${zones.map(regionCardHtml).join("")}</div>
      </section>`;
    view = new MapView($("#map-view"), {
      mode: "view",
      fit: wineWestBox(), fitPad: 0.08,
      onZone: (name) => openZone(name),
    });
  }

  function regionCardHtml(z) {
    const state = zoneState(z.name);
    const opened = data.progress?.get(z.name);
    return `
      <button class="region-card region-card--${state}" type="button" data-action="map-zone" data-zone="${esc(z.name)}">
        <span class="region-card__img">${z.image ? img(z.image, "", "") : icon("pin")}</span>
        <span class="region-card__text">
          <span class="region-card__name">${esc(z.name)}</span>
          <span class="region-card__meta">${z.wines} ${plural(z.wines, "вино", "вина", "вин")} · ${z.manufacturers} ${plural(z.manufacturers, "винодельня", "винодельни", "виноделен")}</span>
          ${state === "opened" ? `<span class="region-card__status">${icon("check")}Попробовано ${opened.wines.length} ${plural(opened.wines.length, "вино", "вина", "вин")}</span>` : ""}
          ${state === "closed" ? `<span class="region-card__status region-card__status--closed">${icon("lock")}Ещё не открыт</span>` : ""}
        </span>
        ${icon("chevron")}
      </button>`;
  }

  // ── Шторка региона ───────────────────────────────────────────────────────
  async function openZone(name) {
    await ensure().catch(() => null);
    const z = derived.zones.get(name);
    if (!z) return;
    track("region_open", { region: name });
    if (view && $("#screen-map").classList.contains("screen--active")) view.animateTo(view.fit(bboxOf(z.subjects)));
    const state = zoneState(name);
    const opened = data.progress?.get(name);
    const subjects = z.subjects.map((id) => subjectsById().get(id)?.name).filter(Boolean);
    openSheet(name, `
      ${z.image ? `<div class="region-hero">${img(z.image, "", name)}
        ${state === "opened" ? `<span class="region-hero__chip">${icon("check")}Открыт ${esc(fmtDate(opened.opened_at))}</span>` : ""}</div>` : ""}
      <p class="region-subjects">${icon("pin")}${subjects.map(esc).join(", ")}</p>
      <div class="region-facts">
        <div><b>${z.wines}</b><span>${plural(z.wines, "вино", "вина", "вин")} в каталоге</span></div>
        <div><b>${z.manufacturers}</b><span>${plural(z.manufacturers, "винодельня", "винодельни", "виноделен")}</span></div>
      </div>
      ${z.grapes.length ? `<p class="region-grapes"><span>Главные сорта:</span> ${z.grapes.map(esc).join(", ")}</p>` : ""}
      ${state === "closed" ? `<p class="region-cta">${icon("camera")}Отсканируйте вино из этого региона — он откроется на карте</p>` : ""}
      ${opened?.wines.length ? `<h3 class="region-sub">Вы попробовали</h3>${opened.wines.map(wineRowHtml).join("")}` : ""}
      <h3 class="region-sub">${state === "opened" ? "Попробуйте ещё" : "Что попробовать"}</h3>
      <div id="region-wines"><div class="skeleton"></div></div>`);
    $("#sheet").classList.add("region-sheet");
    try {
      const tried = new Set((opened?.wines || []).map((w) => w.slug));
      const lists = await Promise.all(z.catalog.map((r) => getJson(`/v1/regions/${encodeURIComponent(r.name)}/wines?limit=8`)));
      const wines = lists.flat().filter((w) => !tried.has(w.slug)).slice(0, 6);
      if ($("#region-wines")) {
        $("#region-wines").innerHTML = wines.length ? wines.map(wineRowHtml).join("") : `<p class="muted">Других вин региона в каталоге нет</p>`;
      }
    } catch (_) {
      if ($("#region-wines")) $("#region-wines").innerHTML = `<p class="muted">Не удалось загрузить вина региона</p>`;
    }
  }

  function wineRowHtml(w) {
    const meta = [w.manufacturer, w.category].filter(Boolean).join(" · ");
    return `
      <button class="alt-row" type="button" data-action="map-wine" data-slug="${esc(w.slug)}">
        <span class="alt-row__img">${img(w.image_url, "", w.title)}</span>
        <span><span class="alt-row__title">${esc(w.title)}</span><span class="alt-row__meta">${esc(meta)}</span></span>
        ${icon("chevron")}
      </button>`;
  }

  // ── Для кабинета: мини-карта в профиле, поздравление, вход с главной ────
  async function previewHtml() {
    await ensure();
    await loadProgress();
    const n = wineZones().length;
    const opened = openedCount();
    return `
      <button class="map-preview" type="button" data-action="map-open" aria-label="Открыть винную карту">
        <span class="map-preview__map">${svgHtml({ mode: "mini", vb: fitBox(wineWestBox(), { pad: 0.08 }) })}</span>
        <span class="map-preview__text">
          <span class="map-preview__title">Открыто ${opened} из ${n}</span>
          <span class="map-preview__lead">${opened ? "Каждое вино из нового региона открывает его на карте" : "Отсканируйте вино — его регион откроется на карте"}</span>
          <span class="map-preview__link">Открыть карту ${icon("chevron")}</span>
        </span>
      </button>`;
  }

  async function popHtml(name) {
    await ensure({ fresh: true });
    await loadProgress();
    const z = derived.zones.get(name);
    if (!z) throw new Error("регион не на карте"); // не сопоставлен — поздравлять нечем
    return `
      <p class="achv-pop__kicker">Новый регион на карте</p>
      <span class="achv-pop__map">${svgHtml({ mode: "mini", vb: fitBox(bboxOf(z.subjects), { pad: 1.2, minW: 220 }), highlight: name })}</span>
      <h2 class="achv-pop__title">${esc(name)}</h2>
      <p class="achv-pop__desc">Открыто ${openedCount()} из ${wineZones().length} винных регионов России</p>`;
  }

  // Для «Поделиться картой»: SVG с цветами в атрибутах (CSS страницы в картинку не попадает)
  async function shareData() {
    await ensure();
    await loadProgress();
    if (!data.progress) return null;
    const fill = { plain: "#EAE0CB", neutral: "#C27B80", closed: "#D9BC86", opened: "#8F3D42" };
    const vb = fitBox(wineWestBox(), { pad: 0.06 });
    const w = 1000, h = Math.round(w * vb.h / vb.w);
    const paths = data.geo.subjects.map((s) => {
      const state = zoneState(derived.subjectZone.get(s.id));
      return `<path d="${data.detail?.[s.id] || s.d}" fill="${fill[state]}" stroke="#FFFFFF" stroke-width="${(0.9 * vb.w / w).toFixed(2)}"/>`;
    }).join("");
    const names = wineZones().filter((z) => zoneState(z.name) === "opened").map((z) => z.name);
    return {
      opened: names.length, total: wineZones().length, names,
      svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="${vb.x} ${vb.y} ${vb.w} ${vb.h}">${paths}</svg>`,
    };
  }

  async function mountHomeEntry() {
    try { await ensure(); } catch (_) { return; } // нет /v1/regions или /v1/map — старый сервис
    const anchor = $("#home-dishes");
    if (!anchor || $("#home-map")) return;
    const n = wineZones().length;
    anchor.insertAdjacentHTML("beforebegin", `
      <button class="home-map" id="home-map" type="button" data-action="map-open">
        <span class="home-map__map">${svgHtml({ mode: "mini" })}</span>
        <span class="home-map__text">
          <span class="home-map__title">Винная карта России</span>
          <span class="home-map__lead">${n} ${plural(n, "регион", "региона", "регионов")} — открывайте их, сканируя вина</span>
        </span>
        ${icon("chevron")}
      </button>`);
  }

  // ── Админка: сопоставление регионов каталога с картой ────────────────────
  const adm = { root: null, editor: null };

  async function mountAdmin(root) {
    adm.root = root;
    root.innerHTML = `<div class="skeleton"></div>`;
    try {
      await ensure({ fresh: true });
      renderAdmin();
    } catch (err) {
      root.innerHTML = `<p class="muted">${esc(err.message)}</p>`;
    }
  }

  function renderAdmin() {
    const zones = [...derived.zones.values()].sort((a, b) => a.name.localeCompare(b.name, "ru"));
    const options = zones.map((z) => `<option value="${z.id}">${esc(z.name)}</option>`).join("");
    adm.root.innerHTML = `
      <section class="info-card map-adm">
        <div class="section__head">
          <h2 class="admin-form__title">Не сопоставлено</h2>
          <span class="section__count">${derived.unmatched.length}</span>
        </div>
        <p class="map-adm__lead">Регион вина из каталога не совпал с названием ни одного региона карты. Отнесите его к существующему региону или создайте новый, отметив субъекты на карте.</p>
        ${derived.unmatched.length ? derived.unmatched.map((r) => `
          <div class="map-adm__row">
            <span class="map-adm__name"><b>${esc(r.name)}</b><span>${r.wines} ${plural(r.wines, "вино", "вина", "вин")}</span></span>
            <span class="map-adm__btns">
              <select class="field__input map-adm__select" data-region="${esc(r.name)}" aria-label="Регион карты для «${esc(r.name)}»">
                <option value="">Отнести к региону…</option>${options}
              </select>
              <button class="ui-button ui-button_elevated ui-button_round" type="button" data-action="map-adm-new" data-region="${esc(r.name)}">${icon("plus")}Новый регион</button>
            </span>
          </div>`).join("") : `<p class="map-adm__ok">${icon("check")}Все регионы каталога на карте</p>`}
      </section>
      <div id="map-adm-editor"></div>
      <section class="section">
        <div class="section__head">
          <h2 class="section__title">Регионы карты</h2>
          <button class="ui-button ui-button_elevated ui-button_round" type="button" data-action="map-adm-new">${icon("plus")}Создать</button>
        </div>
        <div class="admin-list">${zones.map((z) => {
          const aliases = data.aliases.filter((a) => a.zone_id === z.id);
          return `
            <div class="admin-row ${z.wines ? "" : "admin-row--off"}">
              <span class="admin-row__text">
                <span class="admin-row__title">${esc(z.name)}${z.wines ? "" : ` <span class="tag">нет вин</span>`}</span>
                <span class="admin-row__meta">${z.subjects.map((id) => esc(subjectsById().get(id)?.name || id)).join(", ")}</span>
                <span class="admin-row__rule">${z.wines} ${plural(z.wines, "вино", "вина", "вин")}${aliases.length ? ` · сопоставлено: ${aliases.map((a) => `
                  <button class="map-adm__alias" type="button" data-action="map-adm-unalias" data-region="${esc(a.catalog_region)}" title="Убрать сопоставление">${esc(a.catalog_region)} ×</button>`).join("")}` : ""}</span>
              </span>
              <span class="admin-row__btns">
                <button class="ui-button ui-button_transparent" type="button" data-action="map-adm-edit" data-id="${z.id}" aria-label="Изменить">${icon("pencil")}</button>
                <button class="ui-button ui-button_transparent" type="button" data-action="map-adm-delete" data-id="${z.id}" aria-label="Удалить">${icon("trash")}</button>
              </span>
            </div>`;
        }).join("")}</div>
      </section>`;
  }

  function openEditor({ zone = null, name = "" } = {}) {
    const picked = new Set(zone?.subjects || []);
    adm.editor = { zone, picked };
    const box = $("#map-adm-editor");
    box.innerHTML = `
      <section class="info-card map-adm map-adm--editor">
        <h2 class="admin-form__title">${zone ? `Регион «${esc(zone.name)}»` : "Новый регион карты"}</h2>
        <form class="form" id="map-adm-form" novalidate>
          <label class="field"><span class="field__label">Название — как в поле «регион» у вин</span>
            <input class="field__input" name="name" maxlength="120" required value="${esc(zone?.name || name)}"></label>
          <p class="field__label">Отметьте субъекты кликом по карте. Уже занятые другими регионами — бежевые</p>
          <div id="map-adm-map"></div>
          <p class="map-adm__picked" id="map-adm-picked"></p>
          <p class="form__error" role="alert" hidden></p>
          <div class="form__btns form__btns--row">
            <button class="ui-button ui-button_primary ui-button_large" type="submit">Сохранить</button>
            <button class="ui-button ui-button_elevated ui-button_large" type="button" data-action="map-adm-cancel">Отмена</button>
          </div>
        </form>
      </section>`;
    const syncPicked = () => {
      $("#map-adm-picked").innerHTML = picked.size
        ? [...picked].map((id) => `<span class="chip">${esc(subjectsById().get(id)?.name || id)}</span>`).join("")
        : `<span class="muted">Пока ничего не отмечено</span>`;
    };
    new MapView($("#map-adm-map"), {
      mode: "pick", picked, editing: zone?.name || null,
      fit: picked.size ? bboxOf([...picked]) : wineWestBox(), fitPad: picked.size ? 0.8 : 0.1, fitMinW: picked.size ? 260 : 140,
      onPick: (id) => { picked.has(id) ? picked.delete(id) : picked.add(id); syncPicked(); },
    });
    syncPicked();
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function saveEditor(form) {
    const { zone, picked } = adm.editor;
    const name = new FormData(form).get("name").trim();
    const error = $(".form__error", form);
    const fail = (msg) => { error.textContent = msg; error.hidden = false; };
    if (!name) return fail("Укажите название");
    if (!picked.size) return fail("Отметьте на карте хотя бы один субъект");
    try {
      await adminCall(zone ? "PATCH" : "POST", zone ? `/v1/admin/map/zones/${zone.id}` : "/v1/admin/map/zones",
        { name, subjects: [...picked] });
      adm.editor = null;
      toast(zone ? "Регион сохранён" : `Регион «${name}» на карте`);
      await afterAdminChange();
    } catch (err) {
      fail(err.message);
    }
  }

  async function adminCall(method, url, body) {
    return getJson(url, body === undefined ? { method } : {
      method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  }

  async function afterAdminChange() {
    data.zonesLoaded = false;
    await ensure({ fresh: true });
    renderAdmin();
  }

  // ── Утилиты ──────────────────────────────────────────────────────────────
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }
  const DATE = new Intl.DateTimeFormat("ru", { day: "numeric", month: "long" });
  const fmtDate = (iso) => (iso ? DATE.format(new Date(iso)) : "");

  // ── Обработчики ──────────────────────────────────────────────────────────
  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-action^='map-']");
    if (!el) return;
    const zoneById = (id) => [...derived.zones.values()].find((z) => String(z.id) === String(id));
    switch (el.dataset.action) {
      case "map-open":
        closeSheet();
        open();
        break;
      case "map-zone":
        openZone(el.dataset.zone);
        break;
      case "map-wine":
        openWine(el.dataset.slug, { backTo: "map" });
        break;
      case "map-adm-new":
        openEditor({ name: el.dataset.region || "" });
        break;
      case "map-adm-edit":
        openEditor({ zone: zoneById(el.dataset.id) });
        break;
      case "map-adm-cancel":
        adm.editor = null;
        $("#map-adm-editor").innerHTML = "";
        break;
      case "map-adm-delete": {
        const z = zoneById(el.dataset.id);
        if (!z || !confirm(`Убрать регион «${z.name}» с карты? Его вина попадут в «Не сопоставлено».`)) return;
        try { await adminCall("DELETE", `/v1/admin/map/zones/${z.id}`); await afterAdminChange(); } catch (err) { toast(err.message); }
        break;
      }
      case "map-adm-unalias":
        try {
          await adminCall("DELETE", `/v1/admin/map/aliases?catalog_region=${encodeURIComponent(el.dataset.region)}`);
          await afterAdminChange();
        } catch (err) { toast(err.message); }
        break;
    }
  });

  document.addEventListener("change", async (e) => {
    const select = e.target.closest(".map-adm__select");
    if (!select || !select.value) return;
    try {
      await adminCall("PUT", "/v1/admin/map/aliases", { catalog_region: select.dataset.region, zone_id: Number(select.value) });
      toast(`«${select.dataset.region}» — теперь на карте`);
      await afterAdminChange();
    } catch (err) { toast(err.message); }
  });

  document.addEventListener("submit", (e) => {
    if (e.target.id !== "map-adm-form") return;
    e.preventDefault();
    saveEditor(e.target);
  });

  document.addEventListener("wine:route", (e) => {
    if (e.detail.screen === "map") open({ push: false });
  });

  window.WineMap = { open, ensure, previewHtml, popHtml, mountAdmin, shareData };

  mountHomeEntry();
  if (location.hash === "#map") open({ push: false });
})();
