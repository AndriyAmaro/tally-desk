import { describe, expect, test } from 'bun:test'
import { decodeFunctionData, type Hex } from 'viem'
import { ANCHORED_TOPIC, anchoredInReceipt, DIRECT_WORKFLOW_NAME, FORWARDER_ABI, forwarderCall, forwarderMetadata, readWorkflowName } from './direct'
import { encodeAnchorReport } from './logic'

// The rawReport of a real anchor sent by `cre workflow simulate --broadcast` (Sepolia tx 0x9c40cf9f…, seq 365).
const SIMULATOR_RAW =
	'0x01bd86a4a5adbc79b2dd406411371363f9b20315d3e962ed85147e51aa1291269b000000640000000100000001111111111111111111111111111111111111111111111111111111111111111162646464326631313062aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0001000000000000000000000000000000000000000000000000000000000000016d156b44cbebd63db516ed69c22ad725eecdb79c636a2c5326637f1f9667c7c3fa00000000000000000000000000000000000000000000000000000749313063ef0000000000000000000000000000000000000000000000000000000000000008000000000000000000000000000000000000000000000000000000006adf7e1c' as Hex

const HEAD = { seq: 365, hash: '156b44cbebd63db516ed69c22ad725eecdb79c636a2c5326637f1f9667c7c3fa', symbol: 'BTCUSDT' }
const PRICE = { answer: 0x749313063efn, decimals: 8, updatedAt: 0x6adf7e1cn }

describe('forwarder metadata', () => {
	test('rebuilds byte for byte the header the CRE simulator sends', () => {
		const metadata = forwarderMetadata({
			executionId: '0xbd86a4a5adbc79b2dd406411371363f9b20315d3e962ed85147e51aa1291269b',
			timestamp: 100,
			workflowId: `0x${'11'.repeat(32)}`,
			workflowName: 'bddd2f110b',
			owner: `0x${'aa'.repeat(20)}`,
		})
		expect(metadata.length).toBe(2 + 109 * 2)
		expect(`${metadata}${encodeAnchorReport(HEAD, PRICE).slice(2)}`).toBe(SIMULATOR_RAW)
	})

	test('names the sender on-chain, so a direct anchor is never mistaken for a CRE run', () => {
		expect(DIRECT_WORKFLOW_NAME).toHaveLength(10)
		expect(readWorkflowName(SIMULATOR_RAW)).toBe('bddd2f110b')
		const call = forwarderCall({ receiver: `0x${'22'.repeat(20)}`, head: HEAD, price: PRICE, executionId: `0x${'33'.repeat(32)}`, timestamp: 1, owner: `0x${'44'.repeat(20)}` })
		const { args } = decodeFunctionData({ abi: FORWARDER_ABI, data: call })
		expect(readWorkflowName(args[1])).toBe(DIRECT_WORKFLOW_NAME)
	})

	test('refuses fields that would shift the layout LedgerAnchor reads', () => {
		const ok = { executionId: `0x${'00'.repeat(32)}` as Hex, timestamp: 1, workflowId: `0x${'00'.repeat(32)}` as Hex, workflowName: 'abcdefghij', owner: `0x${'00'.repeat(20)}` as Hex }
		expect(() => forwarderMetadata({ ...ok, workflowName: 'short' })).toThrow()
		expect(() => forwarderMetadata({ ...ok, executionId: '0x00' })).toThrow()
		expect(() => forwarderMetadata({ ...ok, owner: '0x00' })).toThrow()
		expect(() => forwarderMetadata({ ...ok, timestamp: 2 ** 32 })).toThrow()
	})
})

describe('confirming the anchor from the receipt', () => {
	const receiver = '0x50443D51Db71A61E131B147B1d36aF21d48CDdF7' as Hex
	const seqTopic = (n: number) => `0x${n.toString(16).padStart(64, '0')}` as Hex
	const log = (address: Hex, topics: Hex[]) => ({ address, topics })

	test('the Anchored event of the contract for that seq is the proof, read from the same block as the transaction', () => {
		expect(anchoredInReceipt([log(receiver.toLowerCase() as Hex, [ANCHORED_TOPIC, seqTopic(386), `0x${'cd'.repeat(32)}`])], receiver, 386)).toBe(true)
	})

	test('no event, another seq or another contract is not an anchor (the mock forwarder does not revert when the receiver does)', () => {
		expect(anchoredInReceipt([], receiver, 386)).toBe(false)
		expect(anchoredInReceipt([log(receiver, [ANCHORED_TOPIC, seqTopic(385)])], receiver, 386)).toBe(false)
		expect(anchoredInReceipt([log(`0x${'11'.repeat(20)}`, [ANCHORED_TOPIC, seqTopic(386)])], receiver, 386)).toBe(false)
	})
})

describe('forwarder call', () => {
	test('carries the same report LedgerAnchor decodes, to the receiver it names', () => {
		const receiver = `0x${'22'.repeat(20)}` as Hex
		const call = forwarderCall({ receiver, head: HEAD, price: PRICE, executionId: `0x${'33'.repeat(32)}`, timestamp: 1_761_480_000, owner: `0x${'44'.repeat(20)}` })
		const { functionName, args } = decodeFunctionData({ abi: FORWARDER_ABI, data: call })
		expect(functionName).toBe('report')
		expect(args[0].toLowerCase()).toBe(receiver)
		expect(args[1].slice(2 + 109 * 2)).toBe(encodeAnchorReport(HEAD, PRICE).slice(2))
		// LedgerAnchor reads the workflow owner at metadata[42:62] of what the forwarder hands it (rawReport[45:107]).
		expect(args[1].slice(2 + 87 * 2, 2 + 107 * 2)).toBe('44'.repeat(20))
	})
})
