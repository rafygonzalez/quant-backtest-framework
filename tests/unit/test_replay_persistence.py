from datetime import datetime, timedelta
from decimal import Decimal as D
import json
import sqlite3
import stat

import pytest

from btframework.data.models import Bar
from btframework.replay.server import ReplayApp


def sample_bars():
    start = datetime(2025, 1, 6, 15)
    rows = [(100, 101, 99, 100), (100, 105, 99, 104), (104, 106, 103, 105),
            (105, 107, 104, 106), (106, 107, 104, 105)]
    return [Bar(start + timedelta(minutes=i), *map(D, map(str, row)), symbol="MNQ", volume=100)
            for i, row in enumerate(rows)]


def mutate(app, revision, action, args=None):
    return app.command(action, {**(args or {}), "expected_revision": revision})


def test_restart_restores_open_position_and_continues_same_native_bar(tmp_path):
    bars = sample_bars()
    database = tmp_path / "replay.sqlite3"
    checkpoints = tmp_path / "checkpoints"
    app = ReplayApp(bars, checkpoint_dir=checkpoints, state_path=database)
    state = mutate(app, 0, "create", {"strategy": "manual"})
    state = mutate(app, state["revision"], "advance")
    state = mutate(app, state["revision"], "order", {
        "side": "BUY", "quantity": 1, "order_type": "MARKET", "sl": 95, "tp": 120,
    })
    state = mutate(app, state["revision"], "advance")
    state = mutate(app, state["revision"], "modify", {"break_even": True, "trailing": 2})
    before = app.session.snapshot()

    reopened = ReplayApp(bars, checkpoint_dir=checkpoints, state_path=database)
    after = reopened.command("state", {})
    for key in ("cursor", "now", "account", "position", "intent", "trades", "equity_curve"):
        assert after[key] == before[key]
    assert after["playing"] is False

    mutate(app, app.revision, "advance")
    mutate(reopened, reopened.revision, "advance")
    assert reopened.session.strategy.trades == app.session.strategy.trades
    assert reopened.session.engine.account.snapshot() == app.session.engine.account.snapshot()


def test_restart_restores_pending_order_and_stale_tab_cannot_mutate(tmp_path):
    bars = sample_bars()
    database = tmp_path / "replay.sqlite3"
    app = ReplayApp(bars, state_path=database, checkpoint_dir=tmp_path / "checkpoints")
    state = mutate(app, 0, "create", {"strategy": "manual"})
    state = mutate(app, state["revision"], "advance")
    state = mutate(app, state["revision"], "order", {
        "side": "BUY", "quantity": 1, "order_type": "LIMIT", "price": 98, "sl": 90, "tp": 120,
    })
    reopened = ReplayApp(bars, state_path=database, checkpoint_dir=tmp_path / "checkpoints")
    assert len(reopened.session.engine.pending_orders) == 1
    assert reopened.session.strategy.intent == app.session.strategy.intent
    with pytest.raises(ValueError, match="stale_session"):
        mutate(reopened, state["revision"] - 1, "advance")
    assert reopened.session.cursor == app.session.cursor


def test_persistent_session_requires_confirmation_before_replacement(tmp_path):
    app = ReplayApp(sample_bars(), state_path=tmp_path / "replay.sqlite3", checkpoint_dir=tmp_path / "checkpoints")
    state = mutate(app, 0, "create", {"strategy": "manual"})
    with pytest.raises(ValueError, match="confirm_new_session"):
        mutate(app, state["revision"], "create", {"strategy": "manual"})
    assert app.session.cursor == 0
    assert stat.S_IMODE((tmp_path / "replay.sqlite3").stat().st_mode) == 0o600


def test_navigating_to_date_requires_explicit_new_session_confirmation(tmp_path):
    bars = sample_bars()
    app = ReplayApp(bars, state_path=tmp_path / "replay.sqlite3", checkpoint_dir=tmp_path / "checkpoints")
    state = mutate(app, 0, "create", {"strategy": "manual"})
    with pytest.raises(ValueError, match="confirm_new_session"):
        mutate(app, state["revision"], "navigate", {"date": bars[0].timestamp.isoformat()})
    assert app.session is not None
    assert app.session.cursor == 0


def test_corrupt_saved_definition_fails_closed(tmp_path):
    database = tmp_path / "replay.sqlite3"
    app = ReplayApp(sample_bars(), state_path=database, checkpoint_dir=tmp_path / "checkpoints")
    mutate(app, 0, "create", {"strategy": "manual"})
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE replay_meta SET value='{' WHERE key='definition'")
    with pytest.raises(ValueError):
        ReplayApp(sample_bars(), state_path=database, checkpoint_dir=tmp_path / "checkpoints")


def test_date_navigation_archives_old_session_and_resume_survives_restart(tmp_path):
    bars = sample_bars()
    database, checkpoints = tmp_path / "replay.sqlite3", tmp_path / "checkpoints"
    app = ReplayApp(bars, state_path=database, checkpoint_dir=checkpoints)
    state = mutate(app, 0, "create", {"strategy": "manual"})
    state = mutate(app, state["revision"], "advance", {"steps": 2})
    expected_cursor = state["cursor"]
    state = mutate(app, state["revision"], "navigate", {
        "date": bars[0].timestamp.isoformat(), "confirm_new_session": True,
    })
    assert state["cursor"] == 1
    assert len(list(checkpoints.glob("replay-checkpoint-*.json"))) == 1

    reopened = ReplayApp(bars, state_path=database, checkpoint_dir=checkpoints)
    assert reopened.checkpoints
    resumed = mutate(reopened, reopened.revision, "resume", {"id": next(iter(reopened.checkpoints))})
    assert resumed["cursor"] == expected_cursor


def test_recovery_receipt_detects_modified_replay_log(tmp_path):
    bars = sample_bars()
    database = tmp_path / "replay.sqlite3"
    app = ReplayApp(bars, state_path=database, checkpoint_dir=tmp_path / "checkpoints")
    state = mutate(app, 0, "create", {"strategy": "manual"})
    mutate(app, state["revision"], "advance")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE replay_commands SET args=? WHERE seq=(SELECT max(seq) FROM replay_commands)",
                           (json.dumps({"steps": 2, "until_event": True}),))
    with pytest.raises(ValueError, match="replay_recovery_mismatch"):
        ReplayApp(bars, state_path=database, checkpoint_dir=tmp_path / "checkpoints")
