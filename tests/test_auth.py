import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import credential_tool
from credential_crypto import CredentialError, decrypt_credentials_token, encrypt_credentials_token

class AutomaticLoginTests(unittest.TestCase):
    def test_torznab_feed_keeps_valid_magnet_link(self):
        release = {
            "title": "Ice Age 2002 2160p",
            "link": "magnet:?xt=urn:btih:abc",
            "details": "https://www.mini4k.com/torrents/807227",
            "size": 123,
            "date": app.datetime.now(app.timezone.utc),
            "category": 2045,
        }
        with (
            patch.object(app, "API_KEY", "test-key"),
            app.app.test_request_context(
                "/v2/api?t=search",
                base_url="http://mini4k-torznab:8787",
            ),
        ):
            xml = app.rss_xml([release])
        self.assertIn("magnet:?xt=urn:btih:abc", xml)
        self.assertNotIn("/v2/download/807227", xml)

    def test_torznab_feed_proxies_direct_torrent_file(self):
        release = {
            "title": "Ice Age 2002 2160p",
            "link": "https://www.mini4k.com/sites/default/files/torrents/test.torrent",
            "details": "https://www.mini4k.com/torrents/807227",
            "size": 123,
            "date": app.datetime.now(app.timezone.utc),
            "category": 2045,
        }
        with (
            patch.object(app, "API_KEY", "test-key"),
            app.app.test_request_context(
                "/v2/api?t=search",
                base_url="http://mini4k-torznab:8787",
            ),
        ):
            xml = app.rss_xml([release])
        self.assertIn(
            "http://mini4k-torznab:8787/v2/download/807227?apikey=test-key",
            xml,
        )
        self.assertNotIn("sites/default/files", xml)

    def test_download_route_returns_torrent_file(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.get_with_solution.return_value = (
            {
                "cookies": [{"name": "cf_clearance", "value": "fresh"}],
                "userAgent": "test-agent",
            },
            '<a href="/sites/default/files/torrents/test.torrent">download</a>',
        )

        response_from_mini4k = MagicMock()
        response_from_mini4k.status_code = 200
        response_from_mini4k.content = b"d4:infod4:name4:testee"

        with (
            patch.object(app, "API_KEY", "test-key"),
            patch.object(app, "Mini4KSession", return_value=session),
            patch.object(app, "flare_cookies", return_value=[]),
            patch.object(
                app.curl_requests,
                "get",
                return_value=response_from_mini4k,
            ) as browser_get,
        ):
            response = app.app.test_client().get(
                "/v2/download/807227?apikey=test-key"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/x-bittorrent")
        self.assertEqual(response.data, b"d4:infod4:name4:testee")
        outbound = browser_get.call_args.args[0]
        self.assertEqual(
            outbound,
            "https://www.mini4k.com/sites/default/files/torrents/test.torrent",
        )
        self.assertEqual(
            browser_get.call_args.kwargs["cookies"],
            {"cf_clearance": "fresh"},
        )
        self.assertEqual(browser_get.call_args.kwargs["impersonate"], "chrome")

    def test_flaresolverr_session_failure_releases_browser_lock(self):
        with (
            patch.object(app, "FETCH_MODE", "flaresolverr"),
            patch.object(app, "flaresolverr_call", side_effect=RuntimeError("temporary failure")),
        ):
            with self.assertRaisesRegex(RuntimeError, "temporary failure"):
                with app.Mini4KSession():
                    pass

        self.assertTrue(app._browser_lock.acquire(blocking=False))
        app._browser_lock.release()

    def test_busy_browser_lock_fails_without_blocking_forever(self):
        app._browser_lock.acquire()
        try:
            with (
                patch.object(app, "FETCH_MODE", "flaresolverr"),
                patch.object(app, "BROWSER_LOCK_TIMEOUT", 0),
            ):
                with self.assertRaisesRegex(RuntimeError, "still running"):
                    with app.Mini4KSession():
                        pass
        finally:
            app._browser_lock.release()

    def test_release_style_query_uses_series_title_only(self):
        self.assertEqual(
            app.normalize_tv_query("Ben 10-4x02-Divided We Stand"),
            ("Ben 10", 4, 2),
        )
        self.assertEqual(
            app.normalize_tv_query("Good Doctor S01E09 Unconditional"),
            ("Good Doctor", 1, 9),
        )
        self.assertEqual(
            app.normalize_tv_query("Good Doctor s01 ep08"),
            ("Good Doctor", 1, 8),
        )

    def test_interactive_tool_does_not_print_plaintext_password(self):
        printed = []
        with (
            patch("builtins.input", return_value="member@example.com"),
            patch.object(credential_tool.getpass, "getpass", side_effect=["plain-secret", "plain-secret"]),
            patch.object(credential_tool, "encrypt_credentials_token", return_value=("key-value", "token-value")),
            patch("builtins.print", side_effect=lambda *values, **kwargs: printed.append(" ".join(map(str, values)))),
        ):
            credential_tool.main()
        output = "\n".join(printed)
        self.assertIn("MINI4K_CREDENTIALS_KEY=key-value", output)
        self.assertIn("MINI4K_CREDENTIALS_TOKEN=token-value", output)
        self.assertNotIn("plain-secret", output)

    def test_encrypted_environment_round_trip(self):
        key, token = encrypt_credentials_token("member@example.com", "secret=value")
        with (
            patch.object(app, "CREDENTIALS_KEY", key),
            patch.object(app, "ENCRYPTED_CREDENTIALS_TOKEN", token),
        ):
            self.assertEqual(
                app.read_credentials(),
                ("member@example.com", "secret=value"),
            )

    def test_tampered_encrypted_environment_is_rejected(self):
        key, token = encrypt_credentials_token("member", "secret")
        replacement = "A" if token[-1] != "A" else "B"
        with self.assertRaises(CredentialError):
            decrypt_credentials_token(token[:-1] + replacement, key)

    def test_login_posts_hidden_fields_and_saves_only_account_cookies(self):
        login_html = """
        <html><body>
          <form class="user-login-form" action="/user/login" method="post">
            <input name="form_build_id" value="token-123">
            <input name="form_id" value="user_login_form">
            <input name="name" type="text">
            <input name="pass" type="password">
            <input name="op" type="submit" value="登录">
          </form>
        </body></html>
        """
        logged_in_html = '<html class="user-logged-in"><body>ok</body></html>'
        calls = []

        def fake_solution(payload):
            calls.append(payload)
            if payload["cmd"] == "request.get":
                return ({"cookies": []}, login_html)
            return (
                {
                    "cookies": [
                        {"name": "SSESSabc", "value": "fresh", "domain": ".mini4k.com"},
                        {"name": "cf_clearance", "value": "do-not-save", "domain": ".mini4k.com"},
                    ]
                },
                logged_in_html,
            )

        with (
            patch.object(app, "read_credentials", return_value=("member@example.com", "p@ss&word=value")),
            patch.object(app, "flaresolverr_solution", side_effect=fake_solution),
            patch.object(app, "flare_cookies", return_value=[]),
            patch.object(app, "save_account_cookies") as save_cookies,
        ):
            app.flaresolverr_login("test-session")

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["cmd"], "request.post")
        submitted = urllib.parse.parse_qs(calls[1]["postData"])
        self.assertEqual(submitted["name"], ["member@example.com"])
        self.assertEqual(submitted["pass"], ["p@ss&word=value"])
        self.assertEqual(submitted["form_build_id"], ["token-123"])
        saved = save_cookies.call_args.args[0]
        self.assertEqual(saved[0]["name"], "SSESSabc")

    def test_cookie_save_filters_cloudflare_values(self):
        with (
            patch.object(Path, "mkdir"),
            patch.object(Path, "write_text") as write_text,
            patch.object(app.os, "replace") as replace,
        ):
            app.save_account_cookies([
                {"name": "SSESSabc", "value": "fresh", "domain": ".mini4k.com"},
                {"name": "cf_clearance", "value": "drop", "domain": ".mini4k.com"},
            ])
        write_text.assert_called_once_with("SSESSabc=fresh", encoding="utf-8")
        replace.assert_called_once()

    def test_expired_cookie_logs_in_once_then_retries_original_url(self):
        logged_out = '<html><a href="/user/login">login</a></html>'
        logged_in = '<html class="user-logged-in"><body>results</body></html>'
        with (
            patch.object(
                app,
                "flaresolverr_solution",
                side_effect=[({"cookies": []}, logged_out), ({"cookies": []}, logged_in)],
            ) as solution,
            patch.object(app, "flaresolverr_login") as login,
            patch.object(app, "flare_cookies", return_value=[]),
        ):
            result = app.flaresolverr_fetch(
                "https://www.mini4k.com/search?text=test",
                "test-session",
            )
        self.assertIn("results", result)
        login.assert_called_once_with("test-session")
        self.assertEqual(solution.call_count, 2)

    def test_failed_login_does_not_expose_password_in_error(self):
        login_html = """
        <form class="user-login-form" action="/user/login">
          <input name="name"><input name="pass" type="password">
        </form>
        """
        with (
            patch.object(app, "read_credentials", return_value=("member", "very-secret-password")),
            patch.object(
                app,
                "flaresolverr_solution",
                side_effect=[({"cookies": []}, login_html), ({"cookies": []}, login_html)],
            ),
            patch.object(app, "flare_cookies", return_value=[]),
        ):
            with self.assertRaises(RuntimeError) as raised:
                app.flaresolverr_login("test-session")
        self.assertNotIn("very-secret-password", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

