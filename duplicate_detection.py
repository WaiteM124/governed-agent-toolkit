"""
Duplicate detection for agent-proposed record creation.

Before a "create" tool is allowed to open an approval, candidate matches against
existing records are scored across weighted fields. The result tells a person
why a row was flagged, and it tells the write path whether to block outright
or to ask for approval with the candidates attached.

Generic reimplementation. Contains no client code, data, schemas, or identifiers.

This scorer walks the records it is given. A production master-data path would
exact-lookup identifiers in an index first and only fuzzy-score the shortlist.
The policy below is the part that has to stay the same when that index exists.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from rapidfuzz import fuzz


class FieldKind(str, Enum):
    """How a field contributes to the score. These are not interchangeable."""

    NAME = "name"
    IDENTIFIER = "identifier"
    CONTACT = "contact"


class MatchBand(str, Enum):
    """POSSIBLE is for a person. LIKELY is strong enough to refuse an automatic create."""

    POSSIBLE = "possible"
    LIKELY = "likely"


@dataclass(frozen=True)
class FieldWeight:
    field_name: str
    weight: float
    kind: FieldKind = FieldKind.NAME


@dataclass(frozen=True)
class MatchCandidate:
    record_id: str
    confidence: float  # 0.0-1.0
    band: MatchBand
    matched_fields: dict[str, float]  # field_name -> similarity actually used
    notes: tuple[str, ...]  # policy decisions a reviewer cannot see from the score alone


# Weights are relative. They do not have to sum to 1; the scorer normalises.
DEFAULT_WEIGHTS: tuple[FieldWeight, ...] = (
    FieldWeight("display_name", 0.5, FieldKind.NAME),
    FieldWeight("external_reference", 0.3, FieldKind.IDENTIFIER),
    FieldWeight("contact_detail", 0.2, FieldKind.CONTACT),
)

# Dropped before name comparison. "Acme Ltd" and "ACME Limited" are the same name;
# token-sort alone scores that pair well below a duplicate, because the suffix
# tokens differ. "the" is filler, not a distinguishing token.
_LEGAL_SUFFIXES = frozenset(
    {
        "ltd",
        "limited",
        "inc",
        "incorporated",
        "llc",
        "llp",
        "plc",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "sarl",
        "pty",
        "bv",
        "ag",
        "the",
    }
)

# A conflicting identifier must not auto-block: the second code may be a second
# site, or a typo a person should look at. Sit just under the likely threshold.
_CONFLICT_CAP_GAP = 0.01

_EMAIL_AT = "@"


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _normalise_name(value: str) -> str:
    text = value.strip().lower().replace("&", " and ")
    text = text.replace(".", "")  # "A.C.M.E." and "Ltd." rather than splitting letters
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    tokens = [token for token in text.split() if token not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def _normalise_identifier(value: str) -> str:
    """Case and separators are formatting. ACM-001, acm 001, and ACM_001 are one id."""
    return re.sub(r"[\s\-_]", "", value.strip().lower())


def _normalise_contact(value: str) -> str:
    text = value.strip().lower()
    if _EMAIL_AT in text:
        return text
    return re.sub(r"[^\d+]", "", text)


def _name_similarity(left: str, right: str) -> float:
    left_name, right_name = _normalise_name(left), _normalise_name(right)
    if not left_name or not right_name:
        return 0.0
    if left_name == right_name:
        return 1.0
    return fuzz.token_sort_ratio(left_name, right_name) / 100.0


def _identifier_similarity(left: str, right: str) -> float:
    """Exact after normalisation, otherwise a miss. Fuzzy ids collide too easily."""
    left_id, right_id = _normalise_identifier(left), _normalise_identifier(right)
    if not left_id or not right_id:
        return 0.0
    return 1.0 if left_id == right_id else 0.0


def _contact_similarity(left: str, right: str) -> float:
    """
    Exact email or phone after normalisation scores 1.

    A one-character email typo still counts. A different mailbox does not, even
    though the two strings share an '@' and a domain shape. Phone numbers match
    on the national significant digits, so a country code doesn't hide a duplicate.
    """
    left_contact, right_contact = _normalise_contact(left), _normalise_contact(right)
    if not left_contact or not right_contact:
        return 0.0
    if left_contact == right_contact:
        return 1.0
    left_email, right_email = _EMAIL_AT in left_contact, _EMAIL_AT in right_contact
    if left_email != right_email:
        return 0.0
    if left_email:
        score = fuzz.ratio(left_contact, right_contact) / 100.0
        return score if score >= 0.92 else 0.0
    left_digits = re.sub(r"\D", "", left_contact)
    right_digits = re.sub(r"\D", "", right_contact)
    if len(left_digits) >= 7 and len(right_digits) >= 7 and left_digits[-7:] == right_digits[-7:]:
        return 1.0
    return 0.0


_COMPARE = {
    FieldKind.NAME: _name_similarity,
    FieldKind.IDENTIFIER: _identifier_similarity,
    FieldKind.CONTACT: _contact_similarity,
}


def _validate_weights(weights: Sequence[FieldWeight]) -> None:
    if not weights:
        raise ValueError("At least one field weight is required")
    seen: set[str] = set()
    for field_weight in weights:
        if field_weight.weight <= 0:
            raise ValueError(f"Weight for {field_weight.field_name} must be positive")
        if field_weight.field_name in seen:
            raise ValueError(f"Duplicate field weight: {field_weight.field_name}")
        seen.add(field_weight.field_name)


def _score_record(
    new_record: dict[str, Any],
    existing: dict[str, Any],
    weights: Sequence[FieldWeight],
    *,
    threshold: float,
    likely_threshold: float,
) -> MatchCandidate | None:
    record_id = existing.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError("Existing record is missing record_id")

    matched_fields: dict[str, float] = {}
    notes: list[str] = []
    weighted_sum = 0.0
    weight_total = 0.0
    identifier_exact = False
    identifier_conflict = False

    for field_weight in weights:
        # Score a field only when both sides have a value. A name-only proposal
        # is not punished because the stored row also has an email, and a stored
        # row with no reference is not punished because the proposal brought one.
        incoming = _as_text(new_record.get(field_weight.field_name))
        current = _as_text(existing.get(field_weight.field_name))
        if incoming is None or current is None:
            continue
        similarity = _COMPARE[field_weight.kind](incoming, current)

        if field_weight.kind is FieldKind.IDENTIFIER:
            if similarity == 1.0:
                identifier_exact = True
                notes.append(f"{field_weight.field_name} matches an existing record")
            else:
                # Drop it from the average. A zero here would wash out a real
                # name match; keeping it out, then capping, leaves the row visible.
                identifier_conflict = True
                matched_fields[field_weight.field_name] = 0.0
                notes.append(
                    f"{field_weight.field_name} differs, so this stays a review item "
                    "instead of an automatic block"
                )
                continue

        if (
            field_weight.kind is FieldKind.NAME
            and similarity == 1.0
            and incoming.strip().lower() != current.strip().lower()
        ):
            notes.append(
                f"{field_weight.field_name} matches once case and legal suffixes are removed"
            )

        matched_fields[field_weight.field_name] = round(similarity, 3)
        weighted_sum += similarity * field_weight.weight
        weight_total += field_weight.weight

    if weight_total == 0 and not identifier_exact:
        return None

    confidence = (weighted_sum / weight_total) if weight_total else 0.0
    if identifier_exact:
        confidence = max(confidence, 0.97)
    if identifier_conflict:
        confidence = min(confidence, likely_threshold - _CONFLICT_CAP_GAP)
    confidence = round(confidence, 3)
    if confidence < threshold:
        return None

    band = MatchBand.LIKELY if confidence >= likely_threshold else MatchBand.POSSIBLE
    return MatchCandidate(
        record_id=record_id.strip(),
        confidence=confidence,
        band=band,
        matched_fields=matched_fields,
        notes=tuple(notes),
    )


def find_candidates(
    new_record: dict[str, Any],
    existing_records: Sequence[dict[str, Any]],
    *,
    weights: Sequence[FieldWeight] | None = None,
    threshold: float = 0.72,
    likely_threshold: float = 0.90,
    max_candidates: int | None = 5,
) -> list[MatchCandidate]:
    """
    Rank existing records against a proposed one.

    `threshold` is the floor for showing a candidate to a person. `likely_threshold`
    is the floor for refusing the create without a human override. Both are
    arguments because a supplier create and a contact create do not share a
    cost of a false merge. Keep a gap between them: a conflicting identifier is
    capped just under `likely_threshold` so it can still be reviewed.

    A field is scored only when both sides have a value.
    """
    if not 0 <= threshold <= likely_threshold <= 1:
        raise ValueError("Thresholds must satisfy 0 <= threshold <= likely_threshold <= 1")
    if max_candidates is not None and max_candidates < 1:
        raise ValueError("max_candidates must be at least 1")

    chosen_weights = DEFAULT_WEIGHTS if weights is None else weights
    _validate_weights(chosen_weights)

    candidates: list[MatchCandidate] = []
    for existing in existing_records:
        candidate = _score_record(
            new_record,
            existing,
            chosen_weights,
            threshold=threshold,
            likely_threshold=likely_threshold,
        )
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda candidate: (-candidate.confidence, candidate.record_id))
    if max_candidates is not None:
        return candidates[:max_candidates]
    return candidates
