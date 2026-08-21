from cadfree.search.standards import (
    annotate_result,
    dedupe_results,
    extract_standard_ids,
    rank_results,
    read_url,
    rewrite_query,
    search_standards,
    source_kind,
)


def test_extract_standard_ids():
    blob = (
        "See ISO 898-1 and ASTM F593; also ASME B31.3, DIN 912, SAE J429, "
        "MIL-STD-810H, NAS 1352, IPC-A-610, NIST SP 800."
    )
    found = {t.upper().replace("  ", " ") for t in extract_standard_ids(blob)}
    assert any(x.startswith("ISO") and "898" in x for x in found)
    assert any("ASTM" in x and "F593" in x for x in found)
    assert any("ASME" in x and "B31" in x for x in found)
    assert any("DIN" in x and "912" in x for x in found)
    assert any("SAE" in x and "429" in x for x in found)
    assert any("MIL" in x and "810" in x for x in found)
    assert any("NAS" in x and "1352" in x for x in found)
    assert any("IPC" in x and "610" in x for x in found)


def test_rewrite_query_parts_does_not_add_iso():
    q = rewrite_query("2207 1750KV motor", intent="parts")
    assert "ISO" not in q
    assert "buy" in q.lower()


def test_rewrite_query_adds_bodies():
    q = rewrite_query("printed PETG bracket tensile")
    assert "ISO" in q or "ASTM" in q
    ds = rewrite_query("PETG", intent="datasheet")
    assert "datasheet" in ds.lower()
    named = rewrite_query("ISO 898-1")
    assert named == "ISO 898-1"


def test_rank_prefers_standards_bodies():
    raw = [
        annotate_result(
            {"title": "blog", "url": "https://example.com/iso-898", "snippet": "ISO 898-1"}
        ),
        annotate_result(
            {"title": "ISO 898-1", "url": "https://www.iso.org/standard/61114.html", "snippet": "ISO 898-1"}
        ),
        annotate_result(
            {"title": "PETG", "url": "https://www.prusa3d.com/petg", "snippet": "datasheet"}
        ),
        annotate_result(
            {"title": "wiki", "url": "https://en.wikipedia.org/wiki/ISO_898", "snippet": "ISO 898"}
        ),
    ]
    ranked = rank_results(raw)
    assert source_kind(ranked[0]["url"]) == "body"
    assert ranked[0]["url"].startswith("https://www.iso.org")


def test_dedupe_results():
    items = [
        {"url": "https://iso.org/a/", "title": "a"},
        {"url": "https://iso.org/a", "title": "a dup"},
        {"url": "https://astm.org/b", "title": "b"},
    ]
    out = dedupe_results(items)
    assert len(out) == 2


def test_read_url_blocks_private(monkeypatch):
    blocked = read_url("http://127.0.0.1/secret")
    assert blocked["ok"] is False
    assert "private" in blocked["error"]
    fileish = read_url("file:///etc/passwd")
    assert fileish["ok"] is False


def test_search_standards_no_live_web(monkeypatch):
    calls = []

    def fake_web(query, max_results=8):
        calls.append(query)
        return [
            {
                "title": "Random blog about ISO 4762",
                "url": "https://example.com/screws",
                "snippet": "hex cap screws ISO 4762",
            }
        ]

    monkeypatch.setattr("cadfree.search.standards.search_web", fake_web)
    out = search_standards("socket head cap screw", intent="standards")
    assert out["ok"] is True
    assert out["citations"]
    assert any("ISO" in (c.get("title") + str(c.get("standard_ids"))) for c in out["citations"])
    assert len(calls) >= 2  # first pass + site-boost retry when no body hits
    assert "site:iso.org" in calls[1]


def test_search_web_can_see_get_setting():
    import cadfree.search.standards as std

    assert callable(getattr(std, "get_setting", None))

