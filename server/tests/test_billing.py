import pytest
from unittest.mock import MagicMock

from core.billing import compute_cost_cents, get_format_multiplier


@pytest.fixture
def settings():
    s = MagicMock()
    s.billing_rate_cents_per_hour = 100
    s.billing_format_multiplier_stl = 1.0
    s.billing_format_multiplier_step = 1.5
    s.billing_format_multiplier_gltf = 2.0
    s.billing_format_multiplier_glb = 2.0
    return s


def test_get_format_multiplier_known(settings):
    assert get_format_multiplier("stl", settings) == 1.0
    assert get_format_multiplier("step", settings) == 1.5
    assert get_format_multiplier("gltf", settings) == 2.0
    assert get_format_multiplier("glb", settings) == 2.0


def test_get_format_multiplier_case_insensitive(settings):
    assert get_format_multiplier("STL", settings) == 1.0
    assert get_format_multiplier("STEP", settings) == 1.5


def test_get_format_multiplier_unknown(settings):
    assert get_format_multiplier("unknown", settings) == 1.0


def test_compute_cost_cents_one_hour_no_multiplier(settings):
    # 3600 seconds * 100c/hr / 3600 * 1.0 = 100 cents
    assert compute_cost_cents(3600, "stl", settings) == 100


def test_compute_cost_cents_half_hour_no_multiplier(settings):
    # 1800 seconds * 100c/hr / 3600 * 1.0 = 50 cents
    assert compute_cost_cents(1800, "stl", settings) == 50


def test_compute_cost_cents_with_multiplier(settings):
    # 3600 seconds * 100c/hr / 3600 * 2.0 = 200 cents
    assert compute_cost_cents(3600, "glb", settings) == 200


def test_compute_cost_cents_zero_duration(settings):
    assert compute_cost_cents(0, "stl", settings) == 0


def test_compute_cost_cents_sub_cent_rounds_to_zero(settings):
    # 1 second * 100c/hr / 3600 * 1.0 ≈ 0.028 cents → rounds to 0
    assert compute_cost_cents(1, "stl", settings) == 0


def test_compute_cost_cents_sub_cent_rounds_up(settings):
    # 19 seconds * 100c/hr / 3600 * 1.0 ≈ 0.528 cents → rounds to 1
    assert compute_cost_cents(19, "stl", settings) == 1


def test_compute_cost_cents_large_duration(settings):
    # 10 hours = 36000 seconds * 100c/hr / 3600 * 1.0 = 1000 cents
    assert compute_cost_cents(36000, "stl", settings) == 1000
