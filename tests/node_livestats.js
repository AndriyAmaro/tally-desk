// Counts the cadence and the LLM era of a ledger with web/stats.js in Node (no candles needed).
// Usage: node tests/node_livestats.js path/to/ledger.jsonl
const fs = require("node:fs");
const stats = require("../web/stats.js");

const ledger = fs.readFileSync(process.argv[2], "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
console.log(JSON.stringify({ cadence: stats.cadence(ledger), llmEra: stats.llmEra(ledger), llmEarly: stats.llmEra(ledger, 5, 45) }));
