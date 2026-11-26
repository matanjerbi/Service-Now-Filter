// Result list, summary line and ticket cards.
import { $ } from "../dom.js";
import { esc, fmtDateTime, highlight, num, priorityClass, relative } from "../format.js";
import { icons } from "../icons.js";
import { app, tableLabel } from "../state.js";

const MODE_TEXT = {
  all: "התאמה לכל המילים",
  any: "דירוג לפי דמיון",
  similar: "אין התאמה מלאה – מוצגים טיקטים דומים לפי רלוונטיות",
  number: "חיפוש לפי מספר טיקט",
  like: "חיפוש טקסט פשוט",
};

export function priorityBadge(priority, tag = "span") {
  if (!priority) return "";
  return `<${tag} class="prio ${priorityClass(priority)}"><i></i><bdi>${esc(priority)}</bdi></${tag}>`;
}

export function statusBadge(ticket) {
  const open = Number(ticket.active) === 1;
  return `<span class="st ${open ? "open" : ""}"><bdi>${esc(ticket.state || (open ? "פתוח" : "סגור"))}</bdi></span>`;
}

export function ticketCard(t) {
  const open = Number(t.active) === 1;
  const title = t.title_hl ? highlight(t.title_hl) : esc(t.short_description);
  return `
  <article class="card ${open ? "open" : ""}" data-ticket="${t.id}" tabindex="0">
    <div class="head">
      <span class="num">${esc(t.number)}</span>
      <span class="type">${esc(tableLabel(t.tbl))}</span>
      ${statusBadge(t)}
    </div>
    <div class="side-info">
      <div title="${esc(fmtDateTime(t.created_on))}">${esc(relative(t.created_on))}</div>
      ${priorityBadge(t.priority, "div")}
    </div>
    <div class="title" dir="auto">${title}</div>
    ${t.snippet ? `<div class="snip" dir="auto">${highlight(t.snippet)}</div>` : ""}
    <div class="meta">
      <span>${icons.team}${esc(t.group_name || "ללא צוות")}</span>
      ${t.assigned_to ? `<span>${icons.user}${esc(t.assigned_to)}</span>` : ""}
      ${t.cmdb_ci ? `<span>${icons.ci}${esc(t.cmdb_ci)}</span>` : ""}
    </div>
  </article>`;
}

export function renderSummary(result) {
  $("#total").textContent = num(result.total);
  $("#totalSub").textContent = (result.total === 1 ? "טיקט" : "טיקטים") + (result.ms != null ? ` · ${result.ms}ms` : "");
  const badge = $("#modeBadge");
  badge.hidden = !result.mode_used;
  badge.textContent = MODE_TEXT[result.mode_used] || "";
  badge.className = "badge" + (result.mode_used === "similar" ? " sim" : "");
}

function emptyState() {
  const storeEmpty = !app.info?.db?.count;
  return `
  <div class="panel empty">
    ${icons.empty}
    <h2>${storeEmpty ? "עדיין אין טיקטים במאגר" : "לא נמצאו טיקטים"}</h2>
    <div>${storeEmpty
      ? "לחץ על <b>סנכרון</b> כדי למשוך טיקטים מ-ServiceNow לפי טווח תאריכים."
      : "נסה מצב <b>דומה</b>, הסר פילטרים או הרחב את טווח התאריכים."}</div>
    ${storeEmpty ? '<br><button class="btn primary" data-action="open-sync">פתח סנכרון</button>' : ""}
  </div>`;
}

export function renderResults(rows, { append = false } = {}) {
  const el = $("#results");
  if (!append && !rows.length) {
    el.innerHTML = emptyState();
    return;
  }
  const html = rows.map(ticketCard).join("");
  if (append) el.insertAdjacentHTML("beforeend", html);
  else el.innerHTML = html;
}

export function renderMoreButton(shown, total) {
  const btn = $("#more");
  btn.hidden = shown >= total;
  btn.textContent = `טען עוד (${num(total - shown)} נוספים)`;
}
