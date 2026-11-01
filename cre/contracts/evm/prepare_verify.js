// Extrai os immutables do LedgerAnchor na Sepolia e compara o runtime com uma compilacao local.
const fs = require("fs");
const path = require("path");
const solc = require("solc");
const { keccak_256 } = require("@noble/hashes/sha3.js");

const RPC = "https://ethereum-sepolia-rpc.publicnode.com";
const ADDR = "0x50443D51Db71A61E131B147B1d36aF21d48CDdF7";

function selector(sig) {
  return Buffer.from(keccak_256(Buffer.from(sig))).toString("hex").slice(0, 8);
}

async function rpc(method, params) {
  const res = await fetch(RPC, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", method, params, id: 1 }),
  });
  const j = await res.json();
  if (j.error) throw new Error(JSON.stringify(j.error));
  return j.result;
}

function encodeArgs(words) {
  // cada arg já vem como hex de 32 bytes, sem 0x
  return words.map((w) => w.padStart(64, "0")).join("");
}

(async () => {
  // 1) getters
  const fwd = await rpc("eth_call", [{ to: ADDR, data: "0x" + selector("forwarder()") }, "latest"]);
  const owner = await rpc("eth_call", [{ to: ADDR, data: "0x" + selector("expectedWorkflowOwner()") }, "latest"]);
  const seq = await rpc("eth_call", [{ to: ADDR, data: "0x" + selector("latestSeq()") }, "latest"]);
  console.log("forwarder()            :", "0x" + fwd.slice(-40));
  console.log("expectedWorkflowOwner():", "0x" + owner.slice(-40));
  console.log("latestSeq()            :", parseInt(seq, 16));

  // 2) compilar exatamente como compile.js
  const src = fs.readFileSync(path.join(__dirname, "src/LedgerAnchor.sol"), "utf8");
  const input = {
    language: "Solidity",
    sources: { "LedgerAnchor.sol": { content: src } },
    settings: {
      optimizer: { enabled: true, runs: 200 },
      outputSelection: { "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object", "metadata"] } },
    },
  };
  const out = JSON.parse(solc.compile(JSON.stringify(input)));
  const errs = (out.errors || []).filter((e) => e.severity === "error");
  if (errs.length) {
    console.error(errs.map((e) => e.formattedMessage).join("\n"));
    process.exit(1);
  }
  const c = out.contracts["LedgerAnchor.sol"].LedgerAnchor;

  // 3) comparar runtime (immutables ficam embutidos, então comparo com máscara grosseira)
  const deployed = (await rpc("eth_getCode", [ADDR, "latest"])).slice(2);
  const local = c.evm.deployedBytecode.object;
  console.log("\nruntime deployado :", deployed.length / 2, "bytes");
  console.log("runtime compilado :", local.length / 2, "bytes");

  // 4) salvar tudo para a verificacao
  const args = encodeArgs([fwd.slice(-40), owner.slice(-40)]);
  fs.mkdirSync(path.join(__dirname, "verify"), { recursive: true });
  fs.writeFileSync(path.join(__dirname, "verify/constructor_args.txt"), args);
  fs.writeFileSync(path.join(__dirname, "verify/LedgerAnchor.sol"), src);
  fs.writeFileSync(path.join(__dirname, "verify/metadata.json"), c.metadata);
  fs.writeFileSync(path.join(__dirname, "verify/standard-input.json"), JSON.stringify(input, null, 1));
  console.log("\nconstructor args:", args);
  console.log("solc:", solc.version());
  console.log("abi entries:", c.abi.length);
})().catch((e) => {
  console.error("ERRO:", e.message.slice(0, 300));
  process.exit(1);
});
