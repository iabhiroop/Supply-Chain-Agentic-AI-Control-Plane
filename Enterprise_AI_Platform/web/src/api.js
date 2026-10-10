const FALLBACK_LINKS = {
  os: "http://127.0.0.1:8010/",
  gateway: "http://127.0.0.1:8000/admin",
  layer2: "http://127.0.0.1:8001/dashboard",
  eval: "http://127.0.0.1:8002/explorer",
  twin: "http://127.0.0.1:8003/",
  graphrag: "http://127.0.0.1:8100/search",
};

export const PR1001 = "aaaaaaaa-bbbb-cccc-dddd-000000000001";

export const TAIWAN =
  "A major supplier in Taiwan has announced a production shutdown. Which open purchase orders are at risk, and what should procurement do?";

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const err = new Error(data.detail || data.error || res.statusText);
    err.payload = data;
    throw err;
  }
  return data;
}

export function linksOf(stack) {
  return { ...FALLBACK_LINKS, ...(stack?.links || {}) };
}

export function pct(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
}

export function money(v, digits = 6) {
  if (v == null) return "—";
  return `$${Number(v).toFixed(digits)}`;
}

export function dashboardHref(links, transactionId) {
  const base = (links?.layer2 || FALLBACK_LINKS.layer2).replace(/\/$/, "");
  return transactionId ? `${base}?tx=${encodeURIComponent(transactionId)}` : base;
}
