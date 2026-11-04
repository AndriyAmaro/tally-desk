"""F4.1: the failover canary and the budget headroom, run inside the cycle and sealed in the dossier; the judge route they gate. No network."""

import json
import os
import tempfile
import unittest
import urllib.request
from decimal import Decimal
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from tests.test_otari import BODY, HEADERS, FakeResponse, plain_config, served_spelling
from verdict_desk import dossier, llm, otari_canary

KEY = "unit-test-secret-0123456789"
ENV = {"OTARI_API_KEY": KEY, "OTARI_SEND_USER": "", "OTARI_BASE_URL": "", "OTARI_USE_POLICIES": "", "OTARI_CYCLE_CAP_USD": "",
       "OTARI_POLICY_JUDGE": "", "OTARI_WEB_SEARCH": "", "OTARI_CODE_EXECUTION": "", "OTARI_MCP": "", "OTARI_MIN_INTERVAL": "", "OTARI_TOOL_CAP_USD": "",
       "OTARI_CANARY": "", "OTARI_HEADROOM": "", "OTARI_JUDGE_ROUTE": ""}
THRESHOLDS = {"hard_model": "deepseek/deepseek-v4-pro", "margin_below": 1.0, "claims_at_least": 8}


def headroom(used="0.007204", limit="0.10", top=0.8, judge="vd-judge", **extra):
    h = {"limit_usd": limit, "max_fraction": top, "judge_user": judge, "users": {judge: {"found": True, "used_usd": used, "blocked": False}}}
    h["users"][judge].update(extra)
    return h


class Env(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.dict(os.environ, ENV), mock.patch.object(llm, "MIN_INTERVAL", 0.0), mock.patch.object(llm, "_next_start", 0.0), mock.patch.object(llm, "_pause"), plain_config()):
            patch.start()
            self.addCleanup(patch.stop)
        llm.reset_cycle()


class HeadroomTest(Env):
    def test_room_means_under_the_fraction_of_the_day_limit_and_the_answer_comes_from_the_sealed_numbers(self):
        self.assertTrue(llm.headroom_ok(headroom("0.0799"))[0])
        self.assertFalse(llm.headroom_ok(headroom("0.08"))[0], "exactly 80% is no room")
        ok, why = llm.headroom_ok(headroom("0.09"))
        self.assertFalse(ok)
        self.assertIn("90%", why)
        self.assertIn("standard route is kept", why)

    def test_unreadable_blocked_or_unlimited_states_keep_the_standard_route(self):
        for h in (None, {}, {"limit_usd": "0.10", "max_fraction": 0.8, "judge_user": "vd-judge", "users": {"vd-judge": {"found": False}}},
                  headroom(blocked=True), headroom(limit="0"), headroom(used="x")):
            ok, why = llm.headroom_ok(h)
            self.assertFalse(ok, h)
            self.assertIn("standard route", why)

    def test_the_state_is_read_for_every_end_user_adds_the_reserve_and_never_trusts_a_missing_user(self):
        cfg = {**llm.otari_config(), "end_user_prefix": "vd-", "end_user_limit_usd": "0.10", "headroom_max_fraction": 0.8, "end_user_budget": "bud", "canary_user": "vd-canary", "send_user": True}
        rows = {"vd-judge": {"external_id": "vd-judge", "budget_id": "bud", "blocked": False, "spend": 0.02, "reserved": 0.01, "next_budget_reset_at": "2026-11-02T00:00:00+00:00"}, "vd-bull": {"external_id": "vd-bull", "budget_id": "bud", "spend": 0.001, "reserved": 0.0}}
        h = llm.read_headroom(info=lambda n: rows.get(n), cfg=cfg)
        self.assertEqual(h["schema"], "otari-headroom/1")
        self.assertEqual(set(h["users"]), {f"vd-{r}" for r in cfg["roles"]} | {"vd-canary"})
        self.assertEqual(h["users"]["vd-judge"]["used_usd"], "0.030000")
        self.assertEqual(h["users"]["vd-judge"]["fraction"], "0.3000")
        self.assertEqual(h["users"]["vd-bear"], {"found": False})
        self.assertEqual((h["judge_ok"], h["judge_user"], h["limit_usd"]), (True, "vd-judge", "0.10"))
        self.assertIn("service key cannot read the budget", h["limit_source"])
        rows["vd-judge"]["spend"] = 0.085
        self.assertFalse(llm.read_headroom(info=lambda n: rows.get(n), cfg=cfg)["judge_ok"])
        self.assertFalse(llm.read_headroom(info=lambda n: None, cfg=cfg)["judge_ok"], "no answer from Otari: the standard route")


class GatedRouteTest(Env):
    def test_a_hard_case_with_room_and_a_working_backup_goes_to_the_larger_model(self):
        r = llm.route_decision(1, 1, 2.0, 1.5, THRESHOLDS, "vd-judge", headroom=headroom(), canary={"passed": True})
        self.assertEqual((r["hard"], r["model"], r["rule"], r["wanted_hard"]), (True, "deepseek/deepseek-v4-pro", "judge-difficulty/v2", True))
        self.assertTrue(r["gates"]["headroom"]["ok"] and r["gates"]["canary"]["ok"])

    def test_no_room_keeps_a_hard_case_on_the_standard_route_and_says_why(self):
        r = llm.route_decision(1, 1, 2.0, 1.5, THRESHOLDS, "vd-judge", headroom=headroom("0.09"), canary={"passed": True})
        self.assertEqual((r["hard"], r["model"], r["wanted_hard"]), (False, "vd-judge", True))
        self.assertIn("kept on the standard route", r["reason"])
        self.assertIn("tight margin", r["reason"])
        self.assertFalse(r["gates"]["headroom"]["ok"])

    def test_a_failed_canary_keeps_a_hard_case_on_the_standard_route(self):
        r = llm.route_decision(1, 1, 2.0, 1.5, THRESHOLDS, "vd-judge", headroom=headroom(), canary={"passed": False})
        self.assertEqual((r["hard"], r["model"]), (False, "vd-judge"))
        self.assertIn("failover canary did not show", r["reason"])

    def test_the_gates_never_make_a_standard_case_hard_and_without_them_the_rule_is_the_old_one(self):
        wide = llm.route_decision(1, 1, 4.0, 1.0, THRESHOLDS, "vd-judge", headroom=headroom(), canary={"passed": True})
        self.assertEqual((wide["hard"], wide["wanted_hard"]), (False, False))
        old = llm.route_decision(1, 1, 2.0, 1.5, THRESHOLDS, "vd-judge")
        self.assertEqual((old["rule"], old["hard"]), ("judge-difficulty/v1", True))
        self.assertNotIn("gates", old)


class CanaryTest(Env):
    def receipt(self, **kw):
        return {"schema": "otari-receipt/1", "role": "judge", "request_id": "c1", "request_id_source": "otari", "sent_model": "vd-judge-drill", "attempt_count": 2, "fallback": True, **kw}

    def test_the_prompt_is_longer_than_the_main_models_context_and_cheap_on_the_backup(self):
        text = otari_canary.prompt()
        self.assertGreater(len(text) / 3.0, 32_768, "at three characters a token or fewer it overflows 32k tokens")
        self.assertLessEqual(Decimal(str(round(otari_canary.UNITS * otari_canary.INPUT_USD_PER_MILLION / 1e6, 6))), Decimal(str(otari_canary.MAX_COST_USD)))
        self.assertIn("canary", otari_canary.SYSTEM)
        self.assertNotIn(KEY, text + otari_canary.SYSTEM)

    def test_it_asks_the_drill_policy_with_its_own_end_user_and_passes_when_the_backup_answered(self):
        seen = {}

        def ask(system, user, **kw):
            seen.update(kw)
            llm._served.receipt = self.receipt(served_by="deepinfra:deepseek-ai/DeepSeek-V4-Flash-0731")
            return "ok"
        out = otari_canary.run(ask=ask)
        self.assertEqual((seen["model"], seen["end_user"], seen["max_tokens"], seen["role"]), ("vd-judge-drill", "vd-canary", 8, "judge"))
        self.assertTrue(out["passed"])
        self.assertIn("canary", out["label"])
        self.assertIn("backup route works", out["reading"])
        self.assertEqual(out["prompt"]["chars"], len(otari_canary.prompt()))

    def test_it_fails_without_a_fallback_after_two_attempts_or_with_a_refusal_or_an_error(self):
        for rc in (self.receipt(fallback=False, attempt_count=1), self.receipt(attempt_count=1), self.receipt(refusal={"status": 403, "code": "budget_exceeded"}), None):
            def ask(system, user, **kw):
                llm._served.receipt = rc
                return "ok"
            out = otari_canary.run(ask=ask)
            self.assertFalse(out["passed"], rc)
            self.assertIn("standard route", out["reading"])

        def boom(system, user, **kw):
            llm._served.receipt = None
            raise llm.LLMError("no answer")
        out = otari_canary.run(ask=boom)
        self.assertEqual((out["passed"], out["error"]), (False, "no answer"))


class SealedInTheDossierTest(Env):
    """A real-path cycle: the canary and the headroom are sealed before the judge, the router consults them, the replay recomputes it all offline."""

    def build(self, pkt, used=0.001, fallback=True, extra_env=None):
        sent = []
        cfg = json.loads(llm.OTARI_CONFIG.read_text())
        cfg.update(judge_route=THRESHOLDS, use_policies=True, canary=True, canary_user="vd-canary", end_user_limit_usd="0.10", headroom_max_fraction=0.8)
        path = Path(tempfile.mkdtemp()) / "otari.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")

        def urlopen(request, timeout=None):
            if request.data is None:  # GET /end-users
                name = request.full_url.rsplit("external_id=", 1)[-1]
                return FakeResponse([{"external_id": name, "budget_id": "bud", "blocked": False, "spend": used, "reserved": 0.0}], {})
            payload = json.loads(request.data.decode())
            sent.append(payload["model"])
            system = payload["messages"][0]["content"]
            headers = {**HEADERS, "Otari-Model": served_spelling(payload["model"])}
            if "failover canary" in system:
                headers = {**headers, "Otari-Attempt-Count": "2" if fallback else "1", "Otari-Fallback": "true" if fallback else "false"}
                content = "ok"
            else:
                content = json.dumps([{"claim": "Bitcoin rally hits record", "cites": ["E4"]}]) if "bull analyst" in system else (
                    '{"lean": "bull", "conviction": "low", "relied_on": ["B1"], "reason": "rally"}' if "judge in a market debate" in system else "[]")
            return FakeResponse({**BODY, "choices": [{"message": {"content": content}, "finish_reason": "stop"}]}, headers)
        with mock.patch.object(llm, "OTARI_CONFIG", path), mock.patch.dict(os.environ, extra_env or {}), mock.patch.object(urllib.request, "urlopen", urlopen):
            d = dossier.build(pkt, dossier.GENESIS, 1, rules_version="v6")
        return d, sent

    def tight(self):
        return packet(0.001, 0.0, ("Bitcoin rally hits record",))

    def test_the_canary_and_the_headroom_are_sealed_and_a_hard_case_with_both_goes_to_the_larger_model(self):
        d, sent = self.build(self.tight())
        otari = d["llm"]["otari"]
        self.assertTrue(otari["canary"]["passed"])
        self.assertIn("canary", otari["canary"]["label"])
        self.assertEqual(otari["canary"]["receipt"]["attempt_count"], 2)
        self.assertTrue(otari["canary"]["receipt"]["fallback"])
        self.assertEqual(otari["canary"]["end_user"], "vd-canary")
        self.assertTrue(otari["headroom"]["judge_ok"])
        route = d["llm"]["judge"]["route_rule"]
        self.assertEqual((route["hard"], route["rule"]), (True, "judge-difficulty/v2"))
        self.assertEqual(d["llm"]["judge"]["receipt"]["sent_model"], "deepseek/deepseek-v4-pro")
        self.assertLess(sent.index("vd-judge-drill"), sent.index("deepseek/deepseek-v4-pro"), "the canary runs before the judge")

    def test_a_spent_judge_end_user_keeps_the_judge_on_the_standard_route_with_the_reason_sealed(self):
        d, sent = self.build(self.tight(), used=0.09)
        route = d["llm"]["judge"]["route_rule"]
        self.assertEqual((route["hard"], route["wanted_hard"]), (False, True))
        self.assertIn("kept on the standard route", route["reason"])
        self.assertEqual(d["llm"]["judge"]["receipt"]["sent_model"], "vd-judge")
        self.assertNotIn("deepseek/deepseek-v4-pro", sent)
        self.assertEqual(d["llm"]["judge"]["receipt"]["route_rule"]["hard"], False)

    def test_a_canary_that_did_not_fall_back_fails_safe_and_is_recorded_as_failed(self):
        d, sent = self.build(self.tight(), fallback=False)
        self.assertFalse(d["llm"]["otari"]["canary"]["passed"])
        self.assertFalse(d["llm"]["judge"]["route_rule"]["hard"])
        self.assertIn("did not show the backup route working", d["llm"]["judge"]["route_rule"]["reason"])
        self.assertNotIn("deepseek/deepseek-v4-pro", sent)

    def test_the_switches_turn_the_canary_and_the_headroom_off_and_an_injected_ask_never_runs_them(self):
        d, sent = self.build(self.tight(), extra_env={"OTARI_CANARY": "0", "OTARI_HEADROOM": "0"})
        self.assertNotIn("canary", d["llm"]["otari"])
        self.assertNotIn("headroom", d["llm"]["otari"])
        self.assertEqual(d["llm"]["judge"]["route_rule"]["rule"], "judge-difficulty/v1")
        self.assertNotIn("vd-judge-drill", sent)
        fake = dossier.build(self.tight(), dossier.GENESIS, 1, ask=lambda s, u: "[]", rules_version="v6")
        self.assertNotIn("canary", (fake["llm"].get("otari") or {}))

    def test_the_replay_reads_the_sealed_numbers_and_catches_an_edited_headroom_or_canary(self):
        d, _ = self.build(self.tight())
        with mock.patch.object(urllib.request, "urlopen", mock.Mock(side_effect=AssertionError("the replay used the network"))):
            r = dossier.replay(d, "auto")
        self.assertTrue(r["matches"] and r["route_rule_ok"] and r["canary_ok"], r)
        spent = json.loads(json.dumps(d))
        head = spent["llm"]["otari"]["headroom"]
        head["users"][head["judge_user"]]["used_usd"] = "0.095"
        self.assertFalse(dossier.replay(spent, "auto")["route_rule_ok"], "a hard route sealed on room that the sealed numbers do not show")
        faked = json.loads(json.dumps(d))
        faked["llm"]["otari"]["canary"]["receipt"]["fallback"] = False
        self.assertFalse(dossier.replay(faked, "auto")["canary_ok"])
        flipped = json.loads(json.dumps(d))
        flipped["llm"]["otari"]["canary"]["passed"] = False
        r2 = dossier.replay(flipped, "auto")
        self.assertFalse(r2["canary_ok"] and r2["route_rule_ok"])

    def test_the_cycle_cost_counts_the_canary(self):
        d, _ = self.build(self.tight())
        cycle = d["llm"]["otari"]["cycle"]
        calls = d["llm"]["otari"]["canary"]["receipt"]["cost_usd"]
        self.assertGreaterEqual(Decimal(cycle["total_spent_usd"]), Decimal(calls))


from tests import chain_guard  # noqa: E402,F401  (no test may send a transaction to Sepolia)

if __name__ == "__main__":
    unittest.main()
