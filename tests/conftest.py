import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar.config import load_config  # noqa: E402
from radar.espn.client import FixtureClient  # noqa: E402
from radar.schedule import ProSchedule  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "2026-11-08"
# the fixtures were recorded on Sunday 8.11.2026 at 16:19 Zagreb (today's games start at 21:30)
NOW = dt.datetime(2026, 11, 8, 16, 19, tzinfo=ZoneInfo("Europe/Zagreb"))


@pytest.fixture(scope="session")
def cfg():
    return load_config(ROOT / "config.yaml")


@pytest.fixture(scope="session")
def client():
    return FixtureClient(FIX)


@pytest.fixture(scope="session")
def schedule(client):
    return ProSchedule(client.pro_schedule())


@pytest.fixture(scope="session")
def run_result(cfg, client):
    from radar.build import run
    return run(cfg, client, NOW, state_path=None)


@pytest.fixture(scope="session")
def legendi(run_result):
    return next(a for a in run_result.analyses if a.key == "legendi")


# ---- 11.11.2026 (Wednesday, 20:40 Zagreb): Legendi with this week's transactions and the scoreboard ----------------
FIX11 = ROOT / "tests" / "fixtures" / "2026-11-11"
NOW11 = dt.datetime(2026, 11, 11, 19, 40, 45, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="session")
def client11():
    return FixtureClient(FIX11)


@pytest.fixture(scope="session")
def res11(cfg, client11):
    from radar.build import run
    from radar.store import Store
    return run(cfg, client11, NOW11, keys=["legendi"], state_path=None, store=Store(":memory:"))


@pytest.fixture(scope="session")
def leg11(res11):
    return res11.analyses[0]
