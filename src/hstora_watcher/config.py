from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    """Load a simple .env file without overwriting existing environment variables."""
    file = Path(path)
    if not file.exists():
        return
    for raw_line in file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    telegram_token: str
    telegram_chat_id: str
    base_url: str = "https://hstora.com/api/v1"
    db_path: str = "hstora-watcher.db"
    interval: int = 300
    low_stock_threshold: int = 10
    timeout: int = 30
    dashboard_username: str = "admin"
    dashboard_password: str = ""
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8787

    @classmethod
    def from_env(cls, require_telegram: bool = True, require_dashboard: bool = False) -> "Config":
        load_dotenv()
        values = {
            "HSTORA_API_KEY": os.getenv("HSTORA_API_KEY", ""),
            "HSTORA_API_SECRET": os.getenv("HSTORA_API_SECRET", ""),
            "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
            "DASHBOARD_PASSWORD": os.getenv("DASHBOARD_PASSWORD", ""),
        }
        required = ["HSTORA_API_KEY", "HSTORA_API_SECRET"]
        if require_telegram:
            required += ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
        if require_dashboard and not values["DASHBOARD_PASSWORD"]:
            required.append("DASHBOARD_PASSWORD")
        missing = [key for key in required if not values[key]]
        if missing:
            raise ValueError("Missing configuration: " + ", ".join(missing))
        return cls(
            api_key=values["HSTORA_API_KEY"],
            api_secret=values["HSTORA_API_SECRET"],
            telegram_token=values["TELEGRAM_BOT_TOKEN"],
            telegram_chat_id=values["TELEGRAM_CHAT_ID"],
            base_url=os.getenv("HSTORA_BASE_URL", "https://hstora.com/api/v1").rstrip("/"),
            db_path=os.getenv("HSTORA_DB_PATH", "hstora-watcher.db"),
            interval=max(10, int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))),
            low_stock_threshold=max(0, int(os.getenv("LOW_STOCK_THRESHOLD", "10"))),
            timeout=max(1, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))),
            dashboard_username=os.getenv("DASHBOARD_USERNAME", "admin"),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            dashboard_host=os.getenv("DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("DASHBOARD_PORT", "8787")),
        )
