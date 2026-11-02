#!/usr/bin/env python3
"""A light runner of regular decision cycles, to build the history of the Otari era. No model of its own: it runs the app.

Every three hours, one cycle: `decide-v6` on one symbol (BTCUSDT, ETHUSDT, SOLUSDT in turn), then verify, replay, an OpenTimestamps stamp of
the new head, a refresh of the README blocks, a scan for secrets, a commit and a push of the new dossier to `origin otari`. It keeps inside
the budgets the dashboard enforces (USD 0.10 a day per end user, USD 1.50 a day for the organization) with a margin of its own, counts what
it spends from the receipts, never spends more than USD 2.00 in all, and stops by itself (a line in the log says why) when:

  - a cycle fails twice in a row;
  - the key file is gone (a recycled container), which is reported as a stop, not as an error;
  - the working tree is not clean, or the branch is not `otari`;
  - a secret shows up in a changed file;
  - the total cap is reached.

    python3 tools/otari_runner.py --dry-run    # what it would do, nothing executed
    python3 tools/otari_runner.py --once       # one tick now, ignoring the schedule
    python3 tools/otari_runner.py              # the loop, in the foreground (run it with nohup or tmux)
    python3 tools/otari_runner.py --status     # state and the reason it stopped, if it did

The key is read from /root/.config/otari/key and handed only to the child process of the cycle; it is never logged and never written.
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KEY_FILE = Path("/root/.config/otari/key")
INTERVAL_S = 3 * 3600
RETRY_S = 30 * 60  # after one failure the next try comes sooner; a second failure in a row stops the runner
MAX_CYCLES_PER_DAY = 8
TOTAL_CAP_USD = Decimal("2.00")
ORG_DAY_CAP_USD = Decimal("1.20")  # of the dashboard's 1.50 a day
USER_DAY_BUDGET_USD = Decimal("0.10")  # the dashboard's budget per end user per day
DEFAULT_USER_CYCLE_USD = Decimal("0.0125")  # until a cycle of its own has been seen
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
MAX_FAILURES = 2
AUTHOR = ("AndriyAmaro", "andriytattoo1992@gmail.com")
# Built in two pieces so that this very file never contains the prefix: the scan below is a git grep over the whole tree.
KEY_PREFIX = "otk" + "_"
FAILED_CYCLE_ESTIMATE_USD = Decimal("0.08")  # a cycle that failed before any dossier says what it spent: counted at its two caps together (0.02 core + 0.06 tools)
PATHS = ["ledger/ledger.jsonl", "ledger/head.json", "ledger/head-dossier.json", "ledger/timestamps", "README.md"]


class Stop(Exception):
    """The runner must not go on: `reason` goes to the log and to the state."""


class Failure(Exception):
    """One cycle failed; the tree is restored and the failure counted. `entry` is what the cycle spent, when its dossier was already written."""

    def __init__(self, message: str, entry: dict | None = None):
        super().__init__(message)
        self.entry = entry


class Runner:
    def __init__(self, root: Path = ROOT, key_file: Path = KEY_FILE, run=subprocess.run, now=None, sleep=time.sleep):
        self.root, self.key_file, self.run, self.sleep = Path(root), Path(key_file), run, sleep
        self.now = now or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.logs = self.root / "logs"
        self.state_path, self.log_path = self.logs / "otari-runner.state.json", self.logs / "otari-runner.log"
        self._secret = ""

    # ------------------------------------------------------------------ state and log
    def load(self) -> dict:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state.setdefault("cycles", 0)
        state.setdefault("failures", 0)
        state.setdefault("total_usd", "0")
        state.setdefault("history", [])
        state.setdefault("next_run", None)
        state.setdefault("stopped", None)
        return state

    def save(self, state: dict) -> None:
        self.logs.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")

    def log(self, message: str) -> None:
        if self._secret:
            message = message.replace(self._secret, "***")
        line = f"{self.now().strftime('%Y-%m-%dT%H:%M:%SZ')} {message}"
        self.logs.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(line, flush=True)

    # ------------------------------------------------------------------ plumbing
    def sh(self, args, env=None, timeout=120):
        """Runs a command; one that outlives its timeout comes back as exit 124 with the reason, never as an exception."""
        try:
            return self.run(args, cwd=str(self.root), capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 124, "", f"timed out after {timeout} s")

    def read_key(self) -> str:
        try:
            key = self.key_file.read_text(encoding="utf-8").strip()
        except OSError:
            raise Stop(f"the key file {self.key_file} is missing or unreadable (a recycled container?): nothing was run") from None
        if not key or not key.isprintable() or " " in key:
            raise Stop(f"the key file {self.key_file} does not hold a usable key: nothing was run")
        self._secret = key
        return key

    def ledger(self) -> list[dict]:
        path = self.root / "ledger" / "ledger.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []

    # ------------------------------------------------------------------ budgets, from the receipts of the cycles already run
    @staticmethod
    def today(state: dict, day: str) -> tuple[int, Decimal, dict]:
        entries = [h for h in state["history"] if h["day"] == day]
        users: dict[str, Decimal] = {}
        for h in entries:
            for user, cost in h["users"].items():
                users[user] = users.get(user, Decimal(0)) + Decimal(cost)
        return len(entries), sum((Decimal(h["usd"]) for h in entries), Decimal(0)), users

    @staticmethod
    def per_cycle_users(state: dict) -> dict:
        worst: dict[str, Decimal] = {}
        for h in state["history"]:
            for user, cost in h["users"].items():
                worst[user] = max(worst.get(user, Decimal(0)), Decimal(cost))
        return worst

    def budget_gate(self, state: dict, day: str) -> str | None:
        """Why no cycle may start now (the runner then waits for the next UTC day), or None."""
        if Decimal(state["total_usd"]) >= TOTAL_CAP_USD:
            raise Stop(f"the total cap of USD {TOTAL_CAP_USD} is reached (spent {state['total_usd']})")
        count, usd, users = self.today(state, day)
        if count >= MAX_CYCLES_PER_DAY:
            return f"{count} cycles already today (the most is {MAX_CYCLES_PER_DAY})"
        if usd + DEFAULT_USER_CYCLE_USD * 3 > ORG_DAY_CAP_USD:
            return f"USD {usd} spent today, the next cycle could pass the organization margin of USD {ORG_DAY_CAP_USD} a day"
        worst = self.per_cycle_users(state)
        for user, spent in users.items():
            if spent + worst.get(user, DEFAULT_USER_CYCLE_USD) > USER_DAY_BUDGET_USD:
                return f"end user {user} spent USD {spent} today, another cycle could pass its USD {USER_DAY_BUDGET_USD} budget"
        return None

    # ------------------------------------------------------------------ one cycle
    def preflight(self) -> str:
        key = self.read_key()
        branch = self.sh(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if branch.stdout.strip() != "otari":
            raise Stop(f"the branch is {branch.stdout.strip() or 'unknown'}, not otari: nothing was run")
        status = self.sh(["git", "status", "--porcelain"])
        if status.returncode != 0 or status.stdout.strip():
            raise Stop("the working tree is not clean (or git did not answer): nothing was run (commit or restore what is there first)")
        # Other work may have pushed to otari since the last cycle: start from the published tip, never on top of a stale one.
        fetch = self.sh(["git", "fetch", "-q", "origin", "otari"], timeout=180)
        if fetch.returncode != 0:
            raise Failure(f"git fetch failed: {(fetch.stderr or '')[-160:].strip()}")
        ff = self.sh(["git", "merge", "--ff-only", "-q", "origin/otari"], timeout=60)
        if ff.returncode != 0:
            raise Stop("the local otari has commits that origin/otari does not (a push failed earlier?): nothing was run, push or reset them first")
        return key

    @staticmethod
    def ots() -> str | None:
        """The OpenTimestamps client, also where pip --user puts it (a background shell may not have that on its PATH)."""
        return shutil.which("ots") or (str(Path.home() / ".local" / "bin" / "ots") if (Path.home() / ".local" / "bin" / "ots").exists() else None)

    def restore(self) -> None:
        """Throws away what a failed cycle left: the appended dossier, its head files, a new stamp, a rewritten README."""
        self.sh(["git", "checkout", "--", "ledger", "README.md"])
        self.sh(["git", "clean", "-fq", "--", "ledger/timestamps"])

    def cycle(self, state: dict, key: str) -> dict:
        symbol = SYMBOLS[state["cycles"] % len(SYMBOLS)]
        before = len(self.ledger())
        env = {**os.environ, "OTARI_API_KEY": key, "VERDICT_DESK_APPEND_PUBLIC": "1"}
        out = self.sh([sys.executable, "-m", "verdict_desk", "decide-v6", symbol, "--config", "config/voices.json"], env=env, timeout=900)
        if out.returncode != 0:
            raise Failure(f"decide-v6 {symbol} {'timed out' if out.returncode == 124 else 'exited ' + str(out.returncode)}: {(out.stderr or out.stdout or '')[-200:].strip()}")
        ledger = self.ledger()
        if len(ledger) != before + 1:
            raise Failure(f"the ledger went from {before} to {len(ledger)} dossiers, not by one")
        dossier = ledger[-1]
        entry = self.account(dossier, symbol)  # what this cycle spent, known from here on even if a later step fails
        try:
            verify = self.sh([sys.executable, "-m", "verdict_desk", "verify"])
            if verify.returncode != 0 or "Chain intact" not in verify.stdout:
                raise Failure("verify did not report an intact chain")
            replay = self.sh([sys.executable, "-m", "verdict_desk", "replay"], timeout=600)
            bad = [line for line in replay.stdout.splitlines() if "MATCHES" not in line and line.strip() and ("differ" in line.lower() or "mismatch" in line.lower())]
            if replay.returncode != 0 or bad:
                raise Failure(f"replay reported a divergence: {(bad or ['exit ' + str(replay.returncode)])[0][:160]}")
            head = self.root / "ledger" / "head.json"
            if head.exists():
                stamp = self.root / "ledger" / "timestamps" / f"{dossier['seq']}.json"
                stamp.parent.mkdir(parents=True, exist_ok=True)
                stamp.write_text(head.read_text(encoding="utf-8"), encoding="utf-8")
                ots = self.ots()
                if ots:
                    done = self.sh([ots, "stamp", f"ledger/timestamps/{dossier['seq']}.json"])
                    if done.returncode != 0:  # a stamp is a witness, not the dossier: its absence is logged, not fatal
                        self.log(f"the OpenTimestamps stamp of {dossier['seq']} failed (kept going; the dossier is intact): {(done.stderr or '')[-120:].strip()}")
                else:
                    self.log("the ots client is not installed: the head of this dossier is not stamped")
            refresh = self.sh([sys.executable, "scripts/refresh_otari_readme.py"])
            if refresh.returncode != 0:
                raise Failure("the README refresh failed")
            self.scan_for_secrets(key)
            self.sh(["git", "add", "--"] + PATHS)
            message = f"otari: era dossier {dossier['seq']} ({symbol}), recorded by the runner\n"  # a data commit of the app, not of a model: no model trailer
            commit = self.sh(["git", "-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}", "commit", "-q", "-m", message])
            if commit.returncode != 0:
                raise Failure(f"git commit failed: {(commit.stderr or commit.stdout or '')[-160:].strip()}")
            push = self.sh(["git", "push", "-q", "origin", "otari"], timeout=180)
            if push.returncode != 0:
                raise Failure(f"git push failed (the commit stays local): {(push.stderr or '')[-160:].strip()}")
            return entry
        except (Failure, Stop) as err:
            err.entry = entry  # a cycle that is discarded after its dossier was written has still spent that money
            raise

    def scan_for_secrets(self, key: str) -> None:
        changed = [p for p in self.sh(["git", "diff", "--name-only"]).stdout.split() if p] + [p for p in self.sh(["git", "ls-files", "--others", "--exclude-standard"]).stdout.split() if p]
        for rel in changed:
            path = self.root / rel
            if path.is_file() and key in path.read_text(encoding="utf-8", errors="replace"):
                self.restore()
                raise Stop(f"the key appeared in {rel}: the cycle was discarded and nothing was committed")
        if self.sh(["git", "grep", "-n", KEY_PREFIX]).returncode == 0:
            self.restore()
            raise Stop("a string that looks like a gateway key is in the tree: the cycle was discarded")

    @staticmethod
    def account(dossier: dict, symbol: str) -> dict:
        from verdict_desk import otari_report
        users: dict[str, Decimal] = {}
        for call in otari_report.calls_of(dossier):
            r = call["receipt"]
            if r.get("end_user") and r.get("cost_usd") is not None:
                users[r["end_user"]] = users.get(r["end_user"], Decimal(0)) + Decimal(str(r["cost_usd"]))
        cycle = ((dossier.get("llm") or {}).get("otari") or {}).get("cycle") or {}
        usd = Decimal(str(cycle.get("total_spent_usd") or sum(users.values())))
        return {"seq": dossier["seq"], "symbol": symbol, "usd": str(usd), "users": {u: str(c) for u, c in users.items()}}

    # ------------------------------------------------------------------ the loop
    def tick(self, force: bool = False) -> str:
        """One decision: returns what happened (also logged). Never raises for the reasons it stops on."""
        state = self.load()
        if state["stopped"]:
            return f"stopped: {state['stopped']}"
        now = self.now()
        if not force and state["next_run"] and now < datetime.datetime.fromisoformat(state["next_run"]):
            return "waiting"
        day = now.strftime("%Y-%m-%d")
        try:
            key = self.preflight()
            wait = self.budget_gate(state, day)
            if wait:
                tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
                state["next_run"] = tomorrow.isoformat()
                self.save(state)
                self.log(f"deferred to {tomorrow.strftime('%Y-%m-%d %H:%M')} UTC: {wait}")
                return "deferred"
            try:
                entry = self.cycle(state, key)
            except Failure as err:
                self.restore()
                state["failures"] += 1
                spent = err.entry or {"seq": None, "symbol": SYMBOLS[state["cycles"] % len(SYMBOLS)], "usd": str(FAILED_CYCLE_ESTIMATE_USD), "users": {}, "estimated": True}
                state["history"].append({**spent, "failed": str(err)[:160], "day": day, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
                state["total_usd"] = str(Decimal(state["total_usd"]) + Decimal(spent["usd"]))  # a failed cycle spent money too
                self.log(f"cycle failed ({state['failures']} in a row): {err}; counted USD {spent['usd']}{' (an estimate: no dossier says)' if spent.get('estimated') else ''}, total USD {state['total_usd']}")
                if state["failures"] >= MAX_FAILURES:
                    state["stopped"] = f"{MAX_FAILURES} cycles failed in a row; the last: {err}"
                    self.log(f"STOPPED: {state['stopped']}")
                else:
                    state["next_run"] = (now + datetime.timedelta(seconds=RETRY_S)).isoformat()
                self.save(state)
                return "failed" if not state["stopped"] else f"stopped: {state['stopped']}"
            entry["day"] = day
            entry["at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            state["history"].append(entry)
            state["cycles"] += 1
            state["failures"] = 0
            state["total_usd"] = str(Decimal(state["total_usd"]) + Decimal(entry["usd"]))
            state["next_run"] = (now + datetime.timedelta(seconds=INTERVAL_S)).isoformat()
            self.save(state)
            self.log(f"cycle ok: dossier {entry['seq']} {entry['symbol']}, USD {entry['usd']}, total USD {state['total_usd']}; next at {state['next_run']}")
            return "ok"
        except Stop as stop:
            spent = getattr(stop, "entry", None)
            if spent:
                state["history"].append({**spent, "failed": str(stop)[:160], "day": day, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
                state["total_usd"] = str(Decimal(state["total_usd"]) + Decimal(spent["usd"]))
            state["stopped"] = str(stop)
            self.save(state)
            self.log(f"STOPPED: {stop}")
            return f"stopped: {stop}"

    def loop(self) -> int:
        while True:
            result = self.tick()
            if result.startswith("stopped"):
                return 3
            state = self.load()
            wake = datetime.datetime.fromisoformat(state["next_run"]) if state["next_run"] else self.now()
            self.sleep(max(60.0, (wake - self.now()).total_seconds()))

    def dry_run(self) -> str:
        state = self.load()
        day = self.now().strftime("%Y-%m-%d")
        lines = [f"next symbol: {SYMBOLS[state['cycles'] % len(SYMBOLS)]}", f"cycles run: {state['cycles']}, spent USD {state['total_usd']} of {TOTAL_CAP_USD}", f"next run: {state['next_run'] or 'now'}",
                 f"stopped: {state['stopped'] or 'no'}"]
        try:
            self.read_key()
            lines.append("key file: present (not read further, never printed)")
        except Stop as stop:
            lines.append(f"key file: PROBLEM, {stop}")
        try:
            lines.append("budget gate: " + (self.budget_gate(state, day) or "open"))
        except Stop as stop:
            lines.append(f"budget gate: STOP, {stop}")
        lines.append("would run: decide-v6, verify, replay, ots stamp, README refresh, secret scan, commit, push origin otari")
        return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--once", action="store_true", help="one tick now, ignoring the schedule")
    parser.add_argument("--dry-run", action="store_true", help="print what it would do and run nothing")
    parser.add_argument("--status", action="store_true", help="print the state, and why it stopped if it did")
    opts = parser.parse_args(argv)
    runner = Runner()
    if opts.dry_run:
        print(runner.dry_run())
        return 0
    if opts.status:
        state = runner.load()
        print(json.dumps({k: state[k] for k in ("cycles", "failures", "total_usd", "next_run", "stopped")}, indent=1))
        return 0
    if opts.once:
        result = runner.tick(force=True)
        return 3 if result.startswith("stopped") else (1 if result == "failed" else 0)
    return runner.loop()


if __name__ == "__main__":
    sys.exit(main())
