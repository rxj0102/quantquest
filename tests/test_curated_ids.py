"""The exact set of curated question ids, so a deleted or renamed question is caught.

When a question is added to or removed from ``data/curated/`` on purpose, update this list in the
same commit. An unintended change (a bad merge, a stray delete) fails here.
"""

from core.loader import load_questions

EXPECTED_CURATED_IDS = [
    "linalg-001", "linalg-002", "linalg-003", "linalg-004", "linalg-005", "linalg-006",
    "linalg-007", "linalg-008", "linalg-009", "linalg-010", "linalg-011", "linalg-012",
    "linalg-013", "linalg-015", "linalg-016", "linalg-017", "linalg-018", "linalg-019",
    "prob-001", "prob-002", "prob-003", "prob-004", "prob-005", "prob-006", "prob-007",
    "prob-008", "prob-009", "prob-010", "prob-011", "prob-014", "prob-015",
    "stat-001", "stat-002", "stat-003", "stat-004", "stat-005", "stat-006",
    "stat-007", "stat-008", "stat-009", "stat-010", "stat-011", "stat-012",
]  # fmt: skip


def test_the_curated_pool_is_exactly_the_expected_ids() -> None:
    ids = sorted(q.id for q in load_questions())
    missing = sorted(set(EXPECTED_CURATED_IDS) - set(ids))
    unexpected = sorted(set(ids) - set(EXPECTED_CURATED_IDS))
    assert not missing, f"curated questions deleted or renamed: {missing}"
    assert not unexpected, f"curated questions added without updating this list: {unexpected}"
    assert ids == sorted(EXPECTED_CURATED_IDS)
