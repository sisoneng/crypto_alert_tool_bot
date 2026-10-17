import json
from datetime import date

import httpx
import respx

from conftest import fixture_bytes
from sec_sentinel.fulltext import FULLTEXT_URL, fetch_fulltext, parse_hits, search_params

MSTR_8K = "0001193125-26-423400"
STRIVE_8K = "0000950103-26-015690"  # three hits: main document plus two exhibits


def bitcoin_filings():
    return {f.accession: f for f in parse_hits(json.loads(fixture_bytes("fulltext_bitcoin.json")))}


def test_search_params_quote_keyword_and_send_base_forms():
    params = search_params("digital asset", ("8-K", "S-1"), date(2026, 10, 14), date(2026, 10, 17))
    assert params == {
        "q": '"digital asset"',
        "forms": "8-K,S-1",
        "dateRange": "custom",
        "startdt": "2026-10-14",
        "enddt": "2026-10-17",
    }


def test_parse_hits_groups_documents_by_accession():
    filings = bitcoin_filings()
    assert len(filings) == 10  # 13 hits
    assert len(filings[STRIVE_8K].entities) == 1


def test_parse_hit_fields():
    mstr = bitcoin_filings()[MSTR_8K]
    assert mstr.form == "8-K"
    assert mstr.items == ("7.01", "8.01")
    assert mstr.primary.cik == 1050446
    assert mstr.primary.name == "Strategy Inc"
    assert mstr.link == (
        "https://www.sec.gov/Archives/edgar/data/1050446/000119312526423400/0001193125-26-423400-index.htm"
    )
    assert mstr.has_time is False
    assert mstr.filed_at.date() == date(2026, 10, 16)


def test_parse_s1_hits():
    forms = {f.form for f in bitcoin_filings().values()}
    assert forms == {"8-K", "S-1", "S-1/A"}


@respx.mock
def test_fetch_fulltext_collects_matched_keywords(sec):
    respx.get(FULLTEXT_URL, params={"q": '"bitcoin"'}).mock(
        return_value=httpx.Response(200, content=fixture_bytes("fulltext_bitcoin.json"))
    )
    respx.get(FULLTEXT_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("fulltext_empty.json")))
    results, ok = fetch_fulltext(sec, ("bitcoin", "spot ETF"), ("8-K", "S-1"), date(2026, 10, 17))
    assert ok
    assert len(results) == 10
    filing, hits = results[MSTR_8K]
    assert hits == ["bitcoin"]


@respx.mock
def test_fetch_fulltext_filters_unconfigured_forms(sec):
    respx.get(FULLTEXT_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("fulltext_bitcoin.json")))
    results, _ = fetch_fulltext(sec, ("bitcoin",), ("8-K",), date(2026, 10, 17))
    assert {f.form for f, _ in results.values()} == {"8-K"}


@respx.mock
def test_fetch_fulltext_window(sec):
    route = respx.get(FULLTEXT_URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("fulltext_empty.json"))
    )
    fetch_fulltext(sec, ("bitcoin",), ("8-K",), date(2026, 10, 17))
    params = route.calls.last.request.url.params
    assert (params["startdt"], params["enddt"]) == ("2026-10-14", "2026-10-17")


@respx.mock
def test_fetch_fulltext_failure_is_reported_not_raised(sec):
    respx.get(FULLTEXT_URL, params={"q": '"bitcoin"'}).mock(return_value=httpx.Response(503))
    respx.get(FULLTEXT_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("fulltext_empty.json")))
    results, ok = fetch_fulltext(sec, ("bitcoin", "spot ETF"), ("8-K",), date(2026, 10, 17))
    assert not ok
    assert results == {}
