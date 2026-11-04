"""The Telegram adapter: a public channel preview as a keyless voice source.

The spotlight names "Discord or Telegram" as optional sources. Telegram closes without any key:
public channels expose a readable web preview at t.me/s/<channel>. The adapter is keyless and, if
Telegram ever changes its HTML, returns empty rather than guessing, so a silent failure looks
like a gap, never like a fabricated voice.

The fixture mirrors the real preview, which an earlier idealised fixture did not: messages are
listed OLDEST first, each carries `data-post="<channel>/<id>"`, a media-only message has a date but
no text, and the text is HTML-escaped. The parser used to take the first N texts (the oldest),
paired texts and dates by position (so a media-only message shifted every date after it) and
linked to the channel instead of the message.
"""

import unittest
from unittest import mock

from verdict_desk import narrative


def message(channel: str, post_id: int, when: str, text: str | None) -> str:
    body = f'<div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>' if text is not None else ""
    return (
        f'<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        f'<div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="{channel}/{post_id}" data-view="x">'
        f'<div class="tgme_widget_message_bubble">{body}'
        f'<div class="tgme_widget_message_footer"><a class="tgme_widget_message_date" href="https://t.me/{channel}/{post_id}">'
        f'<time datetime="{when}" class="time">12:00</time></a></div></div></div></div>'
    )


HTML = "<!doctype html><html><body>" + "".join([
    message("example", 100, "2026-10-22T08:00:00+00:00", "Oldest: <i>Solana</i> outflows"),
    message("example", 101, "2026-10-22T09:00:00+00:00", None),  # a photo post: a date and no text
    message("example", 102, "2026-10-22T10:00:00+00:00", "Bitcoin <b>breaks</b> a key level &amp; ETH follows"),
    message("example", 103, "2026-10-22T11:00:00+00:00", "Newest: &quot;SOL&quot; <a href=\"https://t.me/example/9\">read more</a>"),
]) + "</body></html>"


class TelegramTest(unittest.TestCase):
    def test_takes_the_newest_messages_newest_first(self):
        items = narrative.parse_telegram(HTML, "example", limit=2)
        self.assertEqual([i["link"] for i in items], ["https://t.me/example/103", "https://t.me/example/102"])

    def test_extracts_text_stripping_tags_and_unescaping(self):
        items = narrative.parse_telegram(HTML, "example")
        by_id = {i["link"].rsplit("/", 1)[1]: i for i in items}
        self.assertEqual(by_id["102"]["title"], "Bitcoin breaks a key level & ETH follows")
        self.assertEqual(by_id["103"]["title"], 'Newest: "SOL" read more')

    def test_a_media_only_message_does_not_shift_the_dates_of_the_others(self):
        by_id = {i["link"].rsplit("/", 1)[1]: i for i in narrative.parse_telegram(HTML, "example")}
        self.assertEqual(by_id["100"]["published"], "2026-10-22T08:00:00+00:00")
        self.assertEqual(by_id["102"]["published"], "2026-10-22T10:00:00+00:00")
        self.assertNotIn("101", by_id, "a message with no text is not a voice")

    def test_the_link_is_the_permalink_of_the_message(self):
        for item in narrative.parse_telegram(HTML, "example"):
            self.assertRegex(item["link"], r"^https://t\.me/example/\d+$")

    def test_the_voice_is_the_display_name_when_given_else_the_channel(self):
        self.assertEqual(narrative.parse_telegram(HTML, "example")[0]["name"], "example")
        self.assertEqual(narrative.parse_telegram(HTML, "example", name="Example Desk")[0]["name"], "Example Desk")

    def test_unrecognised_html_yields_empty_not_garbage(self):
        self.assertEqual(narrative.parse_telegram("<html><body>nothing</body></html>", "x"), [])

    def test_fetch_feed_routes_t_me_to_the_telegram_parser_with_the_display_name(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return HTML.encode()

        with mock.patch.object(narrative.urllib.request, "urlopen", lambda request, timeout=0: Response()):
            items = narrative.fetch_feed("https://t.me/s/example", "Example Desk")
        self.assertEqual(items[0]["name"], "Example Desk")
        self.assertEqual(items[0]["link"], "https://t.me/example/103")


if __name__ == "__main__":
    unittest.main()
