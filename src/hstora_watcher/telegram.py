from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class TelegramError(RuntimeError):
    pass


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: int = 30):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, text: str) -> None:
        body = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": text[:4096],
            "disable_web_page_preview": "true",
        }).encode()
        request = urllib.request.Request(self.url, data=body, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramError(f"Telegram returned HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TelegramError(f"Could not reach Telegram: {exc}") from exc
        if not result.get("ok"):
            raise TelegramError(str(result))

