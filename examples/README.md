# Examples: agents that are not trading

`summarize_url.py` reads one web page through Otari's fetch tool and writes three neutral sentences and one number it quotes, with one open-weight model (`reader` in [`otari_roles.json`](otari_roles.json)). It seals the call into a ledger of your own with [`verdict_desk/seal.py`](../verdict_desk/seal.py). The page that explains the API is [docs/bring-your-own-agent.md](../docs/bring-your-own-agent.md).

## Run it without a key (dry run)

```
python3 examples/summarize_url.py https://example.com/article --dry-run --ledger /tmp/my-ledger.jsonl
```

A fake transport answers with canned text, so nothing leaves the machine and no key is read. The real client still runs on top of it: role config, receipt, spend caps. The record says `dry-run` and its closure carries a warning, so it cannot pass for a live one.

## Run it for real

```
export OTARI_API_KEY=...        # from the Otari dashboard
python3 examples/summarize_url.py https://example.com/article --ledger my-ledger.jsonl
```

One call, a few cents at most (the tool cap in `otari_roles.json` is USD 0.05). With a service key, `OTARI_SEND_USER=1` bills the call to its own end user, `byo-reader`. The ledger is a file of your own: the script refuses `ledger/ledger.jsonl`, which only the hourly workflow writes. Do not commit a ledger from a real run unless you mean to publish the page you read.

## Verify the ledger

Same commands as for the trading ledger:

```
python3 -m verdict_desk --ledger my-ledger.jsonl verify   # Chain intact: N dossiers, head ...
python3 -m verdict_desk --ledger my-ledger.jsonl replay   # #1 replay MATCHES: no rules to re-derive (receipts record)
```

`replay` re-checks each record's hash, its receipts and its closure. It cannot re-derive a summary, and says so.

In the browser, open `web/verify.html` (or the published Verify page), paste the whole ledger file, press Verify. Each record shows as intact and linked, its receipts schema and closure are recomputed, and the page states that there is no verdict to re-derive. Edit one character of the pasted text and press Verify again to see it fail.
