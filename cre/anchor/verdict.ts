// Re-derives a dossier's verdict from its own frozen evidence, inside the CRE workflow, before anything is anchored.
//
// A line-for-line port of verdict_desk/features_v2.py, the argument rules of verdict_desk/agents_v2.py (rules v2 to
// v6, including the multi-KOL convergence and the v6 LLM judge) and citations.validate_ruling. verdict.test.ts runs
// it over every v2+ dossier of the published ledger and fails on the first one whose features, action or edge differ
// from what the Python recorded. Pure functions: no CRE runtime, no network.
import { sha256, toBytes } from 'viem'

type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
type Obj = Record<string, any>

export interface Argument {
	claim: string
	evidence_ref: string
	weight: number
}

export interface Replay {
	seq: number
	hash: string
	rulesVersion: string
	action: string
	edge: number
	recordedAction: string
	recordedEdge: number
	featuresMatch: boolean
	/** bull and bear scores, and for a position its entry, stop, target and size, equal the record */
	numbersMatch: boolean
	matches: boolean
}

const CAPITAL = 10_000.0
const STOP_ATR = 1.5
const TAKE_ATR = 3.0

/** agents_v2.judge's sizing: entry at the last close, ATR stop and target, size from the recorded risk limit. */
function sizing(f: Obj, action: string, riskPct: number) {
	if (action !== 'LONG' && action !== 'SHORT') return null
	const entry = f.last_close
	const stopDistance = f.atr_14 * STOP_ATR
	const direction = action === 'LONG' ? 1 : -1
	return {
		entry,
		stop: pyRound(entry - direction * stopDistance, 6),
		take_profit: pyRound(entry + direction * f.atr_14 * TAKE_ATR, 6),
		size: pyRound((CAPITAL * riskPct) / 100 / stopDistance, 8),
	}
}

// ---------- the same canonical JSON and SHA-256 as verdict_desk/canonical.py and web/canonical.js ----------

export function canonical(v: Json): string {
	if (v === null || typeof v !== 'object') {
		if (typeof v === 'number' && !Number.isFinite(v)) throw new Error('NaN and Infinity cannot be canonicalized')
		return JSON.stringify(v)
	}
	if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`
	return `{${Object.keys(v)
		.sort()
		.map((k) => `${JSON.stringify(k)}:${canonical(v[k] as Json)}`)
		.join(',')}}`
}

/** SHA-256 of the canonical body (every key but `hash`): what the ledger chains and what the head publishes. */
export function dossierHash(dossier: Obj): string {
	const body: Obj = {}
	for (const k of Object.keys(dossier)) if (k !== 'hash') body[k] = dossier[k]
	return sha256(toBytes(canonical(body as Json))).slice(2)
}

// ---------- Python's arithmetic, where JavaScript differs ----------

/** Python's round(): the exact binary value decides, and an exact tie goes to the even digit (as web/trust.js). */
export function pyRound(x: number, places: number): number {
	const long = x.toFixed(places + 20)
	const dot = long.indexOf('.')
	const tail = long.slice(dot + 1 + places)
	if (/^50*$/.test(tail)) {
		const kept = long.slice(0, dot + 1 + places)
		const lastDigit = Number(kept.replace(/[^0-9]/g, '').slice(-1))
		if (lastDigit % 2 === 0) return Number(kept)
		return Number((Number(kept) + (x < 0 ? -1 : 1) * 10 ** -places).toFixed(places))
	}
	return Number(x.toFixed(places))
}

/** Python's math.fsum (Shewchuk): exactly rounded, so the order of the weights cannot change the sum. */
export function fsum(values: number[]): number {
	const partials: number[] = []
	for (let x of values) {
		let i = 0
		for (let y of partials) {
			if (Math.abs(x) < Math.abs(y)) [x, y] = [y, x]
			const hi = x + y
			const lo = y - (hi - x)
			if (lo) partials[i++] = lo
			x = hi
		}
		partials.length = i
		partials.push(x)
	}
	return partials.reduce((a, b) => a + b, 0)
}

const sum = (xs: number[]): number => xs.reduce((a, b) => a + b, 0) // Python's sum(): left to right from 0

// ---------- features_v2.compute ----------

const BULLISH_WORDS = ['surge', 'rally', 'record', 'approval', 'inflow', 'adoption', 'gain', 'soar', 'bull']
const BEARISH_WORDS = ['crash', 'plunge', 'hack', 'ban', 'lawsuit', 'outflow', 'liquidat', 'drop', 'bear', 'fraud']

function ema(values: number[], period: number): number {
	const k = 2 / (period + 1)
	let current = values[0]
	for (const value of values.slice(1)) current = value * k + current * (1 - k)
	return current
}

const sma = (values: number[], period: number): number => sum(values.slice(-period)) / period

function rsi(closes: number[], period = 14): number {
	const gains: number[] = []
	const losses: number[] = []
	for (let i = 1; i < closes.length; i++) {
		const diff = closes[i] - closes[i - 1]
		gains.push(Math.max(0, diff))
		losses.push(Math.max(0, -diff))
	}
	let avgGain = sum(gains.slice(0, period)) / period
	let avgLoss = sum(losses.slice(0, period)) / period
	for (let i = period; i < gains.length; i++) {
		avgGain = (avgGain * (period - 1) + gains[i]) / period
		avgLoss = (avgLoss * (period - 1) + losses[i]) / period
	}
	if (avgLoss === 0) return 100.0
	const rs = avgGain / avgLoss
	return 100 - 100 / (1 + rs)
}

function macd(closes: number[], fast = 12, slow = 26, signal = 9) {
	const macdLine = ema(closes, fast) - ema(closes, slow)
	const values: number[] = []
	for (let i = slow; i <= closes.length; i++) values.push(ema(closes.slice(0, i), fast) - ema(closes.slice(0, i), slow))
	const signalLine = values.length >= signal ? ema(values, signal) : macdLine
	return { macd: pyRound(macdLine, 6), signal: pyRound(signalLine, 6), histogram: pyRound(macdLine - signalLine, 6) }
}

function bollinger(closes: number[], period = 20, numStd = 2.0) {
	const recent = closes.slice(-period)
	const middle = sma(closes, period)
	const variance = sum(recent.map((c) => (c - middle) ** 2)) / period
	const std = Math.sqrt(variance) // Python's variance ** 0.5 is the correctly rounded square root
	return {
		upper: pyRound(middle + numStd * std, 6),
		middle: pyRound(middle, 6),
		lower: pyRound(middle - numStd * std, 6),
		pct_b: std > 0 ? pyRound((closes[closes.length - 1] - (middle - numStd * std)) / (2 * numStd * std), 4) : 0.5,
	}
}

function newsTone(articles: Obj[]) {
	const bull = articles.filter((a) => BULLISH_WORDS.some((w) => String(a.title).toLowerCase().includes(w)))
	const bear = articles.filter((a) => BEARISH_WORDS.some((w) => String(a.title).toLowerCase().includes(w)))
	return {
		bullish: bull.length,
		bearish: bear.length,
		total: articles.length,
		bullish_titles: bull.slice(0, 3).map((a) => a.title),
		bearish_titles: bear.slice(0, 3).map((a) => a.title),
	}
}

export function computeFeatures(packet: Obj): Obj {
	const candles: Obj[] = packet.market.candles
	const closes = candles.map((c) => c.c as number)
	const volumes = candles.map((c) => c.v as number)
	const last = closes[closes.length - 1]
	const lookback = candles.slice(-25, -1)
	const trueRanges = candles.slice(-14).map((c) => c.h - c.l)
	const m = macd(closes)
	const bb = bollinger(closes)
	const out: Obj = {
		last_close: last,
		ema_fast: pyRound(ema(closes, 12), 6),
		ema_slow: pyRound(ema(closes, 48), 6),
		return_24: pyRound((last / closes[closes.length - 25] - 1) * 100, 4),
		range_high_24: Math.max(...lookback.map((c) => c.h)),
		range_low_24: Math.min(...lookback.map((c) => c.l)),
		volume_ratio: pyRound(volumes[volumes.length - 1] / (sum(volumes.slice(-25, -1)) / 24), 4),
		atr_14: pyRound(sum(trueRanges) / trueRanges.length, 6),
		funding_rate: packet.funding.rate ?? null,
		news: newsTone(packet.news?.articles ?? []),
		rsi_14: pyRound(rsi(closes), 4),
		macd: m.macd,
		macd_signal: m.signal,
		macd_histogram: m.histogram,
		bb_upper: bb.upper,
		bb_middle: bb.middle,
		bb_lower: bb.lower,
		bb_pct_b: bb.pct_b,
	}
	if ('freshness' in packet) out.freshness = packet.freshness
	return out
}

// ---------- the argument rules of agents_v2 ----------

interface Rules {
	version: string
	entryThreshold: number
	volatilityPct: number
	richStates: boolean
	narrativeSignal: boolean
	trustWeighted: boolean
	llmJudge: boolean
}

const RULES: Record<string, Rules> = Object.fromEntries(
	(
		[
			['v2', false, false, false, false],
			['v3', true, false, false, false],
			['v4', true, true, false, false],
			['v5', true, true, true, false],
			['v6', true, true, true, true],
		] as const
	).map(([version, richStates, narrativeSignal, trustWeighted, llmJudge]) => [
		version,
		{ version, entryThreshold: 1.5, volatilityPct: 1.5, richStates, narrativeSignal, trustWeighted, llmJudge },
	]),
)

export const SUPPORTED_RULES = Object.keys(RULES)

const arg = (claim: string, evidence_ref: string, weight: number): Argument => ({ claim, evidence_ref, weight })

function bullArgs(f: Obj): Argument[] {
	const a: Argument[] = []
	if (f.ema_fast > f.ema_slow) a.push(arg('Short-term trend above long-term trend', 'features.ema_fast>ema_slow', 1.5))
	if (f.last_close > f.range_high_24) a.push(arg('Close broke above the 24-candle range high', 'features.range_high_24', 2.0))
	if (f.return_24 > 2) a.push(arg('Strong momentum over the last 24 candles', 'features.return_24', 0.75))
	if (f.volume_ratio > 1.5 && f.return_24 > 0) a.push(arg('Rising price on above-average volume', 'features.volume_ratio', 0.75))
	if (f.funding_rate !== null && f.funding_rate < 0) a.push(arg('Negative funding: shorts are paying, crowded short side', 'features.funding_rate', 0.5))
	if (f.news.bullish > f.news.bearish) a.push(arg('News flow leans positive', 'features.news.bullish_titles', 0.5))
	if (f.rsi_14 != null && f.rsi_14 < 35) a.push(arg('RSI oversold (<35): bounce likely', 'features.rsi_14', 1.25))
	if (f.macd_histogram != null && f.macd_histogram > 0) a.push(arg('MACD histogram positive: bullish momentum', 'features.macd_histogram', 1.0))
	if (f.bb_pct_b != null && f.bb_pct_b < 0.1) a.push(arg('Price near Bollinger lower band (%B<0.1): oversold', 'features.bb_pct_b', 1.0))
	if (f.volume_ratio > 2.0) a.push(arg('Volume surge (>2x average): strong conviction', 'features.volume_ratio', 1.0))
	if (f.bb_lower != null && f.last_close < f.bb_lower) a.push(arg('Price below Bollinger lower band: mean reversion likely', 'features.bb_lower', 1.25))
	return a
}

function bearArgs(f: Obj): Argument[] {
	const a: Argument[] = []
	if (f.ema_fast < f.ema_slow) a.push(arg('Short-term trend below long-term trend', 'features.ema_fast<ema_slow', 1.5))
	if (f.last_close < f.range_low_24) a.push(arg('Close broke below the 24-candle range low', 'features.range_low_24', 2.0))
	if (f.return_24 < -2) a.push(arg('Strong downside momentum over the last 24 candles', 'features.return_24', 1.0))
	if (f.return_24 < -4) a.push(arg('Severe drop over 24 candles: selloff intensifying', 'features.return_24', 1.5))
	if (f.volume_ratio > 1.5 && f.return_24 < 0) a.push(arg('Falling price on above-average volume', 'features.volume_ratio', 0.75))
	if (f.funding_rate !== null && f.funding_rate > 0.0005) a.push(arg('High positive funding: longs are crowded', 'features.funding_rate', 0.5))
	if (f.news.bearish > f.news.bullish) a.push(arg('News flow leans negative', 'features.news.bearish_titles', 0.5))
	if (f.rsi_14 != null && f.rsi_14 > 65) a.push(arg('RSI elevated (>65): overbought, pullback risk', 'features.rsi_14', 1.0))
	if (f.rsi_14 != null && f.rsi_14 > 75) a.push(arg('RSI very high (>75): extreme overbought', 'features.rsi_14', 1.25))
	if (f.macd_histogram != null && f.macd_histogram < 0) a.push(arg('MACD histogram negative: bearish momentum', 'features.macd_histogram', 1.0))
	if (f.bb_pct_b != null && f.bb_pct_b > 0.85) a.push(arg('Price near Bollinger upper band (%B>0.85): stretched', 'features.bb_pct_b', 1.0))
	if (f.bb_pct_b != null && f.bb_pct_b > 0.95) a.push(arg('Price above Bollinger upper band (%B>0.95): extreme', 'features.bb_pct_b', 1.25))
	if (f.bb_upper != null && f.last_close > f.bb_upper) a.push(arg('Price above Bollinger upper: mean reversion due', 'features.bb_upper', 1.25))
	if (f.macd_histogram != null && f.return_24 > 0 && f.macd_histogram < 0) a.push(arg('Price up but MACD negative: bearish divergence', 'features.macd_histogram', 1.5))
	if (f.last_close < f.ema_fast && f.ema_fast < f.ema_slow) a.push(arg('Price below both EMAs: confirmed downtrend', 'features.ema_fast<ema_slow', 1.0))
	if (f.last_close <= f.range_low_24 * 1.002) a.push(arg('Close at 24-candle low: distribution pattern', 'features.range_low_24', 0.75))
	return a
}

/** agents_v2.convergence_args: a multi-KOL convergence on the symbol becomes one argument with a stated weight. */
function convergenceArgs(narrative: Obj | undefined, symbol: string | undefined, trustWeighted: boolean): [Argument[], Argument[]] {
	if (!narrative || !symbol) return [[], []]
	let base = symbol.toUpperCase()
	for (const suffix of ['USDT', 'USDC', 'BUSD', 'USDE', 'USD', 'DAI']) {
		if (base.endsWith(suffix)) {
			base = base.slice(0, -suffix.length)
			break
		}
	}
	const bull: Argument[] = []
	const bear: Argument[] = []
	for (const c of narrative.convergence ?? []) {
		if (String(c.token || '').toUpperCase() !== base) continue
		const voices: string[] = [...(c.voices ?? [])]
		let weight: number
		if (trustWeighted) {
			const table = narrative.trust?.voices || {}
			const unique = [...new Set(voices)] // the Python builds a dict keyed by voice
			const weights = unique.map((v) => Number(table[v]?.weight ?? 1.0))
			weight = fsum([...weights].sort((a, b) => b - a).slice(0, 4)) * 0.5
		} else {
			weight = Math.min(voices.length, 4) * 0.5
		}
		if (c.max_conviction === 'high') weight += 0.5
		const a = arg(`convergence ${c.bias} on ${c.token}`, `narrative.convergence.${c.token}`, pyRound(weight, 2))
		;(c.bias === 'bullish' ? bull : bear).push(a)
	}
	return [bull, bear]
}

// ---------- citations.validate_ruling (rules v6) ----------

export const JUDGE_WEIGHT: Record<string, number> = { low: 0.5, medium: 0.75, high: 1.0 }
const NUMBER = /-?\d+(?:\.\d+)?/g
const CLAIM_TOKEN = /\b[BSE]\d+\b/g

function normNumber(n: string): string {
	const r = pyRound(Number.parseFloat(n), 6)
	return Object.is(r, -0) ? '-0' : String(r) // Python compares repr(): -0.0 and 0.0 differ
}

export function validateRuling(ruling: unknown, bullOk: Obj[], bearOk: Obj[], items: Record<string, string>): { accepted: boolean; weight: number } {
	const voided = { accepted: false, weight: 0 }
	if (typeof ruling !== 'object' || ruling === null || Array.isArray(ruling)) return voided
	const r = ruling as Obj
	if (!['bull', 'bear', 'neutral'].includes(r.lean)) return voided
	if (r.lean === 'neutral') return { accepted: true, weight: 0 }
	if (!(typeof r.conviction === 'string' && r.conviction in JUDGE_WEIGHT)) return voided
	const relied = r.relied_on
	if (!Array.isArray(relied) || relied.length === 0) return voided
	const labels: Record<string, Obj> = {}
	bullOk.forEach((c, i) => (labels[`B${i + 1}`] = c))
	bearOk.forEach((c, i) => (labels[`S${i + 1}`] = c))
	if (relied.some((x) => typeof x !== 'string' || !(x in labels))) return voided
	if (!relied.some((x: string) => x.startsWith(r.lean === 'bull' ? 'B' : 'S'))) return voided
	const cited = relied
		.flatMap((x: string) => (labels[x].cites ?? []).filter((e: unknown) => typeof e === 'string').map((e: string) => items[e] ?? ''))
		.join(' ')
	const allowed = new Set((cited.match(NUMBER) ?? []).map(normNumber))
	const reason = String(r.reason ?? '').replace(CLAIM_TOKEN, ' ')
	if ((reason.match(NUMBER) ?? []).some((n) => !allowed.has(normNumber(n)))) return voided
	return { accepted: true, weight: JUDGE_WEIGHT[r.conviction] }
}

function judgeArgs(layer: Obj | undefined): [Argument[], Argument[]] {
	if (!layer || typeof layer.judge !== 'object' || layer.judge === null) return [[], []]
	const ruling = layer.judge.ruling
	const check = validateRuling(ruling, layer.bull?.accepted || [], layer.bear?.accepted || [], layer.evidence_index || {})
	if (!check.accepted || check.weight <= 0) return [[], []]
	const a = arg(`LLM judge leans ${ruling.lean}`, 'llm.judge', check.weight)
	return ruling.lean === 'bull' ? [[a], []] : [[], [a]]
}

// ---------- agents_v2.judge: the action and the edge ----------

function action(edge: number, rules: Rules): string {
	if (edge >= rules.entryThreshold) return 'LONG'
	if (edge <= -rules.entryThreshold) return 'SHORT'
	return 'FLAT'
}

function richFlat(edge: number, bull: Argument[], bear: Argument[], f: Obj, rules: Rules): string {
	const t = rules.entryThreshold
	const bullScore = sum(bull.map((a) => a.weight))
	const bearScore = sum(bear.map((a) => a.weight))
	const lastClose = f.last_close || 0
	const atrPct = lastClose ? ((f.atr_14 || 0) / lastClose) * 100 : 0.0
	if (Math.min(bullScore, bearScore) >= t && atrPct >= rules.volatilityPct) return 'REDUCE_EXPOSURE'
	if (bull.some((a) => edge - a.weight <= -t)) return 'WATCH_REVERSAL'
	if (bear.some((a) => edge + a.weight >= t)) return 'WATCH_REVERSAL'
	return 'FLAT'
}

export function decide(f: Obj, rules: Rules, narrative?: Obj, symbol?: string, layer?: Obj) {
	const bull = bullArgs(f)
	const bear = bearArgs(f)
	if (rules.narrativeSignal) {
		const [cb, cs] = convergenceArgs(narrative, symbol, rules.trustWeighted)
		bull.push(...cb)
		bear.push(...cs)
	}
	if (rules.llmJudge) {
		const [jb, js] = judgeArgs(layer)
		bull.push(...jb)
		bear.push(...js)
	}
	const edge = pyRound(sum(bull.map((a) => a.weight)) - sum(bear.map((a) => a.weight)), 4)
	const fresh = f.freshness
	if (fresh != null && !(fresh.ok ?? true)) return { action: 'BLOCKED_STALE', edge, bull, bear }
	let act = action(edge, rules)
	if (act === 'FLAT' && rules.richStates) act = richFlat(edge, bull, bear, f, rules)
	return { action: act, edge, bull, bear }
}

/** Recomputes the features and the verdict of one dossier from its frozen evidence and compares them with the record. */
export function replayDossier(dossier: Obj): Replay {
	const recorded = dossier.verdict ?? {}
	const version = String(recorded.rules_version ?? 'v1')
	const rules = RULES[version]
	if (!rules) throw new Error(`rules ${version} cannot be replayed here (supported: ${SUPPORTED_RULES.join(', ')})`)
	const evidence = dossier.evidence
	const f = computeFeatures(evidence)
	const featuresMatch = canonical(f as Json) === canonical(dossier.features as Json)
	const out = decide(f, rules, evidence.narrative, evidence.symbol, dossier.llm)
	const bullScore = sum(out.bull.map((a) => a.weight))
	const bearScore = sum(out.bear.map((a) => a.weight))
	let numbersMatch = bullScore === recorded.bull_score && bearScore === recorded.bear_score
	const pos = sizing(f, out.action, recorded.risk_pct ?? 1.0)
	if (pos && out.action === recorded.action) {
		numbersMatch &&= pos.entry === recorded.entry && pos.stop === recorded.stop && pos.take_profit === recorded.take_profit && pos.size === recorded.size
	} else if (!pos) {
		// A decision that sizes no position must not carry one: otherwise a WATCH or FLAT head could gain a size and still replay.
		numbersMatch &&= ['entry', 'stop', 'take_profit', 'size'].every((k) => recorded[k] === undefined || recorded[k] === null)
	}
	const matches = featuresMatch && numbersMatch && out.action === recorded.action && out.edge === recorded.edge
	return {
		seq: dossier.seq,
		hash: dossier.hash,
		rulesVersion: version,
		action: out.action,
		edge: out.edge,
		recordedAction: recorded.action,
		recordedEdge: recorded.edge,
		featuresMatch,
		numbersMatch,
		matches,
	}
}

/**
 * What the workflow requires before it anchors: the dossier hashes to the head both sources published,
 * and its verdict re-derives from its own evidence. Throws with the reason; never anchors on a doubt.
 */
export function verifyHeadDossier(text: string, head: { seq: number; hash: string }): Replay {
	let dossier: Obj
	try {
		dossier = JSON.parse(text)
	} catch {
		throw new Error('head dossier is not valid JSON')
	}
	if (dossier?.seq !== head.seq) throw new Error(`head dossier is seq ${dossier?.seq}, the head is seq ${head.seq}`)
	const recomputed = dossierHash(dossier)
	if (recomputed !== head.hash || dossier.hash !== head.hash) throw new Error(`head dossier #${head.seq} does not hash to the published head`)
	const replay = replayDossier(dossier)
	if (!replay.matches) {
		throw new Error(
			`head dossier #${head.seq} does not replay: recomputed ${replay.action} edge ${replay.edge}, recorded ${replay.recordedAction} edge ${replay.recordedEdge}` +
				(replay.featuresMatch ? '' : ' (features differ)') +
				(replay.numbersMatch ? '' : ' (scores or position numbers differ)'),
		)
	}
	return replay
}
