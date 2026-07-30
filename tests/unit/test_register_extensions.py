"""Tests for the extension synchronization CLI lifecycle."""

import asyncio

from app.bitrix import register_extensions


def test_main_synchronizes_and_disposes_on_the_same_event_loop(monkeypatch):
    loops = []

    async def synchronize() -> None:
        loops.append(asyncio.get_running_loop())

    async def dispose_database_engine() -> None:
        loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(register_extensions, "synchronize", synchronize)
    monkeypatch.setattr(register_extensions, "dispose_database_engine", dispose_database_engine)

    register_extensions.main()

    assert len(loops) == 2
    assert loops[0] is loops[1]
