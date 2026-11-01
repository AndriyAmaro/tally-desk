// Anchors the published ledger head from GitHub Actions, with no operator machine and without the CRE runtime.
//
// The CRE workflow (workflow.ts) needs either a deployed DON or `cre workflow simulate --broadcast`, and the CLI
// cannot log in from CI without a CRE API key, which this account cannot create. So this script runs the same
// checks with the same code, outside the runtime: the head must agree on two publications, its dossier must hash to
// it and re-derive its own verdict (verdict.ts), and the BTC/USD Data Feed must agree on Sepolia and Ethereum
// mainnet (logic.ts). Then it hands the same report to the same MockKeystoneForwarder the simulator uses, with a
// metadata header whose workflow name is "gh-actions", so every direct anchor is told apart from a CRE run on-chain.
//
//   SEPOLIA_PRIVATE_KEY=0x... bun run direct.ts            # anchor (skips when the head is already anchored)
//   SEPOLIA_PRIVATE_KEY=0x... bun run direct.ts --dry-run  # every check plus an eth_call of the transaction, nothing sent
import {
	type Address,
	bytesToString,
	concatHex,
	createPublicClient,
	createWalletClient,
	encodeFunctionData,
	type Hex,
	hexToBytes,
	http,
	keccak256,
	numberToHex,
	parseAbi,
	type PublicClient,
	sliceHex,
	stringToHex,
	toBytes,
} from 'viem'
import { privateKeyToAccount } from 'viem/accounts'
import { mainnet, sepolia } from 'viem/chains'
import { agreeOnHead, crossCheck, encodeAnchorReport, formatPrice, type LedgerHead, parseHead, type PriceReading } from './logic'
import { verifyHeadDossier } from './verdict'
import type { Config } from './workflow'

export const DIRECT_WORKFLOW_NAME = 'gh-actions'
// keccak256("Anchored(uint64,bytes32,int256,uint8,uint256)"), the event LedgerAnchor emits for each accepted head
export const ANCHORED_TOPIC = '0x43fd32c7e0eebd332a0c60efbf6c8c8a64b80609ef27f6262bf060b938a26f6c'
export const FORWARDER_ABI = parseAbi(['function report(address receiver, bytes rawReport, bytes reportContext, bytes[] signatures)'])
const ANCHOR_ABI = parseAbi([
	'function latestSeq() view returns (uint64)',
	'function isAnchored(uint64 seq, bytes32 headHash) view returns (bool)',
])
const FEED_ABI = parseAbi([
	'function description() view returns (string)',
	'function decimals() view returns (uint8)',
	'function latestRoundData() view returns (uint80, int256, uint256, uint256, uint80)',
])

export interface Metadata {
	executionId: Hex // 32 bytes
	timestamp: number // uint32 seconds
	workflowId: Hex // 32 bytes
	workflowName: string // exactly 10 ASCII characters
	owner: Hex // 20 bytes
}

const fixed = (value: Hex, bytes: number, what: string): Hex => {
	if (!/^0x[0-9a-fA-F]*$/.test(value) || value.length !== 2 + bytes * 2) throw new Error(`${what} must be ${bytes} bytes`)
	return value.toLowerCase() as Hex
}

/**
 * The 109-byte header the Keystone forwarder expects in front of a report: version (1), execution id (32),
 * timestamp (4), DON id (4), config version (4), workflow id (32), workflow name (10), workflow owner (20),
 * report id (2). Version, DON id, config version and report id are the values the CRE simulator sends.
 */
export function forwarderMetadata(m: Metadata): Hex {
	if (!Number.isInteger(m.timestamp) || m.timestamp < 0 || m.timestamp >= 2 ** 32) throw new Error('timestamp must fit in 4 bytes')
	if (!/^[\x20-\x7e]{10}$/.test(m.workflowName)) throw new Error('workflow name must be exactly 10 ASCII characters')
	return concatHex([
		'0x01',
		fixed(m.executionId, 32, 'execution id'),
		numberToHex(m.timestamp, { size: 4 }),
		numberToHex(1, { size: 4 }),
		numberToHex(1, { size: 4 }),
		fixed(m.workflowId, 32, 'workflow id'),
		stringToHex(m.workflowName, { size: 10 }),
		fixed(m.owner, 20, 'workflow owner'),
		'0x0001',
	])
}

/** The workflow name written in a rawReport's header: "gh-actions" for this script, a hash prefix for a CRE run. */
export function readWorkflowName(rawReport: Hex): string {
	return bytesToString(hexToBytes(sliceHex(rawReport, 77, 87)))
}

// Identifies this script as the "workflow" in the header; the name above is what a reader looks at.
const DIRECT_WORKFLOW_ID = keccak256(toBytes('verdict-desk/cre/anchor/direct.ts'))

/** Calldata of MockKeystoneForwarder.report carrying the anchor report for `head`. The mock checks no signatures. */
export function forwarderCall(a: { receiver: Address; head: LedgerHead; price: PriceReading; executionId: Hex; timestamp: number; owner: Address }): Hex {
	const metadata = forwarderMetadata({ executionId: a.executionId, timestamp: a.timestamp, workflowId: DIRECT_WORKFLOW_ID, workflowName: DIRECT_WORKFLOW_NAME, owner: a.owner })
	const rawReport = concatHex([metadata, encodeAnchorReport(a.head, a.price)])
	return encodeFunctionData({ abi: FORWARDER_ABI, functionName: 'report', args: [a.receiver, rawReport, '0x', []] })
}

/**
 * True when the transaction's own logs hold LedgerAnchor's Anchored event for `seq`. Read from the receipt, not from
 * a later eth_call: a load-balanced public node can answer the call from a node that has not seen the block yet
 * (run 26 Oct 23:09: the anchor landed, the read said it had not). The mock forwarder does not revert when the
 * receiver does, so a successful transaction alone proves nothing.
 */
export function anchoredInReceipt(logs: { address: string; topics: readonly string[] }[], receiver: string, seq: number): boolean {
	const seqTopic = `0x${seq.toString(16).padStart(64, '0')}`
	return logs.some((l) => l.address.toLowerCase() === receiver.toLowerCase() && l.topics[0] === ANCHORED_TOPIC && l.topics[1] === seqTopic)
}

const getText = async (url: string, what: string): Promise<string> => {
	// a query string bypasses the CDN copies of raw.githubusercontent.com and GitHub Pages (minutes old otherwise)
	const response = await fetch(`${url}?t=${Date.now()}`, { cache: 'no-store' })
	if (response.status !== 200) throw new Error(`${what} request to ${url} failed with status ${response.status}`)
	return response.text()
}

async function readFeed(client: PublicClient, address: Address) {
	const [pair, decimals, round] = await Promise.all([
		client.readContract({ address, abi: FEED_ABI, functionName: 'description' }),
		client.readContract({ address, abi: FEED_ABI, functionName: 'decimals' }),
		client.readContract({ address, abi: FEED_ABI, functionName: 'latestRoundData' }),
	])
	return { pair, reading: { answer: round[1], decimals, updatedAt: round[3] } }
}

const log = (line: string) => console.log(line)

async function run(dryRun: boolean): Promise<void> {
	const config: Config = await Bun.file(`${import.meta.dir}/config.staging.json`).json()
	const deployment = await Bun.file(`${import.meta.dir}/../deployment.sepolia.json`).json()
	const key = process.env.SEPOLIA_PRIVATE_KEY
	if (!key) throw new Error('SEPOLIA_PRIVATE_KEY is not set')
	const account = privateKeyToAccount((key.startsWith('0x') ? key : `0x${key}`) as Hex)
	const receiver = config.anchorAddress as Address
	const forwarder = deployment.forwarder as Address
	const sep = createPublicClient({ chain: sepolia, transport: http(process.env.SEPOLIA_RPC_URL || 'https://ethereum-sepolia-rpc.publicnode.com') })
	const main = createPublicClient({ chain: mainnet, transport: http(process.env.MAINNET_RPC_URL || 'https://ethereum-rpc.publicnode.com') })

	log(`Anchoring ledger head from ${config.headUrl} and ${config.headUrlMirror} (direct, GitHub Actions; not the CRE runtime)`)
	const head = agreeOnHead(parseHead(await getText(config.headUrl, 'ledger head')), parseHead(await getText(config.headUrlMirror, 'ledger head')))
	verifyHeadDossier(await getText(config.headDossierUrl, 'head dossier'), head)
	const replay = verifyHeadDossier(await getText(config.headDossierUrlMirror, 'head dossier'), head)
	log(`Replayed dossier #${head.seq} under rules ${replay.rulesVersion}: ${replay.action} edge ${replay.edge}, the same as recorded`)

	const latest = await sep.readContract({ address: receiver, abi: ANCHOR_ABI, functionName: 'latestSeq' })
	if (BigInt(head.seq) <= latest) {
		log(`Skipped: seq ${head.seq} is not past the contract's latest anchored seq ${latest}`)
		console.log(JSON.stringify({ status: 'skipped', seq: head.seq, latestSeq: Number(latest) }))
		return
	}

	const feed = await readFeed(sep, config.priceFeedAddress as Address)
	const other = await readFeed(main, config.mainnetPriceFeedAddress as Address)
	const cross = crossCheck(feed.reading, other.reading, config.maxFeedDeviationBps)
	log(`Data Feed ${feed.pair} ${config.priceFeedAddress}: ${formatPrice(feed.reading.answer, feed.reading.decimals)} (updatedAt ${feed.reading.updatedAt})`)
	log(`Cross-chain check: ${feed.pair} on Sepolia ${cross.sepolia}, ${other.pair} on ethereum-mainnet ${cross.mainnet}, gap ${cross.deviationBps} bps (limit ${config.maxFeedDeviationBps})`)

	const timestamp = Math.floor(Date.now() / 1000)
	const executionId = keccak256(toBytes(`${process.env.GITHUB_RUN_ID || 'local'}:${head.seq}:${timestamp}`))
	const data = forwarderCall({ receiver, head, price: feed.reading, executionId, timestamp, owner: account.address })
	// An eth_call first: a revert (another anchor won the race, a stale seq) costs nothing and is reported as such.
	await sep.call({ account: account.address, to: forwarder, data })
	if (dryRun) {
		log(`Dry run: the forwarder call for seq ${head.seq} succeeds as an eth_call; nothing was sent`)
		console.log(JSON.stringify({ status: 'dry-run', seq: head.seq, head: head.hash }))
		return
	}
	const wallet = createWalletClient({ account, chain: sepolia, transport: http(process.env.SEPOLIA_RPC_URL || 'https://ethereum-sepolia-rpc.publicnode.com') })
	// The practice desk sends from the same wallet: one retry if both picked the same nonce.
	const send = () => wallet.sendTransaction({ to: forwarder, data, gas: BigInt(config.gasLimit) })
	const tx = await send().catch((e: Error) => {
		if (!/nonce/i.test(e.message)) throw e
		return new Promise((r) => setTimeout(r, 15_000)).then(send)
	})
	const receipt = await sep.waitForTransactionReceipt({ hash: tx, timeout: 180_000 })
	if (receipt.status !== 'success') throw new Error(`anchor transaction ${tx} reverted`)
	if (!anchoredInReceipt(receipt.logs, receiver, head.seq)) {
		// The contract refused the report inside the forwarder: another anchor got this seq first. Not an error.
		const now = await sep.readContract({ address: receiver, abi: ANCHOR_ABI, functionName: 'latestSeq', blockNumber: receipt.blockNumber })
		if (now >= BigInt(head.seq)) {
			log(`Skipped: seq ${head.seq} was anchored by another run first (transaction ${tx} carried a stale report)`)
			console.log(JSON.stringify({ status: 'skipped', seq: head.seq, latestSeq: Number(now), tx }))
			return
		}
		throw new Error(`transaction ${tx} succeeded but LedgerAnchor emitted no Anchored event for seq ${head.seq}`)
	}
	log(`Anchored seq ${head.seq} head ${head.hash} at tx ${tx} (block ${receipt.blockNumber})`)
	console.log(JSON.stringify({
		status: 'anchored', via: 'gh-actions', seq: head.seq, head: head.hash, symbol: head.symbol,
		replayed: { rules: replay.rulesVersion, action: replay.action, edge: replay.edge },
		feed: feed.pair, price: formatPrice(feed.reading.answer, feed.reading.decimals), crossChain: cross, txHash: tx,
	}))
}

if (import.meta.main) {
	run(process.argv.includes('--dry-run')).catch((e: Error) => {
		// The Pages copy is published minutes after the repository file: a run in that window waits for the next one.
		if (e.message.startsWith('ledger head sources disagree')) {
			console.log(`Skipped: ${e.message} (the Pages deploy is behind the repository; the next run anchors)`)
			console.log(JSON.stringify({ status: 'skipped', reason: e.message }))
			return
		}
		console.error(`refused: ${e.message}`)
		process.exit(1)
	})
}
