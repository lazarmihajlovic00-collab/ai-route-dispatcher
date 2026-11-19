import pytest

from dispatcher import service
from dispatcher.data import make_problem
from dispatcher.solver import plan_day
from dispatcher.store import Store


@pytest.fixture(scope="session")
def small():
    p = make_problem(3, n_orders=40)
    return p, plan_day(p, 2.0)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_DISABLED", "1")
    st = Store(str(tmp_path / "t.db"))
    service.seed(st, 3, 2.0, n_orders=40)
    return st
