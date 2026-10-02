import os
import tempfile

# Isolate all state writes from the real committed state/ folder.
os.environ.setdefault("OMEGA_STATE_DIR", tempfile.mkdtemp(prefix="omega_test_state_"))
os.environ.setdefault("OMEGA_DATA_MODE", "synthetic")

import pytest  # noqa: E402

from core.synthetic import SyntheticSpec, generate_market  # noqa: E402


@pytest.fixture(scope="session")
def raw():
    return generate_market(SyntheticSpec(n_bars=2500, seed=3), "BTCUSDT")


@pytest.fixture(scope="session")
def settings():
    from core.config import get_settings

    return get_settings()


@pytest.fixture(scope="session")
def trained(raw, settings):
    from omega.model import OmegaModel
    from omega.pipeline import prepare

    X, sigma = prepare(raw)
    return OmegaModel().fit(X, raw, sigma, settings, n_rounds=60, k=3), X, sigma
