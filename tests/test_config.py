import pytest

from sec_sentinel.config import ConfigError, load_config, parse_config

BASE = {"timezone_display": ["America/New_York"], "forms": ["8-K"]}


def test_repo_config_loads():
    config = load_config("config.yaml")
    assert config.fulltext_forms == ("8-K", "S-1", "N-1A", "485APOS", "8-A12B", "424B3")
    assert {"8-A12B", "N-1A", "485APOS", "424B3"} <= set(config.forms)
    assert {"HBAR", "dogecoin", "avalanche", "litecoin", "cardano"} <= set(config.keywords)
    assert config.federal_register is True
    assert {"MSTR", "GBTC", "IBIT", "FBTC", "ETHA", "BITB", "ARKB"} <= set(config.watchlist_tickers)
    assert {"Grayscale", "Bitwise", "21Shares", "CoinShares"} <= set(config.keywords)
    assert not {"Canary", "Osprey", "tron"} & set(config.keywords)


def test_fulltext_disabled_by_default():
    assert parse_config(BASE).fulltext_forms == ()


def test_fulltext_rejects_amendment_forms():
    with pytest.raises(ConfigError, match="base forms"):
        parse_config({**BASE, "fulltext_forms": ["8-K", "S-1/A"]})


def test_federal_register_disabled_by_default():
    assert parse_config(BASE).federal_register is False


def test_federal_register_must_be_bool():
    with pytest.raises(ConfigError, match="federal_register"):
        parse_config({**BASE, "federal_register": "yes"})
