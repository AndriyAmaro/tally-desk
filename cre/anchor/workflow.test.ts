import { describe, expect } from 'bun:test'
import { getNetwork, TxStatus } from '@chainlink/cre-sdk'
import { EvmMock, HttpActionsMock, newTestRuntime, REPORT_METADATA_HEADER_LENGTH, test } from '@chainlink/cre-sdk/test'
import { bytesToHex, type Address } from 'viem'
import { newAggregatorV3Mock } from '../contracts/evm/ts/generated/AggregatorV3_mock'
import { newLedgerAnchorMock } from '../contracts/evm/ts/generated/LedgerAnchor_mock'
import { decodeAnchorReport } from './logic'
import { dossierHash } from './verdict'
import { anchorLedger, type Config, initWorkflow, onCronTrigger } from './workflow'

const enc = (s: string) => new TextEncoder().encode(s)

const SEPOLIA_SELECTOR = 16015286601757825753n // ethereum-testnet-sepolia
const FEED = '0x1b44F3514812d835EB1BDB0acB33d3fA3351Ee43' as Address
const MAINNET_FEED = '0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c' as Address
const MAINNET_SELECTOR = getNetwork({ chainFamily: 'evm', chainSelectorName: 'ethereum-mainnet', isTestnet: false })!.chainSelector.selector
const ANCHOR = '0x00000000000000000000000000000000000a11c0' as Address
const HASH = 'ab'.repeat(32)
const HEAD = { seq: 12, hash: HASH, symbol: 'BTCUSDT' }

const config: Config = {
	schedule: '0 0 0 * * *',
	headUrl: 'https://example.invalid/head.json',
	headUrlMirror: 'https://mirror.invalid/head.json',
	headDossierUrl: 'https://example.invalid/head-dossier.json',
	headDossierUrlMirror: 'https://mirror.invalid/head-dossier.json',
	chainSelectorName: 'ethereum-testnet-sepolia',
	priceFeedAddress: FEED,
	anchorAddress: ANCHOR,
	gasLimit: '500000',
	mainnetChainSelectorName: 'ethereum-mainnet',
	mainnetPriceFeedAddress: MAINNET_FEED,
	maxFeedDeviationBps: 500,
}

/** The same pair on Ethereum mainnet, read as a second chain next to Sepolia. */
function mainnetFeed(answer = 6_710_000_000_000n) {
	const feed = newAggregatorV3Mock(MAINNET_FEED, EvmMock.testInstance(MAINNET_SELECTOR))
	feed.decimals = () => 8
	feed.description = () => 'BTC / USD'
	feed.latestRoundData = () => [1n, answer, 1_759_999_000n, 1_760_000_100n, 1n]
}

function mocks(txStatus: TxStatus = TxStatus.SUCCESS) {
	const evm = EvmMock.testInstance(SEPOLIA_SELECTOR)
	const feed = newAggregatorV3Mock(FEED, evm)
	feed.decimals = () => 8
	feed.description = () => 'BTC / USD'
	feed.latestRoundData = () => [1n, 6_700_012_345_678n, 1_759_999_000n, 1_760_000_000n, 1n]
	mainnetFeed()
	const anchor = newLedgerAnchorMock(ANCHOR, evm)
	const seen: Array<{ receiver: string; payload: Uint8Array; gasLimit: string }> = []
	anchor.writeReport = (input) => {
		seen.push({
			receiver: bytesToHex(input.receiver),
			payload: input.report.rawReport.slice(REPORT_METADATA_HEADER_LENGTH),
			gasLimit: String(input.gasConfig.gasLimit),
		})
		return { txStatus, txHash: new Uint8Array(32).fill(7) } as never // the mock accepts the plain JSON shape of the reply
	}
	return seen
}

describe('anchorLedger', () => {
	test('reads the Data Feed and sends the ledger head and price to LedgerAnchor', () => {
		const seen = mocks()
		const runtime = newTestRuntime(null, undefined, config)
		const summary = JSON.parse(anchorLedger(runtime, HEAD))

		expect(summary).toMatchObject({ seq: 12, head: HASH, symbol: 'BTCUSDT', feed: 'BTC / USD', price: '67000.12345678',
			crossChain: { sepolia: '67000.12345678', mainnet: '67100', deviationBps: 14 } })
		expect(seen).toHaveLength(1)
		expect(seen[0].receiver.toLowerCase()).toBe(ANCHOR.toLowerCase())
		expect(seen[0].gasLimit).toBe('500000')
		expect(decodeAnchorReport(bytesToHex(seen[0].payload))).toEqual({
			seq: 12n,
			headHash: `0x${HASH}`,
			price: 6_700_012_345_678n,
			decimals: 8,
			priceUpdatedAt: 1_760_000_000n,
		})
	})

	test('writes nothing when Sepolia and mainnet disagree by more than the limit', () => {
		const seen = mocks()
		mainnetFeed(9_000_000_000_000n)
		expect(() => anchorLedger(newTestRuntime(null, undefined, config), HEAD)).toThrow('disagree by')
		expect(seen).toHaveLength(0)
	})

	test('fails loudly when the forwarder reports a failed transaction', () => {
		mocks(TxStatus.REVERTED)
		expect(() => anchorLedger(newTestRuntime(null, undefined, config), HEAD)).toThrow('failed to write report')
	})

	test('refuses to anchor a non-positive Data Feed answer', () => {
		const evm = EvmMock.testInstance(SEPOLIA_SELECTOR)
		const feed = newAggregatorV3Mock(FEED, evm)
		feed.decimals = () => 8
		feed.description = () => 'BTC / USD'
		feed.latestRoundData = () => [1n, 0n, 0n, 0n, 1n]
		mainnetFeed()
		newLedgerAnchorMock(ANCHOR, evm).writeReport = () => ({ txStatus: TxStatus.SUCCESS }) as never
		expect(() => anchorLedger(newTestRuntime(null, undefined, config), HEAD)).toThrow('price feed answer must be positive')
	})
})

describe('onCronTrigger', () => {
	test('requires the scheduled execution time', () => {
		expect(() => onCronTrigger(newTestRuntime(null, undefined, config), {} as any)).toThrow('scheduled execution time is required')
	})
})

describe('initWorkflow', () => {
	test('subscribes onCronTrigger to the configured schedule', () => {
		const handlers = initWorkflow(config)
		expect(handlers).toHaveLength(1)
		expect(handlers[0].fn).toBe(onCronTrigger)
		expect((handlers[0].trigger as { config?: { schedule?: string } }).config?.schedule).toBe(config.schedule)
	})
})

// The real last dossier of the published ledger: the workflow must re-derive its verdict before anchoring it.
const LAST = JSON.parse((await Bun.file(`${import.meta.dir}/../../ledger/ledger.jsonl`).text()).trim().split('\n').pop() as string)
const REAL_HEAD = { seq: LAST.seq, hash: LAST.hash, symbol: LAST.evidence.symbol }

describe('two independent sources for the ledger head', () => {
	const bodyOf = (head: object) => enc(JSON.stringify(head))
	const HEAD = REAL_HEAD
	const DOSSIER = enc(JSON.stringify(LAST))
	const both = (extra: Record<string, { statusCode: number; body: Uint8Array }> = {}) => ({
		[config.headUrl]: { statusCode: 200, body: bodyOf(HEAD) },
		[config.headUrlMirror]: { statusCode: 200, body: bodyOf(HEAD) },
		[config.headDossierUrl]: { statusCode: 200, body: DOSSIER },
		[config.headDossierUrlMirror]: { statusCode: 200, body: DOSSIER },
		...extra,
	})
	function http(replies: Record<string, { statusCode: number; body: Uint8Array }>) {
		const asked: string[] = []
		HttpActionsMock.testInstance().sendRequest = (input) => {
			asked.push(input.url)
			return (replies[input.url] ?? { statusCode: 404, body: enc('') }) as never
		}
		return asked
	}
	const fire = () => onCronTrigger(newTestRuntime(null, undefined, config), { scheduledExecutionTime: { seconds: 1n, nanos: 0 } } as any)

	test('asks both sources for the head and its dossier, replays the verdict, then anchors', () => {
		const seen = mocks()
		const asked = http(both())
		const summary = JSON.parse(fire())
		expect(asked.sort()).toEqual([config.headUrl, config.headUrlMirror, config.headDossierUrl, config.headDossierUrlMirror].sort())
		expect(summary).toMatchObject({ seq: HEAD.seq, head: HEAD.hash, replayed: { action: LAST.verdict.action, edge: LAST.verdict.edge } })
		expect(seen).toHaveLength(1)
	})

	test('writes nothing when the head dossier does not replay to its recorded verdict', () => {
		const seen = mocks()
		const forged = JSON.parse(JSON.stringify(LAST))
		forged.verdict.action = forged.verdict.action === 'LONG' ? 'SHORT' : 'LONG'
		forged.hash = dossierHash(forged)
		const head = { ...HEAD, hash: forged.hash }
		http({
			[config.headUrl]: { statusCode: 200, body: bodyOf(head) },
			[config.headUrlMirror]: { statusCode: 200, body: bodyOf(head) },
			[config.headDossierUrl]: { statusCode: 200, body: enc(JSON.stringify(forged)) },
			[config.headDossierUrlMirror]: { statusCode: 200, body: enc(JSON.stringify(forged)) },
		})
		expect(() => fire()).toThrow('does not replay')
		expect(seen).toHaveLength(0)
	})

	test('writes nothing when a published dossier is not the head', () => {
		const seen = mocks()
		const other = JSON.parse(JSON.stringify(LAST))
		other.evidence.symbol = 'XRPUSDT'
		http(both({ [config.headDossierUrlMirror]: { statusCode: 200, body: enc(JSON.stringify(other)) } }))
		expect(() => fire()).toThrow('does not hash to the published head')
		expect(seen).toHaveLength(0)
	})

	test('fails closed and writes nothing when the sources disagree', () => {
		const seen = mocks()
		http(both({ [config.headUrlMirror]: { statusCode: 200, body: bodyOf({ ...HEAD, seq: HEAD.seq - 1 }) } }))
		expect(() => fire()).toThrow('sources disagree')
		expect(seen).toHaveLength(0)
	})

	test('fails closed when either source is unavailable', () => {
		const seen = mocks()
		http({ [config.headUrl]: { statusCode: 200, body: bodyOf(HEAD) } })
		expect(() => fire()).toThrow('status 404')
		expect(seen).toHaveLength(0)
	})
})

describe('what the price next to the head means', () => {
	test('names the feed, because the head may be a SOLUSDT dossier while the feed is always BTC / USD', () => {
		mocks()
		const summary = JSON.parse(anchorLedger(newTestRuntime(null, undefined, config), { seq: 9, hash: HASH, symbol: 'SOLUSDT' }))
		expect(summary.symbol).toBe('SOLUSDT')
		expect(summary.feed).toBe('BTC / USD')
	})
})
