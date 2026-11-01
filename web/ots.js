/* Leitor de prova OpenTimestamps no navegador (global VerdictOTS) e no Node (module.exports).
 *
 * Uma prova .ots diz: "este digest sha256, passando por estas operacoes (append, prepend, sha256),
 * vira este valor, e este valor esta escrito no cabecalho do bloco N do Bitcoin" (ou, ainda, "um
 * calendario prometeu incluir"). Este modulo le a prova, refaz as operacoes e devolve o valor que
 * cada atestado afirma. Quem chama compara esse valor com o merkle root do bloco, buscado em um ou
 * mais exploradores publicos: assim o navegador confere o Bitcoin sem confiar em nos nem no cliente `ots`.
 *
 * So as operacoes que os calendarios usam (sha256, append, prepend, reverse, hexlify). Qualquer outra
 * (sha1, ripemd160, keccak) e recusada em voz alta em vez de ser lida pela metade.
 */
(function (root) {
  "use strict";
  const MAGIC = Uint8Array.from([0x00, ...Array.from("OpenTimestamps", (c) => c.charCodeAt(0)), 0x00, 0x00, ...Array.from("Proof", (c) => c.charCodeAt(0)), 0x00, 0xbf, 0x89, 0xe2, 0xe8, 0x84, 0xe8, 0x92, 0x94]);
  const TAG_BITCOIN = "0588960d73d71901";
  const TAG_PENDING = "83dfe30d2ef90c8e";
  const DIGEST_LENGTH = { 0x08: 32 };

  const toHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  const fromHex = (hex) => Uint8Array.from(hex.match(/../g).map((h) => parseInt(h, 16)));
  const concat = (...parts) => { const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0)); let at = 0; for (const p of parts) { out.set(p, at); at += p.length; } return out; };

  function reader(bytes) {
    let at = 0;
    const need = (n) => { if (at + n > bytes.length) throw new Error("the proof is truncated (unexpected end of data)"); };
    return {
      byte() { need(1); return bytes[at++]; },
      bytes(n) { need(n); const out = bytes.slice(at, at + n); at += n; return out; },
      varuint() { let value = 0, shift = 0; for (;;) { const b = this.byte(); value += (b & 0x7f) * 2 ** shift; if (!(b & 0x80)) return value; shift += 7; if (shift > 49) throw new Error("the proof has an oversized number"); } },
      varbytes() { return this.bytes(this.varuint()); },
      atEnd() { return at >= bytes.length; },
    };
  }

  function readAttestation(r) {
    const tag = toHex(r.bytes(8));
    const payload = reader(r.varbytes());
    if (tag === TAG_BITCOIN) return { type: "bitcoin", height: payload.varuint() };
    if (tag === TAG_PENDING) return { type: "pending", uri: new TextDecoder().decode(payload.varbytes()) };
    return { type: "other", tag };
  }

  function readOp(tag, r) {
    if (tag === 0x08 || tag === 0xf2 || tag === 0xf3) return { tag };
    if (tag === 0xf0 || tag === 0xf1) return { tag, arg: r.varbytes() };
    throw new Error("unsupported operation 0x" + tag.toString(16) + " in the proof (the browser reader knows sha256, append, prepend, reverse and hexlify)");
  }

  function readTimestamp(r) {
    const node = { ops: [], attestations: [] };
    const one = (tag) => {
      if (tag === 0x00) node.attestations.push(readAttestation(r));
      else { const op = readOp(tag, r); node.ops.push({ ...op, next: readTimestamp(r) }); }
    };
    let tag = r.byte();
    while (tag === 0xff) { one(r.byte()); tag = r.byte(); }
    one(tag);
    return node;
  }

  function parse(bytes) {
    const r = reader(bytes);
    const head = r.bytes(MAGIC.length);
    if (!MAGIC.every((b, i) => head[i] === b)) throw new Error("this is not an OpenTimestamps proof (wrong header)");
    if (r.varuint() !== 1) throw new Error("unsupported OpenTimestamps proof version");
    const hashOp = r.byte();
    if (!DIGEST_LENGTH[hashOp]) throw new Error("unsupported operation 0x" + hashOp.toString(16) + " as the file hash (only sha256 is read here)");
    const digest = r.bytes(DIGEST_LENGTH[hashOp]);
    const tree = readTimestamp(r);
    return { digest, tree };
  }

  function walk(node, visit) {
    node.attestations.forEach((a) => visit(a));
    node.ops.forEach((o) => walk(o.next, visit));
  }

  function attestations(proof) {
    const out = [];
    walk(proof.tree, (a) => out.push(a));
    return out;
  }

  async function sha256(bytes) {
    return new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));
  }

  async function apply(op, msg) {
    if (op.tag === 0x08) return sha256(msg);
    if (op.tag === 0xf0) return concat(msg, op.arg);
    if (op.tag === 0xf1) return concat(op.arg, msg);
    if (op.tag === 0xf2) return Uint8Array.from(msg).reverse();
    return new TextEncoder().encode(toHex(msg));
  }

  async function digestMatches(proof, fileBytes) {
    const got = await sha256(fileBytes);
    return got.length === proof.digest.length && got.every((b, i) => b === proof.digest[i]);
  }

  async function commitments(proof) {
    const out = [];
    async function go(node, msg) {
      for (const a of node.attestations) out.push({ ...a, message: msg });
      for (const op of node.ops) await go(op.next, await apply(op, msg));
    }
    await go(proof.tree, proof.digest);
    return out;
  }

  /* Explorers print the merkle root in the reverse byte order of what the proof computes. */
  function matchesMerkleRoot(message, merkleRootHex) {
    return toHex(Uint8Array.from(message).reverse()) === String(merkleRootHex).toLowerCase();
  }

  const api = { parse, attestations, commitments, digestMatches, matchesMerkleRoot, toHex, fromHex };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.VerdictOTS = api;
})(typeof window !== "undefined" ? window : globalThis);
