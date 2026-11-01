/* Leitor, no navegador (global VerdictRekor) e no Node (module.exports), do que o log de transparencia
 * do Sigstore (Rekor) guarda sobre uma ponta do ledger assinada pelo workflow do GitHub.
 *
 * O workflow assina a ponta sem chave: o GitHub prova ao Sigstore qual repositorio e qual workflow esta
 * rodando, o Sigstore emite um certificado de minutos que nomeia essa identidade, e a assinatura entra
 * no log publico, que tem horario proprio. Aqui o navegador pede a entrada ao log, confere que o digest
 * assinado e o sha256 da ponta que ele mesmo baixou, e le no certificado de quem foi a assinatura.
 *
 * Nao verifica a cadeia do certificado nem a prova de inclusao: isso e o `cosign verify-blob`, que o
 * texto da pagina manda rodar. O que este modulo garante e o que da para refazer sem biblioteca:
 * digest igual, identidade nomeada no certificado, indice e horario do log.
 */
(function (root) {
  "use strict";
  const OID_SAN = [0x06, 0x03, 0x55, 0x1d, 0x11]; // 2.5.29.17 subjectAltName
  const b64 = (s) => (typeof Buffer !== "undefined" ? Uint8Array.from(Buffer.from(s, "base64")) : Uint8Array.from(atob(s), (c) => c.charCodeAt(0)));
  const text = (bytes) => Array.from(bytes, (b) => String.fromCharCode(b)).join("");
  const toHex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

  /* Indice da entrada no log, no formato antigo do cosign (rekorBundle) ou no novo (tlogEntries). */
  function logIndexOf(bundle) {
    const legacy = bundle && bundle.rekorBundle && bundle.rekorBundle.Payload;
    if (legacy && legacy.logIndex != null) return Number(legacy.logIndex);
    const tlog = bundle && bundle.verificationMaterial && bundle.verificationMaterial.tlogEntries;
    if (tlog && tlog.length && tlog[0].logIndex != null) return Number(tlog[0].logIndex);
    throw new Error("the bundle holds no transparency log entry");
  }

  function derLength(der, at) {
    const first = der[at];
    if (first < 0x80) return { length: first, next: at + 1 };
    const n = first & 0x7f;
    if (n < 1 || n > 3) throw new Error("unsupported DER length");
    let length = 0;
    for (let i = 1; i <= n; i++) length = length * 256 + der[at + i];
    return { length, next: at + 1 + n };
  }

  /* As identidades (URI e e-mail) do subjectAltName do certificado em DER. */
  function identitiesOf(der) {
    for (let i = 0; i + OID_SAN.length < der.length; i++) {
      if (!OID_SAN.every((b, k) => der[i + k] === b)) continue;
      let at = i + OID_SAN.length;
      if (der[at] === 0x01 && der[at + 1] === 0x01) at += 3; // critical BOOLEAN
      if (der[at] !== 0x04) continue;
      at = derLength(der, at + 1).next;
      if (der[at] !== 0x30) continue;
      const seq = derLength(der, at + 1);
      const end = seq.next + seq.length;
      const out = [];
      for (let p = seq.next; p < end;) {
        const tag = der[p];
        const len = derLength(der, p + 1);
        if (tag === 0x86 || tag === 0x81) out.push(text(der.slice(len.next, len.next + len.length)));
        p = len.next + len.length;
      }
      return out;
    }
    return [];
  }

  function pemToDer(pemBytes) {
    const body = text(pemBytes).replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
    return b64(body);
  }

  /* `entry` pode ser a resposta do log ({uuid: {...}}) ou a entrada em si. */
  function entryFacts(entry) {
    const inner = entry && entry.body === undefined ? Object.values(entry)[0] : entry;
    if (!inner || !inner.body) throw new Error("the log returned no entry");
    const body = JSON.parse(text(b64(inner.body)));
    if (body.kind !== "hashedrekord") throw new Error(`expected a hashedrekord entry, got ${body.kind}`);
    const spec = body.spec;
    const cert = pemToDer(b64(spec.signature.publicKey.content));
    return {
      kind: body.kind,
      sha256: String(spec.data.hash.value).toLowerCase(),
      identities: identitiesOf(cert),
      integratedTime: Number(inner.integratedTime),
      logIndex: Number(inner.logIndex),
    };
  }

  async function sha256Hex(bytes) {
    return toHex(new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes)));
  }

  /* A entrada fala deste arquivo e foi assinada pelo workflow deste repositorio? */
  async function check(entry, headBytes, expected) {
    const facts = entryFacts(entry);
    const prefix = `https://github.com/${expected.repo}/.github/workflows/${expected.workflow}@refs/`;
    return {
      facts,
      digestOk: facts.sha256 === (await sha256Hex(headBytes)),
      identityOk: facts.identities.some((id) => id.startsWith(prefix)),
    };
  }

  const api = { logIndexOf, entryFacts, check, identitiesOf };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.VerdictRekor = api;
})(typeof window !== "undefined" ? window : globalThis);
