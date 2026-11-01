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





export const AggregatorV3ABI = [{"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"description","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"},{"inputs":[],"name":"latestRoundData","outputs":[{"internalType":"uint80","name":"roundId","type":"uint80"},{"internalType":"int256","name":"answer","type":"int256"},{"internalType":"uint256","name":"startedAt","type":"uint256"},{"internalType":"uint256","name":"updatedAt","type":"uint256"},{"internalType":"uint80","name":"answeredInRound","type":"uint80"}],"stateMutability":"view","type":"function"}] as const

export class AggregatorV3 {
  constructor(
    private readonly client: EVMClient,
    public readonly address: Address,
  ) {}

  decimals(
    runtime: Runtime<unknown>,
  ): number {
    const callData = encodeFunctionData({
      abi: AggregatorV3ABI,
      functionName: 'decimals' as const,
    })

    const result = this.client
      .callContract(runtime, {
        call: encodeCallMsg({ from: zeroAddress, to: this.address, data: callData }),
        blockNumber: LAST_FINALIZED_BLOCK_NUMBER,
      })
      .result()

    return decodeFunctionResult({
      abi: AggregatorV3ABI,
      functionName: 'decimals' as const,
      data: bytesToHex(result.data),
    }) as number
  }

  description(
    runtime: Runtime<unknown>,
  ): string {
    const callData = encodeFunctionData({
      abi: AggregatorV3ABI,
      functionName: 'description' as const,
    })

    const result = this.client
      .callContract(runtime, {
        call: encodeCallMsg({ from: zeroAddress, to: this.address, data: callData }),
        blockNumber: LAST_FINALIZED_BLOCK_NUMBER,
      })
      .result()

    return decodeFunctionResult({
      abi: AggregatorV3ABI,
      functionName: 'description' as const,
      data: bytesToHex(result.data),
    }) as string
  }

  latestRoundData(
    runtime: Runtime<unknown>,
  ): readonly [bigint, bigint, bigint, bigint, bigint] {
    const callData = encodeFunctionData({
      abi: AggregatorV3ABI,
      functionName: 'latestRoundData' as const,
    })

    const result = this.client
      .callContract(runtime, {
        call: encodeCallMsg({ from: zeroAddress, to: this.address, data: callData }),
        blockNumber: LAST_FINALIZED_BLOCK_NUMBER,
      })
      .result()

    return decodeFunctionResult({
      abi: AggregatorV3ABI,
      functionName: 'latestRoundData' as const,
      data: bytesToHex(result.data),
    }) as readonly [bigint, bigint, bigint, bigint, bigint]
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
}

