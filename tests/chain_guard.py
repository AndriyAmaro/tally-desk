"""No test may send a transaction to Sepolia or spend on Otari, whatever is in the environment.

`practice.chain_executor()` builds the real executor (a node script that signs and sends a swap) whenever SEPOLIA_PRIVATE_KEY is set
and the deployment record, the script and node exist. A developer who keeps that key in the shell would otherwise send real
testnet transactions every time a test ran the decide flow. Importing this module (every test module does, and a test checks
that none forgets) takes the key out of the environment for the whole run, points the executor's script at a path that does not
exist, and replaces `chain_executor` with one that returns None, the simulated path. A test that wants to exercise the executor builds
its own request and a fake `execute`; none may call the real one.
"""

import os
from pathlib import Path

# Secrets and switches a developer may have exported are taken out first, before anything of ours is imported: the two private keys of the
# chain flows, and every OTARI_* variable (the Otari key and its knobs), so a test written for the keyless behavior cannot be turned into a
# live Otari call, or fail, because of the shell it runs in. A test that needs one sets it itself with mock.patch.dict.
for _name in [n for n in os.environ if n in ("SEPOLIA_PRIVATE_KEY", "CRE_ETH_PRIVATE_KEY") or n.startswith("OTARI_")]:
    os.environ.pop(_name, None)

from verdict_desk import practice  # noqa: E402

practice.SCRIPT = Path("/nonexistent/no-test-may-send-a-transaction.mjs")
practice.DEPLOYMENT = Path("/nonexistent/no-deployment.json")


def _no_chain():
    return None


practice.chain_executor = _no_chain
GUARDED = True
