from datetime import datetime, timedelta, timezone

import httpx
import respx

from conftest import fixture_bytes
from sec_sentinel.edgar import ATOM_URL, extract_accession, fetch_filings, index_link, parse_atom


def test_parse_8k_feed():
    filings = parse_atom(fixture_bytes("atom_8k.xml"))
    assert len(filings) == 8
    first = filings[0]
    assert first.accession == "0001104659-26-116660"
    assert first.form == "8-K"
    assert first.primary.name == "NATIONAL RURAL UTILITIES COOPERATIVE FINANCE CORP /DC/"
    assert first.primary.cik == 70502
    assert first.primary.role == "Filer"
    assert first.filed_at == datetime(2026, 10, 16, 17, 30, 58, tzinfo=timezone(timedelta(hours=-4)))
    assert first.link == (
        "https://www.sec.gov/Archives/edgar/data/70502/000110465926116660/0001104659-26-116660-index.htm"
    )
    forms = {f.form for f in filings}
    assert forms == {"8-K", "8-K/A"}
    assert "AT&T INC." in {f.primary.name for f in filings}  # XML entities decoded


def test_8k_items_parsed_from_summary():
    filings = {f.primary.name: f for f in parse_atom(fixture_bytes("atom_8k.xml"))}
    assert filings["NATIONAL RURAL UTILITIES COOPERATIVE FINANCE CORP /DC/"].items == ("8.01", "9.01")
    assert filings["LQR House Inc."].items == ("1.01", "5.02", "7.01", "9.01")
    assert filings["GoPro, Inc."].items == ("2.02", "4.02", "8.01")


def test_non_8k_has_no_items():
    assert all(f.items == () for f in parse_atom(fixture_bytes("atom_4.xml")))


def test_index_link_matches_feed_links():
    first = parse_atom(fixture_bytes("atom_8k.xml"))[0]
    assert index_link(first.primary.cik, first.accession) == first.link


def test_form4_entries_grouped_by_accession():
    filings = parse_atom(fixture_bytes("atom_4.xml"))
    assert len(filings) == 6  # 12 entries: one Reporting + one Issuer per filing
    asp = filings[0]
    assert asp.accession == "0001477932-26-006312"
    assert {(e.role, e.cik) for e in asp.entities} == {("Reporting", 1951275), ("Issuer", 1921865)}
    assert asp.primary.name == "ASP Isotopes Inc."
    assert asp.ciks == {1951275, 1921865}


def test_schedule_13d_primary_is_subject():
    filings = parse_atom(fixture_bytes("atom_13d.xml"))
    hilltop = filings[0]
    assert hilltop.form == "SCHEDULE 13D/A"
    assert hilltop.primary.role == "Subject"
    assert hilltop.primary.name == "Hilltop Holdings Inc."


def test_extract_accession():
    assert extract_accession("urn:tag:sec.gov,2008:accession-number=0001213900-26-110367") == (
        "0001213900-26-110367"
    )
    assert extract_accession("urn:tag:sec.gov,2008:no-accession") is None


@respx.mock
def test_fetch_filters_forms_and_merges_feeds(sec):
    # The S-1 feed also returns S-1/A and S-1MEF; querying "S-1/A" returns overlapping entries.
    respx.get(ATOM_URL, params={"type": "S-1"}).mock(
        return_value=httpx.Response(200, content=fixture_bytes("atom_s1.xml"))
    )
    respx.get(ATOM_URL, params={"type": "S-1/A"}).mock(
        return_value=httpx.Response(200, content=fixture_bytes("atom_s1.xml"))
    )
    filings, failed = fetch_filings(sec, ("S-1", "S-1/A"))
    assert failed == []
    assert len(filings) == 11  # 12 entries minus S-1MEF; duplicates merged
    assert {f.form for f in filings.values()} == {"S-1", "S-1/A"}
    assert all(len(f.entities) == 1 for f in filings.values())


@respx.mock
def test_fetch_reports_failed_feed(sec):
    respx.get(ATOM_URL, params={"type": "8-K"}).mock(
        return_value=httpx.Response(200, content=fixture_bytes("atom_8k.xml"))
    )
    respx.get(ATOM_URL, params={"type": "4"}).mock(return_value=httpx.Response(503))
    filings, failed = fetch_filings(sec, ("8-K", "4"))
    assert failed == ["4"]
    assert len(filings) == 8
