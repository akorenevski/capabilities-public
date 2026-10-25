"""A minimal Telegram Bot API client: the few methods a bot needs.

The token is part of every request URL, so it is held here and nowhere else:
every error this module raises has the token scrubbed out of its text, because
an httpx error names the URL it failed on.

A send is not repeated blindly. A failure before the request left this machine
(no connection, a connect timeout, no free connection in the pool) is retried;
one after it may have reached Telegram - a read timeout, a dropped connection,
a 5xx - raises `uncertain` for `sendMessage` instead of a retry, because a
retry would post the message twice whenever the first one did arrive. A rare
loss is preferred to a duplicate, and the caller says it is not sure the
message arrived. Every other method is safe to repeat and is retried.

`transport` is the seam the suite replaces: a callable `(method, payload,
timeout) -> dict` answering the decoded JSON body. The default speaks HTTPS
through httpx, imported only when a real call is made.
"""

from __future__ import annotations

import time
from email.utils import parsedate_to_datetime

API_BASE = "https://api.telegram.org"
MAX_RETRIES = 3


# Methods a retry could repeat in the chat.
NOT_REPEATABLE = frozenset({"sendMessage"})


class BotApiError(Exception):
    """A refused or failed call. `kind` is `auth` (401/404 on the token),
    `network` (the request never left), `uncertain` (it may have arrived),
    `rate_limited`, `server` or `refused`; `status` the HTTP status or the
    API's error_code."""

    def __init__(self, kind: str, message: str, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after


def clock_offset(date_header, now: float) -> float | None:
    """`now` minus the time an HTTP Date header names, or None when it names none."""
    try:
        return now - parsedate_to_datetime(str(date_header)).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


class BotApi:
    def __init__(self, token: str, *, transport=None, base: str = API_BASE,
                 sleep=time.sleep):
        if not token:
            raise BotApiError("auth", "no bot token resolved")
        self._token = token
        self._base = base.rstrip("/")
        self._transport = transport or self._http
        self._sleep = sleep
        self._client = None
        # This machine's clock minus Telegram's, from the last answer's Date
        # header: a message's age is judged by Telegram's clock, not ours.
        self.clock_offset: float | None = None

    def _scrub(self, text) -> str:
        return str(text).replace(self._token, "<token>")

    def _http(self, method: str, payload: dict, timeout: float) -> dict:
        import httpx
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(timeout + 15.0))
        try:
            resp = self._client.post(f"{self._base}/bot{self._token}/{method}",
                                     json=payload, timeout=timeout + 15.0)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise BotApiError("network", self._scrub(f"{type(exc).__name__}: {exc}")) from None
        except httpx.HTTPError as exc:
            # Sent, or partly sent: Telegram may have acted on it.
            raise BotApiError("uncertain", self._scrub(f"{type(exc).__name__}: {exc}")) from None
        offset = clock_offset(resp.headers.get("date"), time.time())
        if offset is not None:
            self.clock_offset = offset
        try:
            body = resp.json()
        except ValueError:
            body = {"ok": False, "error_code": resp.status_code,
                    "description": self._scrub(resp.text[:300])}
        if not isinstance(body, dict):
            body = {"ok": False, "error_code": resp.status_code, "description": "not JSON"}
        body.setdefault("error_code", resp.status_code if not body.get("ok") else None)
        return body

    def call(self, method: str, payload: dict | None = None, *, timeout: float = 30.0):
        """One method call, with bounded retries on 429 and 5xx; the `result`."""
        payload = {k: v for k, v in (payload or {}).items() if v is not None}
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                body = self._transport(method, payload, timeout)
            except BotApiError as exc:
                repeatable = exc.kind == "network" or (
                    exc.kind == "uncertain" and method not in NOT_REPEATABLE)
                if repeatable and attempt < MAX_RETRIES:
                    self._sleep(attempt * 1.5)
                    continue
                kind = "network" if exc.kind == "uncertain" and method not in NOT_REPEATABLE \
                    else exc.kind
                raise BotApiError(kind, self._scrub(exc), exc.status) from None
            if body.get("ok"):
                return body.get("result")
            code = body.get("error_code")
            text = self._scrub(body.get("description") or "")
            if code == 429:
                retry = float((body.get("parameters") or {}).get("retry_after") or attempt * 1.5)
                if attempt < MAX_RETRIES and retry <= 60:
                    self._sleep(retry)
                    continue
                raise BotApiError("rate_limited", f"{method}: {text}", 429, retry)
            if isinstance(code, int) and code >= 500:
                if method in NOT_REPEATABLE:
                    raise BotApiError("uncertain", f"{method}: {text}", code)
                if attempt < MAX_RETRIES:
                    self._sleep(attempt * 1.5)
                    continue
                raise BotApiError("server", f"{method}: {text}", code)
            if code in (401, 404) and method == "getMe":
                raise BotApiError("auth", f"the token was refused: {text}", code)
            if code == 401:
                raise BotApiError("auth", f"{method}: {text}", code)
            raise BotApiError("refused", f"{method}: {text}", code)
        raise BotApiError("network", f"{method}: no answer after {MAX_RETRIES} attempts")

    # --- The methods a bot uses -----------------------------------------------

    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: int | None, timeout: int,
                    allowed_updates=("message",)) -> list:
        return self.call("getUpdates", {"offset": offset, "timeout": int(timeout),
                                        "allowed_updates": list(allowed_updates)},
                         timeout=float(timeout)) or []

    def send_message(self, chat_id, text: str, *, message_thread_id=None,
                     reply_parameters=None, parse_mode=None) -> dict:
        return self.call("sendMessage", {"chat_id": chat_id, "text": text,
                                         "message_thread_id": message_thread_id,
                                         "reply_parameters": reply_parameters,
                                         "parse_mode": parse_mode})

    def send_chat_action(self, chat_id, action: str = "typing", *,
                         message_thread_id=None) -> None:
        try:
            self.call("sendChatAction", {"chat_id": chat_id, "action": action,
                                         "message_thread_id": message_thread_id},
                      timeout=10.0)
        except BotApiError:
            pass

    def delete_webhook(self) -> None:
        """Long polling and a webhook exclude each other; polling clears it."""
        self.call("deleteWebhook", {"drop_pending_updates": False})


