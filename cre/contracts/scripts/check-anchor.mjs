#!/usr/bin/env node
// Checks what the LedgerAnchor contract on Sepolia holds against the public ledger. Read-only: no key needed.
//
//   node cre/contracts/scripts/check-anchor.mjs                      # address from anchor/config.staging.json
//   node cre/contracts/scripts/check-anchor.mjs --address 0x... --ledger https://andriyamaro.github.io/verdict-desk/ledger/ledger.jsonl
//   node cre/contracts/scripts/check-anchor.mjs --json               # the same facts as JSON, for filling cre/SIMULATION-OUTPUT.md
//
// It (1) verifies the whole hash chain of the ledger with the same code the dashboard runs, (2) reads the
// latest anchored sequence number and head hash from the contract, (3) asks the contract `isAnchored(seq, hash)`
// for the hash this ledger has at that sequence number, and (4) finds the transaction that anchored it.
// Exit code 0 only when the chain is intact and the anchored head is exactly the ledger's dossier; 2 when
// nothing is deployed or anchored yet.
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { createPublicClient, getAddress, http, isAddress, zeroAddress } from 'viem'
import { sepolia } from 'viem/chains'

const here = path.dirname(fileURLToPath(import.meta.url))
const cre = path.join(here, '..', '..')
const { verifyLedger } = createRequire(import.meta.url)(path.join(cre, '..', 'web', 'canonical.js'))

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : fallback
}
const asJson = process.argv.includes('--json')
const say = (line) => { if (!asJson) console.log(line) }
const fail = (message, code = 1) => {
  if (asJson) console.log(JSON.stringify({ ok: false, error: message }, null, 2))
  else console.error(`error: ${message}`)
  process.exit(code)
}

const config = JSON.parse(fs.readFileSync(path.join(cre, 'anchor', 'config.staging.json'), 'utf8'))
const address = arg('address', config.anchorAddress)
if (!isAddress(address) || address === zeroAddress) fail('no LedgerAnchor address yet: pass --address or set anchorAddress in cre/anchor/config.staging.json', 2)
const rpc = arg('rpc', process.env.SEPOLIA_RPC_URL || 'https://ethereum-sepolia-rpc.publicnode.com')
const ledgerSource = arg('ledger', path.join(cre, '..', 'ledger', 'ledger.jsonl'))
const abi = JSON.parse(fs.readFileSync(path.join(cre, 'contracts', 'evm', 'src', 'abi', 'LedgerAnchor.abi'), 'utf8'))

const PUBLIC_LEDGER = 'https://raw.githubusercontent.com/AndriyAmaro/verdict-desk/main/ledger/ledger.jsonl'
const sha = async (t) => crypto.createHash('sha256').update(t).digest('hex')
const load = async (source) => (/^https?:\/\//.test(source) ? await (await fetch(source)).text() : fs.readFileSync(source, 'utf8'))
let usedSource = ledgerSource
let chain = await verifyLedger(await load(ledgerSource), sha)
say(`ledger ${ledgerSource}: ${chain.rows.length} dossiers, chain ${chain.problems.length ? 'BROKEN: ' + chain.problems.join('; ') : 'intact (every hash recomputed here)'}`)

const client = createPublicClient({ chain: sepolia, transport: http(rpc) })
if ((await client.getChainId()) !== 11155111) fail(`${rpc} is not Ethereum Sepolia`)
const read = (functionName, args = []) => client.readContract({ address, abi, functionName, args })
const latestSeq = await read('latestSeq')
if (latestSeq === 0n) fail(`contract ${address} has anchored nothing yet`, 2)
const [headHash, price, decimals, priceUpdatedAt, anchoredAt] = await read('anchors', [latestSeq])
const iso = (seconds) => new Date(Number(seconds) * 1000).toISOString()
const feedPrice = Number(price) / 10 ** decimals
say(`contract ${getAddress(address)} on Sepolia: latest anchored seq ${latestSeq}, head hash ${headHash.slice(2)}`)
say(`  Data Feed price recorded with it: ${feedPrice} (feed updated ${iso(priceUpdatedAt)}), anchored at ${iso(anchoredAt)}`)

let row = chain.rows.find((r) => BigInt(r.dossier.seq) === latestSeq)
if (!row && usedSource !== PUBLIC_LEDGER) {
  // The anchor is newer than this copy of the ledger (the hourly bot keeps adding dossiers): compare with the public one instead of failing.
  say(`  this local copy has no dossier with seq ${latestSeq} (it is behind the anchored head; \`git pull\` brings it up to date); trying the public ledger`)
  try {
    const publicChain = await verifyLedger(await load(PUBLIC_LEDGER), sha)
    say(`ledger ${PUBLIC_LEDGER}: ${publicChain.rows.length} dossiers, chain ${publicChain.problems.length ? 'BROKEN: ' + publicChain.problems.join('; ') : 'intact (every hash recomputed here)'}`)
    chain = publicChain
    usedSource = PUBLIC_LEDGER
    row = chain.rows.find((r) => BigInt(r.dossier.seq) === latestSeq)
  } catch (e) {
    say(`  could not read the public ledger (${e.shortMessage || e.message})`)
  }
}
let match = false
if (!row) {
  say(`  this ledger has no dossier with seq ${latestSeq}`)
} else {
  match = await read('isAnchored', [latestSeq, `0x${row.dossier.hash}`])
  say(`  dossier ${row.dossier.seq} in this ledger has hash ${row.dossier.hash}`)
  say(`  isAnchored(${latestSeq}, hash) on-chain: ${match ? 'TRUE: the anchored head is exactly this dossier' : 'FALSE: the ledger differs from what was anchored'}`)
}

// The event log only names the transaction. Public nodes prune history (publicnode answers "pruned history unavailable"
// for some ranges), so try a short window first and a longer one after, and never let this decide the result.
let tx = null
const anchoredEvent = abi.find((e) => e.type === 'event' && e.name === 'Anchored')
const latestBlock = Number(await client.getBlockNumber())
for (const window of [2000, 40000]) {
  try {
    const logs = await client.getLogs({ address, event: anchoredEvent, args: { seq: latestSeq }, fromBlock: BigInt(Math.max(0, latestBlock - window)) })
    if (logs.length) { tx = logs[logs.length - 1].transactionHash; break }
  } catch (e) {
    say(`  (event log unavailable from this RPC for the last ${window} blocks: ${e.details || e.shortMessage || e.message})`)
  }
}
if (tx) say(`  anchoring transaction: https://sepolia.etherscan.io/tx/${tx}`)
else say('  anchoring transaction: not found in the recent event log (the contract reads above do not depend on it)')

const ok = chain.problems.length === 0 && match
if (asJson) {
  console.log(JSON.stringify({
    ok,
    network: 'ethereum-testnet-sepolia',
    contract: getAddress(address),
    ledger: { source: usedSource, dossiers: chain.rows.length, chainIntact: chain.problems.length === 0, problems: chain.problems },
    anchored: { seq: Number(latestSeq), headHash: headHash.slice(2), anchoredAt: iso(anchoredAt) },
    dossierHashInLedger: row ? row.dossier.hash : null,
    isAnchored: match,
    dataFeed: { price: feedPrice, answer: price.toString(), decimals, updatedAt: iso(priceUpdatedAt) },
    anchoringTx: tx,
    explorer: { contract: `https://sepolia.etherscan.io/address/${getAddress(address)}`, tx: tx ? `https://sepolia.etherscan.io/tx/${tx}` : null },
  }, null, 2))
}
process.exit(ok ? 0 : 1)
