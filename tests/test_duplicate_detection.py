from duplicate_detection import find_candidates


EXISTING_RECORDS = [
    {
        "record_id": "rec-1",
        "display_name": "Acme Limited",
        "external_reference": "ACM-001",
        "contact_detail": "accounts@acme.example.com",
    },
    {
        "record_id": "rec-2",
        "display_name": "Northwind Traders",
        "external_reference": "NWT-002",
        "contact_detail": "billing@northwind.example.com",
    },
]


def test_close_match_is_flagged():
    new_record = {
        "display_name": "ACME Ltd",
        "external_reference": "ACM-001",
        "contact_detail": "accounts@acme.example.com",
    }
    candidates = find_candidates(new_record, EXISTING_RECORDS)
    assert len(candidates) == 1
    assert candidates[0].record_id == "rec-1"
    assert candidates[0].confidence > 0.8


def test_unrelated_record_is_not_flagged():
    new_record = {
        "display_name": "Zephyr Consulting",
        "external_reference": "ZEP-099",
        "contact_detail": "hello@zephyr.example.com",
    }
    candidates = find_candidates(new_record, EXISTING_RECORDS)
    assert candidates == []


def test_ranking_orders_highest_confidence_first():
    new_record = {
        "display_name": "Northwind Trader",
        "external_reference": "NWT-002",
        "contact_detail": "billing@northwind.example.com",
    }
    candidates = find_candidates(new_record, EXISTING_RECORDS, threshold=0.3)
    assert candidates[0].record_id == "rec-2"
    if len(candidates) > 1:
        assert candidates[0].confidence >= candidates[1].confidence