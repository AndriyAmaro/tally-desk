// Cloudflare Workers entry of the guardrails service. Web-standard Request and Response only: no Node API.
// The detector, the auth and the three endpoints are the modules in lib/, the same files the Node tests and the Vercel functions use.
// `env.GUARD_TOKEN` is a Worker secret (npx wrangler secret put GUARD_TOKEN). Nothing here logs: the text and the token never reach a log line.
import service from "./lib/service.js";

const ROUTES = {
  "/validate": "validate",
  "/validate/validate": "validate", // Otari posts to <url>/validate, so a mandate url that already ends in /validate reaches the same endpoint
  "/profiles": "profiles",
  "/health": "health",
};

function toResponse(answer, method) {
  return new Response(method === "HEAD" ? null : JSON.stringify(answer.body), { status: answer.status, headers: answer.headers });
}

export default {
  async fetch(request, env) {
    const route = ROUTES[new URL(request.url).pathname.replace(/(.)\/+$/, "$1")];
    const method = request.method;
    const settings = { GUARD_TOKEN: env && env.GUARD_TOKEN };
    const head = { method, headers: { authorization: request.headers.get("authorization") } };
    try {
      if (route === "health") return toResponse(service.health(head, settings), method);
      if (route === "profiles") return toResponse(service.profiles(head, settings), method);
      if (route !== "validate") return toResponse({ status: 404, headers: { "Content-Type": "application/json; charset=utf-8" }, body: { detail: "not found" } }, method);
      const denied = service.authorize(head, settings); // the body of an unauthenticated request is never read
      if (denied) return toResponse(denied, method);
      let body = "";
      if (method === "POST") {
        const declared = Number(request.headers.get("content-length") || 0);
        body = declared > service.MAX_BODY_BYTES ? "x".repeat(service.MAX_BODY_BYTES + 1) : await request.text();
      }
      return toResponse(service.validateRequest({ ...head, body }, settings), method);
    } catch (_) { // the error text may hold the request, so it is neither logged nor returned
      return toResponse({ status: 500, headers: { "Content-Type": "application/json; charset=utf-8" }, body: { detail: "internal error" } }, method);
    }
  },
};
