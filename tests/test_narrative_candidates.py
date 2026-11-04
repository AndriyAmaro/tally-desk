"""The narrative agent explains what it rejected, and where its voices can come from.

Two properties the bounty's spotlight asks for and that are easy to fake:
  * saying WHY a token did not become a narrative (not just staying silent);
  * accepting user-selected voices from any feed, which is what makes an X or Telegram
    bridge a configuration choice rather than a rewrite.
"""

import unittest
from unittest import mock

from verdict_desk import narrative


def ex(name, tokens, stance, voice="V1", conviction="high", urgency="medium"):
    return {"voice": voice, "name": name, "tokens": tokens, "stance": stance,
            "conviction": conviction, "urgency": urgency, "quote": "q"}


class RejectedCandidatesTest(unittest.TestCase):
    def test_converged_token_is_not_listed_as_rejected(self):
        accepted = [ex("CoinDesk", ["BTC"], "bullish", "V1"), ex("Cointelegraph", ["BTC"], "bullish", "V2")]
        conv = narrative.converge_extractions(accepted)
        rejected = narrative.rejected_candidates(accepted, conv)
        self.assertEqual([r["token"] for r in rejected], [])

    def test_single_voice_token_says_only_one_voice(self):
        accepted = [ex("CoinDesk", ["ETH"], "bullish", "V1"), ex("Decrypt", ["BTC"], "bullish", "V2"),
                    ex("The Block", ["BTC"], "bullish", "V3")]
        conv = narrative.converge_extractions(accepted)
        rejected = {r["token"]: r for r in narrative.rejected_candidates(accepted, conv)}
        self.assertIn("ETH", rejected)
        self.assertIn("only one voice", rejected["ETH"]["reason"])

    def test_conflicting_voices_are_reported_as_disagreement(self):
        accepted = [ex("CoinDesk", ["SOL"], "bullish", "V1"), ex("Decrypt", ["SOL"], "bearish", "V2")]
        rejected = {r["token"]: r for r in narrative.rejected_candidates(accepted, [])}
        self.assertIn("SOL", rejected)
        self.assertIn("disagree", rejected["SOL"]["reason"])

    def test_neutral_only_mentions_are_explained(self):
        accepted = [ex("CoinDesk", ["BNB"], "neutral", "V1")]
        rejected = {r["token"]: r for r in narrative.rejected_candidates(accepted, [])}
        self.assertIn("neutral", rejected["BNB"]["reason"])

    def test_block_carries_rejected_candidates_in_the_llm_path(self):
        accepted = [ex("CoinDesk", ["ETH"], "bullish", "V1")]
        with mock.patch.object(narrative, "extract",
                                        return_value={"accepted": accepted, "rejected": [], "chunk_errors": []}):
            block = narrative.build([{"name": "CoinDesk", "title": "t"}], ask=lambda s, u: "{}")
        self.assertIn("rejected_candidates", block)
        self.assertEqual(block["rejected_candidates"][0]["token"], "ETH")


class FeedSourceTest(unittest.TestCase):
    ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>BTC looks strong here</title><link href="https://x.com/somekol/status/1"/>
      <updated>2026-10-22T10:00:00Z</updated></entry></feed>"""

    def test_an_x_style_bridge_feed_is_accepted_like_any_other(self):
        """A Nitter/RSSHub style bridge is just a feed URL, so no code changes are needed."""
        items = narrative.parse_feed(self.ATOM, "somekol")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "BTC looks strong here")
        self.assertIn("x.com", items[0].get("link", ""))


if __name__ == "__main__":
    unittest.main()
