// Code generated — DO NOT EDIT.
import type { Address } from 'viem'
import { addContractMock, type ContractMock, type EvmMock } from '@chainlink/cre-sdk/test'

import { LedgerAnchorABI } from './LedgerAnchor'

export type LedgerAnchorMock = {
  anchors?: (arg0: bigint) => readonly [`0x${string}`, bigint, number, bigint, bigint]
  expectedWorkflowOwner?: () => `0x${string}`
  forwarder?: () => `0x${string}`
  isAnchored?: (seq: bigint, headHash: `0x${string}`) => boolean
  latestSeq?: () => bigint
  supportsInterface?: (interfaceId: `0x${string}`) => boolean
} & Pick<ContractMock<typeof LedgerAnchorABI>, 'writeReport'>

export function newLedgerAnchorMock(address: Address, evmMock: EvmMock): LedgerAnchorMock {
  return addContractMock(evmMock, { address, abi: LedgerAnchorABI }) as LedgerAnchorMock
}

