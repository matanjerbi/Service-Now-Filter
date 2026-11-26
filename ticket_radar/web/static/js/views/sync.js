// Sync dialog: connection test, full / incremental sync with live progress.
import { api } from "../api.js";
import { $, $$, toast } from "../dom.js";
import { esc, isoDay, num } from "../format.js";
import { app, tableLabel } from "../state.js";

const SCOPE_TEXT = { all: "כל מה שמותר לי לראות", my_groups: "הקבוצות שלי", groups: "קבוצות נבחרות" };
const POLL_MS = 1000;

let pollTimer = null;
let onFinished = () => {};

export function initSync({ onDone }) {
  onFinished = onDone;
  $("#btnTest").addEventListener("click", testConnection);
  $("#btnFull").addEventListener("click", () => start("full"));
  $("#btnInc").addEventListener("click", () => start("incremental"));
  $("[data-close]").addEventListener("click", closeSync);
  $("#modal").addEventListener("click", (e) => e.target.id === "modal" && closeSync());
}

export function openSync() {
  const info = app.info ?? {};
  $("#modal").classList.add("on");
  $("#connInfo").innerHTML = info.demo
    ? '<span class="err">מצב דמו – אין חיבור ל-ServiceNow. הגדר SN_INSTANCE והפעל בלי --demo.</span>'
    : `מחובר ל-<b class="mono">${esc(info.instance)}</b> · היקף: <b>${esc(SCOPE_TEXT[info.scope?.mode] || "הכל")}</b>`;
  $("#sTables").innerHTML =
    '<span class="flabel">טבלאות:</span> ' +
    (info.tables || [])
      .map((t) => `<label><input type="checkbox" value="${esc(t)}" checked> ${esc(tableLabel(t))} <span class="muted">(${esc(t)})</span></label>`)
      .join(" ");
  if (!$("#sFrom").value) {
    const from = new Date();
    from.setMonth(from.getMonth() - 3);
    $("#sFrom").value = isoDay(from);
    $("#sTo").value = isoDay(new Date());
  }
  $$("#btnTest,#btnFull,#btnInc").forEach((b) => (b.disabled = !!info.demo));
  if (info.sync?.running || info.sync?.log?.length) render(info.sync);
  if (info.sync?.running) poll();
}

export function closeSync() {
  $("#modal").classList.remove("on");
}

async function testConnection() {
  const out = $("#testOut");
  out.textContent = "בודק…";
  try {
    const { results } = await api.testConnection();
    out.innerHTML = results
      .map((r) =>
        r.ok
          ? `<span class="ok">✓ ${esc(tableLabel(r.table))}${r.visible_records != null ? ` (${num(r.visible_records)} גלויים)` : ""}</span>`
          : `<span class="err">✗ ${esc(tableLabel(r.table))}: ${esc(r.error)}</span>`
      )
      .join("<br>");
  } catch (e) {
    out.innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

async function start(mode) {
  const tables = $$("#sTables input:checked").map((i) => i.value);
  if (!tables.length) return toast("בחר לפחות טבלה אחת");
  const body =
    mode === "incremental"
      ? { mode, tables }
      : { mode, tables, from: $("#sFrom").value, to: $("#sTo").value, field: $("input[name=sField]:checked").value };
  try {
    await api.startSync(body);
  } catch (e) {
    return toast(e.message);
  }
  poll();
}

async function poll() {
  clearTimeout(pollTimer);
  let status;
  try {
    status = await api.syncStatus();
  } catch (e) {
    return toast(e.message);
  }
  render(status);
  if (status.running) {
    pollTimer = setTimeout(poll, POLL_MS);
  } else {
    await onFinished();
    if (!status.error && status.finished) toast("הסנכרון הסתיים");
  }
}

function render(s) {
  $("#syncBox").hidden = false;
  $("#syncTitle").innerHTML = s.running
    ? `מסנכרן ${esc(s.table || "")}…`
    : s.error
      ? '<span class="err">הסנכרון נכשל</span>'
      : '<span class="ok">הסתיים ✓</span>';
  $("#syncCount").textContent = s.running
    ? `${num(s.fetched)}${s.total ? ` / ${num(s.total)}` : ""}`
    : s.saved
      ? `${num(s.saved)} טיקטים`
      : "";
  const bar = $("#prog");
  bar.classList.toggle("ind", s.running && !s.total);
  bar.firstElementChild.style.width = s.running ? (s.total ? `${Math.min(100, (s.fetched / s.total) * 100)}%` : "") : "100%";
  const logEl = $("#log");
  logEl.textContent = (s.log || []).join("\n");
  logEl.scrollTop = logEl.scrollHeight;
  $$("#btnFull,#btnInc").forEach((b) => (b.disabled = s.running || app.info?.demo));
}
