// Pure logic of the anchor workflow: no CRE runtime, so it is unit-testable and readable on its own.
import { decodeAbiParameters, encodeAbiParameters, type Hex } from 'viem'

export interface LedgerHead {
	seq: number
	hash: string // 64 lowercase hex characters: SHA-256 of the canonical body of the last dossier
	symbol: string
}

export interface PriceReading {
	answer: bigint
	decimals: number
	updatedAt: bigint
}

export interface AnchorReport {
	seq: bigint
	headHash: Hex
	price: bigint
	decimals: number
	priceUpdatedAt: bigint
}

const HASH = /^[0-9a-f]{64}$/

/** Parses the head published by `python -m verdict_desk head`. Throws on anything unexpected. */
export function parseHead(text: string): LedgerHead {
	let raw: unknown
	try {
		raw = JSON.parse(text)
	} catch {
		throw new Error('ledger head is not valid JSON')
	}
	if (typeof raw !== 'object' || raw === null) throw new Error('ledger head must be a JSON object')
	const { seq, hash, symbol } = raw as Record<string, unknown>
	if (typeof seq !== 'number' || !Number.isSafeInteger(seq) || seq < 1) throw new Error('ledger head seq must be a positive integer')
	if (typeof hash !== 'string' || !HASH.test(hash)) throw new Error('ledger head hash must be 64 lowercase hex characters')
	if (typeof symbol !== 'string' || symbol.length === 0) throw new Error('ledger head symbol is missing')
	return { seq, hash, symbol }
}

/** Two independent sources must publish exactly the same head; anything else is refused, never averaged or guessed. */
export function agreeOnHead(a: LedgerHead, b: LedgerHead): LedgerHead {
	if (a.seq !== b.seq) throw new Error(`ledger head sources disagree: seq ${a.seq} vs ${b.seq}`)
	if (a.hash !== b.hash) throw new Error(`ledger head sources disagree: hash differs at seq ${a.seq}`)
	if (a.symbol !== b.symbol) throw new Error(`ledger head sources disagree: symbol ${a.symbol} vs ${b.symbol}`)
	return a
}

const REPORT_TYPES = [
	{ type: 'uint64' },
	{ type: 'bytes32' },
	{ type: 'int256' },
	{ type: 'uint8' },
	{ type: 'uint256' },
] as const

/** ABI-encodes the report that LedgerAnchor.onReport decodes. */
export function encodeAnchorReport(head: LedgerHead, price: PriceReading): Hex {
	if (price.answer <= 0n) throw new Error('price feed answer must be positive')
	if (price.decimals < 0 || price.decimals > 255) throw new Error('price feed decimals out of range')
	return encodeAbiParameters(REPORT_TYPES, [BigInt(head.seq), `0x${head.hash}`, price.answer, price.decimals, price.updatedAt])
}

export function decodeAnchorReport(report: Hex): AnchorReport {
	const [seq, headHash, price, decimals, priceUpdatedAt] = decodeAbiParameters(REPORT_TYPES, report)
	return { seq, headHash, price, decimals, priceUpdatedAt }
}

/** "67000.12" from an answer and its decimals, without floating point. */
export function formatPrice(answer: bigint, decimals: number): string {
	const base = 10n ** BigInt(decimals)
	const whole = answer / base
	const frac = (answer % base).toString().padStart(decimals, '0').replace(/0+$/, '')
	return frac ? `${whole}.${frac}` : whole.toString()
}

export interface CrossCheck {
	sepolia: string
	mainnet: string
	deviationBps: number
}

/**
 * The same Data Feed pair read on two chains (Sepolia, where the anchor is written, and Ethereum mainnet): the gap
 * in basis points of the mainnet price, in exact integer arithmetic. Above `maxBps` the chains disagree and nothing
 * is anchored; the gap is reported either way.
 */
export function crossCheck(sepolia: PriceReading, mainnet: PriceReading, maxBps: number): CrossCheck {
	if (sepolia.answer <= 0n || mainnet.answer <= 0n) throw new Error('price feed answer must be positive on both chains')
	const d = Math.max(sepolia.decimals, mainnet.decimals)
	const a = sepolia.answer * 10n ** BigInt(d - sepolia.decimals)
	const b = mainnet.answer * 10n ** BigInt(d - mainnet.decimals)
	const gap = a > b ? a - b : b - a
	const deviationBps = Number((gap * 10_000n) / b)
	if (deviationBps > maxBps) throw new Error(`the Sepolia and mainnet Data Feeds disagree by ${deviationBps} bps (limit ${maxBps})`)
	return { sepolia: formatPrice(sepolia.answer, sepolia.decimals), mainnet: formatPrice(mainnet.answer, mainnet.decimals), deviationBps }
}
