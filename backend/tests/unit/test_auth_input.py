import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from services.identity import auth
from services.identity.main import Login, PasswordChange, Register

pytestmark = pytest.mark.unit


def test_password_whitespace_is_not_silently_removed():
    password = "  exact password  "
    assert (
        Register(
            first_name="Имя", last_name="Фамилия", email="user@example.org", password=password
        ).password
        == password
    )
    assert Login(email="user@example.org", password=password).password == password
    assert PasswordChange(current_password=password, new_password=password).new_password == password


async def test_password_memory_work_is_bounded(monkeypatch):
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def work(*args):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.01)
        with lock:
            state["active"] -= 1
        return "hash"

    monkeypatch.setattr(auth, "password_hasher", SimpleNamespace(hash=work))
    monkeypatch.setattr(auth, "password_capacity", asyncio.Semaphore(2))
    await asyncio.gather(*(auth.hash_password("password") for _ in range(10)))
    assert state["peak"] == 2
