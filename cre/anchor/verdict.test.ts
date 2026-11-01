import { describe, expect, test } from 'bun:test'
import { dossierHash, fsum, pyRound, replayDossier, SUPPORTED_RULES, validateRuling, verifyHeadDossier } from './verdict'

const LEDGER: any[] = (await Bun.file(`${import.meta.dir}/../../ledger/ledger.jsonl`).text())
	.split('\n')
	.filter(Boolean)
	.map((line: string) => JSON.parse(line))
const REPLAYABLE = LEDGER.filter((d) => SUPPORTED_RULES.includes(d.verdict?.rules_version))
const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x))
const headOf = (d: any) => ({ seq: d.seq, hash: d.hash })

describe('parity with the Python judge on the published ledger', () => {
	test('there are dossiers of every replayable rules version to check', () => {
		expect(REPLAYABLE.length).toBeGreaterThan(200)
	})

	test('every dossier hashes to the hash it carries (same canonical JSON as the Python)', () => {
		const bad = LEDGER.filter((d) => dossierHash(d) !== d.hash).map((d) => d.seq)
		expect(bad).toEqual([])
	})

	test('every v2+ dossier re-derives the same features, action and edge from its own evidence', () => {
		const bad = REPLAYABLE.map(replayDossier)
			.filter((r: any) => !r.matches)
			.map((r: any) => `#${r.seq} ${r.rulesVersion}: ${r.action} ${r.edge} vs ${r.recordedAction} ${r.recordedEdge} features ${r.featuresMatch}`)
		expect(bad).toEqual([])
	})

	test('the v6 judge is replayed too, and its vote really moves the edge it is counted in', () => {
		const judged = REPLAYABLE.filter((d) => d.verdict.rules_version === 'v6' && d.llm?.judge?.accepted && d.llm.judge.weight > 0)
		expect(judged.length).toBeGreaterThan(0)
		for (const d of judged) {
			const without = clone(d)
			delete without.llm.judge
			expect(replayDossier(without).edge).not.toBe(d.verdict.edge)
		}
	})
})

describe('the check the workflow runs before it anchors', () => {
	const head = LEDGER[LEDGER.length - 1]
	const text = JSON.stringify(head)

	test('accepts the real head dossier', () => {
		const r = verifyHeadDossier(text, headOf(head))
		expect(r.matches).toBe(true)
		expect(r.action).toBe(head.verdict.action)
	})

	test('refuses a dossier that is not the published head', () => {
		expect(() => verifyHeadDossier(text, { seq: head.seq + 1, hash: head.hash })).toThrow('the head is seq')
		expect(() => verifyHeadDossier(text, { seq: head.seq, hash: '0'.repeat(64) })).toThrow('does not hash to the published head')
	})

	test('refuses an edited dossier even when its hash field was rewritten to match', () => {
		const forged = clone(head)
		forged.evidence.market.candles[forged.evidence.market.candles.length - 1].c *= 1.5
		forged.hash = dossierHash(forged)
		expect(() => verifyHeadDossier(JSON.stringify(forged), headOf(forged))).toThrow('does not replay')
	})

	test('refuses a verdict that its evidence does not support, even with a consistent hash', () => {
		const forged = clone(head)
		forged.verdict.action = forged.verdict.action === 'LONG' ? 'SHORT' : 'LONG'
		forged.hash = dossierHash(forged)
		expect(() => verifyHeadDossier(JSON.stringify(forged), headOf(forged))).toThrow('does not replay')
	})

	test('refuses a decision that sizes no position but carries one, even with a consistent hash', () => {
		const declined = [...LEDGER].reverse().find((d: any) => d.verdict?.entry === undefined && d.verdict?.rules_version === 'v6')
		expect(declined).toBeDefined()
		expect(() => verifyHeadDossier(JSON.stringify(declined), headOf(declined))).not.toThrow()
		const forged = clone(declined)
		forged.verdict.size = 1
		forged.hash = dossierHash(forged)
		expect(() => verifyHeadDossier(JSON.stringify(forged), headOf(forged))).toThrow('does not replay')
	})

	test('refuses text that is not JSON', () => {
		expect(() => verifyHeadDossier('nope', headOf(head))).toThrow('not valid JSON')
	})
})

describe('the ruling check matches citations.validate_ruling', () => {
	const items = { E1: 'BTCUSDT last close 101.74', E2: 'Return over the last 24 candles: -2.5 percent' }
	const bull = [{ claim: 'Holds at 101.74', cites: ['E1'] }]
	const bear = [{ claim: 'Lost -2.5 percent', cites: ['E2'] }]
	const ruling = (over: object = {}) => ({ lean: 'bear', conviction: 'medium', relied_on: ['S1'], reason: 'S1 shows -2.5 percent.', ...over })

	test('weights by conviction and voids what does not hold', () => {
		expect(validateRuling(ruling(), bull, bear, items)).toEqual({ accepted: true, weight: 0.75 })
		expect(validateRuling(ruling({ lean: 'neutral', relied_on: [] }), bull, bear, items)).toEqual({ accepted: true, weight: 0 })
		for (const bad of [{ lean: 'up' }, { conviction: 'sure' }, { relied_on: [] }, { relied_on: ['S9'] }, { lean: 'bull' }, { reason: 'Down to 95.' }]) {
			expect(validateRuling(ruling(bad), bull, bear, items).accepted).toBe(false)
		}
		expect(validateRuling('x', bull, bear, items).accepted).toBe(false)
	})
})

describe("Python's arithmetic", () => {
	test('round() goes by the exact binary value and sends exact ties to the even digit', () => {
		expect(pyRound(0.625, 2)).toBe(0.62)
		expect(pyRound(1.835, 2)).toBe(1.83)
		expect(pyRound(-2.3099999999999996, 4)).toBe(-2.31)
	})

	test('fsum is exact whatever the order', () => {
		expect(fsum([0.1, 0.2, 0.3])).toBe(0.6)
		expect(fsum([1e100, 1.0, -1e100])).toBe(1.0)
	})
})
