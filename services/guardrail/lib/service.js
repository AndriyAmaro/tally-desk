"use strict";
/*
 * The three endpoints of the guardrails service as pure functions: request in ({method, headers, body}), answer out ({status, headers, body}).
 * worker.js adapts them to Cloudflare Workers (fetch), the files in api/ to Vercel's (req, res). Web-standard JavaScript only. Nothing here logs: the text under test and the token never reach a log line.
 */
const { PROFILE, GUARDRAIL_NAME, THRESHOLD, validate } = require("./detector");
const { expectedToken, bearerMatches } = require("./auth");

const MAX_BODY_BYTES = 1000000; // 1 MB of JSON; a larger body is refused with 413 before it is parsed

const reply = (status, body, headers = {}) => ({ status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers }, body });
const fail = (status, detail, headers) => reply(status, { detail }, headers);

/** 503 when GUARD_TOKEN is missing, 401 when the bearer token is wrong or absent, else null. */
function authorize(request, env) {
  const expected = expectedToken(env);
  if (expected === null) return fail(503, "the service is not configured: GUARD_TOKEN is not set");
  const header = request.headers && (request.headers.authorization || request.headers.Authorization);
  if (!bearerMatches(header, expected)) return fail(401, "missing or wrong bearer token", { "WWW-Authenticate": "Bearer" });
  return null;
}

const TOO_LARGE = Symbol("too large");

function parseJson(raw) {
  if (raw && typeof raw === "object") return raw; // the platform already parsed it
  const text = typeof raw === "string" ? raw : "";
  if (text.length > MAX_BODY_BYTES || new TextEncoder().encode(text).length > MAX_BODY_BYTES) return TOO_LARGE;
  try { return JSON.parse(text); } catch (_) { return undefined; }
}

function health(request, env) {
  if (request.method !== "GET" && request.method !== "HEAD") return fail(405, "use GET", { Allow: "GET, HEAD" });
  const configured = expectedToken(env) !== null;
  return reply(configured ? 200 : 503, { status: configured ? "ok" : "unconfigured", service: "tally-guardrail", profile: PROFILE, threshold: THRESHOLD });
}

function profiles(request, env) {
  const denied = authorize(request, env);
  if (denied) return denied;
  if (request.method !== "GET") return fail(405, "use GET", { Allow: "GET" });
  return reply(200, [{ name: PROFILE, guardrail_name: GUARDRAIL_NAME }]);
}

function validateRequest(request, env) {
  const denied = authorize(request, env);
  if (denied) return denied;
  if (request.method !== "POST") return fail(405, "use POST", { Allow: "POST" });
  const body = parseJson(request.body);
  if (body === TOO_LARGE) return fail(413, `the body is larger than ${MAX_BODY_BYTES} bytes`);
  if (body === undefined || body === null || typeof body !== "object" || Array.isArray(body)) return fail(400, "the body must be a JSON object");
  if (typeof body.input_text !== "string") return fail(422, "input_text must be a string");
  if (body.profile !== PROFILE) return fail(404, `unknown profile; this service serves '${PROFILE}'`);
  return reply(200, { profile: PROFILE, result: validate(body.input_text) });
}

module.exports = { MAX_BODY_BYTES, health, profiles, validateRequest, authorize };
