// Code generated — DO NOT EDIT.
import type { Address } from 'viem'
import { addContractMock, type ContractMock, type EvmMock } from '@chainlink/cre-sdk/test'

import { AggregatorV3ABI } from './AggregatorV3'

export type AggregatorV3Mock = {
  decimals?: () => number
  description?: () => string
  latestRoundData?: () => readonly [bigint, bigint, bigint, bigint, bigint]
} & Pick<ContractMock<typeof AggregatorV3ABI>, 'writeReport'>

export function newAggregatorV3Mock(address: Address, evmMock: EvmMock): AggregatorV3Mock {
  return addContractMock(evmMock, { address, abi: AggregatorV3ABI }) as AggregatorV3Mock
}

