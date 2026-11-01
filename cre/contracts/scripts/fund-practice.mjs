#!/usr/bin/env node
// Tops up the PracticeDesk on Sepolia with test WETH, wrapped from the wallet's own Sepolia ETH (test money).
//
//   SEPOLIA_PRIVATE_KEY=0x... node cre/contracts/scripts/fund-practice.mjs 0.012          # shows what it would do
//   SEPOLIA_PRIVATE_KEY=0x... node cre/contracts/scripts/fund-practice.mjs 0.012 --send   # wraps and sends
//
// Each ETHUSDT SHORT sells about 0.0019 WETH from the desk; when the desk runs out, the send fails and the dossier
// records the position as simulated. It keeps 0.002 ETH in the wallet for gas (a desk or anchor transaction costs
// a fraction of that on Sepolia). The key comes from the environment and is never printed.
import fs from 'node:fs'
import { formatEther, parseAbi, parseEther } from 'viem'
import { clients, DEPLOYMENT, ERC20, SEPOLIA } from './practice-common.mjs'

const GAS_RESERVE = parseEther('0.002')
const amount = parseEther(process.argv[2] || '0')
const send = process.argv.includes('--send')
const { account, pub, wallet } = clients()
const desk = JSON.parse(fs.readFileSync(DEPLOYMENT, 'utf8')).contract
const balanceOf = parseAbi(['function balanceOf(address) view returns (uint256)'])

const eth = await pub.getBalance({ address: account.address })
const deskWeth = await pub.readContract({ address: SEPOLIA.weth, abi: balanceOf, functionName: 'balanceOf', args: [desk] })
console.log(`wallet ${account.address}: ${formatEther(eth)} ETH; desk ${desk}: ${formatEther(deskWeth)} WETH`)
if (amount <= 0n) throw new Error('give the amount of ETH to wrap, e.g. 0.012')
if (eth - amount < GAS_RESERVE) throw new Error(`that leaves less than ${formatEther(GAS_RESERVE)} ETH for gas; get Sepolia ETH from a faucet first`)
console.log(`plan: wrap ${formatEther(amount)} ETH into WETH and send it to the desk (about ${Math.floor(Number(formatEther(amount)) / 0.0019)} more ETH shorts)`)
if (!send) {
  console.log('dry run: add --send to do it')
  process.exit(0)
}
const step = async (what, request) => {
  const hash = await wallet.writeContract(request)
  const receipt = await pub.waitForTransactionReceipt({ hash, timeout: 180_000 })
  if (receipt.status !== 'success') throw new Error(`${what} reverted: ${hash}`)
  console.log(`${what}: https://sepolia.etherscan.io/tx/${hash}`)
}
await step('wrap', { account, address: SEPOLIA.weth, abi: ERC20, functionName: 'deposit', args: [], value: amount, chain: wallet.chain })
await step('send to the desk', { account, address: SEPOLIA.weth, abi: ERC20, functionName: 'transfer', args: [desk, amount], chain: wallet.chain })
const after = await pub.readContract({ address: SEPOLIA.weth, abi: balanceOf, functionName: 'balanceOf', args: [desk] })
console.log(`desk now holds ${formatEther(after)} WETH`)
