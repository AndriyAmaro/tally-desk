// Confere um ledger com o verificador do navegador (web/canonical.js) rodando no Node.
// Uso: node tests/node_verify.js caminho/do/ledger.jsonl
const fs = require("node:fs");
const { createHash } = require("node:crypto");
const { verifyLedger } = require("../web/canonical.js");

const sha256Hex = async (text) => createHash("sha256").update(text, "utf8").digest("hex");
verifyLedger(fs.readFileSync(process.argv[2], "utf8"), sha256Hex).then((result) => {
  console.log(JSON.stringify({ dossiers: result.rows.length, problems: result.problems }));
});
