class BTFrameworkError(Exception):
    """Base exception for the framework."""

class ConfigError(BTFrameworkError):
    """Invalid configuration."""

class DataError(BTFrameworkError):
    """Data loading/processing error."""

class InsufficientFundsError(BTFrameworkError):
    """Not enough balance/margin for operation."""

class InvalidOrderError(BTFrameworkError):
    """Order validation failed."""

class StrategyError(BTFrameworkError):
    """Strategy execution error."""

class RegistryError(BTFrameworkError):
    """Component registry error (not found, duplicate, etc.)."""

class MiddlewareError(BTFrameworkError):
    """Middleware pipeline error."""

class ExchangeError(BTFrameworkError):
    """Simulated exchange error."""
