// Entry point: wires state, API and views together.
import { api } from "./api.js";
import { $, $$, setActive, toast } from "./dom.js";
import { esc, isoDay, num, relative } from "./format.js";
import { PAGE_SIZE, app, loadFromHash, saveToHash, state, toggle, toParams } from "./state.js";
import { closeDrawer, openTicket } from "./views/drawer.js";
import { renderFacets } from "./views/facets.js";
import { renderMoreButton, renderResults, renderSummary } from "./views/results.js";
import { closeSync, initSync, openSync } from "./views/sync.js";

const SEARCH_DEBOUNCE_MS = 280;
let requestSeq = 0;

// ---------------------------------------------------------------- search
async function runSearch({ append = false } = {}) {
  if (!append) state.offset = 0;
  saveToHash();
  const seq = ++requestSeq;
  const list = $("#results");
  list.classList.add("loading");
  let result;
  try {
    result = await api.search(toParams());
  } catch (e) {
    toast(e.message);
    return;
  } finally {
    if (seq === requestSeq) list.classList.remove("loading");
  }
  if (seq !== requestSeq) return; // a newer search superseded this one
  app.lastResult = result;
  renderSummary(result);
  if (result.facets) renderFacets(result.facets);
  renderResults(result.results, { append });
  renderMoreButton(state.offset + result.results.length, result.total);
}

async function refreshStatus() {
  app.info = await api.status();
  const { db, demo, instance, fts } = app.info;
  $("#status").innerHTML = `<span class="dot ${demo ? "warn" : ""}"></span>
    ${demo ? "מצב דמו" : esc(instance.replace(/^https?:\/\//, ""))} ·
    ${num(db.count)} טיקטים${db.last_sync ? ` · סונכרן ${esc(relative(db.last_sync))}` : ""}`;
  if (!fts) toast("שים לב: SQLite בלי FTS5 – החיפוש יעבוד במצב פשוט");
}

// ---------------------------------------------------------------- filters → state
function setDateRange(days) {
  if (!days) {
    state.from = state.to = "";
  } else {
    const from = new Date();
    from.setDate(from.getDate() - days);
    state.from = isoDay(from);
    state.to = "";
  }
  $("#from").value = state.from;
  $("#to").value = state.to;
}

function syncControlsFromState() {
  $("#q").value = state.q;
  $("#from").value = state.from;
  $("#to").value = state.to;
  $("#sort").value = state.sort;
  setActive($$("#mode button"), (b) => b.dataset.v === state.mode);
  setActive($$("#status-seg button"), (b) => b.dataset.v === state.status);
  if (!state.from && !state.to) $('[data-range="0"]').classList.add("on");
}

// ---------------------------------------------------------------- events
function bindEvents() {
  let debounce;
  const q = $("#q");
  q.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.q = q.value;
      runSearch();
    }, SEARCH_DEBOUNCE_MS);
  });
  q.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    clearTimeout(debounce);
    state.q = q.value;
    runSearch();
  });

  $("#mode").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.mode = b.dataset.v;
    setActive($$("#mode button"), (x) => x === b);
    runSearch();
  });
  $("#status-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.status = b.dataset.v;
    setActive($$("#status-seg button"), (x) => x === b);
    runSearch();
  });
  $("#sort").addEventListener("change", (e) => {
    state.sort = e.target.value;
    runSearch();
  });
  for (const key of ["from", "to"]) {
    $(`#${key}`).addEventListener("change", (e) => {
      state[key] = e.target.value;
      $$("[data-range]").forEach((p) => p.classList.remove("on"));
      runSearch();
    });
  }
  $$("[data-range]").forEach((pill) =>
    pill.addEventListener("click", () => {
      setActive($$("[data-range]"), (x) => x === pill);
      setDateRange(Number(pill.dataset.range));
      runSearch();
    })
  );

  $("#groups").addEventListener("click", (e) => {
    const row = e.target.closest("[data-group]");
    if (!row) return;
    e.preventDefault();
    toggle(state.groups, row.dataset.group);
    runSearch();
  });
  $("#gclear").addEventListener("click", () => {
    state.groups.clear();
    runSearch();
  });
  $("#gq").addEventListener("input", () => app.lastResult?.facets && renderFacets(app.lastResult.facets));
  $("#types").addEventListener("click", (e) => {
    const pill = e.target.closest("[data-table]");
    if (!pill) return;
    toggle(state.tables, pill.dataset.table);
    runSearch();
  });
  $("#prios").addEventListener("click", (e) => {
    const pill = e.target.closest("[data-prio]");
    if (!pill) return;
    toggle(state.prios, pill.dataset.prio);
    runSearch();
  });

  $("#more").addEventListener("click", () => {
    state.offset += PAGE_SIZE;
    runSearch({ append: true });
  });
  $("#btnSync").addEventListener("click", openSync);
  $("#btnExport").addEventListener("click", () => {
    location.href = api.exportUrl(toParams({ paged: false }));
  });
  $("#scrim").addEventListener("click", closeDrawer);

  // Delegated actions (cards, drawer buttons, empty-state button).
  document.addEventListener("click", (e) => {
    const action = e.target.closest("[data-action]");
    if (action) {
      const name = action.dataset.action;
      if (name === "close-drawer") closeDrawer();
      else if (name === "open-sync") openSync();
      else if (name === "copy") navigator.clipboard?.writeText(action.dataset.value).then(() => toast("הועתק"));
      return;
    }
    const card = e.target.closest("[data-ticket]");
    if (card && !e.target.closest("a")) openTicket(card.dataset.ticket);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
      e.preventDefault();
      $("#q").focus();
    } else if (e.key === "Escape") {
      closeDrawer();
      closeSync();
    } else if (e.key === "Enter" && document.activeElement?.dataset?.ticket) {
      openTicket(document.activeElement.dataset.ticket);
    }
  });
}

// ---------------------------------------------------------------- boot
async function boot() {
  loadFromHash();
  syncControlsFromState();
  bindEvents();
  initSync({
    onDone: async () => {
      await refreshStatus();
      await runSearch();
    },
  });
  try {
    await refreshStatus();
  } catch (e) {
    toast(e.message);
    return;
  }
  await runSearch();
  if (app.info.sync?.running) openSync();
  $("#q").focus();
}

boot();
