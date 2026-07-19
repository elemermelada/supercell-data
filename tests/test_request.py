"""Unit tests for request.py: the CSRF fetch, the GDPR submit and the
browser-cookie fetcher selection.

No real HTTP or browsers — ``requests.Session`` and ``browser_cookie3`` are
mocked. Run with: pytest tests/test_request.py
(or, without pytest: python -m unittest tests.test_request)
"""

import unittest
from unittest import mock

import request
from config import BROWSER_CHOICES


class FetchCsrfTests(unittest.TestCase):
    def _session(self, csrf_value):
        session = mock.Mock()
        session.get.return_value = mock.Mock()  # r.raise_for_status() is a no-op
        session.cookies.get.return_value = csrf_value
        return session

    def test_returns_cookie_when_present(self):
        session = self._session("tok-123")

        token = request.fetch_csrf(session, game="hay-day", action="request")

        self.assertEqual(token, "tok-123")
        # Hit the begin endpoint with the right params and a timeout.
        (url,), kwargs = session.get.call_args
        self.assertEqual(url, request.BEGIN_URL)
        self.assertEqual(kwargs["params"], {"game": "hay-day", "action": "request"})
        self.assertEqual(kwargs["timeout"], request.HTTP_TIMEOUT)
        session.get.return_value.raise_for_status.assert_called_once()
        session.cookies.get.assert_called_once_with("csrf_")

    def test_raises_when_cookie_absent(self):
        session = self._session(None)
        with self.assertRaises(RuntimeError) as ctx:
            request.fetch_csrf(session, game="hay-day", action="request")
        self.assertIn("CSRF", str(ctx.exception))


class SubmitRequestTests(unittest.TestCase):
    def test_sends_token_headers_and_payload(self):
        session = mock.Mock()
        session.post.return_value = mock.Mock(status_code=200, text="ok")

        resp = request.submit_request(
            session, csrf_token="tok-123", game="hay-day", action="request"
        )

        self.assertIs(resp, session.post.return_value)
        (url,), kwargs = session.post.call_args
        self.assertEqual(url, request.SUBMIT_URL)
        self.assertEqual(kwargs["json"], {"game": "hay-day", "action": "request"})
        self.assertEqual(kwargs["timeout"], request.HTTP_TIMEOUT)

        headers = kwargs["headers"]
        self.assertEqual(headers["X-CSRF-Token"], "tok-123")
        self.assertEqual(headers["Origin"], "https://support.supercell.com")
        self.assertEqual(
            headers["Referer"],
            "https://support.supercell.com/hay-day/en/articles/gdpr.html",
        )
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["User-Agent"], request.USER_AGENT)


class RequestTests(unittest.TestCase):
    def test_non_200_submit_raises_with_status(self):
        resp = mock.Mock(status_code=503, text="service unavailable")
        with (
            mock.patch.object(request.requests, "Session", return_value=mock.Mock()),
            mock.patch.object(request, "load_browser_cookies"),
            mock.patch.object(request, "fetch_csrf", return_value="tok"),
            mock.patch.object(request, "submit_request", return_value=resp),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                request.request()
        self.assertIn("503", str(ctx.exception))

    def test_success_does_not_raise(self):
        resp = mock.Mock(status_code=200, text="ok")
        with (
            mock.patch.object(request.requests, "Session", return_value=mock.Mock()),
            mock.patch.object(request, "load_browser_cookies") as load,
            mock.patch.object(request, "fetch_csrf", return_value="tok") as csrf,
            mock.patch.object(request, "submit_request", return_value=resp) as submit,
        ):
            request.request()  # must not raise
        load.assert_called_once()
        csrf.assert_called_once()
        submit.assert_called_once()


class BrowserCookieFetcherTests(unittest.TestCase):
    def _fetcher_for(self, browser_type):
        with mock.patch.object(request, "settings") as settings:
            settings.browser_type = browser_type
            return request.browser_cookie_fetcher()

    def test_returns_firefox_fetcher(self):
        self.assertIs(self._fetcher_for("firefox"), request.browser_cookie3.firefox)

    def test_returns_chrome_fetcher(self):
        self.assertIs(self._fetcher_for("chrome"), request.browser_cookie3.chrome)

    def test_fetchers_cover_exactly_the_config_choices(self):
        # A browser added to config.BROWSER_CHOICES but not to request.FETCHERS
        # would pass config validation and then KeyError at runtime (and vice
        # versa). Keep the two in lockstep.
        self.assertEqual(set(request.FETCHERS), set(BROWSER_CHOICES))


if __name__ == "__main__":
    unittest.main()
