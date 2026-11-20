import pytest

from duplicate_detection import FieldKind, FieldWeight, MatchBand, find_candidates


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


def test_close_match_is_flagged_and_explains_the_normalised_name():
    candidates = find_candidates(
        {
            "display_name": "ACME Ltd",
            "external_reference": "ACM-001",
            "contact_detail": "Accounts@Acme.example.com",
        },
        EXISTING_RECORDS,
    )
    assert len(candidates) == 1
    assert candidates[0].record_id == "rec-1"
    assert candidates[0].confidence > 0.8
    assert candidates[0].band is MatchBand.LIKELY
    assert any("legal suffixes" in note for note in candidates[0].notes)


def test_legal_suffix_alone_is_enough_to_flag_the_same_name():
    candidates = find_candidates(
        {"display_name": "Acme Ltd."},
        [{"record_id": "rec-1", "display_name": "Acme Limited"}],
    )
    assert len(candidates) == 1
    assert candidates[0].confidence == 1.0
    assert candidates[0].band is MatchBand.LIKELY


def test_exact_reference_flags_a_record_whose_name_was_retyped():
    candidates = find_candidates(
        {"display_name": "A.C.M.E. Consulting", "external_reference": "acm 001"},
        EXISTING_RECORDS,
    )
    assert candidates[0].record_id == "rec-1"
    assert candidates[0].confidence >= 0.97
    assert candidates[0].band is MatchBand.LIKELY
    assert any("external_reference matches" in note for note in candidates[0].notes)


def test_conflicting_reference_stays_reviewable_and_does_not_auto_block():
    candidates = find_candidates(
        {"display_name": "Acme Ltd", "external_reference": "OTHER-9"},
        [{"record_id": "rec-1", "display_name": "Acme Limited", "external_reference": "ACM-001"}],
    )
    assert len(candidates) == 1
    assert candidates[0].band is MatchBand.POSSIBLE
    assert candidates[0].confidence < 0.90
    assert any("differs" in note for note in candidates[0].notes)


def test_unrelated_record_is_not_flagged():
    candidates = find_candidates(
        {
            "display_name": "Zephyr Consulting",
            "external_reference": "ZEP-099",
            "contact_detail": "hello@zephyr.example.com",
        },
        EXISTING_RECORDS,
    )
    assert candidates == []


def test_phone_numbers_match_with_or_without_a_country_code():
    candidates = find_candidates(
        {"contact_detail": "+44 20 7946 0958"},
        [{"record_id": "rec-9", "contact_detail": "020 7946 0958"}],
    )
    assert candidates[0].confidence == 1.0
    assert candidates[0].matched_fields["contact_detail"] == 1.0


def test_a_different_email_does_not_score_as_a_partial_match():
    candidates = find_candidates(
        {"contact_detail": "other@acme.example.com"},
        [{"record_id": "rec-1", "contact_detail": "accounts@acme.example.com"}],
        threshold=0.0,
    )
    assert candidates[0].matched_fields["contact_detail"] == 0.0


def test_ranking_is_deterministic_and_highest_confidence_first():
    candidates = find_candidates(
        {
            "display_name": "Northwind Trader",
            "external_reference": "NWT-002",
            "contact_detail": "billing@northwind.example.com",
        },
        EXISTING_RECORDS,
        threshold=0.3,
    )
    assert candidates[0].record_id == "rec-2"
    assert candidates == sorted(candidates, key=lambda candidate: (-candidate.confidence, candidate.record_id))


def test_max_candidates_truncates_after_ranking():
    rows = [
        {"record_id": "rec-b", "display_name": "Acme Limited"},
        {"record_id": "rec-a", "display_name": "Acme Ltd"},
    ]
    candidates = find_candidates({"display_name": "Acme"}, rows, max_candidates=1)
    assert len(candidates) == 1
    assert candidates[0].record_id == "rec-a"


def test_weights_and_thresholds_are_validated():
    with pytest.raises(ValueError):
        find_candidates({}, [], weights=[])
    with pytest.raises(ValueError):
        find_candidates({}, [], weights=[FieldWeight("display_name", 0, FieldKind.NAME)])
    with pytest.raises(ValueError):
        find_candidates({}, [], threshold=0.95, likely_threshold=0.5)
    with pytest.raises(ValueError):
        find_candidates({}, [{"display_name": "Acme"}])
