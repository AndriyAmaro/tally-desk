"""No test may send a transaction to Sepolia, whatever is in the environment.

`practice.chain_executor()` builds the real executor (a node script that signs and sends a swap) whenever SEPOLIA_PRIVATE_KEY is set
and the deployment record, the script and node exist. A developer who keeps that key in the shell would otherwise send real
testnet transactions every time a test ran the decide flow. Importing this module (every test module does, and a test checks
that none forgets) takes the key out of the environment for the whole run, points the executor's script at a path that does not
exist, and replaces `chain_executor` with one that returns None, the simulated path. A test that wants to exercise the executor builds
its own request and a fake `execute`; none may call the real one.
"""

import os
from pathlib import Path

from verdict_desk import practice

os.environ.pop("SEPOLIA_PRIVATE_KEY", None)
practice.SCRIPT = Path("/nonexistent/no-test-may-send-a-transaction.mjs")
practice.DEPLOYMENT = Path("/nonexistent/no-deployment.json")


def _no_chain():
    return None


practice.chain_executor = _no_chain
GUARDED = True
