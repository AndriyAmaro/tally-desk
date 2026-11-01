#!/usr/bin/env python3
"""Refreshes every published figure from the ledger, so no number in the docs is typed by hand.

  python3 scripts/refresh_numbers.py             # README "Live operation", the build-page draft, the judge page commit line, the test count
  python3 scripts/refresh_numbers.py --check     # change nothing; exit 1 if a document is out of date with the ledger

The figures come from `python3 -m verdict_desk stats --git` (verdict_desk/livestats.py) on the verified chain,
and the test count from a real run of the suite. Run it again right before submitting: the chain grows hourly.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verdict_desk import battery, dossier, livestats  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import readme_cards  # noqa: E402

LEDGER = ROOT / "ledger" / "ledger.jsonl"
README = ROOT / "README.md"
BUILD_PAGE = ROOT / "SUBMISSAO_DORAHACKS.md"
JUDGE_PAGE = ROOT / "web" / "judge.html"
MSG = ROOT / "MSG_ORGANIZADORES.md"
DESCRICAO_PAGE = ROOT / "DESCRICAO_PAGINA.md"
FIGURES_CARD = ROOT / "docs" / "readme" / "figures.svg"
# The tamper battery takes minutes, so its result is kept as a snapshot and only re-run on a full refresh.
BATTERY_SNAPSHOT = ROOT / "docs" / "readme" / "battery.json"
BATTERY_TRIALS = 1500  # the same size the hourly workflow publishes to site/battery.json


# Private drafting docs (build page, organizer message, page description) live outside this repository; when present they are refreshed too.
# Only the phrases that state the size of the suite: other "N tests" in the docs (the CRE ones) are different counts.
TEST_COUNT_PATTERNS = [
    (README, r"(standard library only \()(?:\d+|TESTS_COUNT)( tests\))"),
    (BUILD_PAGE, r"(\| \*\*Testing\*\* \| )(?:\d+|TESTS_COUNT)( unit tests)"),
    (BUILD_PAGE, r"(- )(?:\d+|TESTS_COUNT)( passing unit tests)"),
    (BUILD_PAGE, r"(# )(?:\d+|TESTS_COUNT)( tests)"),
    (MSG, r"(- \*\*)(?:\d+|TESTS_COUNT)( tests\*\*)"),
    (JUDGE_PAGE, r"(The scale behind the page: )(?:[0-9]+|TESTS_COUNT)( tests)"),
    (DESCRICAO_PAGE, r"(Testing: )(?:\d+|TESTS_COUNT)( unit tests)"),
]


# A page that names a dashboard card ("the <b>Positions</b> card") must name one that is actually drawn.
CARD_LABEL = re.compile(r'card\("([^"]+)"')
CARD_REFERENCE = re.compile(r"<b>([^<]+)</b> card")


def card_reference_problems() -> list[str]:
    """Every card a page names must be a card the dashboard draws.

    The dashboard card for sized dossiers was once labelled 'Commits'; after it was renamed to
    'Positions', the judge page still said 'the Commits card' and nothing failed — a dead link that
    only a reader would notice. This makes it a build failure instead.
    """
    labels = set(CARD_LABEL.findall((ROOT / "web" / "index.html").read_text(encoding="utf-8")))
    problems = []
    for page in sorted((ROOT / "web").glob("*.html")):
        for name in CARD_REFERENCE.findall(page.read_text(encoding="utf-8")):
            if name not in labels:
                problems.append(f"{page.relative_to(ROOT)} names the '{name}' card, which index.html does not draw (it draws {sorted(labels)})")
    return problems


def count_tests() -> int:
    """The size of the suite, from a real run — but not a run that can deadlock on itself.

    test_published_figures_match_the_ledger calls this script with --check, so if that one failure
    also blocked the count, a single new ledger record would freeze every published figure for good.
    It is tolerated here and only here: the run that tolerates it is the run that repairs it, and
    the suite re-checks the repaired docs afterwards. Any other failing test still stops us.
    """
    run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "MISTRAL_API_KEY": ""})
    found = re.search(r"Ran (\d+) tests", run.stderr)
    if not found:
        raise SystemExit("the test suite did not run, so there is no honest test count to publish:\n" + run.stderr[-800:])
    if run.returncode != 0:
        failing = re.findall(r"^(?:FAIL|ERROR): (\w+)", run.stderr, re.M)
        if failing != ["test_published_figures_match_the_ledger"]:
            raise SystemExit("the test suite does not pass, so there is no honest test count to publish:\n" + run.stderr[-800:])
    return int(found.group(1))


def live_section(ledger: list[dict]) -> str:
    s = livestats.summarize(ledger)
    authors = livestats.commit_authors(LEDGER, ledger)
    if not authors:
        raise SystemExit("needs a git checkout of the ledger (git blame) to count who recorded each dossier")
    bot = authors.get("verdict-desk-bot", [])
    other = sorted(n for name, seqs in authors.items() if name != "verdict-desk-bot" for n in seqs)
    c, e, h = s["cadence"], s["llm_era"], s["head"]
    per = ", ".join(f"{k} {v}" for k, v in c["per_symbol"].items())
    gap_h, gap_m = divmod(c["longest_gap_minutes"], 60)
    first, last = c["first"][:16].replace("T", " "), c["last"][:16].replace("T", " ")
    sides = e["sides"]
    reasons = ", ".join(f'"{k}" {v}' for k, v in e["rejection_reasons"].items())
    # The history before the LLM era is derived too; the asserts stop a silent change of the ledger from reaching the docs.
    none, failed, early = livestats.llm_era(ledger, 1, 4), livestats.llm_era(ledger, 5, 34), livestats.llm_era(ledger, 35, 45)
    failed_calls = failed["sides"]["bull"]["errors"] + failed["sides"]["bear"]["errors"]
    assert (len(none["dossiers_without_llm"]), failed_calls, early["accepted"]) == (4, 60, 0), "the recorded history changed: re-read the ledger before publishing"
    why = early["rejection_reasons"]
    return f"""<!-- live-numbers: generated from dossier {h['seq']} by `python3 scripts/refresh_numbers.py` (counts: `python3 -m verdict_desk stats --git`) -->
Counted over `ledger/ledger.jsonl` on the chain as of **dossier {h['seq']}** (head `{h['hash'][:16]}`, collected {last} UTC), after `verify` passed. Nothing is estimated: every figure is a count over ledger lines, plus `git blame` of that file for the author rows. The dashboard recomputes the same numbers in your browser from whatever ledger you load, and a test checks that the Python and the JavaScript agree on this ledger. The chain grows with every run, so these figures are a snapshot; the dashboard and the commands below always show the current chain.

| What | Number |
|---|---|
| Dossiers in the verified chain | {c['total']} ({per}) |
| Operating window, first to last dossier | {c['window_hours']} h, {c['window_days']} days ({first} to {last} UTC) |
| Clock hours (UTC) in that window with at least one dossier | {c['clock_hours_with_records']} of {c['clock_hours_in_window']} ({c['coverage_pct']}%) |
| Longest gap between consecutive dossiers of the same symbol | {gap_h} h {gap_m} min ({c['longest_gap_symbol']}); {c['gaps_over_90_minutes']} gaps over 90 minutes |
| Recorded by the workflow bot (scheduled or manually dispatched runs) | {len(bot)} (dossiers {livestats.seq_ranges(bot)}) |
| Recorded by other commits | {len(other)}{(' (dossiers ' + livestats.seq_ranges(other) + '): runs made outside the workflow while the demo tooling and the v2 rules were being built; the ledger is append-only, so they stay') if other else ''} |
| LLM era, dossier {e['first_seq']} onward | {e['dossiers']} dossiers, {e['dossiers_with_analysts']} with analyst output{(' (dossiers ' + livestats.seq_ranges(e['dossiers_without_llm']) + ' have no LLM layer)') if e['dossiers_without_llm'] else ''} |
| Claims **accepted** by the citation validator | {e['accepted']} (bull {sides['bull']['accepted']}, bear {sides['bear']['accepted']}) |
| Claims **rejected** by the citation validator | {e['rejected']} (bull {sides['bull']['rejected']}, bear {sides['bear']['rejected']}); reason: {reasons} |
| Share of claims accepted | {e['accepted_pct']}% |

The window is not continuous coverage: scheduled GitHub runs are best effort and most hourly slots did not fire, which is why the coverage and gap rows sit next to the window. The dashboard's cadence panel shows the same figures. From 25 Oct the hourly workflow is triggered four times an hour behind a gate that records at most one set of decisions per 50 minutes, and a second workflow, `pacemaker`, waits 52 minutes, dispatches the next run and queues its own successor, because scheduled runs alone did not fire for six hours that day; these rows will show whether that helped, and they are not rewritten if it did not.

Earlier dossiers stay exactly as they were recorded, so the history of how the LLM layer came to work is in the ledger itself:

| Dossiers | What the ledger shows |
|---|---|
| 1 to 4 | No LLM layer: no key was set (the dashboard shows these as "off") |
| 5 to 34 | The first model provider (an NVIDIA-hosted model) answered HTTP 403 on every call: {failed_calls} analyst calls failed, each failure recorded in its dossier |
| 35 to 45 | Mistral analysts answered, but {early['accepted']} of {early['rejected']} claims passed the validator ({why.get('unknown evidence id', 0)} cited an id that does not exist, {why.get('number not in cited evidence', 0)} used a number that is not in the cited item) |
| 46 onward | Analysts answer and claims pass the validator, as counted above |

Reproduce any row:

```bash
python3 -m verdict_desk stats --git                                  # the table above
python3 -m verdict_desk stats --first-llm-seq 35 --last-llm-seq 45   # an earlier range, e.g. the third row of the history
python3 -m verdict_desk stats --json                                 # every number, machine readable
```

"""


def battery_snapshot(ledger: list[dict], run_now: bool) -> dict:
    """The tamper battery on this chain: re-run on a full refresh, otherwise read back and checked against the chain."""
    if run_now:
        result = battery.run(ledger, ROOT / "ledger" / "timestamps", ledger[-1]["hash"], trials=BATTERY_TRIALS)
        snap = {key: result[key] for key in ("seed", "dossiers", "head", "total_trials", "total_missed", "all_caught")}
        BATTERY_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        BATTERY_SNAPSHOT.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return snap
    if not BATTERY_SNAPSHOT.exists():
        raise SystemExit(f"{BATTERY_SNAPSHOT.relative_to(ROOT)} is missing: run scripts/refresh_numbers.py without --skip-battery")
    snap = json.loads(BATTERY_SNAPSHOT.read_text(encoding="utf-8"))
    by_seq = {d["seq"]: d["hash"] for d in ledger}
    if by_seq.get(snap["head"]["seq"]) != snap["head"]["hash"]:
        raise SystemExit(f"the battery snapshot names dossier {snap['head']['seq']} with a hash this chain does not have")
    return snap


def differentiators(ledger: list[dict], snap: dict) -> tuple[str, str]:
    """The table of what a reader can check here, and the figures card above it, from one set of counts."""
    s = livestats.summarize(ledger)
    c, e, h = s["cadence"], s["llm_era"], s["head"]
    replays = [dossier.replay(d, rules_version=d.get("verdict", {}).get("rules_version", "v1")) for d in ledger]
    matched = sum(1 for r in replays if r["matches"])
    wit = battery.witness_index(ROOT / "ledger" / "timestamps", ROOT / "cre" / "deployment.sepolia.json", rekor=ROOT / "ledger" / "rekor")
    cre_battery = json.loads((ROOT / "cre" / "evidence" / "cre-battery.json").read_text(encoding="utf-8"))
    # The whole ledger, not only the LLM era, so the card says what the dashboard's "LLM claims" card says.
    every = livestats.llm_era(ledger, 1)
    no_number = every["rejection_reasons"].get("number not in cited evidence", 0)
    unknown_id = every["rejection_reasons"].get("unknown evidence id", 0)
    anchor = (wit.get("anchor") or {}).get("address", "")
    first = c["first"][:10]
    tried = f"{snap['total_trials']:,}"
    card = readme_cards.figures_svg(
        [
            (f"{c['total']}", "dossiers", f"{c['window_days']} days, hash-chained"),
            (f"{matched}/{len(ledger)}", "verdicts re-derived", "from frozen evidence"),
            (f"{every['rejected']}", "AI claims refused", "by the citation check"),
            ("3", "outside witnesses", "Bitcoin, Sigstore, Sepolia"),
            (f"{snap['total_missed']}/{tried}", "attacks missed", "on our own verifier"),
        ],
        f"As of dossier {h['seq']} · every figure counted from ledger/ledger.jsonl by scripts/refresh_numbers.py",
    )
    table = f"""<!-- differentiators:start: generated from dossier {h['seq']} by `python3 scripts/refresh_numbers.py` -->
## What you can check here that a debate log cannot show

| | The claim | Measured | Check it yourself |
|:---:|---|---|---|
| 🔁 | **The verdict is re-derived, not only re-hashed.** The deterministic judge rules again from the frozen evidence, so a record that no longer explains its own decision fails. Chainlink CRE acts as an **independent auditor**: before it anchors a head, the workflow re-derives that verdict inside the CRE runtime and refuses a forged one | **{matched} of {len(ledger)}** verdicts re-derive. CRE check: **{cre_battery['refusedUnderPublishedHead']:,} of {cre_battery['tried']:,}** edits of a dossier refused (dossier {cre_battery['seq']}) | `python3 -m verdict_desk replay` · [`cre/`](cre/README.md) |
| 🔢 | **Every number an AI analyst states must be in the evidence it cites.** A claim that cites an item id which does not hold that number is refused and kept with its reason | **{every['accepted']:,}** claims accepted, **{every['rejected']}** refused ({no_number} for a number not in the cited evidence, {unknown_id} for an evidence id that does not exist) | [Dashboard](https://andriyamaro.github.io/verdict-desk/), open any row |
| 🛰️ | **Three outside witnesses the operator does not control.** The head is timestamped into Bitcoin through OpenTimestamps (each proof reads pending until a block confirms it), signed in the public Sigstore log by a GitHub workflow (no key of ours), and anchored on Sepolia | **{len(wit['heads'])}** heads timestamped (OpenTimestamps), **{len(wit['rekor'])}** signed in Sigstore, contract [`{anchor[:10]}…`](https://sepolia.etherscan.io/address/{anchor}) | [Witnesses](https://andriyamaro.github.io/verdict-desk/witnesses.html) |
| 📜 | **A long public record, gaps included.** Every hourly decision is kept, the ones that lost money too, and every gap between runs is shown, never filled in | **{c['total']}** dossiers over **{c['window_days']} days** since {first} | `python3 -m verdict_desk stats --git` |
| 🧨 | **We attack our own verifier and publish the score.** Edits, deletions, swaps, forged heads, a truncated tail and re-hashed history, on a copy of the ledger | **{snap['total_missed']} missed** of **{tried}** attacks (dossier {snap['head']['seq']}) | `python3 -m verdict_desk battery` |

All five rows are counted from the ledger by the same script on every refresh; none is typed by hand. The trail shows what was decided and why; it does not show that a decision was right.
<!-- differentiators:end -->"""
    return table, card


def build_page_line(ledger: list[dict]) -> str:
    s = livestats.summarize(ledger)
    c, e, h = s["cadence"], s["llm_era"], s["head"]
    return (
        f"{c['total']} dossiers in `ledger/ledger.jsonl` as of dossier {h['seq']} ({c['last'][:16].replace('T', ' ')} UTC), over a window of {c['window_hours']} h ({c['window_days']} days); "
        f"from dossier {e['first_seq']} on, the citation validator accepted {e['accepted']} claims and rejected {e['rejected']} ({e['accepted_pct']}% accepted). "
        f"Counted by `python3 -m verdict_desk stats --git`; the chain keeps growing"
    )


def refreshed(tests: int | None, run_battery: bool = False) -> dict[Path, str]:
    ledger = dossier.read_ledger(LEDGER)
    problems = dossier.verify(ledger)
    if problems:
        raise SystemExit("the ledger does not verify, nothing to publish: " + "; ".join(problems))
    card_problems = card_reference_problems()
    if card_problems:
        raise SystemExit("a page names a card the dashboard does not draw: " + "; ".join(card_problems))
    out = {}
    text = README.read_text(encoding="utf-8")
    table, card = differentiators(ledger, battery_snapshot(ledger, run_battery))
    text, n = re.subn(r"<!-- differentiators:start.*?<!-- differentiators:end -->", lambda _: table, text, flags=re.S)
    if n != 1:
        raise SystemExit("README.md has no differentiators block to refresh")
    out[FIGURES_CARD] = card
    text, n = re.subn(r"<!-- live-numbers:.*?(?=## Auditing someone else's agent)", lambda _: live_section(ledger), text, flags=re.S)
    if n != 1:
        raise SystemExit("README.md has no live-numbers block to refresh")
    narrative = sum(1 for d in ledger if (d.get("evidence") or {}).get("narrative"))
    text, n = re.subn(r"(\d+) of (\d+) dossiers carry a narrative layer",
                      f"{narrative} of {len(ledger)} dossiers carry a narrative layer", text)
    if n != 1:
        raise SystemExit("README.md has no narrative-coverage line to refresh")
    convergences = sum(len((d.get("evidence") or {}).get("narrative", {}).get("convergence") or []) for d in ledger)
    text, n = re.subn(r"\*\*\d+ convergences\*\* recorded across the live ledger, as of dossier \d+",
                      f"**{convergences} convergences** recorded across the live ledger, as of dossier {ledger[-1]['seq']}", text)
    if n != 1:
        raise SystemExit("README.md has no convergence-count line to refresh")
    latest = next((d for d in reversed(ledger) if (d.get("evidence") or {}).get("narrative", {}).get("method") == "llm"), None)
    if latest is not None:
        block = latest["evidence"]["narrative"]
        text, n = re.subn(r"\d+ extractions accepted and \d+ rejected by the grounding gate in the latest dossier \(#\d+\)",
                          f"{len(block.get('extractions') or [])} extractions accepted and {len(block.get('rejected') or [])} rejected by the grounding gate in the latest dossier (#{latest['seq']})", text)
        if n != 1:
            raise SystemExit("README.md has no extraction-count line to refresh")
    out[README] = text
    if BUILD_PAGE.exists():
        page = BUILD_PAGE.read_text(encoding="utf-8")
        page, n = re.subn(r"<!-- numbers:start -->\n.*?\n<!-- numbers:end -->", lambda _: f"<!-- numbers:start -->\n- {build_page_line(ledger)}\n<!-- numbers:end -->", page, flags=re.S)
        if n != 1:
            raise SystemExit("SUBMISSAO_DORAHACKS.md has no numbers block to refresh")
        out[BUILD_PAGE] = page
    judge = JUDGE_PAGE.read_text(encoding="utf-8")
    now = livestats.summarize(ledger)
    acts = now["actions"]
    sized = acts.get("LONG", 0) + acts.get("SHORT", 0)
    parts = [str(now["head"]["seq"]), str(sized), str(len(ledger) - sized), str(acts.get("FLAT", 0)), str(acts.get("WATCH_REVERSAL", 0))]
    judge, n = re.subn(
        r"(It commits instead of hiding behind a HOLD: as of dossier )\d+(, )\d+( of them carry a sized LONG or SHORT; the other )\d+( are labelled FLAT \()\d+(\) or WATCH_REVERSAL \()\d+(\))",
        lambda m: m.group(1) + parts[0] + m.group(2) + parts[1] + m.group(3) + parts[2] + m.group(4) + parts[3] + m.group(5) + parts[4] + m.group(6),
        judge)
    if n != 1:
        raise SystemExit("web/judge.html has no commits-instead-of-HOLD line to refresh")
    out[JUDGE_PAGE] = judge
    page_d = DESCRICAO_PAGE.read_text(encoding="utf-8") if DESCRICAO_PAGE.exists() else None
    conv = sum(len((d.get("evidence") or {}).get("narrative", {}).get("convergence") or []) for d in ledger)
    llm = now["llm_era"]
    repl = {
        r"(\d+) convergences recorded so far, across (\d+) of (\d+) dossiers": f"{conv} convergences recorded so far, across {narrative} of {len(ledger)} dossiers",
        r"(\d+) accepted, (\d+) refused with reasons kept": f"{llm['accepted']} accepted, {llm['rejected']} refused with reasons kept",
        r"(\d+) dossiers and counting": f"{len(ledger)} dossiers and counting",
        r"(\d+) published verdicts": f"{len(ledger)} published verdicts",
    }
    for pat, repl_text in (repl.items() if page_d is not None else []):
        page_d, n = re.subn(pat, repl_text, page_d)
        if n != 1:
            raise SystemExit(f"DESCRICAO_PAGINA.md has no line matching {pat!r}")
    if page_d is not None:
        out[DESCRICAO_PAGE] = page_d
    if tests is not None:
        for path, pattern in TEST_COUNT_PATTERNS:
            if not path.exists():
                continue
            out.setdefault(path, path.read_text(encoding="utf-8"))
            out[path], n = re.subn(pattern, lambda m: f"{m.group(1)}{tests}{m.group(2)}", out[path])
            if n != 1:
                raise SystemExit(f"{path.name} has no test count matching {pattern!r}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="change nothing; exit 1 if a document is out of date with the ledger")
    parser.add_argument("--skip-tests", action="store_true", help="do not run the suite; leave the test counts as they are")
    parser.add_argument("--skip-battery", action="store_true", help="do not re-run the tamper battery (minutes); use its last snapshot")
    opts = parser.parse_args()
    tests = None if opts.skip_tests else count_tests()
    updated = refreshed(tests, run_battery=not (opts.check or opts.skip_battery))
    stale = [path for path, text in updated.items() if not path.exists() or text != path.read_text(encoding="utf-8")]
    if opts.check:
        for path in stale:
            print(f"out of date with the ledger: {path.relative_to(ROOT)}")
        return 1 if stale else 0
    for path in stale:
        path.write_text(updated[path], encoding="utf-8")
        print(f"updated {path.relative_to(ROOT)}")
    print("nothing to update" if not stale else "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
