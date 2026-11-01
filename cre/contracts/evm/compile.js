// Compiles one Solidity file with solc-js and writes the ABI and the creation bytecode of the contract named
// after the file (or the one named in the fourth argument).
// Usage: node compile.js src/LedgerAnchor.sol src/abi/LedgerAnchor.abi build/LedgerAnchor.bin [ContractName]
const solc = require("solc");
const fs = require("fs");
const path = require("path");

const [source, abiOut, binOut, named] = process.argv.slice(2);
const file = path.basename(source);
const name = named || file.replace(/\.sol$/, "");
const input = {
  language: "Solidity",
  sources: { [file]: { content: fs.readFileSync(source, "utf8") } },
  settings: { optimizer: { enabled: true, runs: 200 }, outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } } },
};
const out = JSON.parse(solc.compile(JSON.stringify(input)));
const problems = out.errors || [];
for (const p of problems) console.error(`${p.severity}: ${p.formattedMessage}`);
if (problems.some((p) => p.severity === "error")) process.exit(1);
const contract = out.contracts[file][name];
for (const file of [abiOut, binOut]) fs.mkdirSync(path.dirname(file), { recursive: true });
fs.writeFileSync(abiOut, JSON.stringify(contract.abi, null, 2) + "\n");
fs.writeFileSync(binOut, contract.evm.bytecode.object);
console.log(`compiled: ${contract.abi.length} ABI entries, ${contract.evm.bytecode.object.length / 2} bytes of creation code`);
