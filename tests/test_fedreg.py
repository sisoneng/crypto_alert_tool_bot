import json
import logging
from datetime import date, datetime, time

import httpx
import respx

from conftest import fixture_bytes
from sec_sentinel.fedreg import EASTERN, FEDREG_URL, FedRegDoc, fetch_fedreg, parse_documents, search_params
from sec_sentinel.priority import Tier, Verdict, classify_fedreg, compile_keywords

KEYWORDS = ("bitcoin", "ethereum", "ether", "solana", "XRP", "digital asset", "crypto asset",
            "stablecoin", "staking", "spot ETF", "avalanche", "chainlink", "dogecoin", "litecoin",
            "cardano", "polkadot", "HBAR", "hedera", "SUI", "BNB", "hyperliquid")
PATTERNS = compile_keywords(KEYWORDS)


def crypto_docs():
    return {d.number: d for d in parse_documents(json.loads(fixture_bytes("fedreg_crypto.json")))}


def test_search_params():
    params = search_params(("bitcoin", "digital asset"), date(2026, 10, 15))
    assert ("conditions[agencies][]", "securities-and-exchange-commission") in params
    assert ("conditions[term]", '"bitcoin" | "digital asset"') in params
    assert ("conditions[publication_date][gte]", "2026-10-15") in params
    assert [v for k, v in params if k == "fields[]"] == [
        "title", "type", "abstract", "document_number", "html_url", "publication_date", "excerpts"
    ]


def test_parse_window_fixture():
    docs = parse_documents(json.loads(fixture_bytes("fedreg_window.json")))
    assert [d.number for d in docs] == ["2026-21149", "2026-21128", "2026-21037"]
    first = docs[0]
    assert first.doc_type == "Notice"
    assert first.title.startswith("Self-Regulatory Organizations; Cboe Exchange, Inc.;")
    assert first.published == datetime.combine(date(2026, 10, 16), time(0), EASTERN)
    assert first.link.startswith("https://www.federalregister.gov/documents/2026/10/16/2026-21149/")
    assert "<span" not in first.excerpts and "Bitcoin" in first.excerpts  # HTML stripped
    assert docs[2].doc_type == "Proposed Rule"


def test_parse_empty_response_without_results_key():
    data = json.loads(fixture_bytes("fedreg_empty.json"))
    assert "results" not in data
    assert parse_documents(data) == []


def test_listing_approvals_are_high():
    docs = crypto_docs()
    generic = classify_fedreg(docs["2025-18258"], PATTERNS)  # generic listing standards approved
    assert generic.tier is Tier.HIGH
    assert generic.reasons[0] == "listing decision"
    grayscale = classify_fedreg(docs["2025-18257"], PATTERNS)  # "...and Approving ... List and Trade Shares"
    assert grayscale.tier is Tier.HIGH


def test_proceedings_and_unrelated_approvals_are_medium():
    docs = crypto_docs()
    # "Order Instituting Proceedings To Determine Whether To Approve or Disapprove" is not a decision.
    assert classify_fedreg(docs["2025-18961"], PATTERNS).tier is Tier.MEDIUM
    # LCH SA risk governance approval: an approval, but not an exchange listing.
    assert classify_fedreg(docs["2025-18941"], PATTERNS).tier is Tier.MEDIUM
    # The KPI binary options approval in the current window permits "the Listing of" options only.
    window = {d.number: d for d in parse_documents(json.loads(fixture_bytes("fedreg_window.json")))}
    assert classify_fedreg(window["2026-21149"], PATTERNS).tier is Tier.MEDIUM


def test_reasons_distinguish_title_and_text():
    docs = crypto_docs()
    bitcoin_options = classify_fedreg(docs["2025-18797"], PATTERNS)  # "...Nasdaq Bitcoin Index Options"
    assert bitcoin_options.tier is Tier.MEDIUM
    assert bitcoin_options.reasons[0] == "keyword: bitcoin"
    pengu = classify_fedreg(docs["2025-18961"], PATTERNS)  # title has no keyword; excerpt mentions Solana
    assert pengu.reasons == ("full-text: solana, digital asset",)


def test_reason_when_excerpt_lacks_keyword():
    # The API matched the body, but the returned excerpt may show other text.
    doc = FedRegDoc("2026-00001", "Investment Company Cross Trading", "Proposed Rule", "",
                    "remuneration payments", "https://www.federalregister.gov/x",
                    datetime.combine(date(2026, 10, 15), time(0), EASTERN))
    assert classify_fedreg(doc, PATTERNS) == Verdict(Tier.MEDIUM, ("full-text match",))


@respx.mock
def test_fetch_ok(sec):
    route = respx.get(FEDREG_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("fedreg_window.json")))
    docs, ok = fetch_fedreg(sec, KEYWORDS, date(2026, 10, 18))
    assert ok and len(docs) == 3
    assert route.calls.last.request.url.params["conditions[publication_date][gte]"] == "2026-10-15"


@respx.mock
def test_fetch_empty(sec):
    respx.get(FEDREG_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("fedreg_empty.json")))
    assert fetch_fedreg(sec, KEYWORDS, date(2026, 10, 18)) == ([], True)


@respx.mock
def test_fetch_warns_when_more_than_one_page(sec, caplog):
    respx.get(FEDREG_URL).mock(return_value=httpx.Response(200, content=fixture_bytes("fedreg_crypto.json")))
    with caplog.at_level(logging.WARNING):
        docs, ok = fetch_fedreg(sec, KEYWORDS, date(2025, 10, 1))
    assert ok and len(docs) == 25
    assert "29 matches, only the first page was read" in caplog.text


@respx.mock
def test_fetch_failure_is_reported_not_raised(sec):
    respx.get(FEDREG_URL).mock(return_value=httpx.Response(503))
    assert fetch_fedreg(sec, KEYWORDS, date(2026, 10, 18)) == ([], False)
