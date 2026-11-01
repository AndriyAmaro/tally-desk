import { describe, expect, test } from 'bun:test'
import { agreeOnHead, crossCheck, decodeAnchorReport, encodeAnchorReport, formatPrice, parseHead } from './logic'

const HASH = 'ab'.repeat(32)

describe('parseHead', () => {
	test('accepts the head the Python side publishes', () => {
		expect(parseHead(JSON.stringify({ seq: 12, hash: HASH, symbol: 'BTCUSDT' }))).toEqual({ seq: 12, hash: HASH, symbol: 'BTCUSDT' })
	})

	test('rejects anything that is not exactly a head', () => {
		const bad = [
			'not json',
			'[]',
			JSON.stringify({ seq: 0, hash: HASH, symbol: 'BTCUSDT' }),
			JSON.stringify({ seq: 1.5, hash: HASH, symbol: 'BTCUSDT' }),
			JSON.stringify({ seq: 1, hash: HASH.toUpperCase(), symbol: 'BTCUSDT' }),
			JSON.stringify({ seq: 1, hash: HASH.slice(2), symbol: 'BTCUSDT' }),
			JSON.stringify({ seq: 1, hash: HASH, symbol: '' }),
		]
		for (const text of bad) expect(() => parseHead(text)).toThrow()
	})
})

describe('anchor report', () => {
	test('round-trips through the ABI encoding that LedgerAnchor decodes', () => {
		const encoded = encodeAnchorReport({ seq: 12, hash: HASH, symbol: 'BTCUSDT' }, { answer: 6_700_012_345_678n, decimals: 8, updatedAt: 1_760_000_000n })
		expect(decodeAnchorReport(encoded)).toEqual({ seq: 12n, headHash: `0x${HASH}`, price: 6_700_012_345_678n, decimals: 8, priceUpdatedAt: 1_760_000_000n })
		expect(encoded.length).toBe(2 + 5 * 64) // five 32-byte words
	})

	test('refuses a non-positive price instead of anchoring garbage', () => {
		expect(() => encodeAnchorReport({ seq: 1, hash: HASH, symbol: 'BTCUSDT' }, { answer: 0n, decimals: 8, updatedAt: 1n })).toThrow()
		expect(() => encodeAnchorReport({ seq: 1, hash: HASH, symbol: 'BTCUSDT' }, { answer: -5n, decimals: 8, updatedAt: 1n })).toThrow()
	})
})

describe('formatPrice', () => {
	test('uses integer math', () => {
		expect(formatPrice(6_700_012_345_678n, 8)).toBe('67000.12345678')
		expect(formatPrice(6_700_000_000_000n, 8)).toBe('67000')
		expect(formatPrice(5n, 8)).toBe('0.00000005')
		expect(formatPrice(123n, 0)).toBe('123')
	})
})

describe('agreeOnHead', () => {
	const head = { seq: 12, hash: HASH, symbol: 'BTCUSDT' }

	test('returns the head when two independent sources publish the same one', () => {
		expect(agreeOnHead(head, { ...head })).toEqual(head)
	})

	test('refuses when the sources differ in sequence, hash or symbol, and says which', () => {
		expect(() => agreeOnHead(head, { ...head, seq: 13 })).toThrow('seq 12 vs 13')
		expect(() => agreeOnHead(head, { ...head, hash: 'cd'.repeat(32) })).toThrow('hash')
		expect(() => agreeOnHead(head, { ...head, symbol: 'ETHUSDT' })).toThrow('symbol')
	})
})

describe('the price read on two chains', () => {
	const sepolia = { answer: 8_006_991_363_197n, decimals: 8, updatedAt: 1n }
	const mainnet = { answer: 8_041_413_000_000n, decimals: 8, updatedAt: 2n }

	test('gives the gap in basis points, exactly, whatever the decimals', () => {
		expect(crossCheck(sepolia, mainnet, 500)).toEqual({ sepolia: '80069.91363197', mainnet: '80414.13', deviationBps: 42 })
		const mainnet18 = { answer: 80_414_130_000_000_000_000_000n, decimals: 18, updatedAt: 2n }
		expect(crossCheck(sepolia, mainnet18, 500).deviationBps).toBe(42)
	})

	test('refuses when the two chains disagree by more than the limit', () => {
		const far = { answer: 9_000_000_000_000n, decimals: 8, updatedAt: 2n }
		expect(() => crossCheck(sepolia, far, 500)).toThrow('disagree by')
	})

	test('refuses a non-positive answer on either chain', () => {
		expect(() => crossCheck(sepolia, { ...mainnet, answer: 0n }, 500)).toThrow('must be positive')
	})
})
