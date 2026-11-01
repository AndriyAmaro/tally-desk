"""PracticeDesk in a real in-memory EVM (eth-tester + py-evm), with a test token and a test router.

The property that matters: a practice-trade receipt cannot exist without the trade. The receipt event and the
swap happen in one transaction, so a swap that fails reverts the receipt too, and one decision can be opened
and closed once each. Markets the testnet does not have (BTC, SOL) get a record, clearly marked as not executed.

Setup: `npm install` in cre/contracts/evm, `pip install web3 "eth-tester[py-evm]"`.
Run:   python -m unittest discover -s test -p 'test_*.py' -v  (from cre/contracts/evm)
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import EthereumTesterProvider, Web3

HERE = Path(__file__).resolve().parent.parent  # cre/contracts/evm
DECISION = bytes.fromhex("ab" * 32)
OPEN, CLOSE = 0, 1
LONG, SHORT = 1, 2


def compile_(source: str, out: Path, name: str):
    abi, binf = out / f"{name}.json", out / f"{name}.bin"
    subprocess.run(["node", "compile.js", source, str(abi), str(binf), name], cwd=HERE, check=True, capture_output=True)
    return json.loads(abi.read_text()), "0x" + binf.read_text()


@unittest.skipUnless(shutil.which("node") and (HERE / "node_modules" / "solc").exists(), "run `npm install` in cre/contracts/evm first")
class PracticeDeskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = Path(tempfile.mkdtemp())
        cls.desk_art = compile_("src/PracticeDesk.sol", out, "PracticeDesk")
        cls.token_art = compile_("test/mocks/MockERC20.sol", out, "MockERC20")
        cls.router_art = compile_("test/mocks/MockSwapRouter.sol", out, "MockSwapRouter")

    def setUp(self):
        self.w3 = Web3(EthereumTesterProvider(EthereumTester(PyEVMBackend())))
        self.owner, self.stranger = self.w3.eth.accounts[:2]
        self.weth = self.deploy(*self.token_art)
        self.usdc = self.deploy(*self.token_art)
        self.router = self.deploy(*self.router_art)
        self.desk = self.deploy(*self.desk_art, self.router.address)
        self.weth.functions.mint(self.desk.address, 10**18).transact({"from": self.owner})

    def deploy(self, abi, bytecode, *args):
        factory = self.w3.eth.contract(abi=abi, bytecode=bytecode)
        tx = factory.constructor(*args).transact({"from": self.owner})
        return self.w3.eth.contract(address=self.w3.eth.wait_for_transaction_receipt(tx).contractAddress, abi=abi)

    def trade(self, seq=7, decision=DECISION, kind=OPEN, side=SHORT, amount=10**16, min_out=1, sender=None):
        fn = self.desk.functions.trade(seq, decision, kind, side, self.weth.address, self.usdc.address, 500, amount, min_out)
        return self.w3.eth.wait_for_transaction_receipt(fn.transact({"from": sender or self.owner}))

    def test_the_receipt_and_the_swap_happen_in_one_transaction(self):
        receipt = self.trade()
        events = self.desk.events.PracticeTrade().process_receipt(receipt)
        self.assertEqual(len(events), 1)
        args = events[0]["args"]
        self.assertEqual((args["seq"], args["decision"], args["kind"], args["side"]), (7, DECISION, OPEN, SHORT))
        self.assertEqual(args["amountIn"], 10**16)
        self.assertEqual(args["amountOut"], 2 * 10**16)
        self.assertEqual(self.usdc.functions.balanceOf(self.desk.address).call(), 2 * 10**16)
        self.assertEqual(self.weth.functions.balanceOf(self.desk.address).call(), 10**18 - 10**16)
        self.assertTrue(self.desk.functions.done(DECISION, OPEN).call())

    def test_no_receipt_without_the_trade_a_failed_swap_reverts_everything(self):
        self.router.functions.setFail(True).transact({"from": self.owner})
        with self.assertRaises(TransactionFailed):
            self.trade()
        self.assertFalse(self.desk.functions.done(DECISION, OPEN).call())
        self.assertEqual(self.weth.functions.balanceOf(self.desk.address).call(), 10**18)

    def test_a_swap_that_returns_less_than_the_minimum_reverts(self):
        with self.assertRaises(TransactionFailed):
            self.trade(min_out=3 * 10**16)
        self.assertFalse(self.desk.functions.done(DECISION, OPEN).call())

    def test_one_decision_is_opened_once_and_closed_once(self):
        self.trade()
        with self.assertRaises(TransactionFailed):
            self.trade()
        self.trade(kind=CLOSE)
        with self.assertRaises(TransactionFailed):
            self.trade(kind=CLOSE)

    def test_only_the_desk_owner_can_trade_or_record(self):
        with self.assertRaises(TransactionFailed):
            self.trade(sender=self.stranger)
        with self.assertRaises(TransactionFailed):
            self.desk.functions.record(9, DECISION, OPEN, LONG, b"BTCUSDT".ljust(32, b"\0")).transact({"from": self.stranger})

    def test_a_market_the_testnet_lacks_gets_a_record_marked_not_executed(self):
        fn = self.desk.functions.record(9, DECISION, OPEN, LONG, b"BTCUSDT".ljust(32, b"\0"))
        receipt = self.w3.eth.wait_for_transaction_receipt(fn.transact({"from": self.owner}))
        events = self.desk.events.PracticeRecord().process_receipt(receipt)
        self.assertEqual(events[0]["args"]["seq"], 9)
        self.assertEqual(events[0]["args"]["symbol"].rstrip(b"\0"), b"BTCUSDT")
        self.assertEqual(len(self.desk.events.PracticeTrade().process_receipt(receipt)), 0)
        self.assertTrue(self.desk.functions.done(DECISION, OPEN).call())

    def test_an_empty_decision_or_an_unknown_side_is_refused(self):
        with self.assertRaises(TransactionFailed):
            self.trade(decision=bytes(32))
        with self.assertRaises(TransactionFailed):
            self.trade(side=3)


if __name__ == "__main__":
    unittest.main()
