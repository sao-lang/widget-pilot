/**
 * M02 apps/bff 占位服务（§4.1.3【契约约定】路径）。
 * 硬约束（§2.2）：不含 user_id 鉴权判定、不签发长期权限、ticket 短期有效。
 * 统一错误映射 mapErrorToResponse：code ∈ {"OK"} ∪ ReasonCode ∪ HttpReasonCode。
 */
import { createServer } from "node:http";

const PORT = Number(process.env.BFF_PORT ?? 8010);
const TICKET_TTL_MS = 60_000; // 短期 ticket（60s），绑定 session_id

function mapErrorToResponse(err: unknown, traceId: string) {
  // 统一错误映射（M02 §4.4）：仅映射，不判定权限
  const message = err instanceof Error ? err.message : "internal error";
  return { code: "MODEL_ERROR", message, traceId, retryable: true };
}

function issueSseTicket(sessionId: string) {
  // BFF 只换票（JWT session → 短期 ticket）；真正鉴权在 M04 服务端按 user_id 裁决
  const ticket = Buffer.from(
    JSON.stringify({ session_id: sessionId, exp: Date.now() + TICKET_TTL_MS }),
  ).toString("base64url");
  return { ticket, expires_at: new Date(Date.now() + TICKET_TTL_MS).toISOString(), events_url: `/api/sessions/${sessionId}/events` };
}

createServer((req, res) => {
  const traceId = crypto.randomUUID();
  res.setHeader("content-type", "application/json");
  if (req.url === "/api/bff/health") {
    res.end(JSON.stringify({ code: "OK", message: "bff ok", traceId, retryable: false }));
  } else if (req.url === "/api/bff/sse-ticket" && req.method === "POST") {
    // 占位：真实 JWT session 校验产生 401（M04）
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      try {
        const { session_id } = JSON.parse(body || "{}");
        if (!session_id) throw new Error("session_id required");
        res.end(JSON.stringify({ code: "OK", message: "ok", traceId, retryable: false, data: issueSseTicket(session_id) }));
      } catch (e) {
        res.statusCode = 400;
        res.end(JSON.stringify(mapErrorToResponse(e, traceId)));
      }
    });
  } else {
    res.statusCode = 404;
    res.end(JSON.stringify({ code: "INPUT_INVALID", message: "not found", traceId, retryable: false }));
  }
}).listen(PORT, () => console.log(`bff placeholder listening :${PORT}（不做鉴权判定）`));
