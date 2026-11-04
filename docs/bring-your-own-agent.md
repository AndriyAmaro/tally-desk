# Bring your own agent

Any agent that calls models through Otari can seal its calls into a hash-chained ledger of its own, and anyone can check that ledger with the same `verify` and `replay` commands and the same browser page as the trading agent. The trading agent is the reference workload; the API below knows nothing about markets.

## The sketch

This block is run by `tests/test_bring_your_own_agent.py`, so it is not a paraphrase. `my_roles.json` is a role config like [`examples/otari_roles.json`](../examples/otari_roles.json): a base URL, an auth header and a `roles` object where each role has a catalog `model`.

```python
from pathlib import Path
from verdict_desk.seal import Session

SYSTEM = "Answer in one plain sentence."

def run(question: str, ledger: Path, transport=None):
    with Session("my-agent", {"question": question}, ledger, config="my_roles.json", transport=transport) as s:
        answer = s.ask("writer", SYSTEM, question, max_tokens=200)   # one Otari call, receipt kept
        s.output("answer", answer)
        result = s.seal()                                            # closure checks, then one line appended
    if not result.sealed:
        raise SystemExit("not sealed: " + "; ".join(result.reasons))
    return result.record
```

`ledger` is a file of your own and has no default. Pass `transport=` (a function from the request payload to the answer text) and the same code runs as a dry run with no key; [`examples/`](../examples/README.md) has a complete agent with a `--dry-run` flag. Without a transport the session needs `OTARI_API_KEY` and says so before it sends anything.

## Check the ledger

```
python3 -m verdict_desk --ledger my-ledger.jsonl verify    # the chain: every hash and every link
python3 -m verdict_desk --ledger my-ledger.jsonl replay    # each record: hash, receipts schema, closure
```

In the browser, paste the whole ledger into [Verify](../web/verify.html). A receipts record is checked for integrity and linkage among the pasted records, its receipts schema and closure are recomputed, and the page says there is no verdict to re-derive.

## What is guaranteed

- **Every call is accounted for.** `Session.ask` goes through `llm.chat`, the client the trading agent uses: role config, per-role end users, idempotent retries, role fallbacks and the spend caps. The receipt of each call is kept, a failed call included.
- **The record is a hash chain.** Each record carries the hash of the one before. Editing, deleting or reordering a record breaks `verify`.
- **The input was frozen first.** Its sha256 is taken when the session opens and compared when it seals.
- **A bad record is not written.** If a hard check fails, nothing is appended and `result.reasons` says why.
- **Your ledger stays yours.** The API refuses the public trading ledger and any ledger that holds anything but `receipts/1` records, so an example can never touch the hourly chain.

## The generic closure checks

Sealed in the record as `closure` and recomputed by `replay` and by the browser. Hard checks refuse the record; soft checks seal it with a warning.

| Check | Class | Passes when |
|---|---|---|
| `calls_accounted` | hard | every call has a receipt with an Otari request id, or a recorded refusal or error |
| `receipts_schema` | hard | every receipt has the `otari-receipt/1` fields |
| `input_frozen` | hard | the recorded input hashes to the digest taken when the session opened |
| `priced_within_cap` | hard | no call is unpriced while a cap is set, and no pool spent more than its cap |
| `has_calls` | soft | the record holds at least one call |
| `live_calls` | soft | the calls went through the gateway (a dry run fails this, by design) |

## Limits

- **No verdict replay for a foreign workload.** `replay` re-checks the record, its receipts and its closure. It cannot re-derive your agent's answer: a model's output is not reproducible, and the rules of your agent are not known to this code. Ship your own `replay` if your decision is deterministic given the frozen outputs.
- **Otari only.** The calls go through the Otari gateway. A call made another way is not a call this layer can see.
- **Integrity after recording, not honesty at collection.** The chain shows the record was not edited. It does not show that the input was true or that you recorded every call you made outside the session.
- **The input is stored in the record.** Do not seal anything you would not publish with the ledger.
- **The ledger is yours.** There is no witness loop (timestamps, signatures) for it. `ots stamp` on the head hash works; the hourly runner is built for the trading agent only.
- **One session at a time per process.** The client keeps its call state per thread and the role config per module.
- **Not read by the audit.** `otari-audit` and the call-by-call page read the trading dossiers and ignore `receipts/1` records.
- **Never run live yet.** The tests use a fake transport. The first live use is the first time the new path meets the real gateway.
