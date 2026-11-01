import { describe, expect, test } from 'bun:test'
import { attack, KINDS } from './battery'

const LEDGER: any[] = (await Bun.file(`${import.meta.dir}/../../ledger/ledger.jsonl`).text())
	.split('\n')
	.filter(Boolean)
	.map((line: string) => JSON.parse(line))
const HEAD = LEDGER[LEDGER.length - 1]

describe('attacks on the check the CRE workflow runs before it anchors', () => {
	const result = attack(HEAD, 300, 'test-seed')

	test('every kind of edit is tried', () => {
		for (const kind of KINDS) expect(result.byKind[kind].tried).toBeGreaterThan(0)
		expect(result.tried).toBe(300)
	})

	test('an edit kept under the published head hash is always refused', () => {
		expect(result.refusedUnderPublishedHead).toBe(result.tried)
	})

	test('with the hash rewritten and republished, no edit that changes the decision gets through', () => {
		expect(result.decisionEditsPassed).toBe(0)
		expect(result.decisionEditsRefused).toBeGreaterThan(0)
	})

	test('edits that leave the decision untouched are counted, not hidden', () => {
		expect(result.decisionEditsRefused + result.inertEdits).toBe(result.tried)
	})

	test('the same seed gives the same attacks', () => {
		expect(attack(HEAD, 50, 'again')).toEqual(attack(HEAD, 50, 'again'))
	})
})
