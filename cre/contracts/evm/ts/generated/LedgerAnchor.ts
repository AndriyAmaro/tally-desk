// Code generated — DO NOT EDIT.
import {
  decodeEventLog,
  decodeFunctionResult,
  encodeEventTopics,
  encodeFunctionData,
  zeroAddress,
} from 'viem'
import type { Address, Hex } from 'viem'
import {
  bytesToHex,
  encodeCallMsg,
  EVMClient,
  hexToBase64,
  LAST_FINALIZED_BLOCK_NUMBER,
  prepareReportRequest,
  type EVMLog,
  type Runtime,
} from '@chainlink/cre-sdk'

export interface DecodedLog<T> extends Omit<EVMLog, 'data'> { data: T }

const encodeTopicValue = (t: Hex | Hex[] | null): string[] => {
  if (t == null) return []
  if (Array.isArray(t)) return t.map(hexToBase64)
  return [hexToBase64(t)]
}





/**
 * Filter params for Anchored. Only indexed fields can be used for filtering.
 * Indexed string/bytes must be passed as keccak256 hash (Hex).
 */
export type AnchoredTopics = {
  seq?: bigint
  headHash?: `0x${string}`
}

/**
 * Decoded Anchored event data.
 */
export type AnchoredDecoded = {
  seq: bigint
  headHash: `0x${string}`
  price: bigint
  priceDecimals: number
  priceUpdatedAt: bigint
}


export const LedgerAnchorABI = [{"inputs":[{"internalType":"address","name":"forwarder_","type":"address"},{"internalType":"address","name":"expectedWorkflowOwner_","type":"address"}],"stateMutability":"nonpayable","type":"constructor"},{"inputs":[],"name":"BadMetadata","type":"error"},{"inputs":[],"name":"EmptyHead","type":"error"},{"inputs":[{"internalType":"address","name":"sender","type":"address"}],"name":"NotForwarder","type":"error"},{"inputs":[{"internalType":"address","name":"owner","type":"address"}],"name":"NotWorkflowOwner","type":"error"},{"inputs":[{"internalType":"uint64","name":"seq","type":"uint64"},{"internalType":"uint64","name":"latest","type":"uint64"}],"name":"StaleSequence","type":"error"},{"anonymous":false,"inputs":[{"indexed":true,"internalType":"uint64","name":"seq","type":"uint64"},{"indexed":true,"internalType":"bytes32","name":"headHash","type":"bytes32"},{"indexed":false,"internalType":"int256","name":"price","type":"int256"},{"indexed":false,"internalType":"uint8","name":"priceDecimals","type":"uint8"},{"indexed":false,"internalType":"uint256","name":"priceUpdatedAt","type":"uint256"}],"name":"Anchored","type":"event"},{"inputs":[{"internalType":"uint64","name":"","type":"uint64"}],"name":"anchors","outputs":[{"internalType":"bytes32","name":"headHash","type":"bytes32"},{"internalType":"int256","name":"price","type":"int256"},{"internalType":"uint8","name":"priceDecimals","type":"uint8"},{"internalType":"uint256","name":"priceUpdatedAt","type":"uint256"},{"internalType":"uint256","name":"anchoredAt","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"expectedWorkflowOwner","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"forwarder","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"uint64","name":"seq","type":"uint64"},{"internalType":"bytes32","name":"headHash","type":"bytes32"}],"name":"isAnchored","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"latestSeq","outputs":[{"internalType":"uint64","name":"","type":"uint64"}],"stateMutability":"view","type":"function"},{"inputs":[{"internalType":"bytes","name":"metadata","type":"bytes"},{"internalType":"bytes","name":"report","type":"bytes"}],"name":"onReport","outputs":[],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"internalType":"bytes4","name":"interfaceId","type":"bytes4"}],"name":"supportsInterface","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"pure","type":"function"}] as const

export class LedgerAnchor {
  constructor(
    private readonly client: EVMClient,
    public readonly address: Address,
  ) {}

  anchors(
    runtime: Runtime<unknown>,
    arg0: bigint,
  ): readonly [`0x${string}`, bigint, number, bigint, bigint] {
    const callData = encodeFunctionData({
      abi: LedgerAnchorABI,
      functionName: 'anchors' as const,
      args: [arg0],
    })

    const result = this.client
      .callContract(runtime, {
        call: encodeCallMsg({ from: zeroAddress, to: this.address, data: callData }),
        blockNumber: LAST_FINALIZED_BLOCK_NUMBER,
      })
      .result()

    return decodeFunctionResult({
      abi: LedgerAnchorABI,
      functionName: 'anchors' as const,
      data: bytesToHex(result.data),
    }) as readonly [`0x${string}`, bigint, number, bigint, bigint]
  }

  expectedWorkflowOwner(
    runtime: Runtime<unknown>,
  ): `0x${string}` {
    const callData = encodeFunctionData({
      abi: LedgerAnchorABI,
      functionName: 'expectedWorkflowOwner' as const,
    })

    const result = this.client
      .callContract(runtime, {
        call: encodeCallMsg({ from: zeroAddress, to: this.address, data: callData }),
        blockNumber: LAST_FINALIZED_BLOCK_NUMBER,
      })
      .result()

    return decodeFunctionResult({
      abi: LedgerAnchorABI,
      functionName: 'expectedWorkflowOwner' as const,
      data: bytesToHex(result.data),
    }) as `0x${string}`
  }

  forwarder(
    runtime: Runtime<unknown>,
  ): `0x${string}` {
    const callData = encodeFunctionData({
      abi: LedgerAnchorABI,
      functionName: 'forwarder' as const,
    })

    const result = this.client
      .callContract(runtime, {
        call: encodeCallMsg({ from: zeroAddress, to: this.address, data: callData }),
        blockNumber: LAST_FINALIZED_BLOCK_NUMBER,
      })
      .result()

    return decodeFunctionResult({
      abi: LedgerAnchorABI,
      functionName: 'forwarder' as const,
      data: bytesToHex(result.data),
    }) as `0x${string}`
  }

  isAnchored(
    runtime: Runtime<unknown>,
    seq: bigint,
    headHash: `0x${string}`,
  ): boolean {
    const callData = encodeFunctionData({
      abi: LedgerAnchorABI,
      functionName: 'isAnchored' as const,
      args: [seq, headHash],
    })

    const result = this.client
      .callContract(runtime, {
        call: encodeCallMsg({ from: zeroAddress, to: this.address, data: callData }),
        blockNumber: LAST_FINALIZED_BLOCK_NUMBER,
      })
      .result()

    return decodeFunctionResult({
      abi: LedgerAnchorABI,
      functionName: 'isAnchored' as const,
      data: bytesToHex(result.data),
    }) as boolean
  }

  latestSeq(
    runtime: Runtime<unknown>,
  ): bigint {
    const callData = encodeFunctionData({
      abi: LedgerAnchorABI,
      functionName: 'latestSeq' as const,
    })

    const result = this.client
      .callContract(runtime, {
        call: encodeCallMsg({ from: zeroAddress, to: this.address, data: callData }),
        blockNumber: LAST_FINALIZED_BLOCK_NUMBER,
      })
      .result()

    return decodeFunctionResult({
      abi: LedgerAnchorABI,
      functionName: 'latestSeq' as const,
      data: bytesToHex(result.data),
    }) as bigint
  }

  supportsInterface(
    runtime: Runtime<unknown>,
    interfaceId: `0x${string}`,
  ): boolean {
    const callData = encodeFunctionData({
      abi: LedgerAnchorABI,
      functionName: 'supportsInterface' as const,
      args: [interfaceId],
    })

    const result = this.client
      .callContract(runtime, {
        call: encodeCallMsg({ from: zeroAddress, to: this.address, data: callData }),
        blockNumber: LAST_FINALIZED_BLOCK_NUMBER,
      })
      .result()

    return decodeFunctionResult({
      abi: LedgerAnchorABI,
      functionName: 'supportsInterface' as const,
      data: bytesToHex(result.data),
    }) as boolean
  }

  writeReportFromOnReport(
    runtime: Runtime<unknown>,
    metadata: `0x${string}`,
    report: `0x${string}`,
    gasConfig?: { gasLimit?: string },
  ) {
    const callData = encodeFunctionData({
      abi: LedgerAnchorABI,
      functionName: 'onReport' as const,
      args: [metadata, report],
    })

    const reportResponse = runtime
      .report(prepareReportRequest(callData))
      .result()

    return this.client
      .writeReport(runtime, {
        receiver: this.address,
        report: reportResponse,
        gasConfig,
      })
      .result()
  }

  writeReport(
    runtime: Runtime<unknown>,
    callData: Hex,
    gasConfig?: { gasLimit?: string },
  ) {
    const reportResponse = runtime
      .report(prepareReportRequest(callData))
      .result()

    return this.client
      .writeReport(runtime, {
        receiver: this.address,
        report: reportResponse,
        gasConfig,
      })
      .result()
  }

  /**
   * Creates a log trigger for Anchored events.
   * The returned trigger's adapt method decodes the raw log into AnchoredDecoded,
   * so the handler receives typed event data directly.
   * When multiple filters are provided, topic values are merged with OR semantics (match any).
   */
  logTriggerAnchored(
    filters?: AnchoredTopics[],
  ) {
    let topics: { values: string[] }[]
    if (!filters || filters.length === 0) {
      const encoded = encodeEventTopics({
        abi: LedgerAnchorABI,
        eventName: 'Anchored' as const,
      })
      topics = encoded.map((t) => ({ values: encodeTopicValue(t) }))
    } else if (filters.length === 1) {
      const f = filters[0]
      const args = {
        seq: f.seq,
        headHash: f.headHash,
      }
      const encoded = encodeEventTopics({
        abi: LedgerAnchorABI,
        eventName: 'Anchored' as const,
        args,
      })
      topics = encoded.map((t) => ({ values: encodeTopicValue(t) }))
    } else {
      const allEncoded = filters.map((f) => {
        const args = {
          seq: f.seq,
          headHash: f.headHash,
        }
        return encodeEventTopics({
          abi: LedgerAnchorABI,
          eventName: 'Anchored' as const,
          args,
        })
      })
      topics = allEncoded[0].map((_, i) => ({
        values: [...new Set(allEncoded.flatMap((row) => encodeTopicValue(row[i])))],
      }))
    }
    const baseTrigger = this.client.logTrigger({
      addresses: [hexToBase64(this.address)],
      topics,
    })
    const contract = this
    return {
      capabilityId: () => baseTrigger.capabilityId(),
      method: () => baseTrigger.method(),
      outputSchema: () => baseTrigger.outputSchema(),
      configAsAny: () => baseTrigger.configAsAny(),
      adapt: (rawOutput: EVMLog): DecodedLog<AnchoredDecoded> => contract.decodeAnchored(rawOutput),
    }
  }

  /**
   * Decodes a log into Anchored data, preserving all log metadata.
   */
  decodeAnchored(log: EVMLog): DecodedLog<AnchoredDecoded> {
    const decoded = decodeEventLog({
      abi: LedgerAnchorABI,
      data: bytesToHex(log.data),
      topics: log.topics.map((t) => bytesToHex(t)) as [Hex, ...Hex[]],
    })
    const { data: _, ...rest } = log
    return { ...rest, data: decoded.args as unknown as AnchoredDecoded }
  }
}

