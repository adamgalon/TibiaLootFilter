"""A deliberately slow, well-behaved HTTP client for public wikis."""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__

USER_AGENT = f"TibiaLootListManager/{__version__} (local desktop helper; Python urllib)"


class SourceError(Exception):
    """A source could not be reached or returned something unusable."""


class Cancelled(SourceError):
    """The user cancelled the operation; no further requests are made."""


class PoliteHttpClient:
    def __init__(self, min_interval: float = 1.0, timeout: float = 20.0, retries: int = 2,
                 cancel: threading.Event | None = None):
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.cancel = cancel  # set it to stop: the next request (or wait) raises Cancelled
        self._last_request = 0.0
        self._lock = threading.Lock()

    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled("Cancelled")

    def _sleep(self, seconds: float) -> None:
        """Wait, but wake up as soon as the operation is cancelled."""
        if self.cancel is not None:
            if self.cancel.wait(seconds):
                raise Cancelled("Cancelled")
        else:
            time.sleep(seconds)

    def _wait_turn(self) -> None:
        with self._lock:
            delay = self._last_request + self.min_interval - time.monotonic()
            if delay > 0:
                self._sleep(delay)
            self._last_request = time.monotonic()

    def get_json(self, url: str, params: dict | None = None) -> dict:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        last_error = None
        for attempt in range(self.retries + 1):
            self._check_cancel()
            self._wait_turn()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}"
                if e.code not in (429, 500, 502, 503, 504):
                    break
                retry_after = e.headers.get("Retry-After") if e.headers else None
                self._sleep(min(int(retry_after), 30) if retry_after and retry_after.isdigit() else 2 ** (attempt + 1))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = f"Network error: {getattr(e, 'reason', e)}"
                self._sleep(2 ** (attempt + 1))
            except ValueError as e:
                last_error = f"Invalid JSON response: {e}"
                break
        raise SourceError(last_error or "Request failed")
