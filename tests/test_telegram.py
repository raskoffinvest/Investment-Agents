from datetime import UTC, datetime

from invagents.notify.telegram import Notifier, SendStatus
from tests.conftest import FakeNet, make_config


def test_sends_only_to_allowlisted_chats(storage, clock):
    net = FakeNet(clock.now)
    cfg = make_config()
    n = Notifier(cfg.telegram, storage, cfg.timezone, client=net.client())
    assert n.send("tank", "news", "[Tank] hi") is SendStatus.SENT
    assert [m["chat_id"] for m in net.telegram] == [111]
    assert net.telegram[0]["parse_mode"] == "HTML"


def test_quiet_hours_block_unless_urgent(storage, clock):
    clock.now = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)  # 00:00 Istanbul
    net = FakeNet(clock.now)
    cfg = make_config()
    n = Notifier(cfg.telegram, storage, cfg.timezone, client=net.client())
    assert n.send("tank", "news", "x") is SendStatus.QUIET_HOURS
    assert n.send("smith", "deadman", "x", urgent=True) is SendStatus.SENT


def test_global_daily_cap(storage, clock):
    net = FakeNet(clock.now)
    cfg = make_config()
    n = Notifier(cfg.telegram, storage, cfg.timezone, client=net.client())
    for _ in range(15):
        assert n.send("neo", "alert", "x") is SendStatus.SENT
    assert n.send("tank", "news", "x") is SendStatus.DAILY_CAP
    assert len(net.telegram) == 15


def test_missing_token_means_dry_run(storage, clock, capsys):
    cfg = make_config()
    tg = cfg.telegram.__class__(bot_token="", chat_ids=())
    n = Notifier(tg, storage, cfg.timezone)
    assert n.send("tank", "news", "hello") is SendStatus.DRY_RUN
    assert "hello" in capsys.readouterr().out
