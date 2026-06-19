from core.config import Settings


def get_format_multiplier(export_format: str, settings: Settings) -> float:
    attr = f"billing_format_multiplier_{export_format.lower()}"
    return float(getattr(settings, attr, 1.0))


def compute_cost_cents(duration_seconds: float, export_format: str, settings: Settings) -> int:
    multiplier = get_format_multiplier(export_format, settings)
    cost = (duration_seconds / 3600) * settings.billing_rate_cents_per_hour * multiplier
    return round(cost)
