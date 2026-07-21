import http from "node:http";

const status = {
  site_id: "US06-5002",
  source_group_id: 4,
  plus_group_id: 6,
  banned_group_id: 7,
  model: "gpt-5.4",
  running: false,
  settings: { enabled: true, interval_minutes: 15 },
  last_run: {
    status: "completed_with_errors",
    candidates: 8,
    tested: 8,
    eligible: 4,
    promoted: 3,
    banned: 2,
    failed: 3,
    finished_at: "2026-07-21T10:00:00+08:00",
  },
};

const items = [
  [10, "plus +237658217169---jerradmontebello5104@outlook.com", "passed", "promoted", null, 914],
  [11, "plusready@example.com", "rate_limited_but_eligible", "promoted", "API returned 429", 1260],
  [12, "blocked@example.com", "unauthorized_banned", "banned", "API returned 401", 488],
  [13, "free@example.com", "model_not_supported", "not_moved", "API returned 400: The model is not supported when using Codex with a ChatGPT account.", 705],
  [14, "failed@example.com", "failed", "not_moved", "API returned 403", 532],
].map(([id, account_name, classification, action_status, error, latency_ms], index) => ({
  id: `US06-5002:${id}`,
  remote_account_id: id,
  account_name,
  classification,
  action_status,
  error,
  latency_ms,
  tested_at: `2026-07-21T10:00:0${index}+08:00`,
}));

const server = http.createServer((request, response) => {
  response.setHeader("Access-Control-Allow-Origin", "http://127.0.0.1:5173");
  response.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
  response.setHeader("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS");
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  if (request.method === "OPTIONS") return response.end();
  if (request.url === "/api/auth/login") {
    return response.end(JSON.stringify({
      access_token: "visual-test-token",
      user: { id: "visual-test-user", email: "owner@example.com", name: "Visual Test", role: "owner" },
    }));
  }
  if (request.url === "/api/plus-self-produced/status") return response.end(JSON.stringify(status));
  if (request.url?.startsWith("/api/plus-self-produced/results")) {
    return response.end(JSON.stringify({ items, total: items.length, page: 1, page_size: 100 }));
  }
  if (request.url === "/api/plus-self-produced/settings") {
    return response.end(JSON.stringify(status.settings));
  }
  if (request.url === "/api/plus-self-produced/run") {
    return response.end(JSON.stringify({ ok: true, promoted: 3, banned: 2 }));
  }
  if (request.url === "/api/presence/heartbeat" || request.url === "/api/presence/leave") {
    return response.end(JSON.stringify({ ok: true }));
  }
  response.statusCode = 404;
  response.end(JSON.stringify({ detail: "Not found" }));
});

server.listen(4174, "127.0.0.1");
