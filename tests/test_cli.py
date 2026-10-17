from sec_sentinel.__main__ import main


def test_refuses_to_start_without_user_agent(monkeypatch, capsys):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert main(["--once", "--dry-run"]) == 2
    assert "SEC_USER_AGENT is required" in capsys.readouterr().err


def test_requires_telegram_secrets_unless_dry_run(monkeypatch, capsys):
    monkeypatch.setenv("SEC_USER_AGENT", "sec-sentinel test@example.com")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main(["--once"]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_once_and_loop_are_exclusive(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["--once", "--loop"])
    assert "not allowed with" in capsys.readouterr().err


def test_mode_is_required(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["--dry-run"])
    assert "one of the arguments --once --loop is required" in capsys.readouterr().err


def test_loop_interval_minimum(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["--loop", "--interval", "5"])
    assert "at least 30 seconds" in capsys.readouterr().err
