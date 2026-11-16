/*
 * Gym Tracker panel for Home Assistant.
 *
 * Plain web component without build step or external dependencies.
 * All calculations happen in the backend, this file renders screens and charts.
 */

const DOMAIN = "gym_tracker";
const EXPORT_PATH = `/api/${DOMAIN}/export.csv`;
const LOCALE = "de-DE";

const RANGES = [
  ["4w", "4 Wochen"],
  ["3m", "3 Monate"],
  ["6m", "6 Monate"],
  ["1y", "1 Jahr"],
  ["all", "Alles"],
];

const RPE_TEXT = {
  1: "Sehr leicht – kaum spürbar",
  2: "Sehr leicht",
  3: "Leicht",
  4: "Leicht – noch sehr viele Wdh. möglich",
  5: "Mittel – viele Wdh. wären noch möglich",
  6: "Etwas anstrengend – ca. 4 Wdh. in Reserve",
  7: "Anstrengend – ca. 3 Wdh. in Reserve",
  8: "Schwer – 2 Wdh. in Reserve",
  9: "Sehr schwer – 1 Wdh. in Reserve",
  10: "Maximal – keine Wdh. mehr möglich",
};

const WEIGHT_STEPS = [0.5, 1, 2.5];
const RPE_TICKS = [2, 4, 6, 8, 10];

/* ---------------------------------------------------------------- helpers */

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function num(value, digits = 0, minDigits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return new Intl.NumberFormat(LOCALE, { minimumFractionDigits: minDigits, maximumFractionDigits: digits }).format(value);
}

const kg = (value) => (value === null || value === undefined ? "–" : `${num(value, 2)} kg`);

function day(iso) {
  return new Date(`${iso}T12:00:00`);
}

function fmtDate(iso, opts = { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" }) {
  return iso ? new Intl.DateTimeFormat(LOCALE, opts).format(day(iso)) : "–";
}

const shortDate = (iso) => fmtDate(iso, { day: "2-digit", month: "2-digit" });

function fmtTime(isoDateTime) {
  return new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit" }).format(new Date(isoDateTime));
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} Min.`;
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")} Std.`;
}

function relDay(iso, todayIso) {
  if (!iso) return "noch keins";
  const diff = Math.round((day(todayIso) - day(iso)) / 86400000);
  if (diff <= 0) return "heute";
  if (diff === 1) return "gestern";
  return `vor ${diff} Tagen`;
}

function targetText(ex) {
  if (ex.duration_s !== null) return `${ex.target_sets} × ${ex.duration_s} Sek.`;
  const reps = ex.reps_min === ex.reps_max ? `${ex.reps_max}` : `${ex.reps_min}–${ex.reps_max}`;
  return `${ex.target_sets} × ${reps}`;
}

function entryText(entry, withRpe = true) {
  if (!entry) return "";
  if (entry.skipped) return "Übersprungen";
  let text = entry.seconds !== null ? `${entry.set_count} × ${entry.seconds} Sek.` : `${entry.set_count} × ${entry.reps}`;
  if (entry.weight !== null) text += ` @ ${kg(entry.weight)}`;
  if (withRpe && entry.rpe !== null) text += ` · RPE ${entry.rpe}`;
  return text;
}

/* ----------------------------------------------------------------- charts */

function niceScale(lo, hi, ticks = 4) {
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const raw = (hi - lo) / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  return { min: Math.floor(lo / step) * step, max: Math.ceil(hi / step) * step, step };
}

function axisDigits(step) {
  return step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
}

const PAD = { l: 44, r: 14, t: 20, b: 26 };

function chartFrame(width, height, scale, unit, ticks = null) {
  const w = width - PAD.l - PAD.r;
  const h = height - PAD.t - PAD.b;
  const y = (v) => PAD.t + h - ((v - scale.min) / (scale.max - scale.min)) * h;
  let svg = `<svg width="${width}" height="${height}" role="img">`;
  const digits = axisDigits(scale.step);
  if (!ticks) {
    ticks = [];
    for (let v = scale.min; v <= scale.max + scale.step / 1000; v += scale.step) ticks.push(v);
  }
  for (const v of ticks) {
    svg += `<line class="grid" x1="${PAD.l}" x2="${PAD.l + w}" y1="${y(v)}" y2="${y(v)}"></line>
      <text class="axis" x="${PAD.l - 6}" y="${y(v) + 4}" text-anchor="end">${num(v, digits)}</text>`;
  }
  if (unit) svg += `<text class="axis" x="${PAD.l - 6}" y="10" text-anchor="end">${esc(unit)}</text>`;
  return { svg, w, h, y };
}

const STAR = (x, y) => `<text class="star" x="${x}" y="${y}" text-anchor="middle">★</text>`;

/* Line chart. def: {points:[{t, v, mark, tip}], min, max, unit} – one series, time on x. */
function lineChart(def, width, height = 190) {
  const pts = def.points;
  const values = pts.map((p) => p.v);
  const scale = niceScale(def.min ?? Math.min(...values), def.max ?? Math.max(...values));
  if (def.min !== undefined) scale.min = def.min;
  if (def.max !== undefined) scale.max = def.max;
  if (scale.min < 0 && Math.min(...values) >= 0) scale.min = 0;
  const frame = chartFrame(width, height, scale, def.unit, def.ticks);
  const t0 = pts[0].t;
  const t1 = pts[pts.length - 1].t;
  const x = (t) => (t1 === t0 ? PAD.l + frame.w / 2 : PAD.l + ((t - t0) / (t1 - t0)) * frame.w);
  const xs = pts.map((p) => x(p.t));
  let svg = frame.svg;
  const labels = pts.length > 1 ? [0, pts.length - 1] : [0];
  for (const i of labels) {
    const anchor = pts.length === 1 ? "middle" : i === 0 ? "start" : "end";
    svg += `<text class="axis" x="${xs[i]}" y="${height - 6}" text-anchor="${anchor}">${esc(pts[i].label)}</text>`;
  }
  svg += `<line class="cross" y1="${PAD.t}" y2="${PAD.t + frame.h}" visibility="hidden"></line>`;
  if (pts.length > 1) {
    svg += `<path class="line" d="${pts.map((p, i) => `${i ? "L" : "M"}${xs[i].toFixed(1)},${frame.y(p.v).toFixed(1)}`).join("")}"></path>`;
  }
  pts.forEach((p, i) => {
    const cy = frame.y(p.v);
    if (p.mark) svg += `<circle class="ring" cx="${xs[i]}" cy="${cy}" r="8"></circle>${STAR(xs[i], cy - 11)}`;
    svg += `<circle class="dot" cx="${xs[i]}" cy="${cy}" r="4"></circle>`;
  });
  return { svg: `${svg}</svg>`, xs };
}

/* Bar chart. def: {bars:[{label, v, mark, tip}], refs:[{v, label}], unit} */
function barChart(def, width, height = 190) {
  const bars = def.bars;
  const refs = def.refs || [];
  const top = Math.max(1, ...bars.map((b) => b.v), ...refs.map((r) => r.v));
  const scale = niceScale(0, top, Math.min(4, def.integer ? Math.max(1, Math.ceil(top)) : 4));
  scale.min = 0;
  if (def.integer) scale.step = Math.max(1, Math.round(scale.step));
  if (def.integer) scale.max = Math.ceil(top / scale.step) * scale.step;
  const frame = chartFrame(width, height, scale, def.unit);
  const slot = frame.w / bars.length;
  const bw = Math.max(2, Math.min(slot - 2, 28));
  const xs = bars.map((_, i) => PAD.l + slot * i + slot / 2);
  const every = Math.max(1, Math.ceil(48 / slot));
  let svg = frame.svg;
  svg += `<line class="cross" y1="${PAD.t}" y2="${PAD.t + frame.h}" visibility="hidden"></line>`;
  const base = frame.y(0);
  bars.forEach((b, i) => {
    if ((bars.length - 1 - i) % every === 0) {
      svg += `<text class="axis" x="${xs[i]}" y="${height - 6}" text-anchor="middle">${esc(b.label)}</text>`;
    }
    if (b.v <= 0) return;
    const yTop = frame.y(b.v);
    const r = Math.min(4, bw / 2, base - yTop);
    const x0 = xs[i] - bw / 2;
    svg += `<path class="bar" d="M${x0},${base}V${yTop + r}Q${x0},${yTop} ${x0 + r},${yTop}H${x0 + bw - r}Q${x0 + bw},${yTop} ${x0 + bw},${yTop + r}V${base}Z"></path>`;
    if (b.mark) svg += STAR(xs[i], yTop - 4);
  });
  for (const ref of refs) {
    svg += `<line class="ref" x1="${PAD.l}" x2="${PAD.l + frame.w}" y1="${frame.y(ref.v)}" y2="${frame.y(ref.v)}"></line>`;
  }
  svg += `<line class="baseline" x1="${PAD.l}" x2="${PAD.l + frame.w}" y1="${base}" y2="${base}"></line>`;
  return { svg: `${svg}</svg>`, xs };
}

/* ------------------------------------------------------------------ style */

const STYLE = `
:host {
  --gt-bg: var(--primary-background-color, #fafafa);
  --gt-surface: var(--card-background-color, #fff);
  --gt-text: var(--primary-text-color, #212121);
  --gt-muted: var(--secondary-text-color, #727272);
  --gt-primary: var(--primary-color, #03a9f4);
  --gt-on-primary: var(--text-primary-color, #fff);
  --gt-divider: var(--divider-color, rgba(127, 127, 127, 0.3));
  --gt-error: var(--error-color, #db4437);
  --gt-success: var(--success-color, #43a047);
  --gt-accent: var(--accent-color, #ff9800);
  display: block; min-height: 100vh; background: var(--gt-bg); color: var(--gt-text);
  font-family: var(--paper-font-body1_-_font-family, Roboto, "Noto Sans", sans-serif);
  font-size: 16px; line-height: 1.4; -webkit-tap-highlight-color: transparent;
}
* { box-sizing: border-box; }
.header { position: sticky; top: 0; z-index: 5; background: var(--app-header-background-color, var(--gt-primary));
  color: var(--app-header-text-color, #fff); padding-top: env(safe-area-inset-top); }
.toolbar { display: flex; align-items: center; height: 56px; padding: 0 4px; }
.main-title { font-size: 20px; margin-left: 8px; flex: 1; }
.tabs { display: flex; }
.tab { flex: 1; background: none; border: 0; border-bottom: 3px solid transparent; color: inherit; opacity: 0.75;
  font: inherit; font-size: 14px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.04em;
  padding: 12px 4px; cursor: pointer; }
.tab.active { opacity: 1; border-bottom-color: currentColor; }
.content { max-width: 720px; margin: 0 auto; padding: 16px 16px calc(32px + env(safe-area-inset-bottom)); }
.content.wide { max-width: 1200px; }
.card { background: var(--gt-surface); border-radius: var(--ha-card-border-radius, 12px);
  border: 1px solid var(--gt-divider); padding: 16px; margin-bottom: 12px; }
.card h3 { margin: 0 0 8px; font-size: 13px; font-weight: 500; color: var(--gt-muted); text-transform: uppercase;
  letter-spacing: 0.05em; }
h1 { font-size: 28px; line-height: 1.2; margin: 4px 0 12px; font-weight: 500; overflow-wrap: anywhere; }
h2 { font-size: 18px; margin: 24px 0 12px; font-weight: 500; }
h2:first-child { margin-top: 0; }
.muted { color: var(--gt-muted); }
.small { font-size: 14px; }
.btn { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; min-height: 52px;
  border-radius: 12px; border: 1px solid var(--gt-divider); background: var(--gt-surface); color: var(--gt-text);
  font: inherit; font-size: 17px; font-weight: 500; padding: 10px 16px; cursor: pointer; text-align: center; }
.btn.primary { background: var(--gt-primary); border-color: var(--gt-primary); color: var(--gt-on-primary); }
.btn.big { min-height: 68px; font-size: 20px; }
.btn.danger { color: var(--gt-error); }
.btn.slim { min-height: 44px; min-width: 44px; width: auto; font-size: 15px; padding: 6px 14px; }
.btn:disabled { opacity: 0.45; cursor: default; }
.btn:active:not(:disabled) { filter: brightness(0.92); }
.row { display: flex; gap: 10px; }
.row > * { flex: 1; }
.stack > * + * { margin-top: 10px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 10px; margin-bottom: 12px; }
.tile { background: var(--gt-surface); border: 1px solid var(--gt-divider); border-radius: 12px; padding: 12px; min-width: 0; }
.tile .label { font-size: 12px; color: var(--gt-muted); }
.tile .value { font-size: 20px; font-weight: 500; line-height: 1.25; }
.tile .sub { font-size: 12px; color: var(--gt-muted); }
.list { list-style: none; margin: 0; padding: 0; }
.list li { display: flex; align-items: center; gap: 12px; padding: 12px 4px; border-top: 1px solid var(--gt-divider);
  min-height: 56px; }
.list li:first-child { border-top: 0; }
.list li[data-action] { cursor: pointer; }
.list .grow { flex: 1; min-width: 0; }
.list .title { font-weight: 500; overflow-wrap: anywhere; }
.list .side { text-align: right; white-space: nowrap; font-size: 14px; color: var(--gt-muted); }
.state { width: 28px; height: 28px; border-radius: 50%; border: 2px solid var(--gt-divider); flex: none; display: flex;
  align-items: center; justify-content: center; font-size: 15px; color: var(--gt-muted); }
.state.done { background: var(--gt-success); border-color: var(--gt-success); color: #fff; }
.state.current { border-color: var(--gt-primary); color: var(--gt-primary); }
.badge { display: inline-block; font-size: 12px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--gt-divider);
  color: var(--gt-muted); vertical-align: middle; }
.progress { height: 6px; border-radius: 3px; background: var(--gt-divider); overflow: hidden; margin: 8px 0 14px; }
.progress > div { height: 100%; background: var(--gt-primary); border-radius: 3px; }
.topline { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 14px; color: var(--gt-muted); }
.facts { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px; }
.fact { background: var(--gt-surface); border: 1px solid var(--gt-divider); border-radius: 12px; padding: 12px; }
.fact .label { font-size: 12px; color: var(--gt-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.fact .value { font-size: 30px; font-weight: 500; line-height: 1.2; white-space: nowrap; }
.recorded { border-color: var(--gt-success); }
.actions { position: sticky; bottom: 0; background: var(--gt-bg); padding: 10px 0 calc(8px + env(safe-area-inset-bottom));
  margin-top: 8px; }
.change { display: flex; justify-content: space-between; gap: 8px; padding: 6px 0; }
.up { color: var(--gt-success); font-weight: 500; white-space: nowrap; }
.down { color: var(--gt-error); font-weight: 500; white-space: nowrap; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.chip { border: 1px solid var(--gt-divider); background: var(--gt-surface); color: var(--gt-text); border-radius: 18px;
  padding: 8px 14px; font: inherit; font-size: 14px; cursor: pointer; min-height: 38px; }
.chip.active { background: var(--gt-primary); border-color: var(--gt-primary); color: var(--gt-on-primary); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr)); gap: 12px; }
.grid .card { margin: 0; min-width: 0; }
.chart { position: relative; min-height: 190px; touch-action: pan-y; }
.chart svg { display: block; }
.chart .grid { stroke: var(--gt-divider); stroke-width: 1; }
.chart .baseline { stroke: var(--gt-muted); stroke-width: 1; }
.chart .axis { fill: var(--gt-muted); font-size: 11px; }
.chart .line { fill: none; stroke: var(--gt-primary); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.chart .dot { fill: var(--gt-primary); stroke: var(--gt-surface); stroke-width: 2; }
.chart .bar { fill: var(--gt-primary); }
.chart .ring { fill: none; stroke: var(--gt-accent); stroke-width: 2; }
.chart .star { fill: var(--gt-accent); font-size: 13px; }
.chart .ref { stroke: var(--gt-muted); stroke-width: 1.5; stroke-dasharray: 5 4; }
.chart .cross { stroke: var(--gt-muted); stroke-width: 1; }
.tip { position: absolute; top: 0; pointer-events: none; background: var(--gt-text); color: var(--gt-bg); font-size: 12px;
  padding: 5px 8px; border-radius: 6px; white-space: nowrap; z-index: 2; display: none; }
.tip b { font-size: 13px; }
.empty { color: var(--gt-muted); text-align: center; padding: 28px 8px; }
.legend { font-size: 12px; color: var(--gt-muted); margin-top: 4px; }
.legend .star { color: var(--gt-accent); }
select, input[type=text] { width: 100%; min-height: 48px; font: inherit; color: var(--gt-text); background: var(--gt-surface);
  border: 1px solid var(--gt-divider); border-radius: 10px; padding: 8px 12px; }
.modal { position: fixed; inset: 0; z-index: 20; background: rgba(0, 0, 0, 0.5); display: flex; align-items: flex-end;
  justify-content: center; }
.sheet { background: var(--gt-bg); width: 100%; max-width: 560px; max-height: 100%; overflow-y: auto;
  border-radius: 18px 18px 0 0; padding: 18px 16px calc(16px + env(safe-area-inset-bottom)); }
@media (min-width: 600px) { .modal { align-items: center; } .sheet { border-radius: 18px; } }
.sheet h2 { margin: 0 0 2px; font-size: 22px; }
.field { margin-top: 16px; }
.field > .label { font-size: 13px; color: var(--gt-muted); text-transform: uppercase; letter-spacing: 0.05em;
  margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center; }
.stepper { display: flex; align-items: center; gap: 10px; }
.stepper button { width: 64px; height: 60px; flex: none; border-radius: 14px; border: 1px solid var(--gt-divider);
  background: var(--gt-surface); color: var(--gt-text); font-size: 30px; line-height: 1; cursor: pointer; }
.stepper button:active { background: var(--gt-primary); color: var(--gt-on-primary); }
.stepper .val { flex: 1; text-align: center; font-size: 30px; font-weight: 500; white-space: nowrap; }
.stepper .val small { font-size: 15px; font-weight: 400; color: var(--gt-muted); }
.steps { display: flex; gap: 6px; }
.steps .chip { padding: 4px 10px; min-height: 30px; font-size: 13px; text-transform: none; letter-spacing: 0; }
.rpe { display: grid; grid-template-columns: repeat(10, 1fr); gap: 4px; }
@media (max-width: 420px) { .rpe { grid-template-columns: repeat(5, 1fr); gap: 6px; } }
.rpe button { height: 50px; border-radius: 10px; border: 1px solid var(--gt-divider); background: var(--gt-surface);
  color: var(--gt-text); font: inherit; font-size: 18px; font-weight: 500; cursor: pointer; padding: 0; }
.rpe button.active { background: var(--gt-primary); border-color: var(--gt-primary); color: var(--gt-on-primary); }
.rpe-text { min-height: 22px; margin-top: 8px; font-size: 14px; color: var(--gt-muted); }
.toast { position: fixed; left: 50%; top: calc(116px + env(safe-area-inset-top)); transform: translateX(-50%);
  background: var(--gt-text); color: var(--gt-bg); padding: 10px 16px; border-radius: 8px; z-index: 30; display: none;
  max-width: 90%; font-size: 14px; }
.toast.error { background: var(--gt-error); color: #fff; }
`;

/* ------------------------------------------------------------------ panel */

class GymTrackerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "training";
    this._view = "home";
    this._idx = 0;
    this._ov = null;
    this._session = null;
    this._summary = null;
    this._form = null;
    this._confirm = null;
    this._range = "3m";
    this._stats = null;
    this._exStats = null;
    this._exerciseId = null;
    this._sessions = null;
    this._detail = null;
    this._charts = {};
    this._busy = false;
    try {
      this._step = Number(localStorage.getItem("gym_tracker_weight_step")) || 2.5;
    } catch (e) {
      this._step = 2.5;
    }
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menuButton) this._menuButton.hass = hass;
    if (first) {
      this._setup();
      this._loadOverview();
    }
  }

  get hass() {
    return this._hass;
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menuButton) this._menuButton.narrow = narrow;
  }

  set panel(panel) {
    this._panel = panel;
  }

  connectedCallback() {
    if (!this._resizeObserver) {
      this._resizeObserver = new ResizeObserver(() => {
        clearTimeout(this._resizeTimer);
        this._resizeTimer = setTimeout(() => this._drawCharts(), 100);
      });
      this._onVisible = () => {
        if (document.visibilityState === "visible" && this._hass && !this._form) this._loadOverview();
      };
    }
    this._resizeObserver.observe(this);
    document.addEventListener("visibilitychange", this._onVisible);
  }

  disconnectedCallback() {
    this._resizeObserver?.disconnect();
    document.removeEventListener("visibilitychange", this._onVisible);
  }

  /* ------------------------------------------------------------- basics */

  _setup() {
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>${STYLE}</style>
      <div class="header">
        <div class="toolbar"><span class="menu"></span><div class="main-title">${esc(this._panel?.title || "Gym Tracker")}</div></div>
        <div class="tabs"></div>
      </div>
      <div class="content"></div>
      <div class="modal-host"></div>
      <div class="toast"></div>`;
    const menu = document.createElement("ha-menu-button");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    this._menuButton = menu;
    root.querySelector(".menu").appendChild(menu);
    root.addEventListener("click", (ev) => this._onClick(ev));
    root.addEventListener("change", (ev) => this._onChange(ev));
    root.addEventListener("input", (ev) => this._onInput(ev));
    root.addEventListener("pointermove", (ev) => this._onPointer(ev));
    root.addEventListener("pointerdown", (ev) => this._onPointer(ev));
    root.addEventListener("pointerleave", () => this._hideTips(), true);
  }

  async _call(type, data = {}) {
    return this._hass.callWS({ type: `${DOMAIN}/${type}`, ...data });
  }

  /* Run a backend action; errors end up in a toast, double taps are ignored. */
  async _run(fn) {
    if (this._busy) return;
    this._busy = true;
    try {
      await fn();
    } catch (err) {
      this._toast(this._errorText(err), true);
    } finally {
      this._busy = false;
    }
  }

  _errorText(err) {
    const known = {
      empty_session: "Es wurde noch keine Übung erfasst.",
      not_running: "Dieses Training läuft nicht mehr.",
      not_found: "Der Eintrag wurde nicht gefunden.",
      not_loaded: "Gym Tracker ist nicht eingerichtet.",
    };
    return known[err?.code] || err?.message || "Unbekannter Fehler";
  }

  _toast(message, error = false) {
    const el = this.shadowRoot.querySelector(".toast");
    el.textContent = message;
    el.className = `toast${error ? " error" : ""}`;
    el.style.display = "block";
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => (el.style.display = "none"), error ? 5000 : 2500);
  }

  async _loadOverview() {
    try {
      this._ov = await this._call("overview");
    } catch (err) {
      this._toast(this._errorText(err), true);
      return;
    }
    this._session = this._ov.active_session;
    if (!this._session && ["warmup", "exercise", "list"].includes(this._view)) this._view = "home";
    if (this._exerciseId === null) this._exerciseId = this._ov.plans[0]?.exercises[0]?.id ?? null;
    this._render();
  }

  async _loadStats() {
    const range = this._range;
    const [stats, exStats] = await Promise.all([
      this._call("stats/overview", { range }),
      this._exerciseId ? this._call("stats/exercise", { exercise_id: this._exerciseId, range }) : null,
    ]);
    this._stats = stats;
    this._exStats = exStats;
    this._render();
  }

  async _loadSessions(more = false) {
    const offset = more ? this._sessions.length : 0;
    const page = await this._call("sessions", { limit: 30, offset });
    this._sessions = more ? this._sessions.concat(page) : page;
    this._hasMore = page.length === 30;
    this._render();
  }

  /* ---------------------------------------------------------- rendering */

  _render() {
    const root = this.shadowRoot;
    const content = root.querySelector(".content");
    if (!content) return;
    const tabs = [["training", "Training"], ["stats", "Auswertung"], ["history", "Verlauf"]];
    root.querySelector(".tabs").innerHTML = tabs
      .map(([id, label]) => `<button class="tab ${this._tab === id ? "active" : ""}" data-action="tab" data-tab="${id}">${label}</button>`)
      .join("");
    this._charts = {};
    content.classList.toggle("wide", this._tab === "stats");
    if (!this._ov) content.innerHTML = `<div class="empty">Lade …</div>`;
    else if (this._tab === "training") content.innerHTML = this._renderTraining();
    else if (this._tab === "stats") content.innerHTML = this._renderStats();
    else content.innerHTML = this._detail ? this._renderDetail() : this._renderHistory();
    this._renderModal();
    this._drawCharts();
  }

  _renderTraining() {
    if (this._view === "summary" && this._summary) return this._renderSummary();
    if (this._session && this._view === "warmup") return this._renderWarmup();
    if (this._session && this._view === "exercise") return this._renderExercise();
    if (this._session && this._view === "list") return this._renderList();
    return this._renderHome();
  }

  _tile(label, value, sub = "") {
    return `<div class="tile"><div class="label">${esc(label)}</div><div class="value">${value}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div>`;
  }

  _sessionRow(s) {
    const parts = [fmtDuration(s.duration_s), `${s.done} Übungen`];
    if (s.volume) parts.push(`${num(s.volume)} kg`);
    const badge = s.status === "cancelled" ? ` <span class="badge">abgebrochen</span>` : "";
    return `<li data-action="open-session" data-id="${s.id}">
      <div class="grow"><div class="title">${esc(fmtDate(s.date))}${badge}</div>
      <div class="small muted">${esc(parts.join(" · "))}</div></div>
      <div class="side">${s.avg_rpe !== null ? `RPE ${num(s.avg_rpe, 1)}` : ""} ›</div></li>`;
  }

  _renderHome() {
    const { summary, recent_sessions: recent, plans, today } = this._ov;
    const plan = plans[0];
    const goal = summary.freq_min === summary.freq_max ? `${summary.freq_min}` : `${summary.freq_min}–${summary.freq_max}`;
    let start;
    if (this._session) {
      const s = this._session;
      const recorded = s.items.filter((i) => i.entry).length;
      start = `<div class="card stack">
          <h3>Laufendes Training</h3>
          <div>${esc(s.plan_name)} · gestartet ${esc(fmtDate(s.local_date, { weekday: "short", day: "2-digit", month: "2-digit" }))}, ${fmtTime(s.started_at)} Uhr</div>
          <div class="progress"><div style="width:${(recorded / s.items.length) * 100}%"></div></div>
          <div class="small muted">${recorded} von ${s.items.length} Übungen erfasst</div>
          <button class="btn primary big" data-action="resume">Training fortsetzen</button>
          <button class="btn danger" data-action="cancel-session">Training abbrechen</button>
        </div>`;
    } else if (plan) {
      start = `<div class="stack" style="margin-bottom:16px">
          <button class="btn primary big" data-action="start">Training starten</button>
          <div class="small muted" style="text-align:center">${esc(plan.name)} · ${plan.exercises.length} Übungen</div>
        </div>`;
    } else {
      start = `<div class="empty">Kein Trainingsplan vorhanden.</div>`;
    }
    const list = recent.length
      ? `<ul class="list">${recent.map((s) => this._sessionRow(s)).join("")}</ul>`
      : `<div class="empty">Noch kein Training absolviert.</div>`;
    return `${start}
      <div class="tiles">
        ${this._tile("Diese Woche", `${summary.week}<span class="muted" style="font-size:15px"> / ${goal}</span>`, "Trainings")}
        ${this._tile("Dieser Monat", summary.month, "Trainings")}
        ${this._tile("Letztes Training", esc(relDay(summary.last_date, today)), summary.last_date ? shortDate(summary.last_date) : "")}
      </div>
      <div class="card"><h3>Letzte Einheiten</h3>${list}</div>`;
  }

  _renderWarmup() {
    return `<div class="topline"><span>Warm-up</span>
        <button class="btn slim" data-action="home">Pause</button></div>
      <h1>Aufwärmen</h1>
      <div class="card"><h3>Warm-up</h3><div style="font-size:19px">${esc(this._session.warmup)}</div></div>
      <div class="actions stack">
        <button class="btn primary big" data-action="warmup-done">✓ Warm-up erledigt</button>
        <button class="btn" data-action="warmup-skip">Ohne Warm-up weiter</button>
      </div>`;
  }

  _renderExercise() {
    const items = this._session.items;
    const item = items[this._idx];
    const { exercise: ex, entry, previous, defaults } = item;
    const recorded = items.filter((i) => i.entry).length;
    const weight = entry && !entry.skipped ? entry.weight : defaults.weight;
    let status = "";
    if (entry) {
      status = `<div class="card recorded"><h3>${entry.skipped ? "Übersprungen" : "Erfasst"}</h3>
        ${entry.skipped ? "" : `<div style="font-size:19px">${esc(entryText(entry))}</div>`}
        ${entry.note ? `<div class="small muted">${esc(entry.note)}</div>` : ""}</div>`;
    }
    const last = previous
      ? `<div class="small muted" style="margin-bottom:12px">Zuletzt (${esc(shortDate(previous.date))}): ${esc(entryText(previous))}</div>`
      : `<div class="small muted" style="margin-bottom:12px">Erstes Training dieser Übung${ex.weighted ? " – Gewicht laut Plan" : ""}</div>`;
    return `<div class="topline"><span>Übung ${this._idx + 1}/${items.length} · ${esc(ex.muscle_group)}</span>
        <button class="btn slim" data-action="list">Übersicht</button></div>
      <div class="progress"><div style="width:${(recorded / items.length) * 100}%"></div></div>
      <h1>${esc(ex.name)}</h1>
      <div class="facts">
        <div class="fact"><div class="label">Ziel</div><div class="value">${esc(targetText(ex))}</div></div>
        ${ex.weighted ? `<div class="fact"><div class="label">Gewicht</div><div class="value">${esc(kg(weight))}</div></div>` : ""}
      </div>
      ${last}
      ${ex.setting ? `<div class="card"><h3>Geräteeinstellung</h3><div style="font-size:18px">${esc(ex.setting)}</div></div>` : ""}
      ${ex.hint ? `<div class="card"><h3>Ausführung</h3><div>${esc(ex.hint)}</div></div>` : ""}
      ${status}
      <div class="actions stack">
        ${entry && !entry.skipped
          ? `<button class="btn primary big" data-action="next">Weiter</button>
             <button class="btn" data-action="complete">Werte bearbeiten</button>`
          : `<button class="btn primary big" data-action="complete">✓ Übung abgeschlossen</button>
             ${entry ? "" : `<button class="btn" data-action="skip">Übung überspringen</button>`}`}
        <div class="row">
          <button class="btn" data-action="prev" ${this._idx === 0 ? "disabled" : ""}>‹ Zurück</button>
          <button class="btn" data-action="next">${this._idx === items.length - 1 ? "Abschluss ›" : "Weiter ›"}</button>
        </div>
      </div>`;
  }

  _stateIcon(entry, current = false) {
    if (entry && entry.skipped) return `<span class="state" title="Übersprungen">↷</span>`;
    if (entry) return `<span class="state done" title="Erfasst">✓</span>`;
    return `<span class="state ${current ? "current" : ""}" title="Offen"></span>`;
  }

  _renderList() {
    const s = this._session;
    const done = s.items.filter((i) => i.entry && !i.entry.skipped).length;
    const open = s.items.filter((i) => !i.entry).length;
    const rows = s.items
      .map((item, idx) => `<li data-action="goto" data-idx="${idx}">${this._stateIcon(item.entry)}
          <div class="grow"><div class="title">${esc(item.exercise.name)}</div>
          <div class="small muted">${esc(item.entry ? entryText(item.entry) : `Ziel ${targetText(item.exercise)}`)}</div></div>
          <div class="side">›</div></li>`)
      .join("");
    return `<div class="topline"><span>${esc(s.plan_name)} · seit ${fmtTime(s.started_at)} Uhr</span>
        <button class="btn slim" data-action="home">Pause</button></div>
      <h1>Übersicht</h1>
      <div class="card"><ul class="list">${rows}</ul></div>
      <div class="actions stack">
        <div class="small muted" style="text-align:center">${done} erfasst · ${s.summary.skipped} übersprungen · ${open} offen</div>
        <button class="btn primary big" data-action="finish" ${done ? "" : "disabled"}>Training beenden</button>
        <button class="btn danger" data-action="cancel-session">Training abbrechen</button>
      </div>`;
  }

  _changes(summary) {
    if (!summary.weight_changes.length) return "";
    const rows = summary.weight_changes
      .map((c) => `<div class="change"><span>${esc(c.name)}</span>
          <span class="${c.delta > 0 ? "up" : "down"}">${c.delta > 0 ? "▲" : "▼"} ${esc(num(c.from, 2))} → ${esc(kg(c.to))}
          (${c.delta > 0 ? "+" : "−"}${esc(num(Math.abs(c.delta), 2))})</span></div>`)
      .join("");
    return `<div class="card"><h3>Gewichtsänderungen</h3>${rows}</div>`;
  }

  _summaryTiles(summary) {
    return `<div class="tiles">
      ${this._tile("Dauer", esc(fmtDuration(summary.duration_s)))}
      ${this._tile("Volumen", `${num(summary.volume)}<span class="muted" style="font-size:15px"> kg</span>`)}
      ${this._tile("Übungen", `${summary.done}<span class="muted" style="font-size:15px"> / ${summary.total}</span>`, summary.skipped ? `${summary.skipped} übersprungen` : "")}
      ${this._tile("Ø Intensität", summary.avg_rpe !== null ? `RPE ${num(summary.avg_rpe, 1)}` : "–")}
    </div>`;
  }

  _renderSummary() {
    const s = this._summary;
    const rows = s.items
      .filter((i) => i.entry)
      .map((i) => `<li>${this._stateIcon(i.entry)}<div class="grow"><div class="title">${esc(i.exercise.name)}</div>
          <div class="small muted">${esc(entryText(i.entry))}</div></div></li>`)
      .join("");
    return `<h1>Training abgeschlossen 💪</h1>
      <div class="muted" style="margin-bottom:12px">${esc(s.plan_name)} · ${esc(fmtDate(s.local_date))}</div>
      ${this._summaryTiles(s.summary)}
      ${this._changes(s.summary)}
      <div class="card"><h3>Übungen</h3><ul class="list">${rows}</ul></div>
      <div class="actions"><button class="btn primary big" data-action="summary-done">Fertig</button></div>`;
  }

  /* -------------------------------------------------------------- stats */

  _chartCard(key, title, def, legend = "") {
    const count = def ? (def.points || def.bars).length : 0;
    if (!count) return `<div class="card"><h3>${esc(title)}</h3><div class="empty">Noch keine Daten im Zeitraum.</div></div>`;
    this._charts[key] = def;
    return `<div class="card"><h3>${esc(title)}</h3><div class="chart" data-chart="${key}"></div>
      ${legend ? `<div class="legend">${legend}</div>` : ""}</div>`;
  }

  _renderStats() {
    const chips = `<div class="chips">${RANGES.map(([id, label]) => `<button class="chip ${this._range === id ? "active" : ""}" data-action="range" data-range="${id}">${label}</button>`).join("")}</div>`;
    if (!this._stats) return `${chips}<div class="empty">Lade …</div>`;
    const st = this._stats;
    const weekly = st.granularity === "week";
    const bucketLabel = (b) => (weekly ? shortDate(b.start) : fmtDate(b.start, { month: "short", year: "2-digit" }));
    const bucketTitle = (b) => (weekly ? `Woche ab ${shortDate(b.start)}` : fmtDate(b.start, { month: "long", year: "numeric" }));
    const per = weekly ? "Woche" : "Monat";
    const goal = st.freq_min === st.freq_max ? `${st.freq_min}` : `${st.freq_min}–${st.freq_max}`;
    const countDef = {
      type: "bar",
      integer: true,
      refs: weekly && st.freq_min ? [{ v: st.freq_min }] : [],
      bars: st.buckets.map((b) => ({ label: bucketLabel(b), v: b.count, tip: `${bucketTitle(b)}<br><b>${b.count} Trainings</b>` })),
    };
    const volumeDef = {
      type: "bar",
      unit: "kg",
      bars: st.buckets.map((b) => ({ label: bucketLabel(b), v: b.volume, tip: `${bucketTitle(b)}<br><b>${num(b.volume)} kg</b>` })),
    };
    const rpeDef = {
      type: "line",
      min: 1,
      max: 10,
      ticks: RPE_TICKS,
      points: st.sessions
        .filter((s) => s.avg_rpe !== null)
        .map((s) => ({ t: day(s.date).getTime(), v: s.avg_rpe, label: shortDate(s.date), tip: `${fmtDate(s.date)}<br><b>Ø RPE ${num(s.avg_rpe, 1)}</b>` })),
    };
    const t = st.totals;
    const overall = `<h2>Gesamt</h2>
      <div class="tiles">
        ${this._tile("Trainings", t.count)}
        ${this._tile("Ø pro Woche", num(t.per_week, 1, 1), st.freq_min ? `Ziel ${goal}` : "")}
        ${this._tile("Gesamtvolumen", `${num(t.volume)}<span class="muted" style="font-size:15px"> kg</span>`)}
        ${this._tile("Ø Intensität", t.avg_rpe !== null ? `RPE ${num(t.avg_rpe, 1)}` : "–")}
        ${this._tile("Ø Dauer", esc(fmtDuration(t.avg_duration_s)))}
      </div>
      <div class="grid">
        ${this._chartCard("count", `Trainings pro ${per}`, t.count ? countDef : null, countDef.refs.length ? `Gestrichelte Linie: Ziel mindestens ${st.freq_min} pro Woche` : "")}
        ${this._chartCard("volume", `Gesamtvolumen pro ${per}`, t.volume ? volumeDef : null, "Sätze × Wdh. × Gewicht")}
        ${this._chartCard("rpe", "Ø Intensität je Training (RPE)", rpeDef)}
      </div>`;
    return `${chips}${overall}${this._renderExerciseStats()}`;
  }

  _renderExerciseStats() {
    const exercises = this._ov.plans.flatMap((p) => p.exercises);
    const select = `<select data-change="exercise" aria-label="Übung">${exercises
      .map((e) => `<option value="${e.id}" ${e.id === this._exerciseId ? "selected" : ""}>${esc(e.name)}</option>`)
      .join("")}</select>`;
    const head = `<h2>Pro Übung</h2><div style="margin-bottom:12px">${select}</div>`;
    const data = this._exStats;
    if (!data) return head;
    const { exercise: ex, points, records } = data;
    const timed = ex.duration_s !== null;
    const base = (p) => ({ t: day(p.date).getTime(), label: shortDate(p.date) });
    const star = (p, key) => (p.records.includes(key) ? " ★ Bestwert" : "");
    const tiles = [];
    const recTile = (label, rec, text) => rec && tiles.push(this._tile(label, text, `★ ${fmtDate(rec.date)}`));
    recTile("Bestes Gewicht", records.weight, esc(kg(records.weight?.value)));
    recTile("Bestes Volumen", records.volume, `${num(records.volume?.value)}<span class="muted" style="font-size:15px"> kg</span>`);
    if (!ex.weighted) recTile("Meiste Wdh. pro Satz", records.reps, num(records.reps?.value));
    recTile("Längste Dauer", records.seconds, `${num(records.seconds?.value)}<span class="muted" style="font-size:15px"> Sek.</span>`);
    const cards = [];
    const legend = `<span class="star">★</span> Bestwert (gesamter Verlauf)`;
    if (ex.weighted) {
      cards.push(this._chartCard("ex-weight", "Gewicht", {
        type: "line",
        unit: "kg",
        points: points.filter((p) => p.weight !== null).map((p) => ({ ...base(p), v: p.weight, mark: p.records.includes("weight"), tip: `${fmtDate(p.date)}<br><b>${kg(p.weight)}</b>${star(p, "weight")}` })),
      }, legend));
    }
    if (timed) {
      cards.push(this._chartCard("ex-seconds", "Dauer pro Satz", {
        type: "line",
        unit: "Sek.",
        points: points.filter((p) => p.seconds !== null).map((p) => ({ ...base(p), v: p.seconds, mark: p.records.includes("seconds"), tip: `${fmtDate(p.date)}<br><b>${p.set_count} × ${p.seconds} Sek.</b>${star(p, "seconds")}` })),
      }, legend));
    } else {
      cards.push(this._chartCard("ex-reps", "Wiederholungen pro Satz", {
        type: "line",
        points: points.filter((p) => p.avg_reps !== null).map((p) => ({ ...base(p), v: p.avg_reps, mark: !ex.weighted && p.records.includes("reps"), tip: `${fmtDate(p.date)}<br><b>${p.set_count} × ${num(p.avg_reps, 1)} Wdh.</b>${ex.weighted ? "" : star(p, "reps")}` })),
      }, ex.weighted ? `Ziel ${targetText(ex)}` : legend));
    }
    cards.push(this._chartCard("ex-rpe", "Intensität (RPE)", {
      type: "line",
      min: 1,
      max: 10,
      ticks: RPE_TICKS,
      points: points.filter((p) => p.rpe !== null).map((p) => ({ ...base(p), v: p.rpe, tip: `${fmtDate(p.date)}<br><b>RPE ${p.rpe}</b>` })),
    }));
    if (ex.weighted) {
      cards.push(this._chartCard("ex-volume", "Volumen je Training", {
        type: "bar",
        unit: "kg",
        bars: points.map((p) => ({ label: shortDate(p.date), v: p.volume, mark: p.records.includes("volume"), tip: `${fmtDate(p.date)}<br><b>${num(p.volume)} kg</b> (${entryText(p, false)})${star(p, "volume")}` })),
      }, `${legend} · Sätze × Wdh. × Gewicht`));
    }
    return `${head}${tiles.length ? `<div class="tiles">${tiles.join("")}</div>` : ""}<div class="grid">${cards.join("")}</div>`;
  }

  _drawCharts() {
    for (const el of this.shadowRoot.querySelectorAll(".chart[data-chart]")) {
      const def = this._charts[el.dataset.chart];
      const width = Math.floor(el.clientWidth);
      if (!def || width < 80) continue;
      const result = def.type === "bar" ? barChart(def, width) : lineChart(def, width);
      def.xs = result.xs;
      el.innerHTML = `${result.svg}<div class="tip"></div>`;
    }
  }

  _onPointer(ev) {
    const el = ev.target.closest?.(".chart[data-chart]");
    if (!el) {
      if (ev.type === "pointerdown") this._hideTips();
      return;
    }
    const def = this._charts[el.dataset.chart];
    if (!def?.xs) return;
    const rect = el.getBoundingClientRect();
    const px = ev.clientX - rect.left;
    let best = 0;
    def.xs.forEach((x, i) => {
      if (Math.abs(x - px) < Math.abs(def.xs[best] - px)) best = i;
    });
    this._hideTips(el);
    const item = (def.points || def.bars)[best];
    const tip = el.querySelector(".tip");
    const cross = el.querySelector(".cross");
    tip.innerHTML = item.tip;
    tip.style.display = "block";
    const half = tip.offsetWidth / 2;
    tip.style.left = `${Math.max(0, Math.min(rect.width - tip.offsetWidth, def.xs[best] - half))}px`;
    cross.setAttribute("x1", def.xs[best]);
    cross.setAttribute("x2", def.xs[best]);
    cross.setAttribute("visibility", "visible");
  }

  _hideTips(except = null) {
    for (const el of this.shadowRoot.querySelectorAll(".chart[data-chart]")) {
      if (el === except) continue;
      const tip = el.querySelector(".tip");
      if (tip) tip.style.display = "none";
      el.querySelector(".cross")?.setAttribute("visibility", "hidden");
    }
  }

  /* ------------------------------------------------------------ history */

  _renderHistory() {
    if (!this._sessions) return `<div class="empty">Lade …</div>`;
    const list = this._sessions.length
      ? `<div class="card"><ul class="list">${this._sessions.map((s) => this._sessionRow(s)).join("")}</ul></div>`
      : `<div class="empty">Noch keine Einheiten vorhanden.</div>`;
    return `<div class="topline" style="margin-bottom:12px"><span>Alle Einheiten</span>
        <button class="btn slim" data-action="export">CSV exportieren</button></div>
      ${list}
      ${this._hasMore ? `<button class="btn" data-action="hist-more">Mehr laden</button>` : ""}`;
  }

  _renderDetail() {
    const s = this._detail;
    const status = { cancelled: "abgebrochen", running: "läuft" }[s.status];
    const rows = s.items
      .map((item) => {
        const { exercise: ex, entry } = item;
        const text = entry ? entryText(entry) : "Nicht erfasst";
        return `<li>${this._stateIcon(entry)}
          <div class="grow"><div class="title">${esc(ex.name)}</div>
          <div class="small muted">${esc(text)}${entry?.note ? ` · ${esc(entry.note)}` : ""}</div></div>
          <button class="btn slim" data-action="edit-entry" data-ex="${ex.id}" ${entry ? 'aria-label="Eintrag bearbeiten"' : ""}>${entry ? "✎" : "Nachtragen"}</button>
          ${entry ? `<button class="btn slim danger" data-action="delete-entry" data-id="${entry.id}" aria-label="Eintrag löschen">✕</button>` : ""}</li>`;
      })
      .join("");
    return `<div class="topline"><button class="btn slim" data-action="hist-back">‹ Alle Einheiten</button>
        ${status ? `<span class="badge">${status}</span>` : ""}</div>
      <h1>${esc(fmtDate(s.local_date))}</h1>
      <div class="muted" style="margin-bottom:12px">${esc(s.plan_name)} · ${fmtTime(s.started_at)}${s.ended_at ? `–${fmtTime(s.ended_at)}` : ""} Uhr</div>
      ${this._summaryTiles(s.summary)}
      ${this._changes(s.summary)}
      <div class="card"><h3>Übungen</h3><ul class="list">${rows}</ul></div>
      <div class="card"><h3>Notiz zur Einheit</h3>
        <div class="row"><input type="text" data-input="session-note" value="${esc(s.note)}" placeholder="Optional" maxlength="300">
        <button class="btn slim" style="flex:none" data-action="save-note">Speichern</button></div></div>
      <button class="btn danger" data-action="delete-session">Einheit löschen</button>`;
  }

  /* -------------------------------------------------------------- modal */

  _stepper(field, label, value, unit, extra = "") {
    return `<div class="field"><div class="label"><span>${label}</span>${extra}</div>
      <div class="stepper">
        <button data-action="form-step" data-field="${field}" data-dir="-1" aria-label="${label} verringern">−</button>
        <div class="val">${value} <small>${unit}</small></div>
        <button data-action="form-step" data-field="${field}" data-dir="1" aria-label="${label} erhöhen">+</button>
      </div></div>`;
  }

  _renderModal() {
    const host = this.shadowRoot.querySelector(".modal-host");
    if (this._confirm) {
      const c = this._confirm;
      host.innerHTML = `<div class="modal"><div class="sheet stack">
        <h2>${esc(c.title)}</h2><div class="muted">${esc(c.text)}</div>
        <button class="btn ${c.danger ? "danger" : "primary"}" data-action="confirm-yes">${esc(c.yes)}</button>
        <button class="btn" data-action="confirm-no">Zurück</button></div></div>`;
      return;
    }
    const f = this._form;
    if (!f) {
      host.innerHTML = "";
      return;
    }
    const steps = `<span class="steps">${WEIGHT_STEPS.map((s) => `<button class="chip ${this._step === s ? "active" : ""}" data-action="weight-step" data-step="${s}">${num(s, 1)} kg</button>`).join("")}</span>`;
    host.innerHTML = `<div class="modal"><div class="sheet">
      <h2>${esc(f.name)}</h2>
      <div class="muted">Ziel ${esc(f.target)}</div>
      ${this._stepper("sets", "Sätze", f.sets, "")}
      ${f.timed ? this._stepper("seconds", "Dauer pro Satz", f.seconds, "Sek.") : this._stepper("reps", "Wiederholungen pro Satz", f.reps, "Wdh.")}
      ${f.weighted ? this._stepper("weight", "Gewicht", num(f.weight, 2), "kg", steps) : ""}
      <div class="field"><div class="label"><span>Intensität (RPE)</span><span>1 leicht – 10 maximal</span></div>
        <div class="rpe">${[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((v) => `<button class="${f.rpe === v ? "active" : ""}" data-action="form-rpe" data-v="${v}" aria-label="RPE ${v}">${v}</button>`).join("")}</div>
        <div class="rpe-text">${f.rpe ? esc(`${f.rpe}: ${RPE_TEXT[f.rpe]}`) : "Wie anstrengend war es? 10 = keine Wdh. mehr möglich"}</div>
      </div>
      <div class="field"><div class="label"><span>Notiz</span></div>
        <input type="text" data-input="form-note" value="${esc(f.note)}" placeholder="Optional" maxlength="300"></div>
      <div class="field stack">
        <button class="btn primary big" data-action="form-save" ${f.rpe ? "" : "disabled"}>${f.rpe ? "Speichern" : "Bitte Intensität wählen"}</button>
        <button class="btn" data-action="form-cancel">Abbrechen</button>
      </div></div></div>`;
  }

  _openForm(session, item, context) {
    const { exercise: ex, entry, defaults } = item;
    const src = entry && !entry.skipped ? entry : null;
    this._form = {
      context,
      sessionId: session.id,
      exerciseId: ex.id,
      isNew: !entry,
      name: ex.name,
      target: targetText(ex),
      timed: ex.duration_s !== null,
      weighted: ex.weighted,
      sets: src ? src.set_count : defaults.sets,
      reps: src ? src.reps : defaults.reps,
      seconds: src ? src.seconds : defaults.seconds,
      weight: src ? src.weight : defaults.weight ?? 0,
      rpe: src ? src.rpe : null,
      note: entry ? entry.note : "",
    };
    this._renderModal();
  }

  /* ------------------------------------------------------------- events */

  _onClick(ev) {
    const el = ev.target.closest?.("[data-action]");
    if (!el || el.disabled) return;
    const handler = this[`_act_${el.dataset.action.replaceAll("-", "_")}`];
    if (handler) handler.call(this, el.dataset);
  }

  _onChange(ev) {
    if (ev.target.dataset?.change === "exercise") {
      this._exerciseId = Number(ev.target.value);
      this._run(() => this._loadStats());
    }
  }

  _onInput(ev) {
    const kind = ev.target.dataset?.input;
    if (kind === "form-note" && this._form) this._form.note = ev.target.value;
    if (kind === "session-note") this._noteDraft = ev.target.value;
  }

  _act_tab({ tab }) {
    this._tab = tab;
    this._detail = null;
    this._render();
    if (tab === "training") this._loadOverview();
    if (tab === "stats") this._run(() => this._loadStats());
    if (tab === "history") this._run(() => this._loadSessions());
    window.scrollTo?.(0, 0);
  }

  _show(view, idx = this._idx) {
    this._view = view;
    this._idx = idx;
    this._render();
    window.scrollTo?.(0, 0);
  }

  /* Jump to the next exercise without entry, or to the overview when all are handled. */
  _gotoNextOpen(from = 0) {
    const items = this._session.items;
    let idx = items.findIndex((item, i) => i >= from && !item.entry);
    if (idx < 0) idx = items.findIndex((item) => !item.entry);
    if (idx < 0) this._show("list");
    else this._show("exercise", idx);
  }

  _enterWorkout() {
    const s = this._session;
    if (s.warmup && !s.warmup_done && !s.items.some((i) => i.entry)) this._show("warmup");
    else this._gotoNextOpen(0);
  }

  _act_start() {
    this._run(async () => {
      this._session = await this._call("session/start");
      this._summary = null;
      this._enterWorkout();
    });
  }

  _act_resume() {
    this._enterWorkout();
  }

  _act_home() {
    this._show("home");
    this._loadOverview();
  }

  _act_warmup_done() {
    this._run(async () => {
      this._session = await this._call("session/warmup", { session_id: this._session.id, done: true });
      this._gotoNextOpen(0);
    });
  }

  _act_warmup_skip() {
    this._gotoNextOpen(0);
  }

  _act_list() {
    this._show("list");
  }

  _act_goto({ idx }) {
    this._show("exercise", Number(idx));
  }

  _act_prev() {
    if (this._idx > 0) this._show("exercise", this._idx - 1);
  }

  _act_next() {
    if (this._idx >= this._session.items.length - 1) this._show("list");
    else this._show("exercise", this._idx + 1);
  }

  _act_complete() {
    this._openForm(this._session, this._session.items[this._idx], "workout");
  }

  _act_skip() {
    const item = this._session.items[this._idx];
    this._run(async () => {
      this._session = await this._call("entry/save", { session_id: this._session.id, exercise_id: item.exercise.id, skipped: true });
      this._gotoNextOpen(this._idx + 1);
    });
  }

  _act_form_step({ field, dir }) {
    const f = this._form;
    const step = { sets: 1, reps: 1, seconds: 5, weight: this._step }[field];
    const limits = { sets: [1, 20], reps: [0, 999], seconds: [0, 3600], weight: [0, 1000] }[field];
    const next = Math.round((f[field] + Number(dir) * step) * 100) / 100;
    f[field] = Math.max(limits[0], Math.min(limits[1], next));
    this._renderModal();
  }

  _act_weight_step({ step }) {
    this._step = Number(step);
    try {
      localStorage.setItem("gym_tracker_weight_step", String(this._step));
    } catch (e) {
      /* storage not available – the choice simply lasts until reload */
    }
    this._renderModal();
  }

  _act_form_rpe({ v }) {
    this._form.rpe = Number(v);
    this._renderModal();
  }

  _act_form_cancel() {
    this._form = null;
    this._renderModal();
  }

  _act_form_save() {
    const f = this._form;
    this._run(async () => {
      const payload = { session_id: f.sessionId, exercise_id: f.exerciseId, sets: f.sets, rpe: f.rpe, note: f.note };
      if (f.timed) payload.seconds = f.seconds;
      else payload.reps = f.reps;
      if (f.weighted) payload.weight = f.weight;
      const result = await this._call("entry/save", payload);
      this._form = null;
      if (f.context === "workout") {
        this._session = result;
        if (f.isNew) this._gotoNextOpen(this._idx + 1);
        else this._render();
      } else {
        this._detail = result;
        this._render();
      }
      this._toast("Gespeichert");
    });
  }

  _ask(confirm) {
    this._confirm = confirm;
    this._renderModal();
  }

  _act_confirm_no() {
    this._confirm = null;
    this._renderModal();
  }

  _act_confirm_yes() {
    const run = this._confirm.run;
    this._confirm = null;
    this._renderModal();
    this._run(run);
  }

  _act_finish() {
    const finish = async () => {
      this._summary = await this._call("session/finish", { session_id: this._session.id });
      this._session = null;
      this._show("summary");
      this._ov = await this._call("overview");
    };
    const open = this._session.items.filter((i) => !i.entry).length;
    if (!open) this._run(finish);
    else {
      this._ask({
        title: "Training beenden?",
        text: open === 1 ? "Eine Übung ist noch offen." : `${open} Übungen sind noch offen.`,
        yes: "Trotzdem beenden",
        run: finish,
      });
    }
  }

  _act_cancel_session() {
    const recorded = this._session.items.filter((i) => i.entry).length;
    this._ask({
      title: "Training abbrechen?",
      text: recorded
        ? "Die bereits erfassten Übungen bleiben im Verlauf erhalten, die Einheit zählt aber nicht als Training."
        : "Es wurde noch nichts erfasst, die Einheit wird verworfen.",
      yes: "Training abbrechen",
      danger: true,
      run: async () => {
        await this._call("session/cancel", { session_id: this._session.id });
        this._session = null;
        this._view = "home";
        await this._loadOverview();
      },
    });
  }

  _act_summary_done() {
    this._summary = null;
    this._show("home");
    this._loadOverview();
  }

  _act_range({ range }) {
    this._range = range;
    this._render();
    this._run(() => this._loadStats());
  }

  _act_open_session({ id }) {
    this._run(async () => {
      this._detail = await this._call("session/get", { session_id: Number(id) });
      this._noteDraft = this._detail.note;
      this._tab = "history";
      if (!this._sessions) this._loadSessions().catch(() => {});
      this._render();
      window.scrollTo?.(0, 0);
    });
  }

  _act_hist_back() {
    this._detail = null;
    this._render();
    this._run(() => this._loadSessions());
  }

  _act_hist_more() {
    this._run(() => this._loadSessions(true));
  }

  _act_edit_entry({ ex }) {
    const item = this._detail.items.find((i) => i.exercise.id === Number(ex));
    this._openForm(this._detail, item, "history");
  }

  _act_delete_entry({ id }) {
    this._ask({
      title: "Eintrag löschen?",
      text: "Die erfassten Werte dieser Übung werden aus der Einheit entfernt.",
      yes: "Eintrag löschen",
      danger: true,
      run: async () => {
        this._detail = await this._call("entry/delete", { entry_id: Number(id) });
        this._render();
      },
    });
  }

  _act_delete_session() {
    this._ask({
      title: "Einheit löschen?",
      text: "Die Einheit wird mit allen Einträgen dauerhaft gelöscht.",
      yes: "Einheit löschen",
      danger: true,
      run: async () => {
        await this._call("session/delete", { session_id: this._detail.id });
        this._detail = null;
        this._ov = await this._call("overview");
        this._session = this._ov.active_session;
        await this._loadSessions();
        this._toast("Einheit gelöscht");
      },
    });
  }

  _act_save_note() {
    this._run(async () => {
      this._detail = await this._call("session/update", { session_id: this._detail.id, note: this._noteDraft ?? "" });
      this._render();
      this._toast("Notiz gespeichert");
    });
  }

  _act_export() {
    this._run(async () => {
      const signed = await this._hass.callWS({ type: "auth/sign_path", path: EXPORT_PATH, expires: 60 });
      const link = document.createElement("a");
      link.href = this._hass.hassUrl ? this._hass.hassUrl(signed.path) : signed.path;
      link.download = "";
      link.click();
    });
  }
}

customElements.define("gym-tracker-panel", GymTrackerPanel);
