"use strict";
/* Web-standard JavaScript only (no Node API), so the same file runs in Cloudflare Workers, on Vercel and under the Node test runner. */

/** The token the deployment expects, or null when GUARD_TOKEN is not set (the service then answers 503). */
function expectedToken(env) {
  const token = env && env.GUARD_TOKEN;
  return typeof token === "string" && token.length > 0 ? token : null;
}

const WIDTH = 512; // tokens up to this many UTF-16 units are compared in the same number of steps

/** Compares two strings in constant time: the loop always runs WIDTH steps and never stops at the first difference. */
function sameString(a, b) {
  let diff = a.length ^ b.length;
  if (a.length > WIDTH || b.length > WIDTH) diff |= 1;
  for (let i = 0; i < WIDTH; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

/** Whether an Authorization header carries `Bearer <expected>` (scheme case-insensitive). */
function bearerMatches(header, expected) {
  const match = typeof header === "string" ? /^Bearer[ \t]+(\S+)[ \t]*$/i.exec(header) : null;
  const same = sameString(match ? match[1] : "", expected);
  return same && match !== null;
}

module.exports = { expectedToken, bearerMatches, sameString };
