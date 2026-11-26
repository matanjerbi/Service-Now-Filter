// Thin wrapper around the local JSON API.
async function request(url, options = {}) {
  let res;
  try {
    res = await fetch(url, options);
  } catch {
    throw new Error("אין חיבור לשרת המקומי – האם run.py עדיין רץ?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const post = (url, body) =>
  request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  status: () => request("/api/status"),
  search: (params) => request(`/api/search?${params}`),
  ticket: (id) => request(`/api/ticket?id=${encodeURIComponent(id)}`),
  similar: (id) => request(`/api/similar?id=${encodeURIComponent(id)}`),
  exportUrl: (params) => `/api/export.csv?${params}`,
  syncStatus: () => request("/api/sync/status"),
  startSync: (body) => post("/api/sync", body),
  testConnection: () => post("/api/test"),
};
