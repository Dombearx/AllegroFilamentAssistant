import os

import pytest

# Set required env vars before any module imports Settings.
os.environ.setdefault("ALLEGRO_CLIENT_ID", "test_id")
os.environ.setdefault("ALLEGRO_CLIENT_SECRET", "test_secret")
os.environ.setdefault("ALLEGRO_ENV", "sandbox")


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path):
    """Give each test its own diskcache directory and reset global singletons."""
    import filament_assistant.config as config_mod
    import filament_assistant.core.cache as cache_mod

    # Reset settings singleton so each test gets a clean Settings instance.
    config_mod._settings = None
    # Point cache at a fresh temp directory for this test.
    os.environ["CACHE_DIR"] = str(tmp_path / "cache")
    cache_mod._cache = None

    yield

    cache_mod._cache = None
    config_mod._settings = None
