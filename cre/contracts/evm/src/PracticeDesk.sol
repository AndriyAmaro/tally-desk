// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

/// Uniswap's SwapRouter02 (no deadline field in this version of the struct).
interface ISwapRouter02 {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}

/// @title PracticeDesk
/// @notice Executes the Verdict Desk's practice trades on a testnet DEX, each one in the same transaction as its
///         receipt, so a receipt cannot exist without the trade. A receipt names the dossier (seq) and a digest of
///         the decision it carries out; the dossier in turn records this transaction's hash before it is sealed.
/// @dev Testnet practice only: the desk holds test tokens, trades go through Uniswap v3 on Sepolia, and the
///      testnet pool price is not the market price. Markets the testnet does not have get `record`, which emits a
///      receipt marked as not executed. Each decision can be opened once and closed once.
contract PracticeDesk {
    uint8 public constant OPEN = 0;
    uint8 public constant CLOSE = 1;
    uint8 public constant LONG = 1;
    uint8 public constant SHORT = 2;

    address public immutable owner;
    address public immutable router;
    mapping(bytes32 => mapping(uint8 => bool)) public done; // decision digest => kind => already carried out

    event PracticeTrade(
        uint64 indexed seq, bytes32 indexed decision, uint8 kind, uint8 side,
        address tokenIn, address tokenOut, uint256 amountIn, uint256 amountOut
    );
    event PracticeRecord(uint64 indexed seq, bytes32 indexed decision, uint8 kind, uint8 side, bytes32 symbol);

    error NotOwner(address sender);
    error EmptyDecision();
    error BadKindOrSide(uint8 kind, uint8 side);
    error AlreadyDone(bytes32 decision, uint8 kind);
    error NothingReceived();

    constructor(address router_) {
        require(router_ != address(0), "router is zero");
        owner = msg.sender;
        router = router_;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner(msg.sender);
        _;
    }

    function _claim(bytes32 decision, uint8 kind, uint8 side) private {
        if (decision == bytes32(0)) revert EmptyDecision();
        if (kind > CLOSE || (side != LONG && side != SHORT)) revert BadKindOrSide(kind, side);
        if (done[decision][kind]) revert AlreadyDone(decision, kind);
        done[decision][kind] = true;
    }

    /// Swaps `amountIn` of `tokenIn` for `tokenOut` on the router and emits the receipt, atomically.
    function trade(
        uint64 seq, bytes32 decision, uint8 kind, uint8 side,
        address tokenIn, address tokenOut, uint24 fee, uint256 amountIn, uint256 minOut
    ) external onlyOwner returns (uint256 amountOut) {
        _claim(decision, kind, side);
        IERC20(tokenIn).approve(router, amountIn);
        amountOut = ISwapRouter02(router).exactInputSingle(
            ISwapRouter02.ExactInputSingleParams(tokenIn, tokenOut, fee, address(this), amountIn, minOut, 0)
        );
        if (amountOut == 0 || amountOut < minOut) revert NothingReceived();
        emit PracticeTrade(seq, decision, kind, side, tokenIn, tokenOut, amountIn, amountOut);
    }

    /// A receipt with no trade, for a market the testnet does not have; it says so by being a different event.
    function record(uint64 seq, bytes32 decision, uint8 kind, uint8 side, bytes32 symbol) external onlyOwner {
        _claim(decision, kind, side);
        emit PracticeRecord(seq, decision, kind, side, symbol);
    }

    /// Moves test tokens out, e.g. to retire the desk.
    function sweep(address token, address to, uint256 amount) external onlyOwner {
        IERC20(token).transfer(to, amount);
    }
}
