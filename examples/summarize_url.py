"""A small agent that is not about trading: read one web page through Otari's fetch tool, write three neutral sentences and quote one number.

    python3 examples/summarize_url.py https://example.com/article --dry-run --ledger /tmp/my-ledger.jsonl   # fake transport, no key
    OTARI_API_KEY=... python3 examples/summarize_url.py https://example.com/article --ledger my-ledger.jsonl # real: one Otari call
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from verdict_desk.llm import LLMError  # noqa: E402
from verdict_desk.seal import Session  # noqa: E402

SYSTEM = ("You read one web page with the web fetch tool. Reply with JSON only: {\"summary\": exactly three neutral sentences, no opinion, "
          "\"number\": one figure that appears on the page, copied exactly as written, and quoted in the summary}.")


def fake_gateway(payload):
    """The dry run's answer: canned text, and a fetch entry shaped like the ones Otari reports. Nothing here read a page."""
    url = payload["messages"][-1]["content"].split()[-1]
    text = json.dumps({"summary": f"This is a canned dry-run answer for {url}. No page was read. The number quoted is 3.", "number": "3"})
    return text, [{"id": "dry-1", "type": "web_fetch", "status": "completed", "url": url, "sources": []}]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--ledger", required=True, help="where to append the sealed record (a file of your own, never ledger/ledger.jsonl)")
    ap.add_argument("--dry-run", action="store_true", help="fake transport: runs without a key and says so in the record")
    opts = ap.parse_args()
    try:
        return run(opts)
    except LLMError as err:  # no key: the session never opened and nothing was sent
        print(f"stopped: {err}")
        return 2


def run(opts) -> int:
    with Session("summarize-url", {"url": opts.url}, opts.ledger, config=HERE / "otari_roles.json", transport=fake_gateway if opts.dry_run else None) as s:
        try:
            answer = s.ask("reader", SYSTEM, f"Fetch this page and summarize it: {opts.url}", label="fetch and summarize", max_tokens=600, timeout=120,
                           extra={"tools": [{"type": "otari_web_fetch"}]})
            out = json.loads(answer[answer.find("{"):answer.rfind("}") + 1])
        except LLMError as err:  # a refusal or a failure: its receipt was kept, so the failure is sealed as it happened
            out = {"summary": None, "number": None}
            s.output("error", str(err))
        except ValueError:
            out = {"summary": answer, "number": None}
        s.output("summary", out.get("summary"))
        s.output("number", out.get("number"))
        s.output("number_is_in_summary", bool(out.get("number")) and str(out["number"]) in str(out.get("summary")))
        result = s.seal()
    if not result.sealed:
        print("not sealed:", *result.reasons, sep="\n  ")
        return 1
    print(f"{out.get('summary')}\n\nsealed #{result.record['seq']} {result.record['hash'][:16]} ({result.record['workload']['mode']}, closure {result.closure['outcome']}) in {result.ledger}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
