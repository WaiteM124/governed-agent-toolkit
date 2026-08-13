"""
MDM (master data management) duplicate-detection for agent-proposed record creation.

Generic reimplementation of a fuzzy multi-field matching pattern used to flag likely
duplicates before an agent is allowed to create a new record. Contains no client
code, data, schemas, or identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from rapidfuzz import fuzz


@dataclass
class FieldWeight:
    field_name: str
    weight: float  # relative importance, 0.0-1.0, weights across fields should sum to ~1.0


@dataclass
class MatchCandidate:
    record_id: str
    confidence: float  # 0.0-1.0
    matched_fields: dict[str, float]  # field_name -> per-field similarity score


DEFAULT_WEIGHTS = [
    FieldWeight(field_name="display_name", weight=0.5),
    FieldWeight(field_name="external_reference", weight=0.3),
    FieldWeight(field_name="contact_detail", weight=0.2),
]


def _normalise(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def _field_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Token-sort ratio handles word-order and minor formatting drift, e.g.
    'Acme Ltd' vs 'ACME Limited' vs 'Ltd, Acme'."""
    a_norm, b_norm = _normalise(a), _normalise(b)
    if not a_norm or not b_norm:
        return 0.0
    return fuzz.token_sort_ratio(a_norm, b_norm) / 100.0


def find_candidates(
    new_record: dict[str, Any],
    existing_records: list[dict[str, Any]],
    *,
    weights: list[FieldWeight] = DEFAULT_WEIGHTS,
    threshold: float = 0.72,
) -> list[MatchCandidate]:
    """
    Compare a proposed new record against existing records across multiple weighted
    fields. Returns candidates at or above `threshold`, ranked highest-confidence first.

    threshold is deliberately a parameter, not a constant: a "create contact" tool
    and a "create supplier" tool might warrant different tolerance for false positives
    vs. false negatives depending on how costly a missed duplicate is in each case.
    """
    candidates: list[MatchCandidate] = []

    for existing in existing_records:
        matched_fields: dict[str, float] = {}
        weighted_sum = 0.0
        weight_total = 0.0

        for fw in weights:
            new_value = new_record.get(fw.field_name)
            existing_value = existing.get(fw.field_name)
            if new_value is None and existing_value is None:
                continue  # field absent on both sides — don't penalise or reward

            similarity = _field_similarity(new_value, existing_value)
            matched_fields[fw.field_name] = round(similarity, 3)
            weighted_sum += similarity * fw.weight
            weight_total += fw.weight

        if weight_total == 0:
            continue

        confidence = weighted_sum / weight_total
        if confidence >= threshold:
            candidates.append(
                MatchCandidate(
                    record_id=existing.get("record_id", "unknown"),
                    confidence=round(confidence, 3),
                    matched_fields=matched_fields,
                )
            )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates