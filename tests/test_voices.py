"""The user's configuration: which voices the agent reads and the risk limit it sizes under.

The bounty's spotlight asks for voices CHOSEN BY THE USER and a position under limits DEFINED BY
THE USER. That is only believable if the choice is a file the user edits, an invalid choice is
refused loudly, and the choice is fingerprinted into every dossier so changing it is a recorded
event. These tests pin each of those.
"""

import argparse
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_dossier import packet
from verdict_desk import __main__ as cli
from verdict_desk import dossier, evidence, narrative, voices

GOOD = {
    "version": 1,
    "voices": [
        {"name": "Desk One", "kind": "rss", "url": "https://example.org/feed.xml"},
        {"name": "Desk Two", "kind": "telegram", "url": "https://t.me/s/example_channel"},
    ],
    "risk": {"max_risk_pct": 2.0},
}


class ValidationTest(unittest.TestCase):
    def refused(self, mutate, expect):
        cfg = copy.deepcopy(GOOD)
        mutate(cfg)
        with self.assertRaises(voices.ConfigError) as caught:
            voices.validate(cfg)
        self.assertIn(expect, str(caught.exception))

    def test_a_good_config_passes(self):
        voices.validate(GOOD)

    def test_more_than_twenty_voices_is_refused(self):
        self.refused(lambda c: c.update(voices=[{"name": f"V{i}", "kind": "rss", "url": "https://e.org/f"} for i in range(21)]), "at most 20")

    def test_a_repeated_name_is_refused_because_a_voice_counts_once(self):
        self.refused(lambda c: c["voices"].append({"name": "desk one", "kind": "rss", "url": "https://e.org/f"}), "repeats")

    def test_plain_http_and_credentials_in_the_url_are_refused(self):
        self.refused(lambda c: c["voices"][0].update(url="http://example.org/feed"), "https URL")
        self.refused(lambda c: c["voices"][0].update(url="https://user:pw@example.org/feed"), "without credentials")

    def test_a_telegram_voice_must_be_a_channel_preview(self):
        self.refused(lambda c: c["voices"][1].update(url="https://t.me/example_channel"), "https://t.me/s/<channel>")
        self.refused(lambda c: c["voices"][1].update(url="https://example.org/s/example_channel"), "https://t.me/s/<channel>")

    def test_an_unknown_kind_is_refused(self):
        self.refused(lambda c: c["voices"][0].update(kind="x"), "kind must be one of")

    def test_the_risk_limit_cannot_pass_the_system_ceiling_or_be_zero(self):
        self.refused(lambda c: c.update(risk={"max_risk_pct": 30}), "max_risk_pct")
        self.refused(lambda c: c.update(risk={"max_risk_pct": 0}), "max_risk_pct")
        self.refused(lambda c: c.update(risk={"max_risk_pct": "1"}), "must be a number")

    def test_an_unsupported_version_is_refused(self):
        self.refused(lambda c: c.update(version=2), "unsupported config version")


class FingerprintTest(unittest.TestCase):
    def test_the_fingerprint_ignores_key_order_but_not_content(self):
        reordered = json.loads(json.dumps(GOOD, sort_keys=True))
        self.assertEqual(voices.fingerprint(GOOD), voices.fingerprint(reordered))
        changed = copy.deepcopy(GOOD)
        changed["risk"]["max_risk_pct"] = 2.5
        self.assertNotEqual(voices.fingerprint(GOOD), voices.fingerprint(changed))
        swapped = copy.deepcopy(GOOD)
        swapped["voices"][0]["url"] = "https://example.org/other.xml"
        self.assertNotEqual(voices.fingerprint(GOOD), voices.fingerprint(swapped))


class ShippedConfigTest(unittest.TestCase):
    """The file the hourly bot actually reads."""

    def test_the_shipped_config_is_valid_and_mixes_newsrooms_with_telegram_voices(self):
        loaded = voices.load()
        record = loaded["record"]
        self.assertLessEqual(record["voices"], voices.MAX_VOICES)
        self.assertGreaterEqual(record["kinds"].get("telegram", 0), 6)
        self.assertGreaterEqual(record["kinds"].get("rss", 0), 3)
        self.assertEqual(len(loaded["feeds"]), record["voices"])

    def test_the_shipped_risk_limit_is_the_documented_default(self):
        self.assertEqual(voices.load()["risk_pct"], 1.0)


class GapsTest(unittest.TestCase):
    def test_a_voice_that_fails_or_says_nothing_is_a_recorded_gap(self):
        def fetch(url, name, timeout=20):
            if name == "down":
                raise OSError("connection refused")
            if name == "quiet":
                return []
            return [{"name": name, "title": f"{name} says Bitcoin rallies", "link": url}]

        feeds = {"loud": "https://e.org/a", "down": "https://e.org/b", "quiet": "https://e.org/c"}
        with mock.patch.object(narrative, "fetch_feed", fetch):
            found, gaps = narrative.collect_with_gaps(feeds)
        self.assertEqual([v["name"] for v in found], ["loud"])
        self.assertEqual(set(gaps), {"down", "quiet"})
        self.assertIn("connection refused", gaps["down"])
        self.assertEqual(gaps["quiet"], "no message about a tracked token")

    def test_collect_keeps_its_old_contract(self):
        with mock.patch.object(narrative, "fetch_feed", lambda url, name, timeout=20: [{"name": name, "title": "Bitcoin up", "link": url}]):
            self.assertEqual(len(narrative.collect({"a": "https://e.org/a"})), 1)


class TelegramRelevanceTest(unittest.TestCase):
    def test_only_messages_that_name_a_tracked_token_reach_the_model(self):
        from tests.test_telegram import message

        page = "<html>" + "".join([
            message("chan", 1, "2026-10-22T08:00:00+00:00", "134,028,134 USDC transferred from unknown wallet"),
            message("chan", 2, "2026-10-22T09:00:00+00:00", "Bitcoin falls under $80,000"),
            message("chan", 3, "2026-10-22T10:00:00+00:00", "#SOL breaks resistance"),
        ]) + "</html>"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return page.encode()

        with mock.patch.object(narrative.urllib.request, "urlopen", lambda request, timeout=0: Response()):
            items = narrative.fetch_feed("https://t.me/s/chan", "Desk")
        self.assertEqual([i["link"] for i in items], ["https://t.me/chan/3", "https://t.me/chan/2"])


class DecideWithConfigTest(unittest.TestCase):
    def run_decide(self, config_path, risk_pct=None):
        path = Path(tempfile.mkdtemp()) / "ledger" / "ledger.jsonl"
        opts = argparse.Namespace(symbols=["BTCUSDT"], interval="1h", no_news=True, feed=None, default_feeds=False,
                                  ledger=path, config=config_path, risk_pct=risk_pct)
        fake = lambda feeds, log=None: ([{"name": n, "title": f"{n}: Bitcoin surge", "link": u} for n, u in feeds.items()], {"Desk Two": "HTTPError: 503"})
        with mock.patch.object(evidence, "collect", lambda symbol, interval, with_news: {**packet(0.004), "symbol": symbol}), \
                mock.patch.object(narrative, "collect_with_gaps", fake), \
                mock.patch.object(cli.llm, "available", lambda: False), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli._decide_flow(opts, "v4")
        return code, path, err.getvalue()

    def write_config(self, cfg):
        path = Path(tempfile.mkdtemp()) / "voices.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def test_the_hash_and_the_gaps_of_the_config_are_frozen_in_the_dossier(self):
        config = self.write_config(GOOD)
        code, path, _ = self.run_decide(config)
        self.assertEqual(code, 0)
        record = dossier.read_ledger(path)[0]["evidence"]["narrative"]["config"]
        self.assertEqual(record["sha256"], voices.fingerprint(GOOD))
        self.assertEqual(record["voices"], 2)
        self.assertEqual(record["gaps"], {"Desk Two": "HTTPError: 503"})

    def test_the_collector_receives_the_sources_the_user_configured(self):
        seen = {}

        def spy(feeds, log=None):
            seen.update(feeds)
            return [], {}

        config = self.write_config(GOOD)
        path = Path(tempfile.mkdtemp()) / "ledger" / "ledger.jsonl"
        opts = argparse.Namespace(symbols=["BTCUSDT"], interval="1h", no_news=True, feed=None, default_feeds=False,
                                  ledger=path, config=config, risk_pct=None)
        with mock.patch.object(evidence, "collect", lambda symbol, interval, with_news: {**packet(0.004), "symbol": symbol}), \
                mock.patch.object(narrative, "collect_with_gaps", spy), \
                mock.patch.object(cli.llm, "available", lambda: False), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli._decide_flow(opts, "v4")
        self.assertEqual(set(seen), {v["name"] for v in GOOD["voices"]})

    def test_sources_over_the_limit_are_recorded_as_gaps_not_dropped_silently(self):
        feeds = {f"Voice {i}": f"https://example.org/{i}.xml" for i in range(narrative.MAX_VOICES + 2)}
        with mock.patch.object(narrative, "fetch_feed", lambda url, name: [{"name": name, "title": "Bitcoin surge", "link": url}]):
            got, gaps = narrative.collect_with_gaps(feeds)
        self.assertEqual(len(got), narrative.MAX_VOICES)
        self.assertEqual(set(gaps), {f"Voice {narrative.MAX_VOICES}", f"Voice {narrative.MAX_VOICES + 1}"})
        self.assertIn("over the limit", gaps[f"Voice {narrative.MAX_VOICES}"])

    def test_the_users_risk_limit_sizes_the_position_unless_the_command_line_asks_for_another(self):
        config = self.write_config(GOOD)
        _, path, _ = self.run_decide(config)
        self.assertEqual(dossier.read_ledger(path)[0]["verdict"]["policy"]["max_risk_pct"], 2.0)
        _, path, _ = self.run_decide(config, risk_pct=0.5)
        self.assertEqual(dossier.read_ledger(path)[0]["verdict"]["policy"]["max_risk_pct"], 0.5)

    def test_an_invalid_config_refuses_and_records_nothing(self):
        bad = copy.deepcopy(GOOD)
        bad["risk"]["max_risk_pct"] = 99
        code, path, err = self.run_decide(self.write_config(bad))
        self.assertEqual(code, 2)
        self.assertFalse(path.exists())
        self.assertIn("refused:", err)


if __name__ == "__main__":
    unittest.main()
