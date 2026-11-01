import {
	bytesToHex,
	consensusIdenticalAggregation,
	type CronPayload,
	cre,
	getNetwork,
	type HTTPSendRequester,
	type Runtime,
	TxStatus,
} from '@chainlink/cre-sdk'
import type { Address } from 'viem'
import { z } from 'zod'
import { AggregatorV3 } from '../contracts/evm/ts/generated/AggregatorV3'
import { LedgerAnchor } from '../contracts/evm/ts/generated/LedgerAnchor'
import { agreeOnHead, crossCheck, encodeAnchorReport, formatPrice, type LedgerHead, parseHead } from './logic'
import { verifyHeadDossier } from './verdict'

export const configSchema = z.object({
	// 6-field cron (seconds first), e.g. "0 0 0 * * *" for daily at 00:00 UTC
	schedule: z.string(),
	// Public URL of the ledger head JSON written by `python -m verdict_desk head`
	headUrl: z.string(),
	// A second, independent publication of the same head (the GitHub Pages site, not the raw repository file)
	headUrlMirror: z.string(),
	// The whole last dossier, published next to each head; its verdict is re-derived before anything is anchored
	headDossierUrl: z.string(),
	headDossierUrlMirror: z.string(),
	chainSelectorName: z.string(),
	priceFeedAddress: z.string(),
	anchorAddress: z.string(),
	gasLimit: z.string(),
	// The same Data Feed pair read on a second chain (Ethereum mainnet) and compared with the Sepolia reading
	mainnetChainSelectorName: z.string(),
	mainnetPriceFeedAddress: z.string(),
	// Above this gap (basis points) between the two chains, nothing is anchored
	maxFeedDeviationBps: z.number().int().positive(),
})

export type Config = z.infer<typeof configSchema>

/** A head both sources agree on, whose dossier hashes to it and re-derives its own recorded verdict. */
export interface VerifiedHead extends LedgerHead {
	rulesVersion: string
	action: string
	edge: number
}

const fetchText = (sendRequester: HTTPSendRequester, url: string, what: string): string => {
	const response = sendRequester.sendRequest({ method: 'GET', url }).result()
	if (response.statusCode !== 200) {
		throw new Error(`${what} request to ${url} failed with status ${response.statusCode}`)
	}
	return Buffer.from(response.body).toString('utf-8')
}

/**
 * Every node fetches the head from TWO independent publications and requires them to agree, then fetches the
 * dossier behind it from both, checks that each hashes to that head, and re-derives the verdict from the frozen
 * evidence (verdict.ts, the same rules as the Python judge). Consensus then requires all nodes to reach the same
 * result. A source down, a disagreement, a dossier that is not the head, or a verdict its evidence does not
 * support: nothing is anchored.
 */
const fetchHead = (sendRequester: HTTPSendRequester, config: Config): VerifiedHead => {
	const head = agreeOnHead(
		parseHead(fetchText(sendRequester, config.headUrl, 'ledger head')),
		parseHead(fetchText(sendRequester, config.headUrlMirror, 'ledger head')),
	)
	let replay = verifyHeadDossier(fetchText(sendRequester, config.headDossierUrl, 'head dossier'), head)
	replay = verifyHeadDossier(fetchText(sendRequester, config.headDossierUrlMirror, 'head dossier'), head)
	return { ...head, rulesVersion: replay.rulesVersion, action: replay.action, edge: replay.edge }
}

function evmClientFor(chainSelectorName: string, isTestnet: boolean) {
	const network = getNetwork({ chainFamily: 'evm', chainSelectorName, isTestnet })
	if (!network) throw new Error(`network not found for chain selector name: ${chainSelectorName}`)
	return new cre.capabilities.EVMClient(network.chainSelector.selector)
}

function readFeed(runtime: Runtime<Config>, evmClient: InstanceType<typeof cre.capabilities.EVMClient>, address: string) {
	const feed = new AggregatorV3(evmClient, address as Address)
	const pair = feed.description(runtime)
	const decimals = feed.decimals(runtime)
	const [, answer, , updatedAt] = feed.latestRoundData(runtime)
	return { pair, answer, decimals, updatedAt }
}

/**
 * Reads the Data Feed on Sepolia and the same pair on Ethereum mainnet, refuses if the two chains disagree beyond
 * the limit, and writes (ledger head, Sepolia price) to LedgerAnchor through a CRE report.
 */
export function anchorLedger(runtime: Runtime<Config>, head: LedgerHead | VerifiedHead): string {
	const config = runtime.config
	const evmClient = evmClientFor(config.chainSelectorName, true)

	const { pair, answer, decimals, updatedAt } = readFeed(runtime, evmClient, config.priceFeedAddress)
	runtime.log(`Data Feed ${pair} ${config.priceFeedAddress}: ${formatPrice(answer, decimals)} (updatedAt ${updatedAt})`)
	const main = readFeed(runtime, evmClientFor(config.mainnetChainSelectorName, false), config.mainnetPriceFeedAddress)
	const cross = crossCheck({ answer, decimals, updatedAt }, { answer: main.answer, decimals: main.decimals, updatedAt: main.updatedAt }, config.maxFeedDeviationBps)
	runtime.log(`Cross-chain check: ${pair} on Sepolia ${cross.sepolia}, ${main.pair} on ${config.mainnetChainSelectorName} ${cross.mainnet}, gap ${cross.deviationBps} bps (limit ${config.maxFeedDeviationBps})`)

	const report = encodeAnchorReport(head, { answer, decimals, updatedAt })
	const anchor = new LedgerAnchor(evmClient, config.anchorAddress as Address)
	const response = anchor.writeReport(runtime, report, { gasLimit: config.gasLimit })
	if (response.txStatus !== TxStatus.SUCCESS) {
		throw new Error(`failed to write report: ${response.errorMessage || response.txStatus}`)
	}
	const txHash = response.txHash ? bytesToHex(response.txHash) : '0x'
	runtime.log(`Anchored seq ${head.seq} head ${head.hash} at tx ${txHash}`)
	const replayed = 'action' in head ? { rules: head.rulesVersion, action: head.action, edge: head.edge } : undefined
	return JSON.stringify({ seq: head.seq, head: head.hash, symbol: head.symbol, replayed, feed: pair, price: formatPrice(answer, decimals), crossChain: cross, txHash })
}

export const onCronTrigger = (runtime: Runtime<Config>, payload: CronPayload): string => {
	if (!payload.scheduledExecutionTime) throw new Error('scheduled execution time is required')
	runtime.log(`Anchoring ledger head from ${runtime.config.headUrl} and ${runtime.config.headUrlMirror}`)
	const head = new cre.capabilities.HTTPClient()
		.sendRequest(runtime, fetchHead, consensusIdenticalAggregation<VerifiedHead>())(runtime.config)
		.result()
	runtime.log(`Replayed dossier #${head.seq} under rules ${head.rulesVersion}: ${head.action} edge ${head.edge}, the same as recorded; anchoring it`)
	return anchorLedger(runtime, head)
}

export function initWorkflow(config: Config) {
	return [cre.handler(new cre.capabilities.CronCapability().trigger({ schedule: config.schedule }), onCronTrigger)]
}
