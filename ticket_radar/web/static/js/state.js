// UI state: current query + filters, mirrored into the URL hash so searches can be bookmarked.
import { localDayToUtc } from "./format.js";

export const PAGE_SIZE = 50;

export const state = {
  q: "",
  mode: "smart",
  from: "",
  to: "",
  status: "",
  sort: "relevance",
  groups: new Set(),
  tables: new Set(),
  prios: new Set(),
  offset: 0,
};

/** Server status (/api/status), refreshed after syncs. */
export const app = { info: null, lastResult: null };

const SCALARS = ["q", "mode", "from", "to", "status", "sort"];
const SETS = ["groups", "tables", "prios"];
const DEFAULTS = { mode: "smart", sort: "relevance" };

export function saveToHash() {
  const p = new URLSearchParams();
  for (const k of SCALARS) if (state[k] && state[k] !== DEFAULTS[k]) p.set(k, state[k]);
  for (const k of SETS) if (state[k].size) p.set(k, [...state[k]].join("|"));
  history.replaceState(null, "", `#${p}`);
}

export function loadFromHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  for (const k of SCALARS) if (p.get(k)) state[k] = p.get(k);
  for (const k of SETS) if (p.get(k)) state[k] = new Set(p.get(k).split("|"));
}

export function toggle(set, value) {
  set.has(value) ? set.delete(value) : set.add(value);
}

/** Query string for /api/search and /api/export.csv. */
export function toParams({ paged = true } = {}) {
  const p = new URLSearchParams({
    q: state.q,
    mode: state.mode,
    sort: state.sort,
    status: state.status,
    from: localDayToUtc(state.from, false),
    to: localDayToUtc(state.to, true),
    groups: [...state.groups].join("|"),
    tables: [...state.tables].join("|"),
    priorities: [...state.prios].join("|"),
  });
  if (paged) {
    p.set("limit", PAGE_SIZE);
    p.set("offset", state.offset);
  }
  return p.toString();
}

export const tableLabel = (t) => app.info?.table_labels?.[t] || t;
