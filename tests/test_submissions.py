import httpx
import respx

from conftest import fixture_bytes
from sec_sentinel.submissions import SUBMISSIONS_URL, fetch_items

MSTR_URL = SUBMISSIONS_URL.format(cik=1050446)


def test_url_format():
    assert MSTR_URL == "https://data.sec.gov/submissions/CIK0001050446.json"


@respx.mock
def test_items_for_accession(sec):
    respx.get(MSTR_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("submissions_mstr.json")))
    assert fetch_items(sec, 1050446, "0001193125-26-423400") == ("7.01", "8.01")


@respx.mock
def test_unknown_accession_returns_empty(sec):
    respx.get(MSTR_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("submissions_mstr.json")))
    assert fetch_items(sec, 1050446, "0000000000-26-000000") == ()


@respx.mock
def test_non_8k_has_empty_items(sec):
    respx.get(MSTR_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("submissions_mstr.json")))
    import json

    recent = json.loads(fixture_bytes("submissions_mstr.json"))["filings"]["recent"]
    defa = recent["accessionNumber"][recent["form"].index("DEFA14A")]
    assert fetch_items(sec, 1050446, defa) == ()


@respx.mock
def test_errors_return_empty(sec):
    respx.get(MSTR_URL).mock(return_value=httpx.Response(503))
    assert fetch_items(sec, 1050446, "0001193125-26-423400") == ()
