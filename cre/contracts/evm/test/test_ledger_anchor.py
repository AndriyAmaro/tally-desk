"""LedgerAnchor in a real in-memory EVM (eth-tester + py-evm).

Setup: `npm install` in cre/contracts/evm, `pip install web3 "eth-tester[py-evm]"`.
Run:   python -m unittest discover -s test -p 'test_*.py' -v  (from cre/contracts/evm)
"""

import ast
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
HEAD = "0x" + "ab" * 32
OWNER = "0x" + "11" * 20


def selector(signature: str) -> bytes:
    return bytes(Web3.keccak(text=signature)[:4])


def revert_selector(exc: Exception) -> bytes:
    """4-byte custom error selector from an eth-tester revert (`execution reverted: b'...'`)."""
    return ast.literal_eval(str(exc).split("execution reverted: ", 1)[1])[:4]


def build_metadata(owner_hex: str) -> bytes:
    # workflow id (32 bytes) + workflow name (10 bytes) + workflow owner (20 bytes), as the forwarder packs it
    return bytes(32) + bytes(10) + bytes.fromhex(owner_hex[2:])


def build_report(w3: Web3, seq: int, head: str = HEAD, price: int = 6_700_000_000_000, decimals: int = 8, updated: int = 1_760_000_000) -> bytes:
    return w3.codec.encode(["uint64", "bytes32", "int256", "uint8", "uint256"], [seq, bytes.fromhex(head[2:]), price, decimals, updated])


@unittest.skipUnless(shutil.which("node") and (HERE / "node_modules" / "solc").exists(), "run `npm install` in cre/contracts/evm first")
class LedgerAnchorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = Path(tempfile.mkdtemp())
        subprocess.run(["node", "compile.js", "src/LedgerAnchor.sol", str(out / "a.json"), str(out / "a.bin")], cwd=HERE, check=True, capture_output=True)
        cls.abi = json.loads((out / "a.json").read_text())
        cls.bytecode = "0x" + (out / "a.bin").read_text()

    def deploy(self, expected_owner: str = "0x" + "00" * 20):
        self.w3 = Web3(EthereumTesterProvider(EthereumTester(PyEVMBackend())))
        self.owner_acct, self.forwarder, self.stranger = self.w3.eth.accounts[:3]
        factory = self.w3.eth.contract(abi=self.abi, bytecode=self.bytecode)
        tx = factory.constructor(self.forwarder, expected_owner).transact({"from": self.owner_acct})
        address = self.w3.eth.wait_for_transaction_receipt(tx).contractAddress
        self.anchor = self.w3.eth.contract(address=address, abi=self.abi)

    def assertReverts(self, error_signature: str, fn, *args, **kwargs):
        with self.assertRaises(TransactionFailed) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(revert_selector(ctx.exception), selector(error_signature))

    def report(self, seq, sender=None, metadata: bytes = b"", **kw):
        return self.anchor.functions.onReport(metadata, build_report(self.w3, seq, **kw)).transact({"from": sender or self.forwarder})

    def test_forwarder_report_is_stored_and_emitted(self):
        self.deploy()
        receipt = self.w3.eth.wait_for_transaction_receipt(self.report(7))
        self.assertEqual(self.anchor.functions.latestSeq().call(), 7)
        stored = self.anchor.functions.anchors(7).call()
        self.assertEqual(stored[0].hex(), HEAD[2:])
        self.assertEqual(stored[1:4], [6_700_000_000_000, 8, 1_760_000_000])
        events = self.anchor.events.Anchored().process_receipt(receipt)
        self.assertEqual(events[0]["args"]["seq"], 7)
        self.assertTrue(self.anchor.functions.isAnchored(7, bytes.fromhex(HEAD[2:])).call())
        self.assertFalse(self.anchor.functions.isAnchored(7, bytes.fromhex("cd" * 32)).call())

    def test_only_the_forwarder_can_report(self):
        self.deploy()
        self.assertReverts("NotForwarder(address)", self.report, 1, sender=self.stranger)
        self.assertEqual(self.anchor.functions.latestSeq().call(), 0)

    def test_an_anchored_head_can_never_be_overwritten_or_rolled_back(self):
        self.deploy()
        self.report(5)
        for seq in (5, 4, 1):
            self.assertReverts("StaleSequence(uint64,uint64)", self.report, seq, head="0x" + "cd" * 32)
        self.assertEqual(self.anchor.functions.anchors(5).call()[0].hex(), HEAD[2:])
        self.report(6)
        self.assertEqual(self.anchor.functions.latestSeq().call(), 6)

    def test_zero_head_is_rejected(self):
        self.deploy()
        self.assertReverts("EmptyHead()", self.report, 1, head="0x" + "00" * 32)

    def test_workflow_owner_check_when_configured(self):
        self.deploy(expected_owner=Web3.to_checksum_address(OWNER))
        self.assertReverts("NotWorkflowOwner(address)", self.report, 1, metadata=build_metadata("0x" + "22" * 20))
        self.assertReverts("BadMetadata()", self.report, 1, metadata=b"short")
        self.report(1, metadata=build_metadata(OWNER))
        self.assertEqual(self.anchor.functions.latestSeq().call(), 1)

    def test_erc165_advertises_ireceiver(self):
        self.deploy()
        on_report_id = bytes(Web3.keccak(text="onReport(bytes,bytes)")[:4])
        self.assertTrue(self.anchor.functions.supportsInterface(on_report_id).call())
        self.assertTrue(self.anchor.functions.supportsInterface(bytes.fromhex("01ffc9a7")).call())
        self.assertFalse(self.anchor.functions.supportsInterface(bytes.fromhex("deadbeef")).call())


if __name__ == "__main__":
    unittest.main()
