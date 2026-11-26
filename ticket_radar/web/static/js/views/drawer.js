// Ticket details drawer with similar tickets.
import { api } from "../api.js";
import { $, toast } from "../dom.js";
import { esc, fmtDateTime } from "../format.js";
import { app, tableLabel } from "../state.js";
import { priorityBadge, statusBadge, ticketCard } from "./results.js";

function serviceNowLink(ticket) {
  const base = app.info?.instance;
  if (!base) return null;
  const target = `${ticket.sys_class || ticket.tbl}.do?sys_id=${ticket.sys_id}`;
  return `${base}/nav_to.do?uri=${encodeURIComponent(target)}`;
}

function fieldGrid(t) {
  const fields = [
    ["צוות", t.group_name],
    ["מטפל", t.assigned_to],
    ["פותח / מדווח", t.opened_by],
    ["רכיב (CI)", t.cmdb_ci],
    ["קטגוריה", t.category],
    ["נפתח", fmtDateTime(t.created_on)],
    ["עודכן", fmtDateTime(t.updated_on)],
    ["נסגר", t.closed_at ? fmtDateTime(t.closed_at) : ""],
  ];
  return fields
    .filter(([, v]) => v)
    .map(([k, v]) => `<div><label>${k}</label><span dir="auto">${esc(v)}</span></div>`)
    .join("");
}

const textBlock = (title, text) =>
  text ? `<div class="sec">${title}</div><div class="txt" dir="auto">${esc(text)}</div>` : "";

export async function openTicket(id) {
  let t;
  try {
    t = await api.ticket(id);
  } catch (e) {
    toast(e.message);
    return;
  }
  const link = serviceNowLink(t);
  $("#dh").innerHTML = `
    <div style="flex:1">
      <div class="head" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span class="num">${esc(t.number)}</span>
        <span class="type">${esc(tableLabel(t.tbl))}</span>
        ${statusBadge(t)} ${priorityBadge(t.priority)}
      </div>
      <h2 dir="auto">${esc(t.short_description)}</h2>
    </div>
    <button class="x" data-action="close-drawer" aria-label="סגור">×</button>`;
  $("#dbody").innerHTML = `
    <div class="dactions">
      ${link ? `<a class="btn primary" href="${esc(link)}" target="_blank" rel="noopener">פתח ב-ServiceNow ↗</a>` : ""}
      <button class="btn" data-action="copy" data-value="${esc(t.number)}">העתק מספר</button>
    </div>
    <div class="kv">${fieldGrid(t)}</div>
    ${textBlock("תיאור", t.description)}
    ${textBlock("הערות סגירה / פתרון", t.close_notes)}
    ${textBlock("הערות ועדכונים", t.journal)}
    <div id="simBox"></div>`;
  $("#drawer").classList.add("on");
  $("#scrim").classList.add("on");
  $("#drawer").scrollTop = 0;
  loadSimilar(t.id);
}

async function loadSimilar(id) {
  const box = $("#simBox");
  box.innerHTML = '<div class="sec">טיקטים דומים</div><div class="muted">מחפש…</div>';
  try {
    const r = await api.similar(id);
    box.innerHTML = `
      <div class="sec">טיקטים דומים (${r.results.length})</div>
      <div class="sim-list results" style="margin-top:6px">
        ${r.results.map(ticketCard).join("") || '<div class="muted">לא נמצאו</div>'}
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}

export function closeDrawer() {
  $("#drawer").classList.remove("on");
  $("#scrim").classList.remove("on");
}
