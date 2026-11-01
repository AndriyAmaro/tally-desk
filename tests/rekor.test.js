// web/rekor.js: read what the Sigstore transparency log (Rekor) holds about a signed ledger head.
// Run: node --test tests/rekor.test.js
//
// The fixtures are built with openssl, not with our own code: cert.pem carries the identity of this
// repository's witness workflow as a URI in subjectAltName (`openssl x509 -ext subjectAltName` prints it),
// and entry.json has the shape the public log returns for a hashedrekord entry. The first real entry the
// workflow writes should be added next to them.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const rekor = require("../web/rekor.js");

const dir = path.join(__dirname, "fixtures", "rekor");
const json = (name) => JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
const head = new Uint8Array(fs.readFileSync(path.join(dir, "head.json")));
const IDENTITY = "https://github.com/AndriyAmaro/verdict-desk/.github/workflows/rekor.yml@refs/heads/main";

test("the log index is read from a bundle in either format cosign writes", () => {
  assert.equal(rekor.logIndexOf(json("bundle-legacy.json")), 424242);
  assert.equal(rekor.logIndexOf(json("bundle-new.json")), 424242);
});

test("a bundle with no log entry is refused instead of guessed", () => {
  assert.throws(() => rekor.logIndexOf({ base64Signature: "x" }), /no transparency log entry/);
});

test("an entry gives the signed digest, the signer's identity and the time the log recorded it", () => {
  const facts = rekor.entryFacts(json("entry.json"));
  assert.equal(facts.kind, "hashedrekord");
  assert.equal(facts.sha256, "5e6f6621d5278fdc5739f8bac1373994c0dc2407009e2ea26425713636dcb3d6"); // sha256sum head.json
  assert.deepEqual(facts.identities, [IDENTITY]); // the value openssl prints from the certificate
  assert.equal(facts.integratedTime, 1792900000);
  assert.equal(facts.logIndex, 424242);
});

test("the head matches when its bytes hash to the digest in the log", async () => {
  const check = await rekor.check(json("entry.json"), head, { repo: "AndriyAmaro/verdict-desk", workflow: "rekor.yml" });
  assert.equal(check.digestOk, true);
  assert.equal(check.identityOk, true);
});

test("one changed byte in the head no longer matches", async () => {
  const edited = Uint8Array.from(head);
  edited[edited.length - 3] ^= 1;
  const check = await rekor.check(json("entry.json"), edited, { repo: "AndriyAmaro/verdict-desk", workflow: "rekor.yml" });
  assert.equal(check.digestOk, false);
});

test("a certificate for another repository or another workflow is not accepted as ours", async () => {
  const other = await rekor.check(json("entry.json"), head, { repo: "someone/else", workflow: "rekor.yml" });
  assert.equal(other.identityOk, false);
  const wrongFlow = await rekor.check(json("entry.json"), head, { repo: "AndriyAmaro/verdict-desk", workflow: "other.yml" });
  assert.equal(wrongFlow.identityOk, false);
});

test("a look-alike prefix is not accepted (the repository name must end at the slash)", async () => {
  const check = await rekor.check(json("entry.json"), head, { repo: "AndriyAmaro/verdict", workflow: "rekor.yml" });
  assert.equal(check.identityOk, false);
});

test("an entry that is not a hashedrekord is refused", () => {
  const entry = json("entry.json");
  const key = Object.keys(entry)[0];
  const body = JSON.parse(Buffer.from(entry[key].body, "base64").toString());
  body.kind = "intoto";
  entry[key].body = Buffer.from(JSON.stringify(body)).toString("base64");
  assert.throws(() => rekor.entryFacts(entry), /hashedrekord/);
});

test("a real entry from the public log is read the way openssl reads its certificate", () => {
  // real-entry.json is log index 3328000009 of rekor.sigstore.dev, fetched unchanged. Its certificate marks the
  // subjectAltName extension critical, which the Sigstore CA does, and `openssl x509 -ext subjectAltName`
  // prints the same single URI. The digest is the one in the entry's own body.
  const facts = rekor.entryFacts(json("real-entry.json"));
  assert.equal(facts.logIndex, 3328000009);
  assert.deepEqual(facts.identities, ["https://issuer.enforce.dev/ce598c79fdf1a62172585bcc151d1951d306e855/9f3c1401b628dc97"]);
  assert.equal(facts.sha256, "069fe629a82cc7d7eb6c6352262bedd0b52dc58c59294f9b1c5f506047d76f3a");
  assert.equal(facts.integratedTime, 1792454470);
});
