#!/usr/bin/env node
// Deploys PracticeDesk on Sepolia and funds it with test tokens, from a TEST key held in the environment.
//
//   cd cre/contracts/evm && npm run compile:practice
//   SEPOLIA_PRIVATE_KEY=0x... node cre/contracts/scripts/deploy-practice.mjs --wrap 0.03 --usdc-from 0.01
//
// --wrap ETH       wraps that much Sepolia ETH into WETH and sends it to the desk (default 0.03)
// --usdc-from ETH  of that wrapped WETH, swaps this much to test USDC first, so the desk can also open LONGs (default 0.01)
// Writes cre/practice.sepolia.json (address, deploy tx, funding txs). Run once; later runs refuse if the file exists.
import fs from 'node:fs'
import path from 'node:path'
import { encodeDeployData, formatEther, formatUnits, getAddress, parseEther } from 'viem'
import { clients, DEPLOYMENT, deskAbi, ERC20, evm, fail, ROUTER, SEPOLIA, usdcPerWeth } from './practice-common.mjs'

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}
if (fs.existsSync(DEPLOYMENT)) fail(`${DEPLOYMENT} exists: the desk is already deployed`)
const wrap = parseEther(arg('wrap', '0.03'))
const toUsdc = parseEther(arg('usdc-from', '0.01'))
if (toUsdc >= wrap) fail('--usdc-from must be less than --wrap')

const { account, pub, wallet } = clients()
const bin = path.join(evm, 'build', 'PracticeDesk.bin')
if (!fs.existsSync(bin)) fail('compile first: cd cre/contracts/evm && npm run compile:practice')
const balance = await pub.getBalance({ address: account.address })
console.log(`deployer ${account.address}, balance ${formatEther(balance)} ETH`)
if (balance < wrap + parseEther('0.005')) fail('not enough Sepolia ETH for the wrap plus gas')

const send = async (label, request) => {
  const hash = await wallet.sendTransaction(request)
  const receipt = await pub.waitForTransactionReceipt({ hash })
  if (receipt.status !== 'success') fail(`${label} failed: ${hash}`)
  console.log(`${label}: ${hash}`)
  return hash
}
const call = (address, abi, functionName, args, value) =>
  pub.simulateContract({ account, address, abi, functionName, args, value }).then(({ request }) => wallet.writeContract(request))
const wait = async (label, hashPromise) => {
  const hash = await hashPromise
  const receipt = await pub.waitForTransactionReceipt({ hash })
  if (receipt.status !== 'success') fail(`${label} failed: ${hash}`)
  console.log(`${label}: ${hash}`)
  return hash
}

const deployTx = await send('deploy PracticeDesk', {
  data: encodeDeployData({ abi: deskAbi(), bytecode: `0x${fs.readFileSync(bin, 'utf8').trim()}`, args: [getAddress(SEPOLIA.router)] }),
})
const desk = (await pub.getTransactionReceipt({ hash: deployTx })).contractAddress
console.log(`desk ${desk}`)

const txs = { deploy: deployTx }
txs.wrap = await wait('wrap ETH into WETH', call(SEPOLIA.weth, ERC20, 'deposit', [], wrap))
txs.approve = await wait('approve the router', call(SEPOLIA.weth, ERC20, 'approve', [SEPOLIA.router, toUsdc]))
const price = await usdcPerWeth(pub)
txs.swap = await wait('swap WETH for test USDC', call(SEPOLIA.router, ROUTER, 'exactInputSingle', [{
  tokenIn: SEPOLIA.weth, tokenOut: SEPOLIA.usdc, fee: SEPOLIA.fee, recipient: account.address,
  amountIn: toUsdc, amountOutMinimum: 1n, sqrtPriceLimitX96: 0n,
}]))
const usdc = await pub.readContract({ address: SEPOLIA.usdc, abi: ERC20, functionName: 'balanceOf', args: [account.address] })
txs.fundWeth = await wait('send WETH to the desk', call(SEPOLIA.weth, ERC20, 'transfer', [desk, wrap - toUsdc]))
txs.fundUsdc = await wait('send test USDC to the desk', call(SEPOLIA.usdc, ERC20, 'transfer', [desk, usdc]))

const record = {
  network: 'ethereum-testnet-sepolia', chainId: 11155111, contract: desk, owner: account.address,
  router: SEPOLIA.router, weth: SEPOLIA.weth, usdc: SEPOLIA.usdc, pool: SEPOLIA.pool, fee: SEPOLIA.fee,
  funded: { weth: formatEther(wrap - toUsdc), usdc: formatUnits(usdc, 6), testnetUsdcPerWeth: Math.round(price) },
  transactions: txs,
  explorer: `https://sepolia.etherscan.io/address/${desk}`,
  note: 'Testnet practice only. The testnet pool price is not the market price.',
}
fs.writeFileSync(DEPLOYMENT, JSON.stringify(record, null, 2) + '\n')
console.log(`wrote ${DEPLOYMENT}`)
