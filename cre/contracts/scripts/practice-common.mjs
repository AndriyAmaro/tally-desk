// Shared pieces of the PracticeDesk scripts: Sepolia addresses, the key from the environment, the clients.
// The key is read from SEPOLIA_PRIVATE_KEY and never printed, logged or written anywhere.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createPublicClient, createWalletClient, http } from 'viem'
import { privateKeyToAccount } from 'viem/accounts'
import { sepolia } from 'viem/chains'

export const here = path.dirname(fileURLToPath(import.meta.url))
export const evm = path.join(here, '..', 'evm')
export const DEPLOYMENT = path.join(here, '..', '..', 'practice.sepolia.json')

// Uniswap v3 on Sepolia (official deployment) and the two test tokens of its deepest pool.
export const SEPOLIA = {
  router: '0x3bFA4769FB09eefC5a80d6E87c3B9C650f7Ae48E', // SwapRouter02
  weth: '0xfFf9976782d46CC05630D1f6eBAb18b2324d6B14',
  usdc: '0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238',
  pool: '0x3289680dd4d6c10bb19b899729cda5eef58aeff1', // WETH/USDC 0.05%
  fee: 500,
}

export const ERC20 = [
  { type: 'function', name: 'approve', stateMutability: 'nonpayable', inputs: [{ name: 's', type: 'address' }, { name: 'a', type: 'uint256' }], outputs: [{ type: 'bool' }] },
  { type: 'function', name: 'transfer', stateMutability: 'nonpayable', inputs: [{ name: 't', type: 'address' }, { name: 'a', type: 'uint256' }], outputs: [{ type: 'bool' }] },
  { type: 'function', name: 'balanceOf', stateMutability: 'view', inputs: [{ name: 'a', type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', name: 'deposit', stateMutability: 'payable', inputs: [], outputs: [] },
]
export const POOL = [
  { type: 'function', name: 'slot0', stateMutability: 'view', inputs: [], outputs: [
    { name: 'sqrtPriceX96', type: 'uint160' }, { name: 'tick', type: 'int24' }, { name: 'a', type: 'uint16' }, { name: 'b', type: 'uint16' },
    { name: 'c', type: 'uint16' }, { name: 'd', type: 'uint8' }, { name: 'unlocked', type: 'bool' }] },
]
export const ROUTER = [
  { type: 'function', name: 'exactInputSingle', stateMutability: 'payable', inputs: [{ name: 'p', type: 'tuple', components: [
    { name: 'tokenIn', type: 'address' }, { name: 'tokenOut', type: 'address' }, { name: 'fee', type: 'uint24' }, { name: 'recipient', type: 'address' },
    { name: 'amountIn', type: 'uint256' }, { name: 'amountOutMinimum', type: 'uint256' }, { name: 'sqrtPriceLimitX96', type: 'uint160' }] }],
    outputs: [{ type: 'uint256' }] },
]

export function fail(message) {
  console.error(`error: ${message}`)
  process.exit(1)
}

export function clients() {
  const raw = (process.env.SEPOLIA_PRIVATE_KEY || '').trim()
  if (!raw) fail('set SEPOLIA_PRIVATE_KEY (a throwaway testnet key) in the environment')
  const key = raw.startsWith('0x') ? raw : `0x${raw}`
  if (!/^0x[0-9a-fA-F]{64}$/.test(key)) fail('the private key must be 64 hex characters (the value is not shown)')
  const account = privateKeyToAccount(key)
  const transport = http(process.env.SEPOLIA_RPC_URL || 'https://ethereum-sepolia-rpc.publicnode.com')
  return { account, pub: createPublicClient({ chain: sepolia, transport }), wallet: createWalletClient({ account, chain: sepolia, transport }) }
}

export function deskAbi() {
  return JSON.parse(fs.readFileSync(path.join(evm, 'src', 'abi', 'PracticeDesk.abi'), 'utf8'))
}

/** Test USDC (6 decimals) per WETH (18 decimals) at the pool's current price; the testnet price is not the market's. */
export async function usdcPerWeth(pub) {
  const [sqrtPriceX96] = await pub.readContract({ address: SEPOLIA.pool, abi: POOL, functionName: 'slot0' })
  // token0 is USDC (lower address): price = token1/token0 = (sqrtP/2^96)^2 raw WETH units per raw USDC unit
  const q = Number(sqrtPriceX96) / 2 ** 96
  return 1 / (q * q * 1e-12)
}
