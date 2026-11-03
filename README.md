<p align="center">
  <a href="https://github.com/AndriyAmaro/verdict-desk-otari/tree/otari"><img src="docs/readme/otari-header.svg" width="100%" alt="Verdict Desk on Otari: a paper-trading agent whose every model call goes through the Otari gateway and leaves a receipt that anyone can re-verify offline"></a>
</p>
<p align="center">
  <img src="https://raw.githubusercontent.com/AndriyAmaro/verdict-desk-otari/otari/docs/readme/otari-figures.svg" width="100%" alt="Figures counted from the ledger: Otari-era dossiers, sealed receipts, provider and model routes that answered, resources proven, backup-route answers">
</p>

# [The hackathon entry lives on the `otari` branch](https://github.com/AndriyAmaro/verdict-desk-otari/tree/otari)

Verdict Desk is a paper-trading agent that answers for every model call it makes: each call goes through the [Otari](https://otari.ai) gateway, each role runs on an open-weight model, and each call leaves a receipt in a hash-chained ledger that anyone can verify and replay offline. No real-money order is ever placed.

**Start here:** the [README of the `otari` branch](https://github.com/AndriyAmaro/verdict-desk-otari/tree/otari#score-map) opens with a score map (rubric line, what we did, where to verify), then the quickstart and the proofs table with request ids. Guardrails are listed there as NOT PROVEN, on purpose.

| Go to | Link |
|---|---|
| Score map and proofs | [`otari` README](https://github.com/AndriyAmaro/verdict-desk-otari/tree/otari#score-map) |
| One cycle, call by call | [`web/cycle.html`](https://github.com/AndriyAmaro/verdict-desk-otari/blob/otari/web/cycle.html) |
| Our feedback to Otari | [`docs/FEEDBACK_LOG.md`](https://github.com/AndriyAmaro/verdict-desk-otari/blob/otari/docs/FEEDBACK_LOG.md) |

This `main` branch keeps the earlier snapshot of the project (the BLI Legal Tech Hackathon 2 build) for history, plus the workflow `otari-cycle`, which the scheduler needs here and which records new dossiers on the `otari` branch. MIT, see [`LICENSE`](LICENSE).
