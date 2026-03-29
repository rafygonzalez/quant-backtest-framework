from __future__ import annotations
from pathlib import Path
from typing import Any
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel, Field
import yaml
from btframework.exceptions import ConfigError


class DataConfig(BaseModel):
    provider: str = "yahoo"
    symbols: list[str] = Field(default_factory=list)
    timeframe: str = "1d"
    start: datetime | None = None
    end: datetime | None = None
    provider_params: dict[str, Any] = Field(default_factory=dict)


class StrategyConfig(BaseModel):
    name: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    indicators: list[dict[str, Any]] = Field(default_factory=list)


class AccountConfig(BaseModel):
    initial_balance: float = 100_000.0
    currency: str = "USD"
    broker: str = "default"
    broker_params: dict[str, Any] = Field(default_factory=dict)


class MiddlewareConfig(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class ExecutionConfig(BaseModel):
    middlewares: list[MiddlewareConfig] = Field(default_factory=list)


class AnalyticsConfig(BaseModel):
    output_dir: str = "output"
    report_format: str = "html"
    benchmark: str | None = None


class BacktestConfig(BaseModel):
    """Root configuration for a backtest."""
    name: str = "backtest"
    data: DataConfig = Field(default_factory=DataConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    account: AccountConfig = Field(default_factory=AccountConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    fail_fast: bool = False

    @classmethod
    def from_yaml(cls, path: Path | str) -> "BacktestConfig":
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ConfigError(f"Invalid config format in {path}")
        return cls(**data)

    def to_yaml(self, path: Path | str) -> None:
        """Save configuration to a YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)
