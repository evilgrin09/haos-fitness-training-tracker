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

const KINDS = [
  ["weight", "Gewicht", "Wdh. mit Gewicht"],
  ["reps", "Ohne Gewicht", "nur Wdh."],
  ["time", "Sekunden", "Zeit pro Satz"],
];

const CARDIO_DEVICES = [
  ["crosstrainer", "Crosstrainer"],
  ["bike", "Fahrrad"],
  ["treadmill", "Laufband"],
];
const CARDIO_LABEL = Object.fromEntries(CARDIO_DEVICES);
const PHASE_TITLE = { warmup: "Aufwärmen (Cardio)", cooldown: "Auslaufen (Cardio)" };

function cardioText(entries) {
  return entries.map((c) => `${CARDIO_LABEL[c.device]} ${c.minutes} Min.`).join(" · ");
}

function uuid() {
  return globalThis.crypto?.randomUUID ? crypto.randomUUID().replaceAll("-", "") : `${Date.now()}${Math.random()}`.replace(".", "");
}

const NEW_EXERCISE = { kind: "weight", target_sets: 3, reps_min: 12, reps_max: 14, duration_s: 30, start_weight: 20 };

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

/* Friendly wording of a progression suggestion. Hints can only be dismissed. */
const HINT_RULES = new Set(["recovery", "bodyweight", "stall_push"]);

function recText(rec, ex) {
  const d = rec.data;
  const since = d.since ? ` seit dem ${shortDate(d.since)}` : "";
  switch (rec.rule) {
    case "weight":
      return { title: `Heute ${kg(d.suggested_weight)} probieren?`, text: `Letztes Mal alle Sätze mit ${d.reps} Wdh. bei RPE ${d.rpe} – das sah nach Luft nach oben aus. Mit dem neuen Gewicht startest du wieder bei ${d.suggested_reps} Wdh.` };
    case "weight_double":
      return { title: `Heute ${kg(d.suggested_weight)} probieren?`, text: `Zweimal in Folge alle Sätze locker geschafft (RPE ${d.rpe}) – vielleicht passt heute ein größerer Schritt (+${num(d.step, 1)} kg). Wiederholungen starten wieder bei ${d.suggested_reps}.` };
    case "reps":
      return { title: "1–2 Wdh. mehr versuchen?", text: `Letztes Mal ${d.reps} Wdh. bei RPE ${d.rpe}. Vorbelegt würden ${d.suggested_reps} Wdh. pro Satz.` };
    case "time":
      return { title: `Heute ${d.suggested_seconds} Sek. probieren?`, text: `Die Zeit hast du letztes Mal bei RPE ${d.rpe} geschafft – vielleicht gehen heute 5 Sekunden mehr.` };
    case "bodyweight":
      return { title: "Mehr Wiederholungen als geplant", text: `Du schaffst mehr als ${d.reps_max} Wdh. – Zusatzgewicht oder eine langsamere Ausführung könnten die Übung wieder fordernder machen.` };
    case "sets":
      return { title: "Ab heute 3 Sätze?", text: `${d.sessions} Einheiten${since} ohne Steigerung, bei Ø RPE ${num(d.avg_rpe, 1)} gut machbar. Ein dritter Satz könnte neuen Schwung bringen. „Übernehmen“ ändert die Ziel-Sätze im Plan.` };
    case "stall_push":
      return { title: "Da geht vielleicht noch mehr", text: `${d.sessions} Einheiten${since} ohne Steigerung bei Ø RPE ${num(d.avg_rpe, 1)}. Etwas mehr Gewicht${ex?.duration_s ? " oder Zeit" : " oder Wiederholungen"} könnte passen.` };
    case "recovery":
      return { title: "Ausführung, Schlaf und Erholung prüfen", text: `${d.sessions} Einheiten${since} ohne Steigerung bei hoher Anstrengung (Ø RPE ${num(d.avg_rpe, 1)}). Steigern hat gerade keine Eile – achte auf saubere Ausführung, Schlaf und Erholung.` };
    default:
      return { title: "Vorschlag", text: "" };
  }
}

function recDecision(rec) {
  if (!rec) return "";
  const decided = { accepted: "übernommen", declined: HINT_RULES.has(rec.rule) ? "ausgeblendet" : "abgelehnt" }[rec.decision];
  return `Vorschlag: ${recText(rec).title} – ${decided || "nicht entschieden"}`;
}

function targetText(ex) {
  if (ex.duration_s !== null) return `${ex.target_sets} × ${ex.duration_s} Sek.`;
  const reps = ex.reps_min === ex.reps_max ? `${ex.reps_max}` : `${ex.reps_min}–${ex.reps_max}`;
  return `${ex.target_sets} × ${reps}`;
}

/* Seconds of the sets if they differ (stopwatch), else null. */
function differentSeconds(entry) {
  const values = (entry.sets || []).map((set) => set.seconds).filter((v) => v !== null && v !== undefined);
  return values.length > 1 && values.some((v) => v !== values[0]) ? values : null;
}

function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function entryText(entry, withRpe = true) {
  if (!entry) return "";
  if (entry.skipped) return "Übersprungen";
  const perSet = entry.seconds !== null ? differentSeconds(entry) : null;
  let text = perSet
    ? `${perSet.join(" / ")} Sek.`
    : entry.seconds !== null
      ? `${entry.set_count} × ${entry.seconds} Sek.`
      : `${entry.set_count} × ${entry.reps}`;
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
  // Stacked bars carry segments [{v, cls}] bottom-up; v is their sum.
  for (const b of bars) if (b.segments) b.v = b.segments.reduce((sum, seg) => sum + seg.v, 0);
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
    const roundTop = (cls, bottom) =>
      `<path class="${cls}" d="M${x0},${bottom}V${yTop + r}Q${x0},${yTop} ${x0 + r},${yTop}H${x0 + bw - r}Q${x0 + bw},${yTop} ${x0 + bw},${yTop + r}V${bottom}Z"></path>`;
    if (b.segments) {
      // 2px surface gap between the segments; only the top segment gets the rounded end.
      const segs = b.segments.filter((seg) => seg.v > 0);
      let sum = 0;
      segs.forEach((seg, k) => {
        const bottom = frame.y(sum) - (k ? 1 : 0);
        sum += seg.v;
        if (k === segs.length - 1) svg += roundTop(`seg ${seg.cls}`, bottom);
        else {
          const y = frame.y(sum) + 1;
          if (bottom - y > 0) svg += `<rect class="seg ${seg.cls}" x="${x0}" y="${y}" width="${bw}" height="${bottom - y}"></rect>`;
        }
      });
    } else svg += roundTop("bar", base);
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
  /* Cardio devices: validated categorical slots 1–3 (blue, orange, aqua) */
  --gt-c1: #2a78d6;
  --gt-c2: #eb6834;
  --gt-c3: #1baf7a;
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
:host(.dark) { --gt-c1: #3987e5; --gt-c2: #d95926; --gt-c3: #199e70; }
* { box-sizing: border-box; }
.header { position: sticky; top: 0; z-index: 5; background: var(--app-header-background-color, var(--gt-primary));
  color: var(--app-header-text-color, #fff); padding-top: env(safe-area-inset-top); }
.toolbar { display: flex; align-items: center; height: 56px; padding: 0 4px; }
.main-title { font-size: 20px; margin-left: 8px; flex: 1; }
.tabs { display: flex; }
.tabs[hidden] { display: none; }
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
.chart .seg.d1 { fill: var(--gt-c1); }
.chart .seg.d2 { fill: var(--gt-c2); }
.chart .seg.d3 { fill: var(--gt-c3); }
.key { display: inline-flex; align-items: center; gap: 5px; margin-right: 12px; }
.key i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.num { width: 100%; max-width: 110px; text-align: center; font: inherit; font-size: 30px; font-weight: 500; border: 0;
  border-bottom: 2px solid var(--gt-divider); background: transparent; color: var(--gt-text); padding: 0; -moz-appearance: textfield; }
.num::-webkit-outer-spin-button, .num::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.num:focus { outline: none; border-bottom-color: var(--gt-primary); }
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
.group-head { display: flex; align-items: center; gap: 8px; margin: 18px 0 6px; font-size: 13px; font-weight: 500;
  color: var(--gt-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.group-head span { flex: 1; }
.rowbtns { display: flex; gap: 6px; margin-left: auto; }
.rowbtns .btn.slim { padding: 6px 10px; font-size: 14px; min-width: 40px; }
.list li.wrap { flex-wrap: wrap; }
.list li.wrap .grow { min-width: 110px; }
.kinds { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.kinds button { min-height: 68px; border-radius: 12px; border: 1px solid var(--gt-divider); background: var(--gt-surface);
  color: var(--gt-text); font: inherit; font-weight: 500; cursor: pointer; padding: 8px 4px; }
.kinds button small { display: block; font-weight: 400; font-size: 12px; color: var(--gt-muted); }
.kinds button.active { background: var(--gt-primary); border-color: var(--gt-primary); color: var(--gt-on-primary); }
.kinds button.active small { color: inherit; opacity: 0.85; }
.kinds button:disabled { opacity: 0.5; cursor: default; }
.kinds button.active:disabled { opacity: 1; }
.hint { font-size: 13px; color: var(--gt-muted); margin-top: 6px; }
.suggest button { display: block; width: 100%; text-align: left; margin-top: 6px; padding: 10px 12px; min-height: 44px;
  border-radius: 10px; border: 1px dashed var(--gt-primary); background: var(--gt-surface); color: var(--gt-text);
  font: inherit; font-size: 15px; cursor: pointer; }
.rec { border-color: var(--gt-primary); border-width: 2px; }
.rec h3 { color: var(--gt-primary); }
.rec .title { font-size: 18px; font-weight: 500; margin-bottom: 4px; }
.rec .row { margin-top: 12px; }
.rec.hint { border-color: var(--gt-divider); border-width: 1px; }
.rec.hint h3 { color: var(--gt-muted); }
.sw { text-align: center; transition: background-color 0.3s, border-color 0.3s, color 0.3s; }
.sw-head { display: flex; justify-content: space-between; gap: 8px; font-size: 14px; font-weight: 500; color: var(--gt-muted);
  text-transform: uppercase; letter-spacing: 0.04em; }
.sw-time { font-size: clamp(76px, 27vw, 128px); font-weight: 600; line-height: 1.05; font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em; margin: 10px 0 0; }
.sw-state { min-height: 26px; font-size: 20px; font-weight: 600; }
.sw-bar { height: 12px; border-radius: 6px; background: var(--gt-divider); overflow: hidden; margin: 10px 0 16px; }
.sw-bar > div { height: 100%; width: 0; background: var(--gt-primary); border-radius: 6px; }
.btn.sw-btn { min-height: 92px; font-size: 28px; }
.btn.stop { background: var(--gt-text); border-color: var(--gt-text); color: var(--gt-bg); }
.sw-opt { display: flex; align-items: center; justify-content: center; gap: 10px; min-height: 48px; font-size: 16px; margin-top: 6px; cursor: pointer; }
.sw-opt input { width: 22px; height: 22px; accent-color: var(--gt-primary); }
.sw .list { text-align: left; margin-top: 4px; }
.sw.counting .sw-time { color: var(--gt-primary); }
/* Target reached: the whole card turns green (white on #1e7a34 = 5.5:1) and flashes once. */
.sw.reached { background: #1e7a34; border-color: #1e7a34; color: #fff; }
.sw.reached .sw-head, .sw.reached .muted, .sw.reached .small { color: rgba(255, 255, 255, 0.88); }
.sw.reached .sw-bar { background: rgba(255, 255, 255, 0.3); }
.sw.reached .sw-bar > div { background: #fff; }
.sw.reached .btn.stop { background: #fff; border-color: #fff; color: #1e7a34; }
.sw.reached .list li { border-color: rgba(255, 255, 255, 0.3); }
.sw.reached .btn.slim { background: transparent; color: #fff; border-color: rgba(255, 255, 255, 0.6); }
.sw.flash { animation: sw-flash 0.7s ease-out 1; }
@keyframes sw-flash { 0% { filter: brightness(1.9); } 100% { filter: brightness(1); } }
@media (prefers-reduced-motion: reduce) { .sw.flash { animation: none; } .sw { transition: none; } }
.setrow { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.setrow .no { width: 52px; font-size: 14px; color: var(--gt-muted); text-align: left; }
.setrow .stepper { flex: 1; gap: 6px; }
.setrow .stepper button { width: 52px; height: 48px; font-size: 24px; }
.setrow .stepper .val { font-size: 22px; }
.toggle { background: none; border: 0; color: var(--gt-primary); font: inherit; font-weight: 500; padding: 10px 0;
  min-height: 44px; cursor: pointer; text-align: left; width: 100%; }
textarea { width: 100%; min-height: 84px; font: inherit; color: var(--gt-text); background: var(--gt-surface);
  border: 1px solid var(--gt-divider); border-radius: 10px; padding: 8px 12px; resize: vertical; }
.yesno { display: flex; gap: 6px; flex: none; }
.yesno .chip { min-width: 56px; min-height: 44px; }
.toast { position: fixed; left: 50%; top: calc(116px + env(safe-area-inset-top)); transform: translateX(-50%);
  background: var(--gt-text); color: var(--gt-bg); padding: 10px 16px; border-radius: 8px; z-index: 30; display: none;
  max-width: 90%; font-size: 14px; pointer-events: none; }
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
    this._exForm = null;
    this._adopt = null;
    this._archOpen = false;
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
    this.classList.toggle("dark", Boolean(hass.themes?.darkMode));
    if (this._menuButton) this._menuButton.hass = hass;
    if (first) {
      this._setup();
      this._loadPrefs();
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
        const editing = this._form || this._exForm || this._adopt || this._confirm || this._cf?.context === "history";
        if (document.visibilityState === "visible" && this._hass && !editing) this._loadOverview();
      };
    }
    this._resizeObserver.observe(this);
    document.addEventListener("visibilitychange", this._onVisible);
    // The stopwatch display is computed from time stamps; the interval only redraws it.
    this._swTimer = setInterval(() => this._swTick(), 100);
    this._onVisibleWake = () => {
      if (document.visibilityState === "visible" && this._swRunning()) this._wake(true);
    };
    document.addEventListener("visibilitychange", this._onVisibleWake);
  }

  disconnectedCallback() {
    this._resizeObserver?.disconnect();
    document.removeEventListener("visibilitychange", this._onVisible);
    document.removeEventListener("visibilitychange", this._onVisibleWake);
    clearInterval(this._swTimer);
    this._wake(false);
  }

  /* --------------------------------------------------------- stopwatch */

  async _loadPrefs() {
    // Per Home Assistant user, stored on the server (frontend user data).
    try {
      const result = await this._hass.callWS({ type: "frontend/get_user_data", key: "gym_tracker" });
      this._prefs = { countdown: true, ...(result?.value || {}) };
    } catch (e) {
      this._prefs = { countdown: true };
    }
  }

  async _savePrefs() {
    try {
      await this._hass.callWS({ type: "frontend/set_user_data", key: "gym_tracker", value: this._prefs });
    } catch (e) {
      /* not stored – the choice lasts until the panel is reloaded */
    }
  }

  get _countdown() {
    return this._prefs?.countdown ?? true;
  }

  _swKey() {
    return `gym_tracker_sw_${this._ov?.user?.id}_${this._session?.id}`;
  }

  /* Stopwatch state of the running session: {exerciseId: {sets: [seconds], startedAt: ms | null}}. */
  _swState() {
    const key = this._swKey();
    if (this._swLoaded !== key) {
      this._swLoaded = key;
      this._swData = {};
      try {
        this._swData = JSON.parse(localStorage.getItem(key) || "{}") || {};
        // Leftovers of other sessions of this user are not needed any more.
        const prefix = `gym_tracker_sw_${this._ov?.user?.id}_`;
        for (let i = localStorage.length - 1; i >= 0; i--) {
          const other = localStorage.key(i);
          if (other?.startsWith(prefix) && other !== key) localStorage.removeItem(other);
        }
      } catch (e) {
        /* storage not available – the stopwatch still works until the panel is reloaded */
      }
      if (this._swRunning()) this._wake(true);
    }
    return this._swData;
  }

  _sw(exerciseId) {
    const data = this._swState();
    if (!data[exerciseId]) data[exerciseId] = { sets: [], startedAt: null };
    return data[exerciseId];
  }

  _swSave() {
    try {
      localStorage.setItem(this._swKey(), JSON.stringify(this._swData || {}));
    } catch (e) {
      /* see _swState */
    }
  }

  _swForget(exerciseId) {
    if (!this._session) return;
    delete this._swState()[exerciseId];
    this._swSave();
    if (!this._swRunning()) this._wake(false);
  }

  _swClearSession() {
    try {
      localStorage.removeItem(this._swKey());
    } catch (e) {
      /* see _swState */
    }
    this._swData = {};
    this._swLoaded = null;
    this._wake(false);
  }

  _swRunning() {
    return Object.values(this._swData || {}).some((state) => state.startedAt !== null);
  }

  async _wake(on) {
    // Keep the screen on while the stopwatch runs; silently skipped where unsupported.
    try {
      if (on && !this._wakeLock && navigator.wakeLock) {
        this._wakeLock = await navigator.wakeLock.request("screen");
        this._wakeLock.addEventListener?.("release", () => (this._wakeLock = null));
      } else if (!on && this._wakeLock) {
        const lock = this._wakeLock;
        this._wakeLock = null;
        await lock.release();
      }
    } catch (e) {
      this._wakeLock = null;
    }
  }

  _swTarget(item) {
    const rec = item.recommendation;
    if (rec?.rule === "time" && rec.decision === "accepted") return rec.data.suggested_seconds;
    return item.exercise.duration_s;
  }

  _stopwatchCard(item) {
    const ex = item.exercise;
    const st = this._sw(ex.id);
    const running = st.startedAt !== null;
    const number = st.sets.length + 1;
    const of = number <= ex.target_sets ? `Satz ${number} von ${ex.target_sets}` : `Satz ${number} (zusätzlich)`;
    const sets = st.sets.length
      ? `<ul class="list">${st.sets
          .map((sec, i) => `<li><div class="grow"><span class="small">Satz ${i + 1}</span> · <b>${sec} Sek.</b></div>
            <button class="btn slim" data-action="sw-del" data-i="${i}" aria-label="Satz ${i + 1} löschen">✕</button></li>`)
          .join("")}</ul>`
      : "";
    return `<div class="card sw" data-sw="${ex.id}">
      <div class="sw-head"><span>Stoppuhr · ${of}</span><span>Ziel ${this._swTarget(item)} Sek.</span></div>
      <div class="sw-time">00:00</div>
      <div class="sw-state"></div>
      <div class="sw-bar"><div></div></div>
      <button class="btn sw-btn ${running ? "stop" : "primary"}" data-action="sw-toggle">${running ? "■ Stopp" : "▶ Start"}</button>
      <label class="sw-opt"><input type="checkbox" data-change="sw-countdown" ${this._countdown ? "checked" : ""}> 3 Sekunden Vorlauf</label>
      ${sets}
    </div>`;
  }

  /* Redraw the running stopwatch from its start time (never from summed-up intervals). */
  _swTick() {
    const card = this.shadowRoot?.querySelector(".sw[data-sw]");
    if (!card || !this._session) return;
    const item = this._session.items.find((i) => i.exercise.id === Number(card.dataset.sw));
    if (!item) return;
    const st = this._sw(item.exercise.id);
    const target = this._swTarget(item);
    const now = Date.now();
    let time = "00:00";
    let state = "";
    let progress = 0;
    let counting = false;
    let reached = false;
    if (st.startedAt !== null && now < st.startedAt) {
      counting = true;
      time = String(Math.ceil((st.startedAt - now) / 1000));
      state = "Gleich geht's los";
    } else if (st.startedAt !== null) {
      const elapsed = (now - st.startedAt) / 1000;
      time = clock(elapsed);
      progress = Math.min(1, elapsed / target);
      reached = elapsed >= target;
      if (reached) state = "✓ Ziel erreicht";
    }
    card.querySelector(".sw-time").textContent = time;
    card.querySelector(".sw-state").textContent = state;
    card.querySelector(".sw-bar > div").style.width = `${progress * 100}%`;
    card.classList.toggle("counting", counting);
    if (reached && !card.classList.contains("reached")) {
      card.classList.add("reached");
      // Flash only once per set, not again after a redraw of the screen.
      const mark = `${item.exercise.id}-${st.startedAt}`;
      if (this._swFlashed !== mark) {
        this._swFlashed = mark;
        card.classList.add("flash");
        card.addEventListener("animationend", () => card.classList.remove("flash"), { once: true });
      }
    } else if (!reached) card.classList.remove("reached");
  }

  _act_sw_toggle() {
    const item = this._session.items[this._idx];
    const ex = item.exercise;
    const st = this._sw(ex.id);
    const now = Date.now();
    let added = false;
    if (st.startedAt === null) {
      st.startedAt = now + (this._countdown ? 3000 : 0);
      this._wake(true);
    } else {
      // Stop during the countdown: no set.
      if (now >= st.startedAt) {
        const seconds = Math.round((now - st.startedAt) / 1000);
        if (seconds >= 1) {
          st.sets.push(seconds);
          added = true;
        }
      }
      st.startedAt = null;
      if (!this._swRunning()) this._wake(false);
    }
    this._swSave();
    this._render();
    this._swTick();
    if (added && st.sets.length >= ex.target_sets) this._openForm(this._session, item, "workout");
  }

  _act_sw_del({ i }) {
    const st = this._sw(this._session.items[this._idx].exercise.id);
    st.sets.splice(Number(i), 1);
    this._swSave();
    this._render();
    this._swTick();
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
    this._epoch = (this._epoch || 0) + 1;
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
      name_required: "Bitte einen Namen eingeben.",
      name_exists: "Eine Übung mit diesem Namen gibt es bereits im Plan.",
      name_archived: "Eine archivierte Übung heißt so – bitte den Vorschlag unter dem Namen wählen, um sie wiederherzustellen.",
      invalid_values: "Bitte die Werte prüfen: alle Werte größer 0, Wdh. von ≤ bis.",
      kind_locked: "Die Art kann nicht geändert werden, weil es bereits Einträge gibt.",
      session_running: "Während eines laufenden Trainings ist der Plan gesperrt.",
      edge: "Weiter lässt sich das nicht verschieben.",
      archived: "Die Übung ist archiviert – bitte zuerst wiederherstellen.",
      no_access: "Du bist für Gym Tracker nicht freigeschaltet.",
      no_plan: "Bitte zuerst einen Plan anlegen.",
      plan_exists: "Du hast bereits einen Plan.",
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
    // An action started while this request was under way has newer data: drop the stale answer.
    const epoch = this._epoch;
    let overview;
    try {
      overview = await this._call("overview");
    } catch (err) {
      if (err?.code === "no_access") {
        this._noAccess = true;
        this._render();
      } else this._toast(this._errorText(err), true);
      return;
    }
    this._noAccess = false;
    if (epoch !== this._epoch || this._busy) return;
    this._applyOverview(overview);
  }

  _applyOverview(overview) {
    this._ov = overview;
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
    root.querySelector(".tabs").hidden = Boolean(this._noAccess || (this._ov && !this._ov.plans.length));
    if (this._noAccess) content.innerHTML = this._renderNoAccess();
    else if (!this._ov) content.innerHTML = `<div class="empty">Lade …</div>`;
    else if (!this._ov.plans.length) content.innerHTML = this._renderCreatePlan();
    else if (this._tab === "training") content.innerHTML = this._renderTraining();
    else if (this._tab === "stats") content.innerHTML = this._renderStats();
    else content.innerHTML = this._detail ? this._renderDetail() : this._renderHistory();
    this._renderModal();
    this._drawCharts();
    this._swTick();
  }

  _renderNoAccess() {
    return `<div class="card" style="margin-top:24px"><h3>Kein Zugriff</h3>
      <div>Du bist für Gym Tracker nicht freigeschaltet. Ein Admin kann dich unter
      Einstellungen → Geräte &amp; Dienste → Gym Tracker → Konfigurieren freischalten.</div></div>`;
  }

  /* Data from before 0.3.0 that nobody owns yet: a plain hint for everybody, details for admins. */
  _unassignedHint() {
    const u = this._ov.unassigned;
    if (!u) return "";
    if (u === true) {
      return `<div class="card"><h3>Hinweis</h3><div>Es gibt Trainingsdaten aus einer früheren Version, die noch
        niemandem zugeordnet sind. Wenn es deine sind, bitte einen Admin, sie dir zuzuordnen – lege dann noch keinen
        eigenen Plan an.</div></div>`;
    }
    return `<div class="card"><h3>Bestehende Daten zuordnen</h3><div>${u.plans} Plan/Pläne und ${u.sessions} Einheit(en)
      aus einer früheren Version sind noch keinem Benutzer zugeordnet. Zuordnen unter Einstellungen → Geräte &amp; Dienste
      → Gym Tracker → Konfigurieren → „Bestehende Daten zuordnen an“. Zur Auswahl stehen nur Benutzer ohne eigenen
      Plan.</div></div>`;
  }

  _renderCreatePlan() {
    const f = this._planForm;
    const empty = f
      ? `<div class="card stack"><h3>Leerer Plan</h3>
          <input type="text" data-input="plan-name" value="${esc(f.name)}" placeholder="Name des Plans" maxlength="100">
          ${this._stepper("freq_min", "Ziel pro Woche: mindestens", f.freq_min, "×", "", "plan-step")}
          ${this._stepper("freq_max", "Ziel pro Woche: höchstens", f.freq_max, "×", "", "plan-step")}
          <button class="btn primary" data-action="plan-create-empty">Plan anlegen</button>
          <button class="btn" data-action="plan-form-cancel">Abbrechen</button></div>`
      : `<button class="btn" data-action="plan-form">Leeren Plan anlegen</button>
         <div class="small muted" style="text-align:center">Name und Ziel pro Woche festlegen, Übungen danach selbst hinzufügen.</div>`;
    return `<h1>Plan anlegen</h1>
      <div class="muted" style="margin-bottom:16px">Hallo ${esc(this._ov.user?.name || "")}! Für dich gibt es noch keinen
        Trainingsplan. Jeder Benutzer hat seinen eigenen Plan, seine eigenen Trainings und seine eigene Auswertung.</div>
      ${this._unassignedHint()}
      <div class="stack">
        <button class="btn primary big" data-action="plan-template">Vorlage „Ganzkörper-Training“ übernehmen</button>
        <div class="small muted" style="text-align:center">10 Übungen, 2–3× pro Woche – lässt sich danach anpassen.</div>
        ${empty}
      </div>`;
  }

  _renderTraining() {
    if (this._view === "summary" && this._summary) return this._renderSummary();
    if (this._session && this._view === "cardio") return this._renderCardioStep();
    if (this._session && this._view === "warmup") return this._renderWarmup();
    if (this._session && this._view === "exercise") return this._renderExercise();
    if (this._session && this._view === "list") return this._renderList();
    if (!this._session && this._view === "plan") return this._renderPlan();
    return this._renderHome();
  }

  _tile(label, value, sub = "") {
    return `<div class="tile"><div class="label">${esc(label)}</div><div class="value">${value}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div>`;
  }

  _sessionRow(s) {
    const parts = [fmtDuration(s.duration_s), `${s.done} Übungen`];
    if (s.volume) parts.push(`${num(s.volume)} kg`);
    if (s.cardio_minutes) parts.push(`${s.cardio_minutes} Min. Cardio`);
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
          <button class="btn" disabled>Plan bearbeiten</button>
          <div class="small muted" style="text-align:center">Während eines laufenden Trainings ist der Plan gesperrt.</div>
        </div>`;
    } else if (plan) {
      start = `<div class="stack" style="margin-bottom:16px">
          <button class="btn primary big" data-action="start">Training starten</button>
          <div class="small muted" style="text-align:center">${esc(plan.name)} · ${plan.exercises.length} Übungen</div>
          <button class="btn" data-action="plan">Plan bearbeiten</button>
        </div>`;
    } else {
      start = `<div class="empty">Kein Trainingsplan vorhanden.</div>`;
    }
    const list = recent.length
      ? `<ul class="list">${recent.map((s) => this._sessionRow(s)).join("")}</ul>`
      : `<div class="empty">Noch kein Training absolviert.</div>`;
    return `${start}
      ${this._ov.unassigned && this._ov.user?.is_admin ? this._unassignedHint() : ""}
      <div class="tiles">
        ${this._tile("Diese Woche", `${summary.week}<span class="muted" style="font-size:15px"> / ${goal}</span>`, "Trainings")}
        ${this._tile("Dieser Monat", summary.month, "Trainings")}
        ${this._tile("Letztes Training", esc(relDay(summary.last_date, today)), summary.last_date ? shortDate(summary.last_date) : "")}
      </div>
      <div class="card"><h3>Letzte Einheiten</h3>${list}</div>`;
  }

  _planText(ex) {
    return `${targetText(ex)}${ex.kind === "weight" ? ` · ${kg(ex.last_weight ?? ex.start_weight)}` : ""}`;
  }

  _renderPlan() {
    const plan = this._ov.plans[0];
    const groups = [];
    for (const ex of plan.exercises) {
      if (!groups.length || groups[groups.length - 1].name !== ex.muscle_group) groups.push({ name: ex.muscle_group, items: [] });
      groups[groups.length - 1].items.push(ex);
    }
    const named = groups.filter((g) => g.name).length;
    const move = (action, attrs, dir, off, label) =>
      `<button class="btn slim" data-action="${action}" ${attrs} data-dir="${dir}" ${off ? "disabled" : ""} aria-label="${label}">${dir === "up" ? "↑" : "↓"}</button>`;
    const blocks = groups
      .map((g, gi) => {
        const attrs = `data-group="${esc(g.name)}"`;
        const head = `<div class="group-head"><span>${esc(g.name || "Ohne Gruppe")}</span>
          ${g.name && named > 1 ? `${move("group-move", attrs, "up", gi === 0, "Gruppe nach oben")}${move("group-move", attrs, "down", gi >= named - 1, "Gruppe nach unten")}` : ""}</div>`;
        const rows = g.items
          .map((ex, i) => `<li class="wrap" data-action="ex-edit" data-id="${ex.id}">
              <div class="grow"><div class="title">${esc(ex.name)}</div><div class="small muted">${esc(this._planText(ex))}</div></div>
              <div class="rowbtns">
                ${move("ex-move", `data-id="${ex.id}"`, "up", i === 0, "Nach oben")}
                ${move("ex-move", `data-id="${ex.id}"`, "down", i === g.items.length - 1, "Nach unten")}
                <button class="btn slim" data-action="ex-archive" data-id="${ex.id}">Archivieren</button>
              </div></li>`)
          .join("");
        return `${head}<div class="card"><ul class="list">${rows}</ul></div>`;
      })
      .join("");
    const archived = plan.archived_exercises;
    const archive = archived.length
      ? `<button class="toggle" data-action="arch-toggle">${this._archOpen ? "▾" : "▸"} Archivierte Übungen (${archived.length})</button>
        ${this._archOpen
          ? `<div class="card"><ul class="list">${archived
              .map((ex) => `<li class="wrap"><div class="grow"><div class="title">${esc(ex.name)}</div>
                <div class="small muted">${esc(this._planText(ex))}${ex.entry_count ? ` · ${ex.entry_count}× trainiert` : ""}</div></div>
                <div class="rowbtns"><button class="btn slim" data-action="ex-restore" data-id="${ex.id}">Wiederherstellen</button></div></li>`)
              .join("")}</ul></div>`
          : ""}`
      : "";
    return `<div class="topline"><button class="btn slim" data-action="home">‹ Zurück</button><span>${esc(plan.name)}</span></div>
      <h1>Plan bearbeiten</h1>
      <div class="small muted">Antippen zum Bearbeiten. Änderungen gelten ab dem nächsten Training.</div>
      ${blocks || `<div class="empty">Noch keine Übungen im Plan.</div>`}
      <div style="margin:16px 0 8px"><button class="btn primary" data-action="ex-add">+ Übung hinzufügen</button></div>
      ${archive}`;
  }

  /* Device tiles, minutes stepper (no default value) and the save button. */
  _cardioForm() {
    const f = this._cf;
    const valid = f.device && f.minutes >= 1 && f.minutes <= 180;
    const tiles = CARDIO_DEVICES.map(([id, label]) => `<button class="${f.device === id ? "active" : ""}" data-action="cf-device" data-device="${id}">${label}</button>`).join("");
    return `<div class="field"><div class="label"><span>Gerät</span></div><div class="kinds">${tiles}</div></div>
      <div class="field"><div class="label"><span>Dauer</span><span>Minuten antippen zum Eintippen</span></div>
        <div class="stepper">
          <button data-action="cf-step" data-dir="-1" aria-label="Minuten verringern">−</button>
          <div class="val"><input class="num" type="number" inputmode="numeric" min="1" max="180" step="1" data-input="cf-minutes"
            value="${f.minutes ?? ""}" placeholder="–" aria-label="Minuten"> <small>Min.</small></div>
          <button data-action="cf-step" data-dir="1" aria-label="Minuten erhöhen">+</button>
        </div></div>
      <div class="field stack">
        <button class="btn primary" data-action="cf-save" ${valid ? "" : "disabled"}>${f.editId ? "Änderung speichern" : "Hinzufügen"}</button>
        ${f.editId || f.context === "history" ? `<button class="btn" data-action="cf-cancel">Abbrechen</button>` : ""}
      </div>`;
  }

  _cardioEntries(entries, editable = true) {
    if (!entries.length) return "";
    return `<ul class="list">${entries
      .map((c) => `<li><span class="state done">✓</span><div class="grow"><div class="title">${esc(CARDIO_LABEL[c.device])}</div>
          <div class="small muted">${c.minutes} Min.</div></div>
          ${editable ? `<button class="btn slim" data-action="cf-edit" data-id="${c.id}" aria-label="Bearbeiten">✎</button>
          <button class="btn slim danger" data-action="cf-delete" data-id="${c.id}" aria-label="Löschen">✕</button>` : ""}</li>`)
      .join("")}</ul>`;
  }

  _renderCardioStep() {
    const phase = this._cardioPhase;
    const entries = this._session.cardio[phase];
    const total = entries.reduce((sum, c) => sum + c.minutes, 0);
    const next = phase === "warmup" ? "Weiter zum Warm-up" : "Weiter zur Übersicht";
    return `<div class="topline"><span>${phase === "warmup" ? "Vor dem Training" : "Nach der letzten Übung"}</span>
        <button class="btn slim" data-action="${phase === "warmup" ? "home" : "list"}">${phase === "warmup" ? "Pause" : "Übersicht"}</button></div>
      <h1>${PHASE_TITLE[phase]}</h1>
      <div class="card">${this._cardioForm()}</div>
      ${entries.length ? `<div class="card"><h3>Erfasst · ${total} Min.</h3>${this._cardioEntries(entries)}</div>` : ""}
      <div class="actions stack">
        ${entries.length
          ? `<button class="btn primary big" data-action="cardio-next">${next}</button>`
          : `<button class="btn big" data-action="cardio-next">Überspringen</button>`}
      </div>`;
  }

  _cardioRow(phase) {
    const entries = this._session.cardio[phase];
    return `<li data-action="cardio-open" data-phase="${phase}">${entries.length ? `<span class="state done">✓</span>` : `<span class="state"></span>`}
      <div class="grow"><div class="title">${PHASE_TITLE[phase]}</div>
      <div class="small muted">${entries.length ? esc(cardioText(entries)) : "Optional – antippen zum Erfassen"}</div></div>
      <div class="side">›</div></li>`;
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
    const rec = item.recommendation;
    let recCard = "";
    if (rec && !rec.decision && !entry) {
      const { title, text } = recText(rec, ex);
      const hint = HINT_RULES.has(rec.rule);
      recCard = `<div class="card rec ${hint ? "hint" : ""}"><h3>${hint ? "Hinweis" : "Vorschlag"}</h3>
        <div class="title">${esc(title)}</div><div class="small">${esc(text)}</div>
        <div class="row">${hint
          ? `<button class="btn slim" data-action="rec-decide" data-v="declined">Ausblenden</button>`
          : `<button class="btn slim primary" data-action="rec-decide" data-v="accepted">Übernehmen</button>
             <button class="btn slim" data-action="rec-decide" data-v="declined">Beibehalten</button>`}</div></div>`;
    } else if (rec?.decision === "accepted" && !entry) {
      recCard = `<div class="small muted" style="margin-bottom:12px">✓ Vorschlag übernommen: ${esc(recText(rec, ex).title.replace(/ probieren\?$|\?$/, ""))}</div>`;
    }
    const last = previous
      ? `<div class="small muted" style="margin-bottom:12px">Zuletzt (${esc(shortDate(previous.date))}): ${esc(entryText(previous))}</div>`
      : `<div class="small muted" style="margin-bottom:12px">Erstes Training dieser Übung${ex.weighted ? " – Gewicht laut Plan" : ""}</div>`;
    return `<div class="topline"><span>Übung ${this._idx + 1}/${items.length}${ex.muscle_group ? ` · ${esc(ex.muscle_group)}` : ""}${item.adhoc ? " · spontan" : ""}</span>
        <button class="btn slim" data-action="list">Übersicht</button></div>
      <div class="progress"><div style="width:${(recorded / items.length) * 100}%"></div></div>
      <h1>${esc(ex.name)}</h1>
      ${ex.duration_s !== null && !entry ? this._stopwatchCard(item) : ""}
      ${recCard}
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

  _badges(item) {
    return `${item.adhoc ? ` <span class="badge">spontan</span>` : ""}${item.exercise.archived ? ` <span class="badge">archiviert</span>` : ""}`;
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
          <div class="grow"><div class="title">${esc(item.exercise.name)}${item.adhoc ? ` <span class="badge">spontan</span>` : ""}</div>
          <div class="small muted">${esc(item.entry ? entryText(item.entry) : `Ziel ${targetText(item.exercise)}`)}</div></div>
          <div class="side">›</div></li>`)
      .join("");
    return `<div class="topline"><span>${esc(s.plan_name)} · seit ${fmtTime(s.started_at)} Uhr</span>
        <button class="btn slim" data-action="home">Pause</button></div>
      <h1>Übersicht</h1>
      <div class="card"><ul class="list">${this._cardioRow("warmup")}${rows}${this._cardioRow("cooldown")}</ul></div>
      <button class="btn" data-action="ex-add-session">+ Übung hinzufügen</button>
      <div class="actions stack">
        <div class="small muted" style="text-align:center">${done} erfasst · ${s.summary.skipped} übersprungen · ${open} offen</div>
        <button class="btn primary big" data-action="finish" ${done || s.summary.cardio.minutes ? "" : "disabled"}>Training beenden</button>
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
      ${summary.cardio.minutes
        ? this._tile("Cardio", `${summary.cardio.minutes}<span class="muted" style="font-size:15px"> Min.</span>`, `Aufw. ${summary.cardio.by_phase.warmup} · Ausl. ${summary.cardio.by_phase.cooldown}`)
        : ""}
    </div>`;
  }

  _stallHints(summary) {
    if (!summary.stall_hints?.length) return "";
    const rows = summary.stall_hints
      .map((h) => {
        const { title, text } = recText(h);
        return `<li><div class="grow"><div class="title">${esc(h.name)}: ${esc(title)}</div><div class="small muted">${esc(text)}</div></div></li>`;
      })
      .join("");
    return `<div class="card"><h3>Hinweise zur Entwicklung</h3><ul class="list">${rows}</ul></div>`;
  }

  _cardioCard(session, editable) {
    const parts = ["warmup", "cooldown"].map((phase) => {
      const entries = session.cardio[phase];
      const add = editable ? `<button class="btn slim" data-action="cf-add-history" data-phase="${phase}">+ Nachtragen</button>` : "";
      if (!entries.length && !editable) return "";
      return `<div class="topline" style="margin-top:8px"><b>${PHASE_TITLE[phase]}</b>${add}</div>
        ${entries.length ? this._cardioEntries(entries, editable) : `<div class="small muted">Nicht erfasst</div>`}`;
    });
    if (!parts.join("")) return "";
    return `<div class="card"><h3>Cardio</h3>${parts.join("")}</div>`;
  }

  _renderSummary() {
    const s = this._summary;
    const rows = s.items
      .filter((i) => i.entry)
      .map((i) => `<li>${this._stateIcon(i.entry)}<div class="grow"><div class="title">${esc(i.exercise.name)}${this._badges(i)}</div>
          <div class="small muted">${esc(entryText(i.entry))}</div></div></li>`)
      .join("");
    return `<h1>Training abgeschlossen 💪</h1>
      <div class="muted" style="margin-bottom:12px">${esc(s.plan_name)} · ${esc(fmtDate(s.local_date))}</div>
      ${this._summaryTiles(s.summary)}
      ${this._changes(s.summary)}
      ${this._stallHints(s.summary)}
      ${this._cardioCard(s, false)}
      ${rows ? `<div class="card"><h3>Übungen</h3><ul class="list">${rows}</ul></div>` : ""}
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
    return `${chips}${overall}${this._renderCardioStats(st, bucketLabel, bucketTitle, per)}${this._renderExerciseStats()}`;
  }

  _renderCardioStats(st, bucketLabel, bucketTitle, per) {
    const c = st.cardio;
    const head = `<h2>Cardio</h2>`;
    if (!c.minutes) return `${head}<div class="card"><div class="empty">Im Zeitraum kein Cardio erfasst.</div></div>`;
    const keyed = CARDIO_DEVICES.map(([id, label], i) => ({ id, label, cls: `d${i + 1}` }));
    const legend = keyed
      .map((d) => `<span class="key"><i style="background:var(--gt-c${d.cls[1]})"></i>${d.label} ${c.by_device[d.id]} Min.</span>`)
      .join("");
    const stackDef = {
      type: "bar",
      unit: "Min.",
      bars: c.buckets.map((b) => ({
        label: bucketLabel(b),
        segments: keyed.map((d) => ({ v: b.by_device[d.id], cls: d.cls })),
        tip: `${bucketTitle(b)}<br><b>${b.minutes} Min.</b>${keyed.filter((d) => b.by_device[d.id]).map((d) => `<br>${d.label}: ${b.by_device[d.id]} Min.`).join("")}`,
      })),
    };
    const lineDef = {
      type: "line",
      unit: "Min.",
      min: 0,
      points: c.sessions.map((p) => ({ t: day(p.date).getTime(), v: p.minutes, label: shortDate(p.date), tip: `${fmtDate(p.date)}<br><b>${p.minutes} Min. Cardio</b>` })),
    };
    return `${head}
      <div class="tiles">
        ${this._tile("Cardio gesamt", `${c.minutes}<span class="muted" style="font-size:15px"> Min.</span>`)}
        ${this._tile("Anteil an der Trainingszeit", c.share !== null ? `${num(c.share * 100)} %` : "–")}
        ${this._tile("Aufwärmen", `${c.by_phase.warmup}<span class="muted" style="font-size:15px"> Min.</span>`)}
        ${this._tile("Auslaufen", `${c.by_phase.cooldown}<span class="muted" style="font-size:15px"> Min.</span>`)}
      </div>
      <div class="grid">
        ${this._chartCard("cardio-stack", `Cardio-Minuten pro ${per} nach Gerät`, stackDef, legend)}
        ${this._chartCard("cardio-line", "Cardio-Minuten je Training", lineDef)}
      </div>`;
  }

  _renderExerciseStats() {
    const exercises = this._ov.plans.flatMap((p) => p.exercises.concat(p.archived_exercises.filter((e) => e.entry_count)));
    const select = `<select data-change="exercise" aria-label="Übung">${exercises
      .map((e) => `<option value="${e.id}" ${e.id === this._exerciseId ? "selected" : ""}>${esc(e.name)}${e.archived ? " (archiviert)" : ""}</option>`)
      .join("")}</select>`;
    const data = this._exStats;
    let note = data?.exercise.archived ? `<div class="hint">Diese Übung ist archiviert und nicht mehr Teil des Plans.</div>` : "";
    const stall = data?.stall;
    if (stall && stall.avg_rpe !== null && (stall.avg_rpe >= 9 || stall.avg_rpe <= 7)) {
      const rule = stall.avg_rpe >= 9 ? "recovery" : "stall_push";
      const { title, text } = recText({ rule, data: stall }, data.exercise);
      note += `<div class="card rec hint" style="margin-top:12px"><h3>Hinweis</h3><div class="title">${esc(title)}</div><div class="small">${esc(text)}</div></div>`;
    }
    const head = `<h2>Pro Übung</h2><div style="margin-bottom:12px">${select}${note}</div>`;
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
      cards.push(this._chartCard("ex-seconds", "Dauer pro Satz (Durchschnitt)", {
        type: "line",
        unit: "Sek.",
        points: points.filter((p) => p.seconds !== null).map((p) => {
          const same = p.min_seconds === p.max_seconds;
          const text = same ? `${p.set_count} × ${p.seconds} Sek.` : `${p.set_count} Sätze, Ø ${num(p.avg_seconds, 1)} Sek. (${p.min_seconds}–${p.max_seconds})`;
          return { ...base(p), v: p.avg_seconds ?? p.seconds, mark: p.records.includes("seconds"), tip: `${fmtDate(p.date)}<br><b>${text}</b>${star(p, "seconds")}` };
        }),
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
        const decision = item.recommendation ? `<div class="small muted">💡 ${esc(recDecision(item.recommendation))}</div>` : "";
        return `<li>${this._stateIcon(entry)}
          <div class="grow"><div class="title">${esc(ex.name)}${this._badges(item)}</div>
          <div class="small muted">${esc(text)}${entry?.note ? ` · ${esc(entry.note)}` : ""}</div>${decision}</div>
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
      ${this._stallHints(s.summary)}
      <div class="card"><h3>Übungen</h3><ul class="list">${rows}</ul></div>
      ${this._cardioCard(s, true)}
      <div class="card"><h3>Notiz zur Einheit</h3>
        <div class="row"><input type="text" data-input="session-note" value="${esc(s.note)}" placeholder="Optional" maxlength="300">
        <button class="btn slim" style="flex:none" data-action="save-note">Speichern</button></div></div>
      <button class="btn danger" data-action="delete-session">Einheit löschen</button>`;
  }

  /* -------------------------------------------------------------- modal */

  _stepper(field, label, value, unit, extra = "", action = "form-step") {
    return `<div class="field"><div class="label"><span>${label}</span>${extra}</div>
      <div class="stepper">
        <button data-action="${action}" data-field="${field}" data-dir="-1" aria-label="${label} verringern">−</button>
        <div class="val">${value} <small>${unit}</small></div>
        <button data-action="${action}" data-field="${field}" data-dir="1" aria-label="${label} erhöhen">+</button>
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
    if (this._adopt) {
      const a = this._adopt;
      const rows = a.items
        .map((item) => `<li><div class="grow"><div class="title">${esc(item.name)}</div></div>
          <div class="yesno">${[["yes", "Ja"], ["no", "Nein"]].map(([v, label]) => `<button class="chip ${a.choice[item.id] === v ? "active" : ""}" data-action="adopt-set" data-id="${item.id}" data-v="${v}">${label}</button>`).join("")}</div></li>`)
        .join("");
      const ready = a.items.every((item) => a.choice[item.id]);
      host.innerHTML = `<div class="modal"><div class="sheet stack">
        <h2>In den Plan übernehmen?</h2>
        <div class="muted">Ja: Die Übung wird Teil des Plans. Nein: Sie wird archiviert, der Eintrag bleibt in Verlauf und Auswertung.</div>
        <ul class="list">${rows}</ul>
        <button class="btn primary big" data-action="adopt-finish" ${ready ? "" : "disabled"}>Training beenden</button>
        <button class="btn" data-action="adopt-cancel">Zurück</button></div></div>`;
      return;
    }
    if (this._exForm) {
      host.innerHTML = this._renderExForm();
      return;
    }
    if (this._cf?.context === "history") {
      host.innerHTML = `<div class="modal"><div class="sheet"><h2>${PHASE_TITLE[this._cf.phase]}</h2>
        <div class="muted">${this._cf.editId ? "Eintrag ändern" : "Nachtragen"}</div>${this._cardioForm()}</div></div>`;
      return;
    }
    const f = this._form;
    if (!f) {
      host.innerHTML = "";
      return;
    }
    const steps = this._weightSteps();
    host.innerHTML = `<div class="modal"><div class="sheet">
      <h2>${esc(f.name)}</h2>
      <div class="muted">Ziel ${esc(f.target)}</div>
      ${f.perSet
        ? this._perSetFields(f)
        : `${this._stepper("sets", "Sätze", f.sets, "")}
           ${f.timed ? this._stepper("seconds", "Dauer pro Satz", f.seconds, "Sek.") : this._stepper("reps", "Wiederholungen pro Satz", f.reps, "Wdh.")}`}
      ${f.weighted ? this._stepper("weight", "Gewicht", num(f.weight, 2), "kg", steps) : ""}
      <div class="field"><div class="label"><span>Intensität (RPE)</span><span>1 leicht – 10 maximal</span></div>
        <div class="rpe">${[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((v) => `<button class="${f.rpe === v ? "active" : ""}" data-action="form-rpe" data-v="${v}" aria-label="RPE ${v}">${v}</button>`).join("")}</div>
        <div class="rpe-text">${f.rpe ? esc(`${f.rpe}: ${RPE_TEXT[f.rpe]}`) : "Wie anstrengend war es? 10 = keine Wdh. mehr möglich"}</div>
      </div>
      <div class="field"><div class="label"><span>Notiz</span></div>
        <input type="text" data-input="form-note" value="${esc(f.note)}" placeholder="Optional" maxlength="300"></div>
      <div class="field stack">
        <button class="btn primary big" data-action="form-save" ${f.rpe && (!f.perSet || f.perSet.length) ? "" : "disabled"}>${f.rpe ? "Speichern" : "Bitte Intensität wählen"}</button>
        <button class="btn" data-action="form-cancel">Abbrechen</button>
      </div></div></div>`;
  }

  _weightSteps() {
    return `<span class="steps">${WEIGHT_STEPS.map((s) => `<button class="chip ${this._step === s ? "active" : ""}" data-action="weight-step" data-step="${s}">${num(s, 1)} kg</button>`).join("")}</span>`;
  }

  _suggestions() {
    const f = this._exForm;
    const text = f.name.trim().toLocaleLowerCase(LOCALE);
    if (f.mode === "edit" || text.length < 2) return "";
    const what = f.mode === "session" ? "in dieses Training übernehmen" : "wiederherstellen";
    return this._ov.plans[0].archived_exercises
      .filter((e) => e.name.toLocaleLowerCase(LOCALE).includes(text))
      .slice(0, 4)
      .map((e) => `<button data-action="ex-suggest" data-id="${e.id}">Archiviert: „${esc(e.name)}“ (${esc(targetText(e))}) – ${what} ›</button>`)
      .join("");
  }

  _renderExForm() {
    const f = this._exForm;
    const title = { add: "Übung hinzufügen", edit: "Übung bearbeiten", session: "Spontane Übung" }[f.mode];
    const kinds = KINDS.map(([id, label, sub]) => `<button class="${f.kind === id ? "active" : ""}" data-action="ex-kind" data-kind="${id}" ${f.kindLocked ? "disabled" : ""}>${label}<small>${sub}</small></button>`).join("");
    const st = (field, label, value, unit, extra = "") => this._stepper(field, label, value, unit, extra, "ex-step");
    let fields = st("target_sets", "Sätze", f.target_sets, "");
    if (f.kind === "time") fields += st("duration_s", "Dauer pro Satz", f.duration_s, "Sek.");
    else fields += st("reps_min", "Wiederholungen von", f.reps_min, "Wdh.") + st("reps_max", "Wiederholungen bis", f.reps_max, "Wdh.");
    if (f.kind === "weight") {
      fields += st("start_weight", "Startgewicht", num(f.start_weight, 2), "kg", this._weightSteps());
      if (f.lastWeight !== null && f.lastWeight !== undefined) {
        fields += `<div class="hint">Gilt nur, solange noch kein Gewicht erfasst wurde – aktuell zählen ${esc(kg(f.lastWeight))} vom ${esc(shortDate(f.lastDate))}.</div>`;
      }
    }
    const groups = [...new Set(this._ov.plans[0].exercises.map((e) => e.muscle_group).filter(Boolean))];
    const chip = (label, active, attrs) => `<button class="chip ${active ? "active" : ""}" ${attrs}>${esc(label)}</button>`;
    const more = f.more
      ? `<div class="field"><div class="label"><span>Gruppe</span></div>
          <div class="chips" style="margin-bottom:8px">
            ${groups.map((g) => chip(g, !f.groupNew && f.group === g, `data-action="ex-group" data-group="${esc(g)}"`)).join("")}
            ${chip("Ohne Gruppe", !f.groupNew && !f.group, `data-action="ex-group" data-group=""`)}
            ${chip("Neue Gruppe …", f.groupNew, `data-action="ex-group-new"`)}
          </div>
          ${f.groupNew ? `<input type="text" data-input="ex-group-text" value="${esc(f.group)}" placeholder="Name der Gruppe" maxlength="60">` : ""}</div>
        <div class="field"><div class="label"><span>Geräteeinstellung</span></div>
          <input type="text" data-input="ex-setting" value="${esc(f.setting)}" placeholder="z. B. Sitz 6" maxlength="300"></div>
        <div class="field"><div class="label"><span>Ausführungshinweis</span></div>
          <textarea data-input="ex-hint" maxlength="1000" placeholder="Optional">${esc(f.hint)}</textarea></div>`
      : "";
    return `<div class="modal"><div class="sheet">
      <h2>${title}</h2>
      ${f.mode === "session" ? `<div class="muted">Wird sofort in diesem Training absolviert. Beim Beenden entscheidest du, ob sie in den Plan kommt.</div>` : ""}
      <div class="field"><div class="label"><span>Name</span></div>
        <input type="text" data-input="ex-name" value="${esc(f.name)}" placeholder="z. B. Wadenheben" maxlength="100" autocomplete="off">
        <div class="suggest">${this._suggestions()}</div></div>
      <div class="field"><div class="label"><span>Art</span></div><div class="kinds">${kinds}</div>
        ${f.kindLocked ? `<div class="hint">Die Art ist nicht änderbar, weil es bereits Einträge zu dieser Übung gibt.</div>` : ""}</div>
      ${fields}
      <button class="toggle" data-action="ex-more">${f.more ? "▾" : "▸"} Weitere Angaben (Gruppe, Geräteeinstellung, Ausführung)</button>
      ${more}
      <div class="field stack">
        <button class="btn primary big" data-action="ex-save">${f.mode === "session" ? "Hinzufügen und starten" : "Speichern"}</button>
        <button class="btn" data-action="ex-cancel">Abbrechen</button>
      </div></div></div>`;
  }

  _openExForm(mode, ex = null) {
    const src = ex || NEW_EXERCISE;
    this._exForm = {
      mode,
      id: ex?.id ?? null,
      name: ex?.name ?? "",
      kind: src.kind,
      kindLocked: Boolean(ex?.entry_count),
      target_sets: src.target_sets,
      reps_min: src.reps_min ?? NEW_EXERCISE.reps_min,
      reps_max: src.reps_max ?? NEW_EXERCISE.reps_max,
      duration_s: src.duration_s ?? NEW_EXERCISE.duration_s,
      start_weight: src.start_weight ?? NEW_EXERCISE.start_weight,
      lastWeight: ex?.last_weight ?? null,
      lastDate: ex?.last_date ?? null,
      group: ex?.muscle_group ?? "",
      groupNew: false,
      setting: ex?.setting ?? "",
      hint: ex?.hint ?? "",
      more: Boolean(ex && (ex.setting || ex.hint)),
    };
    this._renderModal();
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
      perSet: null,
    };
    if (ex.duration_s !== null) {
      // Measured sets (stopwatch) or an entry with different times per set: one row per set.
      const measured = context === "workout" && !src ? this._sw(ex.id).sets : [];
      if (measured.length) this._form.perSet = [...measured];
      else if (src && differentSeconds(src)) this._form.perSet = differentSeconds(src);
    }
    this._renderModal();
  }

  _perSetFields(f) {
    const rows = f.perSet
      .map((sec, i) => `<div class="setrow"><span class="no">Satz ${i + 1}</span>
        <div class="stepper">
          <button data-action="form-set-step" data-i="${i}" data-dir="-1" aria-label="Satz ${i + 1} kürzer">−</button>
          <div class="val">${sec} <small>Sek.</small></div>
          <button data-action="form-set-step" data-i="${i}" data-dir="1" aria-label="Satz ${i + 1} länger">+</button>
        </div>
        <button class="btn slim" data-action="form-set-del" data-i="${i}" aria-label="Satz ${i + 1} entfernen">✕</button></div>`)
      .join("");
    return `<div class="field"><div class="label"><span>Sätze · Sekunden</span><span>gemessen, änderbar</span></div>
      ${rows || `<div class="small muted">Kein Satz – bitte mindestens einen Satz hinzufügen.</div>`}
      <button class="btn slim" style="margin-top:10px" data-action="form-set-add">+ Satz</button></div>`;
  }

  _act_form_set_step({ i, dir }) {
    const f = this._form;
    f.perSet[Number(i)] = Math.max(0, Math.min(3600, f.perSet[Number(i)] + Number(dir) * 5));
    this._renderModal();
  }

  _act_form_set_del({ i }) {
    this._form.perSet.splice(Number(i), 1);
    this._renderModal();
  }

  _act_form_set_add() {
    const f = this._form;
    if (f.perSet.length < 20) f.perSet.push(f.perSet[f.perSet.length - 1] ?? f.seconds ?? 30);
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
    if (ev.target.dataset?.change === "sw-countdown") {
      this._prefs = { ...(this._prefs || {}), countdown: ev.target.checked };
      this._savePrefs();
      return;
    }
    if (ev.target.dataset?.change === "exercise") {
      this._exerciseId = Number(ev.target.value);
      this._run(() => this._loadStats());
    }
  }

  _onInput(ev) {
    const kind = ev.target.dataset?.input;
    if (kind === "form-note" && this._form) this._form.note = ev.target.value;
    if (kind === "session-note") this._noteDraft = ev.target.value;
    if (kind === "plan-name" && this._planForm) this._planForm.name = ev.target.value;
    if (kind === "cf-minutes" && this._cf) {
      const value = parseInt(ev.target.value, 10);
      this._cf.minutes = Number.isNaN(value) ? null : value;
      // Only the button is updated, so the field keeps the focus while typing.
      const valid = this._cf.device && this._cf.minutes >= 1 && this._cf.minutes <= 180;
      const button = this.shadowRoot.querySelector('[data-action="cf-save"]');
      if (button) button.disabled = !valid;
    }
    const f = this._exForm;
    if (!f || !kind?.startsWith("ex-")) return;
    if (kind === "ex-name") {
      f.name = ev.target.value;
      // Only the suggestions are redrawn, so the text field keeps the focus.
      this.shadowRoot.querySelector(".suggest").innerHTML = this._suggestions();
    }
    if (kind === "ex-group-text") f.group = ev.target.value;
    if (kind === "ex-setting") f.setting = ev.target.value;
    if (kind === "ex-hint") f.hint = ev.target.value;
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
    if (idx < 0) this._afterLastExercise();
    else this._show("exercise", idx);
  }

  /* Phases of the cardio steps already passed in this session (kept while the panel is open). */
  _cardioPassed(phase) {
    return this._passed?.session === this._session.id && this._passed[phase];
  }

  _passCardio(phase) {
    if (this._passed?.session !== this._session.id) this._passed = { session: this._session.id };
    this._passed[phase] = true;
  }

  _showCardio(phase) {
    this._cardioPhase = phase;
    this._cf = { context: "workout", phase, device: null, minutes: null, editId: null };
    this._show("cardio");
  }

  /* After the last exercise: cool-down cardio once, then the overview. */
  _afterLastExercise() {
    if (!this._session.cardio.cooldown.length && !this._cardioPassed("cooldown")) this._showCardio("cooldown");
    else this._show("list");
  }

  _strengthStart() {
    const s = this._session;
    if (s.warmup && !s.warmup_done && !s.items.some((i) => i.entry)) this._show("warmup");
    else this._gotoNextOpen(0);
  }

  _enterWorkout() {
    const s = this._session;
    const fresh = !s.warmup_done && !s.items.some((i) => i.entry) && !s.cardio.warmup.length && !s.cardio.cooldown.length;
    if (fresh && !this._cardioPassed("warmup")) this._showCardio("warmup");
    else this._strengthStart();
  }

  _act_cardio_next() {
    const phase = this._cardioPhase;
    this._passCardio(phase);
    this._cf = null;
    if (phase === "warmup") this._strengthStart();
    else this._show("list");
  }

  _act_cardio_open({ phase }) {
    this._showCardio(phase);
  }

  _cfRender() {
    if (this._cf?.context === "history") this._renderModal();
    else this._render();
  }

  _act_cf_device({ device }) {
    this._cf.device = device;
    this._cfRender();
  }

  _act_cf_step({ dir }) {
    const f = this._cf;
    f.minutes = Math.max(1, Math.min(180, (f.minutes ?? 0) + Number(dir)));
    this._cfRender();
  }

  _act_cf_edit({ id }) {
    const context = this._tab === "history" ? "history" : "workout";
    const session = context === "history" ? this._detail : this._session;
    const entry = [...session.cardio.warmup, ...session.cardio.cooldown].find((c) => c.id === Number(id));
    this._cf = { context, phase: entry.phase, device: entry.device, minutes: entry.minutes, editId: entry.id, sessionId: session.id };
    this._cfRender();
  }

  _act_cf_cancel() {
    const f = this._cf;
    this._cf = f.context === "history" ? null : { context: "workout", phase: f.phase, device: null, minutes: null, editId: null };
    this._cfRender();
  }

  _act_cf_add_history({ phase }) {
    this._cf = { context: "history", phase, device: null, minutes: null, editId: null, sessionId: this._detail.id };
    this._renderModal();
  }

  _applyCardioResult(session) {
    if (this._tab === "history") {
      this._detail = session;
      this._cf = null;
      this._renderModal();
    } else {
      this._session = session;
      this._cf = { context: "workout", phase: this._cardioPhase, device: null, minutes: null, editId: null };
    }
    this._render();
  }

  _act_cf_save() {
    const f = this._cf;
    this._run(async () => {
      const session = f.editId
        ? await this._call("cardio/update", { cardio_id: f.editId, device: f.device, minutes: f.minutes })
        : await this._call("cardio/add", {
            session_id: f.context === "history" ? f.sessionId : this._session.id,
            phase: f.phase,
            device: f.device,
            minutes: f.minutes,
            uuid: uuid(),
          });
      this._applyCardioResult(session);
      this._toast("Gespeichert");
    });
  }

  _act_cf_delete({ id }) {
    const remove = async () => this._applyCardioResult(await this._call("cardio/delete", { cardio_id: Number(id) }));
    if (this._tab === "history") {
      this._ask({ title: "Cardio-Eintrag löschen?", text: "Der Eintrag wird aus der Einheit entfernt.", yes: "Löschen", danger: true, run: remove });
    } else this._run(remove);
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
    if (this._idx >= this._session.items.length - 1) this._afterLastExercise();
    else this._show("exercise", this._idx + 1);
  }

  _act_rec_decide({ v }) {
    const item = this._session.items[this._idx];
    this._run(async () => {
      this._session = await this._call("recommendation/decide", { session_id: this._session.id, exercise_id: item.exercise.id, decision: v });
      this._render();
      if (v === "accepted") this._toast(item.recommendation.rule === "sets" ? "Plan auf 3 Sätze geändert" : "Vorbelegung angepasst");
    });
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
      if (f.perSet) payload.seconds_per_set = f.perSet;
      else if (f.timed) payload.seconds = f.seconds;
      else payload.reps = f.reps;
      if (f.weighted) payload.weight = f.weight;
      const result = await this._call("entry/save", payload);
      this._form = null;
      if (f.context === "workout") {
        this._swForget(f.exerciseId);
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
    const { run, then } = this._confirm;
    this._confirm = null;
    this._renderModal();
    if (then) then();
    else this._run(run);
  }

  _finish(adopt) {
    this._run(async () => {
      this._summary = await this._call("session/finish", { session_id: this._session.id, adopt });
      this._swClearSession();
      this._session = null;
      this._adopt = null;
      this._show("summary");
      this._ov = await this._call("overview");
    });
  }

  /* Spontaneous exercises with an entry need a decision before the session ends; untouched ones are dropped. */
  _askAdopt() {
    const items = this._session.items.filter((i) => i.adhoc && i.entry).map((i) => ({ id: i.exercise.id, name: i.exercise.name }));
    if (!items.length) {
      this._finish([]);
      return;
    }
    this._adopt = { items, choice: {} };
    this._renderModal();
  }

  _act_finish() {
    const open = this._session.items.filter((i) => !i.entry).length;
    if (!open) this._askAdopt();
    else {
      this._ask({
        title: "Training beenden?",
        text: open === 1 ? "Eine Übung ist noch offen." : `${open} Übungen sind noch offen.`,
        yes: "Trotzdem beenden",
        then: () => this._askAdopt(),
      });
    }
  }

  _act_adopt_set({ id, v }) {
    this._adopt.choice[id] = v;
    this._renderModal();
  }

  _act_adopt_cancel() {
    this._adopt = null;
    this._renderModal();
  }

  _act_adopt_finish() {
    const a = this._adopt;
    this._finish(a.items.filter((item) => a.choice[item.id] === "yes").map((item) => item.id));
  }

  /* ---------------------------------------------------- plan creation */

  _createPlan(data) {
    this._run(async () => {
      await this._call("plan/create", data);
      this._planForm = null;
      this._view = "home";
      this._applyOverview(await this._call("overview"));
      this._toast("Plan angelegt");
    });
  }

  _act_plan_template() {
    this._createPlan({ template: true });
  }

  _act_plan_form() {
    this._planForm = { name: "", freq_min: 2, freq_max: 3 };
    this._render();
    this.shadowRoot.querySelector('[data-input="plan-name"]')?.focus();
  }

  _act_plan_form_cancel() {
    this._planForm = null;
    this._render();
  }

  _act_plan_step({ field, dir }) {
    const f = this._planForm;
    f[field] = Math.max(1, Math.min(14, f[field] + Number(dir)));
    if (field === "freq_min" && f.freq_min > f.freq_max) f.freq_max = f.freq_min;
    if (field === "freq_max" && f.freq_max < f.freq_min) f.freq_min = f.freq_max;
    this._render();
  }

  _act_plan_create_empty() {
    const f = this._planForm;
    if (!f.name.trim()) {
      this._toast("Bitte einen Namen für den Plan eingeben.", true);
      return;
    }
    this._createPlan({ name: f.name, freq_min: f.freq_min, freq_max: f.freq_max });
  }

  /* ---------------------------------------------------- plan editing */

  _act_plan() {
    this._show("plan");
  }

  _planExercise(id) {
    const plan = this._ov.plans[0];
    return plan.exercises.concat(plan.archived_exercises).find((e) => e.id === Number(id));
  }

  /* Run a plan command; all of them answer with the updated plans. */
  _planCall(type, data, message = "") {
    this._run(async () => {
      this._ov.plans = await this._call(type, data);
      this._exForm = null;
      this._render();
      if (message) this._toast(message);
    });
  }

  _act_ex_add() {
    this._openExForm("add");
  }

  _act_ex_add_session() {
    this._openExForm("session");
  }

  _act_ex_edit({ id }) {
    this._openExForm("edit", this._planExercise(id));
  }

  _act_ex_move({ id, dir }) {
    this._planCall("exercise/move", { exercise_id: Number(id), direction: dir });
  }

  _act_group_move({ group, dir }) {
    this._planCall("exercise/move_group", { group, direction: dir });
  }

  _act_ex_archive({ id }) {
    const ex = this._planExercise(id);
    this._ask({
      title: `„${ex.name}“ archivieren?`,
      text: "Die Übung erscheint nicht mehr im Training. Verlauf und Auswertung bleiben erhalten, und sie lässt sich jederzeit wiederherstellen.",
      yes: "Archivieren",
      danger: true,
      then: () => this._planCall("exercise/archive", { exercise_id: ex.id }, "Übung archiviert"),
    });
  }

  _act_ex_restore({ id }) {
    this._planCall("exercise/restore", { exercise_id: Number(id) }, "Übung wiederhergestellt");
  }

  _act_arch_toggle() {
    this._archOpen = !this._archOpen;
    this._render();
  }

  _act_ex_kind({ kind }) {
    this._exForm.kind = kind;
    this._renderModal();
  }

  _act_ex_step({ field, dir }) {
    const f = this._exForm;
    const step = { target_sets: 1, reps_min: 1, reps_max: 1, duration_s: 5, start_weight: this._step }[field];
    const [low, high] = { target_sets: [1, 20], reps_min: [1, 999], reps_max: [1, 999], duration_s: [5, 3600], start_weight: [0.5, 1000] }[field];
    f[field] = Math.max(low, Math.min(high, Math.round((f[field] + Number(dir) * step) * 100) / 100));
    // Keep the range valid: moving one end past the other drags it along.
    if (field === "reps_min" && f.reps_min > f.reps_max) f.reps_max = f.reps_min;
    if (field === "reps_max" && f.reps_max < f.reps_min) f.reps_min = f.reps_max;
    this._renderModal();
  }

  _act_ex_more() {
    this._exForm.more = !this._exForm.more;
    this._renderModal();
  }

  _act_ex_group({ group }) {
    this._exForm.group = group;
    this._exForm.groupNew = false;
    this._renderModal();
  }

  _act_ex_group_new() {
    this._exForm.group = "";
    this._exForm.groupNew = true;
    this._renderModal();
    this.shadowRoot.querySelector('[data-input="ex-group-text"]')?.focus();
  }

  _act_ex_cancel() {
    this._exForm = null;
    this._renderModal();
  }

  /* A spontaneous exercise joins the running session and is opened right away. */
  _addToSession(data) {
    this._run(async () => {
      const result = await this._call("session/add_exercise", { session_id: this._session.id, ...data });
      this._session = result.session;
      this._exForm = null;
      this._show("exercise", this._session.items.findIndex((i) => i.exercise.id === result.exercise_id));
      this._call("overview").then((ov) => (this._ov = ov)).catch(() => {});
    });
  }

  _act_ex_suggest({ id }) {
    if (this._exForm.mode === "session") this._addToSession({ exercise_id: Number(id) });
    else this._planCall("exercise/restore", { exercise_id: Number(id) }, "Übung wiederhergestellt");
  }

  _act_ex_save() {
    const f = this._exForm;
    if (!f.name.trim()) {
      this._toast(this._errorText({ code: "name_required" }), true);
      return;
    }
    const data = {
      name: f.name,
      kind: f.kind,
      target_sets: f.target_sets,
      muscle_group: f.group,
      setting: f.setting,
      hint: f.hint,
    };
    if (f.kind === "time") data.duration_s = f.duration_s;
    else Object.assign(data, { reps_min: f.reps_min, reps_max: f.reps_max });
    if (f.kind === "weight") data.start_weight = f.start_weight;
    if (f.mode === "session") this._addToSession(data);
    else if (f.mode === "edit") this._planCall("exercise/update", { exercise_id: f.id, ...data }, "Gespeichert");
    else this._planCall("exercise/add", data, "Übung hinzugefügt");
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
        this._swClearSession();
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
