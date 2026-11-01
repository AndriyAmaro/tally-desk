> **About this repository.** It starts as a snapshot of [AndriyAmaro/verdict-desk](https://github.com/AndriyAmaro/verdict-desk) at commit `c65bab9` (its state when the BLI Legal Tech Hackathon 2 closed), so that repository stays frozen while it is judged. The scheduled GitHub workflows and the demo videos stay there, and so does the git history that records which route wrote each dossier; the three tests that read those (`test_workflows`, `test_rekor_workflow`, `test_numbers_fresh`) only pass in the original. The work for MozFest Developers Wilding: Online, which puts every LLM call of the agent through the [Otari](https://otari.ai) gateway, lives on the `otari` branch.

<p align="center">
  <a href="https://andriyamaro.github.io/verdict-desk/"><img src="docs/readme/header.svg" width="100%" alt="Verdict Desk: an autonomous agent makes a market decision; Verdict Desk turns it into evidence a lawyer can file"></a>
</p>
<p align="center">
  <img src="docs/readme/figures.svg" width="100%" alt="Figures counted from the ledger: dossiers, verdicts re-derived, AI claims refused, outside witnesses, attacks missed">
</p>
<p align="center">
  <a href="https://andriyamaro.github.io/verdict-desk/"><img src="docs/readme/button-dashboard.svg" height="42" alt="Live dashboard"></a>
  <a href="https://andriyamaro.github.io/verdict-desk/demo/verdict-desk-demo-v8.mp4"><img src="docs/readme/button-video.svg" height="42" alt="Watch the demo"></a>
  <a href="https://andriyamaro.github.io/verdict-desk/judge.html"><img src="docs/readme/button-judge.svg" height="42" alt="Judge path"></a>
  <br>
  <a href="https://andriyamaro.github.io/verdict-desk/ask.html"><img src="docs/readme/button-ask.svg" height="42" alt="Ask the record"></a>
  <a href="https://andriyamaro.github.io/verdict-desk/evidence.html"><img src="docs/readme/button-lawyer.svg" height="42" alt="Evidence for a lawyer"></a>
  <a href="https://andriyamaro.github.io/verdict-desk/witnesses.html"><img src="docs/readme/button-witnesses.svg" height="42" alt="Witnesses"></a>
</p>

Audit-grade, replayable decisions for AI market agents. **Simulated only: no real-money order is ever placed** (from dossier 375 practice trades also go to Sepolia testnet with test tokens). Built for the BLI Legal Tech Hackathon 2 (bounty "Autonomous Agents", with a Chainlink CRE workflow for the second bounty).

<!-- differentiators:start: generated from dossier 791 by `python3 scripts/refresh_numbers.py` -->
## What you can check here that a debate log cannot show

| | The claim | Measured | Check it yourself |
|:---:|---|---|---|
| 🔁 | **The verdict is re-derived, not only re-hashed.** The deterministic judge rules again from the frozen evidence, so a record that no longer explains its own decision fails. Chainlink CRE acts as an **independent auditor**: before it anchors a head, the workflow re-derives that verdict inside the CRE runtime and refuses a forged one | **791 of 791** verdicts re-derive. CRE check: **3,000 of 3,000** edits of a dossier refused (dossier 356) | `python3 -m verdict_desk replay` · [`cre/`](cre/README.md) |
| 🔢 | **Every number an AI analyst states must be in the evidence it cites.** A claim that cites an item id which does not hold that number is refused and kept with its reason | **4,319** claims accepted, **299** refused (254 for a number not in the cited evidence, 44 for an evidence id that does not exist) | [Dashboard](https://andriyamaro.github.io/verdict-desk/), open any row |
| 🛰️ | **Three outside witnesses the operator does not control.** The head is timestamped into Bitcoin through OpenTimestamps (each proof reads pending until a block confirms it), signed in the public Sigstore log by a GitHub workflow (no key of ours), and anchored on Sepolia | **224** heads timestamped (OpenTimestamps), **174** signed in Sigstore, contract [`0x50443D51…`](https://sepolia.etherscan.io/address/0x50443D51Db71A61E131B147B1d36aF21d48CDdF7) | [Witnesses](https://andriyamaro.github.io/verdict-desk/witnesses.html) |
| 📜 | **A long public record, gaps included.** Every hourly decision is kept, the ones that lost money too, and every gap between runs is shown, never filled in | **791** dossiers over **15.2 days** since 2026-10-17 | `python3 -m verdict_desk stats --git` |
| 🧨 | **We attack our own verifier and publish the score.** Edits, deletions, swaps, forged heads, a truncated tail and re-hashed history, on a copy of the ledger | **0 missed** of **2,989** attacks (dossier 791) | `python3 -m verdict_desk battery` |

All five rows are counted from the ledger by the same script on every refresh; none is typed by hand. The trail shows what was decided and why; it does not show that a decision was right.
<!-- differentiators:end -->

## Check it yourself in 60 seconds

Nothing to install and nothing to take on trust: each page below downloads the raw ledger and redoes the work in **your own browser**.

| Open | What your browser does |
|---|---|
| 📊 [Dashboard](https://andriyamaro.github.io/verdict-desk/) | Recomputes every SHA-256 of the ledger. Press **Test the chain** and watch an edited record turn red. |
| 💬 [Ask the record](https://andriyamaro.github.io/verdict-desk/ask.html) | Talk to a recorded decision next to the chart of the candles it saw: why it acted, what would have changed its mind, the risk, **what happened next** (the later candles drawn and the exit marked, by the scoreboard's own rule), what the AI analysts and judge said, whether it went to Sepolia. No model runs on the page: every answer is built from the frozen record, every number is a chip that shows its source and lights its mark on the chart, and the page refuses any answer with a number no cited source contains. |
| 📣 [Market voices](https://andriyamaro.github.io/verdict-desk/narrative.html) | Recomputes each voice's track record (skill above what luck gives, no lookahead). Tick the voices that agree, set your own risk limit and see the practice position that follows. |
| 🛡️ [Witnesses](https://andriyamaro.github.io/verdict-desk/witnesses.html) | Reads the Bitcoin proof of the ledger head, asks two block explorers for the block, asks the Sigstore transparency log for the hourly signature of the head (signed by a GitHub workflow, no key of ours), reads the Sepolia anchor, and shows how many attacks on our own verifier got through. |
| ⚖️ [Evidence for a lawyer](https://andriyamaro.github.io/verdict-desk/evidence.html) | What each layer establishes and what it does not, and a foundation declaration drafted from a real dossier. |
| 📂 [Use case](https://andriyamaro.github.io/verdict-desk/usecase.html) | One real decision walked through the way a lawyer would: the exhibit, the evidence pack to download and paste into the verifier, the three outside witnesses, and what the record does not show. |
| 🧪 [Cause and effect lab](https://andriyamaro.github.io/verdict-desk/lab.html) | Remove one argument from a real decision and watch the verdict flip. |
| 🎓 [Learn it by breaking it](https://andriyamaro.github.io/verdict-desk/learn.html) | Seven short exercises, each one a way to try to fool the system and what it should teach you. |
| ✅ [Judge path](https://andriyamaro.github.io/verdict-desk/judge.html) | Every requirement of both bounties, each mapped to the file, command or transaction that proves it. |

From a terminal: `python3 -m unittest discover -s tests` (the suite), `python3 -m verdict_desk verify` (the chain), `replay` (every verdict re-derived from its frozen evidence), `trust-check` (every frozen voice table recomputed from the dossiers before it) and `battery` (attack a copy of the ledger and count what the verifier misses).

**Why it exists.** When an autonomous agent acts, the people who answer for it afterwards (an auditor, a compliance officer, a lawyer) need two things: exactly what the agent saw and decided at that moment, and a way to check that record without taking the operator's word for it. Verdict Desk produces that trail. Each decision is frozen as a **dossier**: the evidence the agent saw, with its sources and times, the arguments for and against, and the verdict. Dossiers are hash-chained, so editing a record leaves a mark that anyone can find by recomputing the hashes in their own browser, with no account and no server in the loop. Rewriting every later hash would hide an edit inside the chain but changes its head, so the head is published where the operator cannot quietly rewrite it: in git history today, and in an on-chain anchor on Sepolia that the CRE workflow wrote on 2026-10-21 (the evidence is below). The reasoning can be re-run from the frozen evidence and either gives the same verdict or shows exactly where it differs. The trail shows what was decided and why; it does not show that a decision was right, and this repository makes no claim about customers or deployments. The agent here trades on paper only, so the mechanism can be tested in public.

How it works in one paragraph: Verdict Desk reads public market evidence, freezes it, lets a bull agent and a bear agent argue over the same facts, and lets a deterministic judge size a *simulated* position inside declared risk limits. Every decision becomes a dossier that is hash-chained to the one before it. Open the dashboard, press **Test the chain**, and break the chain yourself in 30 seconds.

**The five checks, in 75 seconds:** [`web/demo/verdict-desk-demo-v8.mp4`](web/demo/verdict-desk-demo-v8.mp4) (captions: [`.srt`](web/demo/verdict-desk-demo-v8.srt)): the five things a debate log cannot show, on the real pages of the deployed site, recorded on 2026-10-30 at ledger head 671. The voice is synthetic. The Chainlink CRE scene is an excerpt of a `cre workflow simulate` run (no transaction sent), labelled as such on screen.

**What is new, in 96 seconds:** [`web/demo/verdict-desk-demo-v7.mp4`](web/demo/verdict-desk-demo-v7.mp4): the rules v6 AI judge on the real exhibit of dossier 296 (it reads only the claims that passed the citation check and adds 0.75, never enough to open a position alone), then the Chainlink CRE workflow re-deriving that verdict before anchoring it on Sepolia, and refusing a forged head. The exhibit is a real page recorded in a real browser; the terminal is drawn, and every line in it is copied from the logs in [`cre/evidence/`](cre/evidence/).

**The full tour (narrated, 2 min 49 s):** [`web/demo/verdict-desk-demo-v6.mp4`](web/demo/verdict-desk-demo-v6.mp4): every frame is a real page loaded from the deployed site and driven in a real browser: the tamper test turning the ledger red, the voice track record recomputed in the browser, the position that follows your own risk limit, the cause-and-effect lab, the three witnesses (a Bitcoin proof checked against two explorers, the Sigstore log, the Sepolia anchor), a fresh Chainlink CRE simulation with the head fetched from two sources and the real anchor checked against the public ledger, and the use case page for a lawyer. Published at `https://andriyamaro.github.io/verdict-desk/demo/verdict-desk-demo-v6.mp4`. Earlier versions are kept as history: [v5, 2 min 42 s](web/demo/verdict-desk-demo-v5.mp4) and [v4, 77 s](web/demo/verdict-desk-demo-v4.mp4).

Also available: the earlier [2-minute silent recording](web/demo/verdict-desk-demo.mp4) of raw terminal output. Recorded on 2026-10-20 against commit `ff8519a`; the on-screen numbers in that older file (46 dossiers) are from that day and are kept as history.

## What the bounty asks, and where to look

| The bounty asks for | Verdict Desk | Look here |
|---|---|---|
| Interpret market evidence | One frozen evidence packet per decision: candles, funding, news, and the narrative of public voices, each with source and time | `verdict_desk/evidence.py`, `narrative.py` |
| Record the simulated operation | Entry, size, stop, target and the portfolio marked against later candles | `verdict_desk/agents.py`, `portfolio.py` |
| Cause and effect | Every argument names the evidence item it rests on; LLM claims must cite item ids and numbers that really appear there | `verdict_desk/citations.py`, dashboard row details |
| A repeatable reasoning trail | `replay` re-derives the verdict from the frozen packet; `verify` checks the hash chain | `verdict_desk/dossier.py` |
| Chainlink CRE (second bounty) | A CRE workflow plus a receiver contract that anchor the ledger head and a Data Feed price on Sepolia, with the contract **source-verified (exact match)** | `cre/` (status table inside); evidence file `cre/SIMULATION-OUTPUT.md` (complete: simulation, Sepolia deployment and the anchored head of dossier 80); `cre/VERIFICATION.md` (source verification record and how to reproduce it) |

### Special Project Spotlight: the narrative agent this build already runs

The bounty's spotlight describes an agent that monitors user-selected crypto voices, extracts the token, sentiment, conviction and urgency from what they say, detects when independent voices converge on the same narrative, and turns that into a practice position under transparent rules and risk limits. That agent exists here and runs on every hourly decision:

| Spotlight requirement | What runs today | Measured evidence |
|---|---|---|
| Up to 20 user-selected voices | 13 voices in [`config/voices.json`](config/voices.json): 5 newsroom RSS feeds and 8 public Telegram channels (market trackers and analysts), all read without a key. Edit the file to choose up to 20; its SHA-256 is frozen in every dossier (`narrative.config`) together with the voices that returned nothing that hour | up to 8 messages per voice per run; 783 of 791 dossiers carry a narrative layer |
| Tokens, sentiment, conviction, urgency | LLM extraction per headline into a closed vocabulary, each with a literal quote from the headline | 31 extractions accepted and 12 rejected by the grounding gate in the latest dossier (#740) |
| Convergence across distinct voices | A narrative needs at least two different voices on the same token **and** the same stance | **2210 convergences** recorded across the live ledger, as of dossier 791 |
| Explain the candidates it rejected | Every mentioned token that did not become a narrative is kept with its reason: only one voice, voices disagreeing, or neutral mentions only | `narrative.rejected_candidates`, frozen in the dossier |
| Practice position under transparent rules and risk limits | The convergence becomes a citable evidence item; the deterministic judge sizes a simulated position inside the risk limit the user sets in the same file (`risk.max_risk_pct`, default 1%, system ceiling 25%, refused loudly above it) with an ATR stop and target | `verdict_desk/agents_v2.py`, `portfolio.py` |

Example from the live ledger: BTC bullish, converging across **CoinDesk, Cointelegraph, Decrypt and The Block**, top conviction *high*, top urgency *medium*, and that convergence is one of the items the judge is allowed to cite when it rules.

Choose your own voices and your own limit, then run it:

```bash
$EDITOR config/voices.json                                    # up to 20 voices (rss or telegram) and risk.max_risk_pct
python3 -m verdict_desk decide-v6 BTCUSDT --config config/voices.json
```

An invalid file is refused with the field and the reason, and nothing is recorded. A voice that fails or has nothing about a tracked token is written into the dossier as a gap (`narrative.config.gaps`), never skipped silently.

**One honest gap:** the spotlight names X as the KOL source. Telegram channels are read natively (their public preview needs no key); there is **no native X API integration**, because that API is paid and this project runs at zero budget. An X account works today through any public RSS bridge declared as `kind: rss` in the config, without touching the extraction, convergence or grounding logic (a test parses an X-shaped feed).

## Verifying a record someone hands you

A dossier that arrives as a file (forwarded, copied out of an email, transcribed) should not
require trusting the sender or this site. [`web/verify.html`](https://andriyamaro.github.io/verdict-desk/verify.html)
takes a pasted record and runs three checks in the reader's own browser:

| Check | What it catches |
|---|---|
| **Integrity** | the SHA-256 over the record's canonical JSON is recomputed; a single edited character fails it |
| **Linkage** | the record's `prev_hash` is matched against the previous record in the published ledger, so the file is part of a chain rather than a standalone claim |
| **Re-derivation** | the stored arguments are pushed back through `web/policy.js`; if today's rules produce a different action, the record no longer explains itself and the page says so |

The re-derivation uses the freshness window that record was made under, not today's, because an old
record should not fail for a rule that changed after it was written.

`bash scripts/check_pages.sh` renders every page in headless Chromium and asserts on the DOM, since
a page that renders nothing still returns HTTP 200.

## A third-party clock that needs no wallet

An on-chain anchor requires a funded key, so the hourly cadence pauses whenever the CI secrets are
absent. This does not: every run stamps the published head with **OpenTimestamps**, submitting the
digest to independent public calendars that commit it into a Bitcoin block. No key, no funds, no
account.

```bash
python3 -c "import json;print(json.load(open('ledger/head.json'))['seq'])"   # e.g. 116
ots verify ledger/timestamps/116.json.ots      # any machine, no trust in us required
```

Two details that decide whether this is evidence or decoration:

- **Proofs are archived per record.** A `.ots` proves the exact bytes that were stamped, so
  overwriting one file each hour would silently invalidate every earlier proof. Each record keeps
  its own snapshot and proof, and a test checks that every snapshot equals the published hash of
  that record.
- **Pending is a state, not a failure.** A fresh proof reads *pending confirmation in Bitcoin
  blockchain* until a block confirms it; later runs upgrade the earlier proofs.

## Decision exhibits: the record as a document, not a log line

`python -m verdict_desk exhibit <seq>` renders one dossier as a self-contained, printable document. It exists because a legal reviewer asks a different question than an engineer: *can I rely on this in a dispute?* Each exhibit answers it in seven sections:

| Section | What it establishes |
|---|---|
| 1. What the agent saw, and when | Custody: every source, item count and age, with freshness judged per source class |
| 2. Who argued what | Each claim next to the **evidence text it cites**, and each claim refused with the exact reason (`number not in cited evidence: 1.6`) |
| 3. Which argument decided it | The record's own sensitivity block: the edge and action that would remain without each argument, with the deciding ones marked |
| 4. The decision | Action, edge, entry, stop, target, size, declared risk and the system cap |
| 5. Market voices | Converging narratives and the tokens that never converged, with reasons |
| 6. Integrity | The hash **recomputed when the exhibit was rendered** against the recorded one, the previous link, the chain position, and the on-chain anchor status |
| 7. What this exhibit does not prove | Simulated only, no orders, fees excluded, LLM opinions validated for citation and not for merit, secondary sources inherit their errors |

The live site publishes the last five exhibits plus the anchored record, regenerated on every hourly run: [`web/exhibit/`](https://andriyamaro.github.io/verdict-desk/exhibit/index.html). The anchored record also ships as a signed PDF certificate with a VERIFIED seal: [`exhibit-80.pdf`](https://andriyamaro.github.io/verdict-desk/exhibit/exhibit-80.pdf).

## Rules v4 and v5: the multi-KOL convergence creates the position

The spotlight asks the agent to "create a practice position using transparent signal rules". Since
rules v4, a narrative convergence matching the symbol becomes a **deterministic** argument whose
weight is a stated formula (0.5 per distinct voice, max 4, plus 0.5 when top conviction is high),
so the multi-KOL layer drives the position by a rule the replay reproduces, not by the LLM's
opinion. Earlier versions (v1/v2/v3) are unchanged and keep replaying under their own version.

Rules v5 answers "which voices are trusted?" with a measurement instead of a list. Each voice's
directional calls are scored against the market's move over the next 4 hours (read from the candles
frozen in the dossiers), compared with the hit rate **luck alone would give that voice** (a voice that
only says "up" earns exactly zero), and the excess is shrunk toward zero on small samples; under 8
settled calls a voice has no record and counts 1.00. A convergence then weighs 0.5 × each voice's
weight (the four heaviest) plus 0.5 at high conviction. There is **no lookahead**: each dossier
freezes the table of the voices it read, computed only from the dossiers before its run, and
`python3 -m verdict_desk trust-check` recomputes every frozen table from that earlier ledger. The
browser recomputes the same table from the raw ledger ([Market voices](web/narrative.html)), compared
with the Python by a test. With no record, v5 equals v4.

## Rules v6: the AI judge counts only on claims that passed the citation check

In v1 to v5 the LLM analysts are recorded but never move the verdict. In v6 a third model, the judge,
reads **only the claims that passed the citation validator** (labelled B1.. for the bull, S1.. for the
bear; refused claims never reach it) and leans bull, bear or neutral. The lean becomes one argument on
that side, with a weight from a fixed table: 0.5, 0.75 or 1.0 for low, medium or high conviction. The
cap of 1.0 sits below the entry band of 1.5, so the judge can tip a close call but **never opens a
position on its own**. The ruling is voided, and weighs zero, when it rests on no kept claim of its own
side, names a claim id that does not exist, or puts a number in its reason that the relied-on evidence
does not contain. The raw ruling is frozen in the dossier (`llm.judge`) and the weight is recomputed
from it on replay, so editing the ruling flips the replay and editing the stored weight changes
nothing. With no LLM, v6 decides exactly like v5.

## The chain of custody in one image

![Verdict Desk chain of custody](web/custody.svg)

Six stages, none of which can be skipped: evidence is frozen, debated with citations, validated,
ruled on deterministically, hash-chained into a dossier, and independently anchored: on Sepolia
through the CRE workflow and in Bitcoin through OpenTimestamps. The last row of each box states
what fails closed.

## Try the cause-and-effect lab

`web/lab.html` answers, in the browser, the two questions a reviewer actually asks:

1. **Which single argument decided this verdict?** Pick any dossier from the published ledger and the page shows, per argument, the edge and the action that would remain if that argument were removed, with the verdict-deciding ones highlighted. This is the dossier's own recorded `sensitivity` block, not a re-run.
2. **What rule would fire for inputs I choose?** Move the sliders and the state is named with the exact rule that produced it: entry band, high-volatility rule, the freshness gate, and fail-closed behaviour on stale price.

The playground is a mirror of the judge in `web/policy.js`. A mirror that is never compared is a second implementation waiting to lie, so `tests/test_policy_parity.py` runs it over 486 input combinations and fails when any state differs from what `verdict_desk/agents_v2.py` returns for the same numbers.

## Risk limits are the user's, inside a ceiling the system owns

`--risk-pct` sets the risk per practice position (default 1%). The system refuses anything above **25%** instead of silently clamping it, and every dossier freezes both numbers, so an auditor sees the limit that was declared next to the cap that governed it, and a replay reapplies the same limit rather than the default.

```bash
python3 -m verdict_desk decide-v6 BTCUSDT --risk-pct 2
python3 -m verdict_desk decide-v6 BTCUSDT --risk-pct 30   # refused: exceeds the system cap of 25%
```

## Freshness is judged per source class, not as one number

A single window is wrong in both directions: it either accepts a stale price (dangerous) or discards a good headline (silly). Price is checked against a **75-minute** window and **blocks** the decision; news and market voices are checked against a **26-hour** window and only **warn**, because yesterday's headline is still auditable while yesterday's price is not. The result is frozen into every dossier under `evidence.freshness.classes`.

## Five-minute judge path

1. Open the dashboard (the GitHub Pages site of this repository). It loads `ledger/ledger.jsonl` and shows the banner "Every hash recomputed in this browser matches". It does not trust the server: the hashing runs locally in `web/canonical.js`.
2. **Test the chain (30 seconds, one click, nothing to download).** Press **Test the chain**. It flips one decision in a copy held in your tab: the row turns red, the banner says the chain no longer verifies and names the dossier, and a three-row table shows the recorded hash next to the one your browser recomputed. "Tamper and re-hash" plays the cleverer attacker: the edited row looks valid again, but the next row no longer links to it (`chain broken`). "Rewrite the whole chain" shows the limit: everything verifies again, but the head hash is different, which is why the head is published outside the operator's reach (git history, other clones, and the on-chain anchor on Sepolia). You can also edit a decision by hand, or download the tampered copy and run `python3 -m verdict_desk --ledger ledger-tampered.jsonl verify`: the Python verifier reports the same dossier. Nothing is sent anywhere and the server's file is never touched.
3. Read the three panels above the table: the **cadence** of the live history (every gap between runs, nothing filled in), the **LLM debate** counts (claims the citation validator accepted and rejected) and the **simulated portfolio** (recomputed in your browser with the same rule as `portfolio.py`). Then click a row. You see the deterministic bull and bear arguments with the exact indicator behind each one, then the LLM analysts' claims with the evidence id each one cites, and the claims the validator **rejected**, with the reason. The LLM debate works from dossier **46** on; the section "Live operation" below says what the earlier dossiers show.
4. Run it yourself:

```bash
python3 -m unittest discover -s tests          # standard library only (567 tests)
python3 -m verdict_desk verify                 # hash chain of ledger/ledger.jsonl
python3 -m verdict_desk replay                 # re-derive every verdict from its frozen evidence
python3 -m verdict_desk portfolio              # simulated positions marked against later candles
python3 -m verdict_desk stats --git            # the live-operation numbers below, counted from the ledger
python3 -m verdict_desk export --seq 95 --out pack.json   # self-verifying evidence pack for one dossier
python3 -m verdict_desk monitor                # compare the ledger with the head anchored on Sepolia
```

## How it works

```
public data ──> frozen evidence packet ──> bull agent ┐
(candles, funding,   (source + time on     bear agent ├─> deterministic judge ──> dossier (hash-chained)
 news, RSS voices)    every item)          narrative  ┘     size, stop, target        │
                                                                                      └─> ledger/ledger.jsonl ──> dashboard, replay, CRE anchor
```

- **Evidence.** Binance public spot mirror (candles), OKX public funding, GDELT news, and public RSS or Atom feeds. No API key is needed for any of them. A source that fails reduces the evidence and is recorded as a gap; it never fakes a decision.
- **Fail-closed on stale evidence.** Market evidence carries a freshness window (2 hours). The age of the newest candle is computed when the packet is frozen and stored inside it, so the check itself is replayable and never reads the clock later. If the evidence is older than the window the judge returns **`BLOCKED_STALE`** and sizes no position: it refuses to decide on a stale price instead of quietly extrapolating one. The refusal is written into the dossier, so a blocked decision is as auditable as a taken one, and the portfolio only ever opens LONG or SHORT. Dossiers recorded before this gate existed have no freshness key and keep replaying unchanged.
- **Evidence pack.** `python3 -m verdict_desk export --seq N --out pack.json` writes one dossier as a self-contained file for a third party: the recomputed hash next to the recorded one, the replay result, the neighbouring hashes that pin it in the chain, the plain-language steps to check it without trusting the exporter, and a `pack_digest` over the whole thing. Anything edited anywhere in the pack changes that digest.
- **Agents.** The bull and bear analysts are LLM agents (Mistral API, free tier) that must cite evidence ids. A validator discards any claim with no citation, an unknown id, or a number that is not in the cited items. The judge that decides action, size, stop and target is deterministic code, so the same frozen packet always yields the same verdict.
- **Named states (rules v3).** When the evidence does not justify an entry, the verdict says why instead of returning a flat `FLAT`. `WATCH_REVERSAL` means one single argument is worth enough that losing it would flip the decision, and the rationale names that argument. `REDUCE_EXPOSURE` means both sides are strong while volatility is high, so nothing new opens and existing exposure should shrink. The thresholds, the sizing and the entry decisions are identical to v2: only the label of a decision that was not taken changed. v2 dossiers keep replaying under v2, which is why the version travels inside each dossier.
- **Cause and effect.** Each dossier also freezes a sensitivity table: how much more net edge would flip the verdict to LONG or SHORT, and, for each argument, what the verdict would be without it (and whether that single argument decides it). It comes from the same arguments and weights the judge used, so it is deterministic and replayed too.
- **Narrative agent.** Reads up to 20 public voices. The LLM extracts token, stance, conviction and urgency per headline. Headlines are untrusted input, so each extraction must carry a quote that is a literal part of the headline, a voice id that exists and values from a closed vocabulary; anything else is dropped and logged in the packet. Convergence needs at least two distinct voices on the same token and stance. If the model is down the packet says `keywords` and uses a plain word-list fallback.
- **Ledger.** JSONL, one dossier per line, each with `prev_hash` and its own `hash` over RFC 8785 canonical JSON. The same canonicalization runs in Python and JavaScript, with shared test vectors, which is what lets the browser recompute the exact hash Python wrote.
- **Anchor monitor.** A CRE workflow anchors the head of the chain on Sepolia. `python3 -m verdict_desk monitor` (read-only, no key) reads the anchored value back from the contract and compares it with the dossier of that same sequence in the published ledger: it prints MATCH, or names the divergence. Rewriting old dossiers breaks this even if every local hash is recomputed, which is the limit the dashboard's "Rewrite the whole chain" button demonstrates. The hourly workflow runs it as a guard: a mismatch fails the run, an unreachable RPC only warns.
- **Frozen LLM output.** The model's words are stored in the dossier and are not regenerated on replay. Replay re-derives everything deterministic and re-checks that each stored citation is still valid against the frozen evidence.

## Live operation, measured from the ledger

<!-- live-numbers: generated from dossier 791 by `python3 scripts/refresh_numbers.py` (counts: `python3 -m verdict_desk stats --git`) -->
Counted over `ledger/ledger.jsonl` on the chain as of **dossier 791** (head `2c3572fbd28f8a51`, collected 2026-11-01 13:08 UTC), after `verify` passed. Nothing is estimated: every figure is a count over ledger lines, plus `git blame` of that file for the author rows. The dashboard recomputes the same numbers in your browser from whatever ledger you load, and a test checks that the Python and the JavaScript agree on this ledger. The chain grows with every run, so these figures are a snapshot; the dashboard and the commands below always show the current chain.

| What | Number |
|---|---|
| Dossiers in the verified chain | 791 (BTCUSDT 276, ETHUSDT 270, SOLUSDT 245) |
| Operating window, first to last dossier | 363.7 h, 15.2 days (2026-10-17 09:26 to 2026-11-01 13:08 UTC) |
| Clock hours (UTC) in that window with at least one dossier | 230 of 365 (63.0%) |
| Longest gap between consecutive dossiers of the same symbol | 8 h 34 min (SOLUSDT); 107 gaps over 90 minutes |
| Recorded by the workflow bot (scheduled or manually dispatched runs) | 788 (dossiers 1-788) |
| Recorded by other commits | 3 (dossiers 789-791): runs made outside the workflow while the demo tooling and the v2 rules were being built; the ledger is append-only, so they stay |
| LLM era, dossier 46 onward | 746 dossiers, 741 with analyst output (dossiers 54-58 have no LLM layer) |
| Claims **accepted** by the citation validator | 4319 (bull 2105, bear 2214) |
| Claims **rejected** by the citation validator | 251 (bull 117, bear 134); reason: "no citation" 1, "number not in cited evidence" 250 |
| Share of claims accepted | 94.5% |

The window is not continuous coverage: scheduled GitHub runs are best effort and most hourly slots did not fire, which is why the coverage and gap rows sit next to the window. The dashboard's cadence panel shows the same figures. From 25 Oct the hourly workflow is triggered four times an hour behind a gate that records at most one set of decisions per 50 minutes, and a second workflow, `pacemaker`, waits 52 minutes, dispatches the next run and queues its own successor, because scheduled runs alone did not fire for six hours that day; these rows will show whether that helped, and they are not rewritten if it did not.

Earlier dossiers stay exactly as they were recorded, so the history of how the LLM layer came to work is in the ledger itself:

| Dossiers | What the ledger shows |
|---|---|
| 1 to 4 | No LLM layer: no key was set (the dashboard shows these as "off") |
| 5 to 34 | The first model provider (an NVIDIA-hosted model) answered HTTP 403 on every call: 60 analyst calls failed, each failure recorded in its dossier |
| 35 to 45 | Mistral analysts answered, but 0 of 48 claims passed the validator (44 cited an id that does not exist, 4 used a number that is not in the cited item) |
| 46 onward | Analysts answer and claims pass the validator, as counted above |

Reproduce any row:

```bash
python3 -m verdict_desk stats --git                                  # the table above
python3 -m verdict_desk stats --first-llm-seq 35 --last-llm-seq 45   # an earlier range, e.g. the third row of the history
python3 -m verdict_desk stats --json                                 # every number, machine readable
```

## Auditing someone else's agent

`audit-journal` takes the journal of an autonomous agent (one turn per line, with the evidence it saw) and does two things. First it checks every candle and price the journal recorded against public Binance data, counting only candles that had already closed at the turn time and comparing prices within a small tolerance because the agent reads perpetual futures while the public mirror is spot (volume is not compared). Second it replays each symbol-hour in shadow: the desk builds a packet from candles closed at that moment plus the funding the journal recorded, applies the same rules, and reports what it would have recorded next to what the agent did. The output is labeled as a shadow replay and never as a trade result, and the journal's free-text reasoning is never copied into it.

## Honest results

These numbers are tests on past data, not live trading results, and they are published because the system is a reasoning-trail tool, not a money machine.

| Walk-forward backtest, rules v1, latest 4,000 hourly candles on 2026-10-17, 0.05% fee per side | Net return | Profit factor | Buy and hold |
|---|---|---|---|
| BTCUSDT | -18.66% | 0.85 | -0.1% |
| ETHUSDT | -9.98% | 0.92 | +3.13% |
| SOLUSDT | +1.31% | 1.01 | +17.98% |

Each `backtest` run prints the sha256 of its candle series so the numbers can be reproduced on the same data. The window rolls, so a later run on newer candles will differ.

The offline study in [`research/`](research/README.md) searched a tuned set of v1 parameters (choose on the first 60%, judge on the last 40%, 36 candidate settings). It beat v1 out of sample on 3 of 3 symbols but was net positive on only 1 of 3 and never beat buy and hold, so it was not adopted.

**Which rules record the live dossiers.** The backtest above is for the v1 rules, which recorded dossiers 1 to 53 and a few manual comparison runs. The hourly workflow now records with the v2 rules in `verdict_desk/agents_v2.py`: the v1 arguments plus RSI, MACD and Bollinger arguments, and an entry threshold of 1.5 instead of 2.0. Each dossier says which rules recorded it in `verdict.rules_version`, and `replay` re-derives it with those rules. The v2 rules are not the tuned parameters of the study above, and **they are not covered by the backtest table**: it describes v1 only. How they split between LONG and SHORT, tested against a mirrored market, and what they earned in the same walk-forward protocol, is measured in [`research/V2_DIAGNOSIS.md`](research/V2_DIAGNOSIS.md).

## Live history

The workflow [`.github/workflows/hourly.yml`](.github/workflows/hourly.yml) is triggered four times an hour (a gate records at most one set of decisions per 50 minutes). Each run appends decisions for BTCUSDT, ETHUSDT and SOLUSDT with `decide-v5` (earlier dossiers keep replaying under the rules they were recorded with), verifies the chain, writes `ledger/head.json`, commits, and redeploys the dashboard. Git commit times are an independent clock. GitHub scheduled runs are best effort and can be delayed or skipped, so read "Live operation" above, the dashboard's cadence panel and the commit log for the real cadence and the gaps. The commands `decide` and `decide-v2` refuse to append to the public ledger outside the workflow (set `VERDICT_DESK_APPEND_PUBLIC=1` to override, or use `--ledger PATH` for a scratch copy), and when they do append to it they refresh `ledger/head.json`, so the published head cannot lag the chain.

## Chainlink CRE

[`cre/`](cre/README.md) holds a workflow that fetches the published ledger head from two independent publications (raw file and Pages copy, which must be identical) with identical consensus, fetches the dossier behind that head from both, requires it to hash to the head and **re-derives its verdict from the frozen evidence inside the workflow** (refusing to anchor if the action or edge differ), reads the BTC/USD Data Feed on two chains (Sepolia and Ethereum mainnet, refusing if they disagree by more than 500 basis points), and writes the head and the price to `LedgerAnchor` through a CRE report. A tamper battery against that check (`cd cre/anchor && bun run battery.ts 3000`) let none of 2,760 decision-changing edits through ([`cre/evidence/cre-battery.json`](cre/evidence/cre-battery.json)). The contract accepts reports only from the CRE forwarder and only with a higher sequence number than the last one.

Status, stated plainly: the workflow is covered by 10 tests with the SDK mocks and type-checks; the contract is covered by 6 tests in an in-memory EVM. **Both ran on 2026-10-21**: the workflow simulated (dry, then with `--broadcast`), `LedgerAnchor` was deployed to Sepolia, and the head of dossier 80 is anchored in a real transaction. `node cre/contracts/scripts/check-anchor.mjs` answers `ok: true`.

The evidence is in [`cre/SIMULATION-OUTPUT.md`](cre/SIMULATION-OUTPUT.md); the summary is copied into this table.

<!-- cre-evidence:start -->
| Evidence | Value |
|---|---|
| Run date (UTC) | 2026-10-21 |
| Head anchored (seq, hash) | 80, `04e260dc39cdecb967ef23513aebd7aee260d2101e2507c7704c94dfb0532283` |
| BTC/USD Data Feed value recorded with it | 84378.12678601 (answer 8437812678601, 8 decimals) |
| `LedgerAnchor` contract on Sepolia | [0x50443D51Db71A61E131B147B1d36aF21d48CDdF7](https://sepolia.etherscan.io/address/0x50443D51Db71A61E131B147B1d36aF21d48CDdF7) |
| Anchoring transaction | [0x53e5a96312ff3f200f7cb1c1abc0b96313272b5fc59c4bf85a136f6a26c2694d](https://sepolia.etherscan.io/tx/0x53e5a96312ff3f200f7cb1c1abc0b96313272b5fc59c4bf85a136f6a26c2694d) |
<!-- cre-evidence:end -->

Anyone can check the chain against the ledger with `node cre/contracts/scripts/check-anchor.mjs` (read-only, no key needed): it verifies the ledger, reads the anchor from Sepolia and asks the contract `isAnchored(seq, hash)`. Runbook and one-command script: [`cre/DEPLOY_SEPOLIA.md`](cre/DEPLOY_SEPOLIA.md).

## Limits

- The hash chain proves **integrity after recording**, not **authenticity at collection time**. Independent witnesses narrow the gap: sources and timestamps are in every packet (anyone can refetch the candles), git history timestamps each commit, and the CRE anchor pins the head on-chain (Sepolia; dossier 80 at the last run).
- A literal headline can still be wrong or manipulative. The grounding gate proves the headline says it, not that it is true. One voice never creates a convergence.
- The model-written text is not reproducible on replay by design; it is frozen and re-validated.
- One exchange mirror, one rule set, simulated fills at the last close with fees. This is a trail of reasoning, not a trading edge.

## Run it

Python 3.10 or newer, standard library only. Optional: Node (cross-language hash test), Bun (CRE workflow tests).

```bash
export MISTRAL_API_KEY=...         # optional, free Mistral API key; without it the LLM layers are off and labeled so
export MISTRAL_MODEL=mistral-small-latest   # optional
python3 -m verdict_desk --ledger scratch.jsonl decide BTCUSDT ETHUSDT --default-feeds       # v1 rules, into a scratch ledger
python3 -m verdict_desk --ledger scratch.jsonl decide SOLUSDT --feed "My voice=https://example.com/feed.xml"
python3 -m verdict_desk --ledger scratch.jsonl decide-v2 BTCUSDT ETHUSDT SOLUSDT --default-feeds   # the v2 rules on the built-in feeds
python3 -m verdict_desk backtest BTCUSDT
python3 -m verdict_desk research BTCUSDT ETHUSDT SOLUSDT
python3 -m verdict_desk audit-journal path/to/agent_journal.jsonl --out audit.json   # check a third-party agent journal against public data, replay it in shadow
python3 -m verdict_desk --ledger scratch.jsonl decide-v6 BTCUSDT ETHUSDT SOLUSDT --config config/voices.json   # what the hourly workflow runs
python3 -m verdict_desk trust                                          # per-voice track record, skill above chance
python3 -m verdict_desk trust-check                                    # every frozen trust table, recomputed from the dossiers before it
python3 -m verdict_desk battery                                        # attack a copy of the ledger and count what verify misses (about a minute)
python3 -m verdict_desk witnesses --out witnesses.json                 # the stamped heads and anchor contract the witnesses page reads
python3 -m verdict_desk head --out ledger/head.json
python3 -m verdict_desk stats --git                                    # live-operation numbers counted from the ledger
python3 -m verdict_desk --ledger ledger-tampered.jsonl verify          # check a copy downloaded from the dashboard
```

The key is read from the environment only and is never written to a dossier or a log.

### Run it through Otari (the `otari` branch)

With `OTARI_API_KEY` in the environment, every LLM call goes through the [Otari](https://otari.ai) gateway at `https://otari.ai/api/v1` and each role uses its own open-weight model from [`config/otari.json`](config/otari.json). Without the variable nothing changes: the client above runs as before.

| Role | Catalog id | Vendor | What it does |
|---|---|---|---|
| `bull` | `openai/gpt-oss-120b` | OpenAI | the case for a long entry |
| `bear` | `nvidia/nemotron-3-super-120b-a12b` | NVIDIA | the case against it |
| `judge` | `deepseek/deepseek-v4-flash-0731` | DeepSeek | rules on the claims that passed the citation check |
| `checker` | `google/gemma-3-27b-it` | Google | re-reads each validated claim against the evidence it cites (advisory: it never changes the verdict) |
| `narrator` | `alibaba/qwen3-235b-a22b-instruct-2507` | Alibaba | extracts tokens and stance from the news headlines |

`python3 tools/otari_catalog_check.py` confirms against the public catalog (no key, no cost) that each id exists and is flagged `open_weights`.

- **Auth.** The key travels in the `Otari-Key` header. `Authorization: Bearer` and `x-api-key` also answered HTTP 200 on 2026-11-01; change `auth_header` in the config to switch. `OTARI_BASE_URL` overrides the base URL.
- **Receipt per call.** Each answer leaves an `otari-receipt/1` next to the block it produced (`llm.bull.receipt`, `llm.bear`, `llm.checker`, `llm.judge`, and `evidence.narrative.receipts` for the extraction batches): `request_id`, `provider`, `sent_model`, `served_model`, `served_by`, `attempt_count`, `fallback`, `duration_ms`, token counts, `cost_usd` (as Otari sent it) and `pricing_source`. A refused call leaves a receipt with the error code, the budget scope and `Retry-After`. The receipts are part of the sealed dossier, so the hash chain covers them. `replay` never calls a model: it re-derives the verdict from the frozen evidence and the frozen judge ruling, and it passes with the network cut.
- **Per-user budgets.** The role name is the `user` field once a service key exists (`send_user` in the config, or `OTARI_SEND_USER=1`). With an ordinary key Otari refuses that field, so it is off by default ([`docs/FEEDBACK_LOG.md`](docs/FEEDBACK_LOG.md), entry 2).
- **What every receipt carries.** `role`, `end_user`, `sent_model` and `policy` (the routing policy named, else null), `request_id` with `request_id_source` (`otari` for the gateway's `Otari-Request-ID`; `client` with the id `client:<our Idempotency-Key>` when no gateway answer exists, as after a timeout or when the cycle cap refused the call, so no receipt is anonymous), `attempt_count`, `fallback`, `client_attempts` on failures and retried calls, and for guardrails the raw header, the parsed list and `guardrail_result` (`not_evaluated`, `pass` or `flagged`). Dossier 793, the first sealed with the service key, predates `policy`, `request_id_source` and `guardrail_result`; its receipts carry the same facts in the older fields. Retries happen only on the same `Idempotency-Key`, with growing waits (exponential, capped at 60 s) and at most five tries; the fallback is Otari's policy, never a loop of ours.
- **No `route` block.** Otari sends counts (`attempt_count`, `fallback`), not the list of attempts, so in Otari mode no `route` or `routing_policy` is sealed; the receipt is the record. **Dossier 792, the first one recorded through Otari, still carries the legacy `route` field** (a one-item list rebuilt from its receipt, `policy: null`) because it was sealed before this was changed, on each of its bull, bear, checker and judge blocks, and the ledger is append-only. Read it as derived from the receipt, not as routing data from Otari. Later dossiers do not have it.
- **Base URL and pacing.** `OTARI_BASE_URL` must be `https://` (anything else is refused before a request, so the key never travels in clear text) and loses a trailing slash. Otari calls are not spaced by the 1.1 s Mistral interval: a burst of 12 parallel calls on 2026-11-01 got twelve 200s and no 429. `OTARI_MIN_INTERVAL` (or `min_interval` in the config) adds spacing if a limit ever shows up.
- **Fallback policies, drills and budgets (F2).** `sent_model` is the catalog id unless a routing policy is set for the role: `use_policies` and `policies` in the config (or `OTARI_USE_POLICIES=1`), or `OTARI_POLICY_<ROLE>` for one role. The receipt then shows the policy as `sent_model`, the model that answered as `served_model`, and `fallback` and `attempt_count`. `OTARI_USER_<ROLE>`, `OTARI_END_USER_BUDGET_<ROLE>` (header `Otari-End-User-Budget`) and `OTARI_MAX_TOKENS_<ROLE>` let one role run as a drill user on a tiny budget. A refused call (403 `budget_exceeded` with `Otari-Budget-Scope` and `Retry-After`, 402 `insufficient_funds`) leaves a refusal receipt in the dossier, the command line prints `[otari judge] NOT answered: ...`, and the decision is still recorded.
- **Service key, drills and the guardrail.** The key is a service key: each role is billed to its own end user (`vd-bull`, `vd-bear`, `vd-judge`, `vd-checker`, `vd-narrator`) on the dashboard budget `vd-analyst`, sent as `Otari-End-User-Budget` (the budget id, not its name). The judge sends the routing policy `vd-judge`, which carries a mandatory `prompt-injection` guardrail in monitor mode; the receipt keeps the `Otari-Guardrails` header raw and parsed, and Otari reported `valid: null` (nothing evaluated), which the app records as "not evaluated", never as a pass. `python3 -m verdict_desk otari-drill fallback|budget|all` runs two real drills and appends their receipts and usage rows to [`docs/evidence/otari-drills.jsonl`](docs/evidence/otari-drills.jsonl) (not to the ledger): `fallback` sends a prompt too long for the 32k-context main model of the policy `vd-judge-drill`, so Otari serves the backup (`Otari-Fallback: true`, `Otari-Attempt-Count: 2`, two usage rows); `budget` bills the end user `vd-drill` on the USD 0.01 budget `vd-drill-tiny` and keeps the 403 `budget_exceeded` refusal as a receipt.
- **Cycle cap.** Behind Otari's own budgets the app keeps a second line: `cycle_cap_usd` in the config (default 0.02, `OTARI_CYCLE_CAP_USD` overrides) is the most one decision cycle may spend, counted from `usage.cost_usd`. Once reached, no further call is sent and each refused role leaves a client-side refusal receipt (`cycle_cap_exceeded`, `source: client`). The cycle's spend is sealed in `llm.otari.cycle`.
- **Retries.** A gateway timeout (HTTP 504) is retried with the same `Idempotency-Key`, so a lost reply is not billed twice. Budget and credit refusals (402, 403) are never retried.

```bash
export OTARI_API_KEY=...      # from the Otari dashboard; never commit it
VERDICT_DESK_APPEND_PUBLIC=1 python3 -m verdict_desk decide-v6 BTCUSDT --config config/voices.json
python3 -m verdict_desk verify && python3 -m verdict_desk replay
```

## Layout

| Path | What it is |
|---|---|
| `verdict_desk/` | The engine: evidence, narrative, voices (the user's config), trust (per-voice track record), features, agents, citations, llm, dossier, portfolio, battery (attacks on our own verifier), backtest, research, canonical JSON, CLI |
| `config/voices.json` | The voices and the risk limit the user chooses (up to 20 voices); its hash is frozen in every dossier |
| `web/` | Static dashboard and the browser verifiers: ledger hashes, voice track record (`trust.js`), OpenTimestamps proofs (`ots.js`), Sigstore log entries (`rekor.js`), the policy mirror (`policy.js`); pages for market voices, witnesses and evidence |
| `ledger/` | The public ledger and its head |
| `cre/` | Chainlink CRE workflow, receiver contract and tests |
| `docs/architecture.md` | Architecture, decisions and risks |
| `research/` | Rules v2 train and test study, and the diagnosis of the v2 rules that record the live dossiers |
| `tests/` | Unit tests, cross-language vectors |

## License and credits

MIT, see [`LICENSE`](LICENSE). The CRE project layout follows the MIT-licensed `smartcontractkit/cre-templates`; `@chainlink/cre-sdk` (BUSL-1.1) is used as a dependency and is not vendored. Everything else is original.

Built by Andri Amaro with the help of an AI coding agent (Claude Code). The AI assistance is disclosed here on purpose.
