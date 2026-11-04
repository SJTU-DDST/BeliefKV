import math

import pytest

from scripts.audit_join_prediction_robustness import relative_error_tolerance


def test_relative_error_bound_is_exact_for_three_adversarial_path_errors():
    tolerance = relative_error_tolerance(20., 5., 3., 1.)
    assert tolerance == pytest.approx(11. / 28.)
    assert (1 - tolerance) * 20. == pytest.approx((1 + tolerance) * 8. + 1.)
    assert relative_error_tolerance(5., 4., 3.) == 0.
    assert relative_error_tolerance(0., 0., 0.) == 0.
    assert relative_error_tolerance(20., 5., 3., 2.) < tolerance
    for invalid in (math.inf, math.nan, -1., True):
        with pytest.raises(ValueError):
            relative_error_tolerance(invalid, 1., 1.)
