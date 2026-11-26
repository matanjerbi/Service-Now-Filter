// Timeline bar chart (SVG). RTL: newest bucket on the left.
import { $ } from "../dom.js";
import { esc } from "../format.js";

const UNIT_LABEL = { day: "לפי יום", week: "לפי שבוע", month: "לפי חודש" };
const WIDTH = 1000;
const HEIGHT = 96;
const AXIS = 16;
const MAX_LABELS = 8;

export function renderChart(timeline) {
  const el = $("#chart");
  const points = timeline?.points ?? [];
  $("#unit").textContent = points.length ? UNIT_LABEL[timeline.unit] || "" : "";
  if (!points.length) {
    el.innerHTML = "";
    return;
  }
  const max = Math.max(...points.map((p) => p[1]));
  const slot = (WIDTH - 10) / points.length;
  const gap = Math.min(4, slot * 0.25);
  const scale = (v) => (v / max) * (HEIGHT - AXIS);

  const bars = points
    .map(([key, total, open], i) => {
      const x = WIDTH - (i + 1) * slot + gap / 2;
      const w = slot - gap;
      return `<g><title>${esc(key)}: ${total} נפתחו, ${open} עדיין פתוחים</title>
        <rect x="${x}" y="${HEIGHT - AXIS - scale(total)}" width="${w}" height="${scale(total)}" rx="2" fill="var(--accent)" opacity=".45"/>
        <rect x="${x}" y="${HEIGHT - AXIS - scale(open)}" width="${w}" height="${scale(open)}" rx="2" fill="var(--open)"/></g>`;
    })
    .join("");

  const every = Math.ceil(points.length / MAX_LABELS);
  const labels = points
    .map(([key], i) => {
      if (i % every !== 0 && i !== points.length - 1) return "";
      const text = timeline.unit === "month" ? key : key.slice(5);
      return `<text class="lbl" x="${WIDTH - (i + 0.5) * slot}" y="${HEIGHT - 3}" text-anchor="middle">${esc(text)}</text>`;
    })
    .join("");

  el.innerHTML = `<svg viewBox="0 0 ${WIDTH} ${HEIGHT}" preserveAspectRatio="none">${bars}${labels}</svg>`;
}
