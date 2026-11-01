// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IMockToken {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function mint(address to, uint256 amount) external;
}

/// Test-only stand-in for Uniswap's SwapRouter02.exactInputSingle: pulls tokenIn, mints `rate` x amountIn / 1e6 of
/// tokenOut to the recipient. `fail` makes every swap revert, to prove the receipt cannot exist without the trade.
contract MockSwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    uint256 public rate = 2_000_000; // tokenOut per tokenIn, scaled by 1e6
    bool public fail;

    function setRate(uint256 r) external {
        rate = r;
    }

    function setFail(bool f) external {
        fail = f;
    }

    function exactInputSingle(ExactInputSingleParams calldata p) external payable returns (uint256 amountOut) {
        require(!fail, "swap failed");
        IMockToken(p.tokenIn).transferFrom(msg.sender, address(this), p.amountIn);
        amountOut = (p.amountIn * rate) / 1e6;
        require(amountOut >= p.amountOutMinimum, "Too little received");
        IMockToken(p.tokenOut).mint(p.recipient, amountOut);
    }
}
