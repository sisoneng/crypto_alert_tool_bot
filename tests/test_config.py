import pytest

from sec_sentinel.config import ConfigError, load_config, parse_config

BASE = {"timezone_display": ["America/New_York"], "forms": ["8-K"]}


def test_repo_config_loads():
    config = load_config("config.yaml")
    assert config.fulltext_forms == ("8-K", "S-1")
    assert "MSTR" in config.watchlist_tickers


def test_fulltext_disabled_by_default():
    assert parse_config(BASE).fulltext_forms == ()


def test_fulltext_rejects_amendment_forms():
    with pytest.raises(ConfigError, match="base forms"):
        parse_config({**BASE, "fulltext_forms": ["8-K", "S-1/A"]})
