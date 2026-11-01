#!/usr/bin/env node
// Deploys LedgerAnchor to Ethereum Sepolia from a TEST key held in the environment.
// The key is read from SEPOLIA_PRIVATE_KEY (or CRE_ETH_PRIVATE_KEY, the name the CRE CLI uses) and is never
// printed, logged or written anywhere. Use a throwaway key that only ever holds testnet ETH.
//
//   cd cre/contracts && bun install && (cd evm && npm install && npm run compile)
//   SEPOLIA_PRIVATE_KEY=0x... node scripts/deploy.mjs --dry-run          # estimate only, sends nothing
//   SEPOLIA_PRIVATE_KEY=0x... node scripts/deploy.mjs --write-config      # deploy, then set anchorAddress
//
// Options: --forwarder mock|production|0xADDRESS (default mock: the Sepolia MockKeystoneForwarder used by
// `cre workflow simulate --broadcast`), --owner 0xADDRESS (workflow owner the contract accepts; default zero
// address, which disables the owner check), --rpc URL, --dry-run, --write-config.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createPublicClient, createWalletClient, encodeDeployData, formatEther, getAddress, http, isAddress, zeroAddress } from 'viem'
import { privateKeyToAccount } from 'viem/accounts'
import { sepolia } from 'viem/chains'

const here = path.dirname(fileURLToPath(import.meta.url))
const evm = path.join(here, '..', 'evm')
const FORWARDERS = {
  mock: '0x15fc6ae953e024d975e77382eeec56a9101f9f88', // MockKeystoneForwarder, simulation (Chainlink CRE Forwarder Directory)
  production: '0xF8344CFd5c43616a4366C34E3EEE75af79a74482', // KeystoneForwarder, deployed workflows
}

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : fallback
}
const flag = (name) => process.argv.includes(`--${name}`)
const fail = (message) => { console.error(`error: ${message}`); process.exit(1) }

const rawKey = (process.env.SEPOLIA_PRIVATE_KEY || process.env.CRE_ETH_PRIVATE_KEY || '').trim()
if (!rawKey) fail('set SEPOLIA_PRIVATE_KEY (a throwaway testnet key) in the environment')
const key = rawKey.startsWith('0x') ? rawKey : `0x${rawKey}`
if (!/^0x[0-9a-fA-F]{64}$/.test(key)) fail('the private key must be 64 hex characters (the value is not shown)')
const account = privateKeyToAccount(key)

const forwarderArg = arg('forwarder', 'mock')
const forwarder = FORWARDERS[forwarderArg] ?? forwarderArg
if (!isAddress(forwarder)) fail(`--forwarder must be mock, production or an address, got ${forwarderArg}`)
const owner = arg('owner', zeroAddress)
if (!isAddress(owner)) fail('--owner must be an address')

const rpc = arg('rpc', process.env.SEPOLIA_RPC_URL || 'https://ethereum-sepolia-rpc.publicnode.com')
const client = createPublicClient({ chain: sepolia, transport: http(rpc) })
if ((await client.getChainId()) !== 11155111) fail(`${rpc} is not Ethereum Sepolia (chain id 11155111)`)

const binPath = path.join(evm, 'build', 'LedgerAnchor.bin')
if (!fs.existsSync(binPath)) fail('compile first: cd cre/contracts/evm && npm install && npm run compile')
const abi = JSON.parse(fs.readFileSync(path.join(evm, 'src', 'abi', 'LedgerAnchor.abi'), 'utf8'))
const bytecode = `0x${fs.readFileSync(binPath, 'utf8').trim()}`

if ((await client.getCode({ address: forwarder })) === undefined) fail(`no contract at the forwarder address ${forwarder} on Sepolia`)
const data = encodeDeployData({ abi, bytecode, args: [getAddress(forwarder), getAddress(owner)] })
const balance = await client.getBalance({ address: account.address })
const gas = await client.estimateGas({ account: account.address, data })
const gasPrice = await client.getGasPrice()
console.log(`deployer ${account.address}`)
console.log(`balance ${formatEther(balance)} ETH on Sepolia; estimated deploy cost ${formatEther(gas * gasPrice)} ETH (${gas} gas at ${gasPrice} wei)`)
console.log(`forwarder ${getAddress(forwarder)} (${forwarderArg in FORWARDERS ? forwarderArg : 'custom'}), expected workflow owner ${getAddress(owner)}`)
if (flag('dry-run')) { console.log('dry run: nothing was sent'); process.exit(0) }
if (balance < gas * gasPrice * 2n) fail(`fund ${account.address} with Sepolia ETH first (a faucet is enough; about ${formatEther(gas * gasPrice * 2n)} ETH is needed)`)

const wallet = createWalletClient({ account, chain: sepolia, transport: http(rpc) })
const txHash = await wallet.deployContract({ abi, bytecode, args: [getAddress(forwarder), getAddress(owner)] })
console.log(`deploy transaction ${txHash}`)
const receipt = await client.waitForTransactionReceipt({ hash: txHash, timeout: 180_000 })
if (receipt.status !== 'success' || !receipt.contractAddress) fail(`deployment failed: ${JSON.stringify({ status: receipt.status, block: String(receipt.blockNumber) })}`)
const address = getAddress(receipt.contractAddress)

const read = (functionName) => client.readContract({ address, abi, functionName })
const onchainForwarder = await read('forwarder'), onchainOwner = await read('expectedWorkflowOwner'), latestSeq = await read('latestSeq')
if (getAddress(onchainForwarder) !== getAddress(forwarder) || getAddress(onchainOwner) !== getAddress(owner) || latestSeq !== 0n) fail('the deployed contract does not report the expected constructor values')

const record = {
  network: 'ethereum-testnet-sepolia',
  chainId: 11155111,
  contract: address,
  forwarder: getAddress(forwarder),
  forwarderKind: forwarderArg in FORWARDERS ? forwarderArg : 'custom',
  expectedWorkflowOwner: getAddress(owner),
  deployer: account.address,
  deployTx: txHash,
  deployBlock: Number(receipt.blockNumber),
  explorer: { contract: `https://sepolia.etherscan.io/address/${address}`, deployTx: `https://sepolia.etherscan.io/tx/${txHash}` },
}
fs.writeFileSync(path.join(here, '..', '..', 'deployment.sepolia.json'), JSON.stringify(record, null, 2) + '\n')
if (flag('write-config')) {
  const file = path.join(here, '..', '..', 'anchor', 'config.staging.json')
  const config = JSON.parse(fs.readFileSync(file, 'utf8'))
  config.anchorAddress = address
  fs.writeFileSync(file, JSON.stringify(config, null, 2) + '\n')
  console.log(`anchorAddress set in ${path.relative(process.cwd(), file)}`)
}
console.log(`LedgerAnchor deployed at ${address} (block ${receipt.blockNumber}, ${receipt.gasUsed} gas)`)
console.log(record.explorer.contract)
console.log(record.explorer.deployTx)
