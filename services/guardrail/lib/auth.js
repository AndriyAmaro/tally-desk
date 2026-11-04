"use strict";
const crypto = require("node:crypto");

/** The token the deployment expects, or null when GUARD_TOKEN is not set (the service then answers 503). */
function expectedToken(env = process.env) {
  const token = env.GUARD_TOKEN;
  return typeof token === "string" && token.length > 0 ? token : null;
}

const sha256 = (value) => crypto.createHash("sha256").update(value).digest();

/**
 * Whether an Authorization header carries `Bearer <expected>`. Both sides are hashed first, so the comparison always runs over two
 * equal-length buffers and its time does not depend on where the strings differ or on how long the given one is.
 */
function bearerMatches(header, expected) {
  const match = typeof header === "string" ? /^Bearer[ \t]+(\S+)[ \t]*$/i.exec(header) : null;
  const same = crypto.timingSafeEqual(sha256(match ? match[1] : ""), sha256(expected));
  return same && match !== null;
}

module.exports = { expectedToken, bearerMatches };
