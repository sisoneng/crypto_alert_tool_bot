from datetime import datetime, timezone

from conftest import fixture_bytes
from sec_sentinel.edgar import Entity, Filing, parse_atom
from sec_sentinel.priority import Tier, classify, compile_keywords

CONFIG_KEYWORDS = compile_keywords(
    ("bitcoin", "ethereum", "ether", "solana", "XRP", "digital asset", "crypto asset",
     "stablecoin", "staking", "spot ETF")
)


def by_name(filings, name):
    return next(f for f in filings if f.primary.name == name)


def make_filing(form, name, cik=1, items=()):
    return Filing("0000000000-26-000001", form, datetime(2026, 10, 17, tzinfo=timezone.utc),
                  [Entity(cik, name, "Filer", "https://example.invalid")], items)


def test_s1_with_keyword_is_high():
    v = classify(make_filing("S-1", "Grayscale Solana Trust (SOL)"), set(), CONFIG_KEYWORDS)
    assert v.tier is Tier.HIGH
    assert v.reasons == ("keyword: solana",)


def test_s1a_with_keyword_is_high_from_real_feed():
    filings = parse_atom(fixture_bytes("atom_s1.xml"))
    tang = by_name(filings, "TANG CAPITAL ACQUISITION CORP.")
    assert tang.form == "S-1/A"
    assert classify(tang, set(), compile_keywords(("acquisition",))).tier is Tier.HIGH


def test_watchlist_8k_with_hot_items_is_high():
    filings = parse_atom(fixture_bytes("atom_8k.xml"))
    lqr = by_name(filings, "LQR House Inc.")  # items 1.01, 5.02, 7.01, 9.01
    v = classify(lqr, {1843165}, CONFIG_KEYWORDS)
    assert v.tier is Tier.HIGH
    assert v.reasons == ("watchlist", "item 1.01", "item 7.01")


def test_watchlist_8k_without_hot_items_is_medium():
    v = classify(make_filing("8-K", "Strategy Inc", cik=1050446, items=("5.02", "9.01")),
                 {1050446}, CONFIG_KEYWORDS)
    assert v.tier is Tier.MEDIUM
    assert v.reasons == ("watchlist",)


def test_watchlist_8k_amendment_is_medium():
    filings = parse_atom(fixture_bytes("atom_8k.xml"))
    powerus = by_name(filings, "Powerus Corp")
    assert powerus.form == "8-K/A"
    v = classify(powerus, {powerus.primary.cik}, CONFIG_KEYWORDS)
    assert v.tier is Tier.MEDIUM


def test_hot_items_need_watchlist():
    filings = parse_atom(fixture_bytes("atom_8k.xml"))
    gopro = by_name(filings, "GoPro, Inc.")  # item 8.01, not on the watchlist
    assert classify(gopro, set(), CONFIG_KEYWORDS).tier is Tier.LOW


def test_fulltext_hit_is_medium():
    v = classify(make_filing("8-K", "FARMHOUSE, INC. /NV"), set(), CONFIG_KEYWORDS, ["bitcoin"])
    assert v.tier is Tier.MEDIUM
    assert v.reasons == ("full-text: bitcoin",)


def test_s1_fulltext_hit_is_high():
    v = classify(make_filing("S-1", "iPower Inc."), set(), CONFIG_KEYWORDS, ["bitcoin"])
    assert v.tier is Tier.HIGH


def test_fulltext_reason_not_repeated_for_name_match():
    v = classify(make_filing("8-K", "Bitcoin Depot Inc."), set(), CONFIG_KEYWORDS, ["bitcoin", "staking"])
    assert v.reasons == ("keyword: bitcoin", "full-text: staking")


def test_watchlist_matches_any_entity_role():
    filings = parse_atom(fixture_bytes("atom_4.xml"))
    asp = filings[0]
    assert classify(asp, {1921865}, []).tier is Tier.MEDIUM  # Issuer
    assert classify(asp, {1951275}, []).tier is Tier.MEDIUM  # Reporting owner


def test_non_watchlist_keyword_is_medium():
    v = classify(make_filing("8-K", "Bitcoin Depot Inc."), set(), CONFIG_KEYWORDS)
    assert v.tier is Tier.MEDIUM
    assert v.reasons == ("keyword: bitcoin",)


def test_multiword_keyword_case_insensitive():
    v = classify(make_filing("10-Q", "Acme DIGITAL ASSET Holdings"), set(), CONFIG_KEYWORDS)
    assert v.reasons == ("keyword: digital asset",)


def test_keyword_needs_word_boundary():
    filings = parse_atom(fixture_bytes("atom_8k.xml"))
    aether = by_name(filings, "Aether Holdings, Inc.")
    assert classify(aether, set(), CONFIG_KEYWORDS).tier is Tier.LOW


def test_s1_without_keyword_is_low():
    filings = parse_atom(fixture_bytes("atom_s1.xml"))
    assert classify(by_name(filings, "Cashlin"), set(), CONFIG_KEYWORDS).tier is Tier.LOW


def test_multiple_fulltext_keywords_grouped_in_one_reason():
    v = classify(make_filing("8-K", "Acme Corp"), set(), CONFIG_KEYWORDS, ["bitcoin", "ethereum"])
    assert v.reasons == ("full-text: bitcoin, ethereum",)


def test_keyword_matches_plural():
    v = classify(make_filing("10-Q", "Acme Digital Assets Corp"), set(), CONFIG_KEYWORDS)
    assert v.reasons == ("keyword: digital asset",)
    v = classify(make_filing("10-Q", "Global Stablecoins Inc"), set(), CONFIG_KEYWORDS)
    assert v.reasons == ("keyword: stablecoin",)


def test_8a12b_with_keyword_is_high():
    v = classify(make_filing("8-A12B", "Grayscale Solana Trust"), set(), CONFIG_KEYWORDS)
    assert v.tier is Tier.HIGH
    assert v.reasons == ("keyword: solana",)


def test_485apos_fulltext_hit_is_medium():
    v = classify(make_filing("485APOS", "ETF Opportunities Trust"), set(), CONFIG_KEYWORDS, ["solana"])
    assert v.tier is Tier.MEDIUM
    assert v.reasons == ("full-text: solana",)
