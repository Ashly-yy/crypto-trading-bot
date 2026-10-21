"""Validated application settings; this module does not connect to an exchange."""

import re
from enum import Enum
from functools import lru_cache
from ipaddress import ip_address
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from crypto_bot.config.timeframe_config import (
    SUPPORTED_CANDLE_INTERVALS, TIMEFRAME_SECONDS, parse_interval_csv, validate_single_interval,
)


_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,20}$")
_ASSET_PATTERN = re.compile(r"^[A-Z0-9]{2,12}$")


def _normalise_symbol_csv(value: str, *, allow_empty: bool = False) -> str:
    """Normalize and validate a comma-separated symbol list."""
    if not isinstance(value, str):
        raise ValueError("must be a comma-separated string")
    if not value.strip():
        if allow_empty:
            return ""
        raise ValueError("must contain at least one symbol")
    symbols = [part.strip().upper() for part in value.split(",")]
    if any(not symbol for symbol in symbols):
        raise ValueError("must not contain empty symbol entries")
    if any(not _SYMBOL_PATTERN.fullmatch(symbol) for symbol in symbols):
        raise ValueError("contains a malformed symbol; use exchange symbol format such as BTCUSDT")
    if len(set(symbols)) != len(symbols):
        raise ValueError("must not contain duplicate symbols")
    return ",".join(symbols)


class TradingMode(str, Enum):
    """Supported operating-mode names; execution adapters are added in later steps."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    BINANCE_SPOT_TESTNET = "BINANCE_SPOT_TESTNET"
    BINANCE_SPOT_LIVE = "BINANCE_SPOT_LIVE"


class Settings(BaseSettings):
    """Settings sourced from environment variables and an optional local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1024, le=65535)
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = Field(default=5173, ge=1024, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Public-market discovery settings; they do not contain credentials.
    market_symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT"
    market_quote_asset: str = "USDT"
    market_excluded_symbols: str = ""

    # Supported intervals are a configurable allowlist. These defaults are for data alignment;
    # they are not validated strategy parameters or evidence of profitability.
    supported_candle_intervals: str = "1m,3m,5m,15m,30m,1h,2h,4h,1d"
    scalp_entry_interval: str = "1m"
    scalp_context_intervals: str = "3m,5m"
    day_entry_interval: str = "5m"
    day_context_intervals: str = "15m,1h"
    swing_entry_interval: str = "1h"
    swing_context_intervals: str = "4h,1d"

    default_trading_mode: TradingMode = TradingMode.BACKTEST
    live_trading_enabled: bool = False

    binance_spot_testnet_api_key: SecretStr | None = None
    binance_spot_testnet_api_secret: SecretStr | None = None
    binance_spot_live_api_key: SecretStr | None = None
    binance_spot_live_api_secret: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    @field_validator("app_host", "dashboard_host")
    @classmethod
    def require_loopback_host(cls, value: str) -> str:
        """Keep local services bound to this computer, not the home network/internet."""
        try:
            address = ip_address(value)
        except ValueError:
            raise ValueError("must be a loopback IP address (127.0.0.1 or ::1)") from None
        if not address.is_loopback:
            raise ValueError("must be a loopback IP address (127.0.0.1 or ::1)")
        return str(address)

    @field_validator("market_symbols")
    @classmethod
    def validate_market_symbols(cls, value: str) -> str:
        return _normalise_symbol_csv(value)

    @field_validator("market_excluded_symbols")
    @classmethod
    def validate_excluded_symbols(cls, value: str) -> str:
        return _normalise_symbol_csv(value, allow_empty=True)

    @field_validator("market_quote_asset")
    @classmethod
    def validate_quote_asset(cls, value: str) -> str:
        asset = value.strip().upper()
        if not _ASSET_PATTERN.fullmatch(asset):
            raise ValueError("must be a valid quote asset code, such as USDT")
        return asset

    @field_validator("supported_candle_intervals")
    @classmethod
    def validate_supported_candle_intervals(cls, value: str) -> str:
        return ",".join(parse_interval_csv(value, field_name="SUPPORTED_CANDLE_INTERVALS"))

    @field_validator("scalp_entry_interval", "day_entry_interval", "swing_entry_interval")
    @classmethod
    def validate_entry_interval(cls, value: str) -> str:
        return validate_single_interval(value, field_name="strategy entry interval")

    @field_validator("scalp_context_intervals", "day_context_intervals", "swing_context_intervals")
    @classmethod
    def validate_context_intervals(cls, value: str) -> str:
        return ",".join(parse_interval_csv(value, field_name="strategy context intervals"))

    @property
    def supported_candle_interval_list(self) -> tuple[str, ...]:
        return parse_interval_csv(self.supported_candle_intervals, field_name="SUPPORTED_CANDLE_INTERVALS")

    @property
    def scalp_context_interval_list(self) -> tuple[str, ...]:
        return parse_interval_csv(self.scalp_context_intervals, field_name="SCALP_CONTEXT_INTERVALS")

    @property
    def day_context_interval_list(self) -> tuple[str, ...]:
        return parse_interval_csv(self.day_context_intervals, field_name="DAY_CONTEXT_INTERVALS")

    @property
    def swing_context_interval_list(self) -> tuple[str, ...]:
        return parse_interval_csv(self.swing_context_intervals, field_name="SWING_CONTEXT_INTERVALS")
    @property
    def market_symbol_list(self) -> tuple[str, ...]:
        """Return the configured market allowlist as an immutable tuple."""
        return tuple(self.market_symbols.split(","))

    @property
    def excluded_market_symbol_list(self) -> tuple[str, ...]:
        """Return explicitly excluded markets as an immutable tuple."""
        return tuple(self.market_excluded_symbols.split(",")) if self.market_excluded_symbols else ()

    @model_validator(mode="after")
    def require_explicit_live_flag(self) -> "Settings":
        """Reject live mode unless its explicit configuration flag is enabled.

        Enabling this flag alone does not implement or authorize live execution.
        The live adapter and readiness gates do not exist at this development stage.
        """
        supported = set(self.supported_candle_interval_list)
        strategy_maps = (
            ("scalp", self.scalp_entry_interval, self.scalp_context_interval_list),
            ("day", self.day_entry_interval, self.day_context_interval_list),
            ("swing", self.swing_entry_interval, self.swing_context_interval_list),
        )
        for style, entry_interval, context_intervals in strategy_maps:
            if entry_interval not in supported:
                raise ValueError(f"{style} entry interval is not in SUPPORTED_CANDLE_INTERVALS.")
            if not context_intervals:
                raise ValueError(f"{style} must configure at least one context interval.")
            for context_interval in context_intervals:
                if context_interval not in supported:
                    raise ValueError(f"{style} context interval is not in SUPPORTED_CANDLE_INTERVALS.")
                if TIMEFRAME_SECONDS[context_interval] <= TIMEFRAME_SECONDS[entry_interval]:
                    raise ValueError(
                        f"{style} context interval {context_interval} must be strictly higher than "
                        f"entry interval {entry_interval}."
                    )
        if self.default_trading_mode is TradingMode.BINANCE_SPOT_LIVE:
            raise ValueError(
                "BINANCE_SPOT_LIVE is permanently locked at this development stage; "
                "live execution and its readiness gates are not implemented."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance for the running application."""
    return Settings()