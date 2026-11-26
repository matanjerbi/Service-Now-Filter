// Formatting / escaping helpers. All timestamps from the server are UTC "YYYY-MM-DD HH:MM:SS".
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

// The server marks matches with \x01…\x02; escape first, then turn markers into <mark>.
export const highlight = (s) => esc(s).replace(/\x01/g, "<mark>").replace(/\x02/g, "</mark>");

export const parseUtc = (s) => (s ? new Date(s.replace(" ", "T") + "Z") : null);

export const num = (n) => Number(n ?? 0).toLocaleString("he-IL");

export function fmtDateTime(s) {
  const d = parseUtc(s);
  return d ? d.toLocaleString("he-IL", { dateStyle: "short", timeStyle: "short" }) : "—";
}

const rtf = new Intl.RelativeTimeFormat("he", { numeric: "auto" });
export function relative(s) {
  const d = parseUtc(s);
  if (!d) return "";
  const sec = (d - Date.now()) / 1000;
  const abs = Math.abs(sec);
  if (abs < 3600) return rtf.format(Math.round(sec / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(sec / 3600), "hour");
  if (abs < 86400 * 45) return rtf.format(Math.round(sec / 86400), "day");
  if (abs < 86400 * 400) return rtf.format(Math.round(sec / 86400 / 30), "month");
  return rtf.format(Math.round(sec / 86400 / 365), "year");
}

/** Local calendar day ("YYYY-MM-DD") → UTC bound string for the API. */
export function localDayToUtc(day, endOfDay) {
  if (!day) return "";
  const d = new Date(`${day}T${endOfDay ? "23:59:59" : "00:00:00"}`);
  return d.toISOString().slice(0, 19).replace("T", " ");
}

export const isoDay = (d) => d.toISOString().slice(0, 10);

export function priorityClass(p) {
  const m = String(p ?? "").match(/^(\d)/);
  return m ? `p${m[1]}` : "";
}

export const NO_GROUP = "__none__";
export const groupLabel = (g) => (g === NO_GROUP || !g ? "(ללא צוות)" : g);
