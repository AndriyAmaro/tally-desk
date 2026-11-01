// A tamper battery against the check the CRE workflow runs before it anchors (verifyHeadDossier).
//
//   bun run battery.ts [trials] [seed]     # prints the result as JSON; the seed defaults to the head hash
//
// Each trial edits one thing in a copy of the head dossier and asks the check twice:
//   1. under the head both publications agree on (the attacker could not republish): must always be refused;
//   2. with the dossier's hash rewritten and republished as the head (the attacker controls the publications):
//      refused if the edit changes the decision (its inputs, the features, the verdict, the scores, the
//      position, or the v6 judge's vote). An edit that changes none of these, such as a candle's open price,
//      cannot be caught by a replay; it is counted as inert, and only the hash chain and the external
//      witnesses (Bitcoin, Sigstore, the Sepolia anchor of the earlier head) can expose it.
import { canonical, computeFeatures, decide, dossierHash, replayDossier, SUPPORTED_RULES, verifyHeadDossier } from './verdict'

type Obj = Record<string, any>

export const KINDS = ['candle', 'funding', 'news', 'feature', 'verdict', 'score', 'position', 'judge', 'drop-candle'] as const
type Kind = (typeof KINDS)[number]

export interface BatteryResult {
	seq: number
	seed: string
	tried: number
	refusedUnderPublishedHead: number
	decisionEditsRefused: number
	decisionEditsPassed: number
	inertEdits: number
	byKind: Record<Kind, { tried: number; refused: number; inert: number; passed: number }>
}

/** A small seeded generator (mulberry32 over an FNV-1a hash of the seed): same seed, same attacks. */
function rng(seed: string) {
	let h = 2166136261
	for (const ch of seed) h = Math.imul(h ^ ch.charCodeAt(0), 16777619)
	let a = h >>> 0
	return () => {
		a = (a + 0x6d2b79f5) >>> 0
		let t = a
		t = Math.imul(t ^ (t >>> 15), t | 1)
		t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296
	}
}

const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x))

function nudge(x: number, r: () => number): number {
	const size = [1e-6, 1e-4, 1e-2, 0.2][Math.floor(r() * 4)]
	const out = x * (1 + (r() < 0.5 ? -1 : 1) * size)
	return out === x ? x + (r() < 0.5 ? -1 : 1) : out
}

function mutate(d: Obj, kind: Kind, r: () => number): void {
	const candles = d.evidence.market.candles
	const v = d.verdict
	switch (kind) {
		case 'candle': {
			const c = candles[Math.floor(r() * candles.length)]
			const field = ['o', 'h', 'l', 'c', 'v'][Math.floor(r() * 5)]
			c[field] = nudge(c[field], r)
			break
		}
		case 'drop-candle':
			candles.splice(Math.floor(r() * candles.length), 1)
			break
		case 'funding':
			d.evidence.funding.rate = nudge(d.evidence.funding.rate ?? 0.0001, r)
			break
		case 'news': {
			const articles = d.evidence.news?.articles ?? []
			if (articles.length) articles[Math.floor(r() * articles.length)].title += r() < 0.5 ? ' crash' : ' rally'
			else d.evidence.news = { ...(d.evidence.news ?? {}), articles: [{ title: r() < 0.5 ? 'Exchange hack' : 'Record rally', url: '', domain: '', seen: '' }] }
			break
		}
		case 'feature': {
			const keys = Object.keys(d.features).filter((k) => typeof d.features[k] === 'number')
			const k = keys[Math.floor(r() * keys.length)]
			d.features[k] = nudge(d.features[k], r)
			break
		}
		case 'verdict':
			if (r() < 0.5) v.action = v.action === 'SHORT' ? 'LONG' : 'SHORT'
			else v.edge = Math.round((v.edge + (r() < 0.5 ? -0.25 : 0.25)) * 100) / 100
			break
		case 'score':
			if (r() < 0.5) v.bull_score += 0.25
			else v.bear_score += 0.25
			break
		case 'position':
			if (v.entry === undefined) {
				v.size = 1 // a declined entry that suddenly carries a size
			} else {
				const field = ['entry', 'stop', 'take_profit', 'size'][Math.floor(r() * 4)]
				v[field] = nudge(v[field], r)
			}
			break
		case 'judge': {
			const j = d.llm?.judge
			if (!j?.ruling) {
				d.llm = { ...(d.llm ?? {}), judge: { ruling: { lean: 'bull', conviction: 'high', relied_on: ['B1'], reason: 'B1.' }, accepted: true, weight: 1 } }
			} else if (r() < 0.5) {
				j.ruling.lean = j.ruling.lean === 'bear' ? 'bull' : 'bear'
			} else {
				j.ruling.conviction = j.ruling.conviction === 'high' ? 'low' : 'high'
			}
			break
		}
	}
}

/** What the decision is: recomputed outputs plus what the dossier states, so a change on either side counts. */
function decisionOf(d: Obj): string {
	const rules = SUPPORTED_RULES.includes(d.verdict?.rules_version) ? d.verdict.rules_version : 'v6'
	let recomputed: unknown = null
	try {
		const f = computeFeatures(d.evidence)
		const out = decide(f, { version: rules, entryThreshold: 1.5, volatilityPct: 1.5, richStates: rules !== 'v2', narrativeSignal: ['v4', 'v5', 'v6'].includes(rules), trustWeighted: ['v5', 'v6'].includes(rules), llmJudge: rules === 'v6' }, d.evidence.narrative, d.evidence.symbol, d.llm)
		recomputed = { f, action: out.action, edge: out.edge, bull: out.bull.map((a) => a.weight), bear: out.bear.map((a) => a.weight) }
	} catch (err) {
		recomputed = String(err)
	}
	const v = d.verdict
	const stated = { features: d.features, action: v.action, edge: v.edge, bull: v.bull_score, bear: v.bear_score, entry: v.entry, stop: v.stop, take: v.take_profit, size: v.size }
	return canonical({ recomputed, stated } as any)
}

export function attack(head: Obj, trials: number, seed?: string): BatteryResult {
	const s = seed ?? head.hash
	const r = rng(s)
	const published = { seq: head.seq, hash: head.hash }
	const before = decisionOf(head)
	if (!replayDossier(head).matches) throw new Error(`head #${head.seq} does not replay before any attack`)
	const byKind = Object.fromEntries(KINDS.map((k) => [k, { tried: 0, refused: 0, inert: 0, passed: 0 }])) as BatteryResult['byKind']
	const out: BatteryResult = { seq: head.seq, seed: s, tried: 0, refusedUnderPublishedHead: 0, decisionEditsRefused: 0, decisionEditsPassed: 0, inertEdits: 0, byKind }
	for (let i = 0; i < trials; i++) {
		const kind = KINDS[i % KINDS.length]
		const d = clone(head)
		mutate(d, kind, r)
		out.tried++
		byKind[kind].tried++
		const refuses = (text: string, h: { seq: number; hash: string }) => {
			try {
				verifyHeadDossier(text, h)
				return false
			} catch {
				return true
			}
		}
		if (refuses(JSON.stringify(d), published)) out.refusedUnderPublishedHead++
		d.hash = dossierHash(d)
		const refused = refuses(JSON.stringify(d), { seq: d.seq, hash: d.hash })
		const changed = decisionOf(d) !== before
		if (refused) {
			out.decisionEditsRefused++
			byKind[kind].refused++
		} else if (changed) {
			out.decisionEditsPassed++
			byKind[kind].passed++
		} else {
			out.inertEdits++
			byKind[kind].inert++
		}
	}
	return out
}

if (import.meta.main) {
	const ledger = (await Bun.file(`${import.meta.dir}/../../ledger/ledger.jsonl`).text()).trim().split('\n')
	const head = JSON.parse(ledger[ledger.length - 1])
	const trials = Number(process.argv[2] ?? 3000)
	console.log(JSON.stringify(attack(head, trials, process.argv[3]), null, 1))
}
