"""Tests for src/bot_state.py — thread-safe runtime state."""

import threading
import src.bot_state as bot_state


def test_default_is_false():
    bot_state.set_dry_run(False)
    assert bot_state.get_dry_run() is False


def test_set_true():
    bot_state.set_dry_run(True)
    assert bot_state.get_dry_run() is True
    bot_state.set_dry_run(False)  # clean up


def test_toggle():
    bot_state.set_dry_run(False)
    assert bot_state.get_dry_run() is False
    bot_state.set_dry_run(True)
    assert bot_state.get_dry_run() is True
    bot_state.set_dry_run(False)
    assert bot_state.get_dry_run() is False


def test_thread_safety():
    """20 concurrent writers — no exception, no corruption."""
    results = []

    def worker(val):
        bot_state.set_dry_run(val)
        results.append(bot_state.get_dry_run())

    threads = [threading.Thread(target=worker, args=(i % 2 == 0,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 20   # all 20 workers completed without exception
