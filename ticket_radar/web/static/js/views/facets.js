// Facet panels: teams sidebar, status counts, ticket types and priorities.
import { $ } from "../dom.js";
import { esc, groupLabel, num } from "../format.js";
import { state, tableLabel } from "../state.js";
import { renderChart } from "./chart.js";

function renderGroups(groups) {
  const filter = $("#gq").value.trim().toLowerCase();
  const max = Math.max(1, ...groups.map((g) => g.count));
  // Keep selected groups visible even when the current filters leave them with 0 tickets.
  const missingSelected = [...state.groups]
    .filter((name) => !groups.some((g) => g.name === name))
    .map((name) => ({ name, count: 0, open: 0 }));
  const list = [...groups, ...missingSelected].filter((g) => !filter || groupLabel(g.name).toLowerCase().includes(filter));

  $("#gcount").textContent = `(${groups.length})`;
  $("#gclear").hidden = !state.groups.size;
  $("#groups").innerHTML =
    list
      .map((g) => {
        const on = state.groups.has(g.name);
        const label = esc(groupLabel(g.name));
        return `
        <label class="g ${on ? "on" : ""}" data-group="${esc(g.name)}">
          <input type="checkbox" ${on ? "checked" : ""}>
          <span class="nm" title="${label}" dir="auto">${label}</span>
          <span class="cnt"><bdi>${num(g.count)}</bdi>${g.open ? `<span class="op"><bdi>${g.open}</bdi> פתוחים</span>` : ""}</span>
          <span class="bar"><b style="width:${(g.open / max) * 100}%"></b><i style="width:${((g.count - g.open) / max) * 100}%"></i></span>
        </label>`;
      })
      .join("") || '<div class="muted" style="padding:10px">אין צוותים</div>';
}

function renderPills(containerSel, items, selected, attr, label) {
  const names = [...new Set([...items.map((x) => x.name), ...selected])];
  $(containerSel).innerHTML = names
    .map((name) => {
      const count = items.find((x) => x.name === name)?.count ?? 0;
      return `<span class="pill ${selected.has(name) ? "on" : ""}" data-${attr}="${esc(name)}"><bdi>${esc(label(name))}</bdi> <span class="n">${num(count)}</span></span>`;
    })
    .join(" ");
}

export function renderFacets(facets) {
  $("#n-all").textContent = num(facets.status.all);
  $("#n-open").textContent = num(facets.status.open);
  $("#n-closed").textContent = num(facets.status.closed);
  renderGroups(facets.groups);
  renderPills("#types", facets.tables, state.tables, "table", tableLabel);
  renderPills("#prios", [...facets.priorities].sort((a, b) => a.name.localeCompare(b.name)), state.prios, "prio", (p) => p);
  renderChart(facets.timeline);
}
