import os
import subprocess
import time

import httpx
import pytest

_TEST_PORT = 8765
_TEST_ENV = {
    **os.environ,
    "ALLEGRO_CLIENT_ID": "test_id",
    "ALLEGRO_CLIENT_SECRET": "test_secret",
    "ALLEGRO_ENV": "sandbox",
    "CACHE_DIR": "/tmp/fa_ui_test_cache",
    "PORT": str(_TEST_PORT),
}


@pytest.fixture(scope="session")
def live_server_url():
    """Start the NiceGUI app in a subprocess and return its base URL."""
    proc = subprocess.Popen(
        ["uv", "run", "filament-assistant"],
        env=_TEST_ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{_TEST_PORT}"

    # Poll until the server responds (up to 30 s)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=2.0)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.terminate()
        raise RuntimeError("NiceGUI app did not start within 30 s")

    yield url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Use the pre-installed Chromium binary."""
    return {**browser_type_launch_args, "executable_path": "/opt/pw-browsers/chromium"}
