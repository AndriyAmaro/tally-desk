"use strict";
const { MAX_BODY_BYTES } = require("./service");

const ENV = () => ({ GUARD_TOKEN: process.env.GUARD_TOKEN });

function readBody(req) {
  if (req.body !== undefined && req.body !== null) return Promise.resolve(req.body); // Vercel parses JSON bodies itself
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES + 1024) { resolve(Buffer.alloc(MAX_BODY_BYTES + 1025)); req.destroy(); return; }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

/** Adapts a pure handler ((request, env) -> {status, headers, body}) to a Vercel Node function (req, res). */
function adapt(handler) {
  return async function vercelHandler(req, res) {
    let answer;
    try {
      const body = req.method === "POST" ? await readBody(req) : undefined;
      answer = handler({ method: req.method, headers: req.headers || {}, body: Buffer.isBuffer(body) ? body.toString("utf8") : body }, ENV());
    } catch (_) { // the error text may hold the request, so it is neither logged nor returned
      answer = { status: 500, headers: { "Content-Type": "application/json; charset=utf-8" }, body: { detail: "internal error" } };
    }
    res.statusCode = answer.status;
    for (const [name, value] of Object.entries(answer.headers || {})) res.setHeader(name, value);
    res.end(req.method === "HEAD" ? undefined : JSON.stringify(answer.body));
  };
}

module.exports = { adapt };
