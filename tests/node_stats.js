// Calcula carteira e cadencia de um ledger com web/stats.js no Node.
// Uso: node tests/node_stats.js caminho/do/ledger.jsonl
const fs = require("node:fs");
const stats = require("../web/stats.js");

const ledger = fs.readFileSync(process.argv[2], "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
console.log(JSON.stringify({ portfolio: stats.portfolio(ledger), cadence: stats.cadence(ledger) }));
