// web/ots.js: read an OpenTimestamps proof in the browser and find what it commits to.
// Run: node --test tests/ots.test.js
//
// The fixtures are two archived proofs of this repository, which never change once written:
//   116 carries one Bitcoin attestation (block 972666) plus calendar promises;
//   170 carries three (blocks 972852, 972858 and 972871).
// The expected values come from the official client (`ots info`) and from a public block explorer,
// so the JavaScript is checked against something other than itself.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ots = require("../web/ots.js");

const stamps = path.join(__dirname, "..", "ledger", "timestamps");
const read = (name) => new Uint8Array(fs.readFileSync(path.join(stamps, name)));
const hex = (bytes) => Buffer.from(bytes).toString("hex");

test("a proof names the digest it is about and the attestations it carries", () => {
  const proof = ots.parse(read("116.json.ots"));
  assert.equal(hex(proof.digest), "c03066c8cc981289fc7d5ade199af686c9ab84ea063f88c180a395104e43e610");
  const kinds = ots.attestations(proof);
  assert.deepEqual(kinds.filter((a) => a.type === "bitcoin").map((a) => a.height), [972666]);
  assert.ok(kinds.filter((a) => a.type === "pending").length >= 3);
});

test("a proof can carry more than one Bitcoin attestation", () => {
  const heights = ots.attestations(ots.parse(read("170.json.ots"))).filter((a) => a.type === "bitcoin").map((a) => a.height).sort();
  assert.deepEqual(heights, [972852, 972858, 972871]); // the same three the official client lists with `ots info`
});

test("the digest in the proof is the sha256 of the snapshot it was made for, and of no other bytes", async () => {
  const proof = ots.parse(read("116.json.ots"));
  assert.equal(await ots.digestMatches(proof, read("116.json")), true);
  const edited = read("116.json");
  edited[5] ^= 1;
  assert.equal(await ots.digestMatches(proof, edited), false);
});

test("evaluating the proof yields the value the Bitcoin block commits to", async () => {
  const proof = ots.parse(read("116.json.ots"));
  const found = (await ots.commitments(proof)).filter((c) => c.type === "bitcoin");
  assert.equal(found.length, 1);
  assert.equal(found[0].height, 972666);
  // explorers display the merkle root in the reverse byte order of what the proof computes
  assert.equal(hex(Uint8Array.from(found[0].message).reverse()), "52a5b100e9c2a70b6ae90b0b03fc388edb579477bd6eda7320d1726ce863fef6");
  assert.equal(ots.matchesMerkleRoot(found[0].message, "52a5b100e9c2a70b6ae90b0b03fc388edb579477bd6eda7320d1726ce863fef6"), true);
  assert.equal(ots.matchesMerkleRoot(found[0].message, "00a5b100e9c2a70b6ae90b0b03fc388edb579477bd6eda7320d1726ce863fef6"), false);
});

function proofBytes(...tail) {
  const magic = [0x00, ...Buffer.from("OpenTimestamps"), 0x00, 0x00, ...Buffer.from("Proof"), 0x00, 0xbf, 0x89, 0xe2, 0xe8, 0x84, 0xe8, 0x92, 0x94];
  return Uint8Array.from([...magic, 0x01, 0x08, ...new Array(32).fill(7), ...tail]);
}
const PENDING = [0x00, 0x83, 0xdf, 0xe3, 0x0d, 0x2e, 0xf9, 0x0c, 0x8e];

test("a proof that only holds a calendar promise is reported as pending, with no Bitcoin block", async () => {
  const uri = Buffer.from("https://cal.example/x");
  const payload = [uri.length, ...uri];
  const proof = ots.parse(proofBytes(...PENDING, payload.length, ...payload));
  const all = await ots.commitments(proof);
  assert.deepEqual(all.map((c) => c.type), ["pending"]);
  assert.equal(all[0].uri, "https://cal.example/x");
});

test("append, prepend and sha256 are evaluated in order", async () => {
  // msg = 7*32 ; append 0xaa ; sha256 ; then a pending attestation
  const proof = ots.parse(proofBytes(0xf0, 1, 0xaa, 0x08, ...PENDING, 3, 2, 0x68, 0x69));
  const [only] = await ots.commitments(proof);
  const crypto = require("node:crypto");
  const start = Buffer.concat([Buffer.alloc(32, 7), Buffer.from([0xaa])]);
  assert.equal(hex(only.message), crypto.createHash("sha256").update(start).digest("hex"));
});

test("damaged proofs are refused loudly, never half-read", () => {
  assert.throws(() => ots.parse(read("116.json.ots").slice(0, 60)), /truncated|unexpected end/i);
  const bad = read("116.json.ots");
  bad[3] ^= 0xff;
  assert.throws(() => ots.parse(bad), /not an OpenTimestamps proof/i);
  assert.throws(() => ots.parse(proofBytes(0x99)), /unsupported operation/i);
});
