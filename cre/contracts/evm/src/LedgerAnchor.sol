// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IERC165 {
    function supportsInterface(bytes4 interfaceId) external view returns (bool);
}

/// Receiver interface of the Chainlink CRE forwarder (same signature as Chainlink's IReceiver).
interface IReceiver is IERC165 {
    function onReport(bytes calldata metadata, bytes calldata report) external;
}

/// @title LedgerAnchor
/// @notice Stores, once per sequence number, the SHA-256 head of the Verdict Desk decision
///         ledger next to a Chainlink Data Feed price, as delivered by a CRE workflow.
/// @dev Append-only by construction: a report must carry a sequence number greater than the
///      last accepted one, so an anchored head can never be overwritten. Only the CRE
///      forwarder can call `onReport`; optionally only reports from one workflow owner.
contract LedgerAnchor is IReceiver {
    struct Anchor {
        bytes32 headHash;
        int256 price;
        uint8 priceDecimals;
        uint256 priceUpdatedAt;
        uint256 anchoredAt;
    }

    address public immutable forwarder;
    address public immutable expectedWorkflowOwner; // zero disables the owner check
    uint64 public latestSeq;
    mapping(uint64 => Anchor) public anchors;

    event Anchored(uint64 indexed seq, bytes32 indexed headHash, int256 price, uint8 priceDecimals, uint256 priceUpdatedAt);

    error NotForwarder(address sender);
    error NotWorkflowOwner(address owner);
    error BadMetadata();
    error StaleSequence(uint64 seq, uint64 latest);
    error EmptyHead();

    constructor(address forwarder_, address expectedWorkflowOwner_) {
        require(forwarder_ != address(0), "forwarder is zero");
        forwarder = forwarder_;
        expectedWorkflowOwner = expectedWorkflowOwner_;
    }

    function onReport(bytes calldata metadata, bytes calldata report) external override {
        if (msg.sender != forwarder) revert NotForwarder(msg.sender);
        if (expectedWorkflowOwner != address(0)) {
            // Forwarder metadata: workflow id (32 bytes), workflow name (10), workflow owner (20).
            if (metadata.length < 62) revert BadMetadata();
            address owner = address(bytes20(metadata[42:62]));
            if (owner != expectedWorkflowOwner) revert NotWorkflowOwner(owner);
        }
        (uint64 seq, bytes32 headHash, int256 price, uint8 decimals, uint256 updatedAt) =
            abi.decode(report, (uint64, bytes32, int256, uint8, uint256));
        if (headHash == bytes32(0)) revert EmptyHead();
        if (seq <= latestSeq) revert StaleSequence(seq, latestSeq);
        latestSeq = seq;
        anchors[seq] = Anchor(headHash, price, decimals, updatedAt, block.timestamp);
        emit Anchored(seq, headHash, price, decimals, updatedAt);
    }

    /// @notice True when `headHash` is the head that was anchored for `seq`.
    function isAnchored(uint64 seq, bytes32 headHash) external view returns (bool) {
        return headHash != bytes32(0) && anchors[seq].headHash == headHash;
    }

    function supportsInterface(bytes4 interfaceId) external pure override returns (bool) {
        return interfaceId == type(IReceiver).interfaceId || interfaceId == type(IERC165).interfaceId;
    }
}
