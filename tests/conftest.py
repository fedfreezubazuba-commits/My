import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeCBR, FakeFedstat  # noqa: E402


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    wd = tmp_path / "MarketData"
    monkeypatch.setenv("HUB_WORKDIR", str(wd))
    return wd


@pytest.fixture
def fakes():
    return {"cbr": FakeCBR(), "fedstat": FakeFedstat()}


@pytest.fixture
def runner(workdir, fakes):
    from hub.core.paths import Paths
    from hub.runner import Runner
    r = Runner(Paths(workdir), ctx_clients=fakes)
    yield r
    r.close()


@pytest.fixture(autouse=True)
def moscow_computer(monkeypatch):
    """Расписание задаётся по Москве; в тестах компьютер «стоит» в Москве (в облаке — UTC)."""
    import datetime as dt
    from hub.core import schedule
    monkeypatch.setattr(schedule, "_local_offset", lambda moment: dt.timedelta(hours=3))
