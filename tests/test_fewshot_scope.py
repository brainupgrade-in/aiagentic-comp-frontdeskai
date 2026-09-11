# The few-shot store is per-employee.
#
# One employee's question text must never be retrievable into another employee's prompt:
# the stored document is text the employee wrote, so an unscoped read is a prompt-injection
# channel between accounts.

from unittest.mock import MagicMock, patch

import fewshot


def test_retrieval_is_scoped_to_the_asking_employee():
    col = MagicMock()
    col.count.return_value = 1          # non-zero, or retrieve_examples returns before querying
    col.query.return_value = {
        "documents": [["how do I claim a damaged laptop?"]],
        "metadatas": [[{"answer": "Equipment Damage, with a photo.", "category": "finance",
                        "confidence": 9, "email": "bob@example.com"}]],
        "distances": [[0.2]],
    }
    with patch.object(fewshot, "_get_collection", return_value=col):
        out = fewshot.retrieve_examples("laptop damage", "finance", "bob@example.com")
    where = col.query.call_args.kwargs["where"]
    assert {"email": "bob@example.com"} in where["$and"], (
        "retrieval must filter on the employee asking, not on category alone"
    )
    assert {"category": "finance"} in where["$and"]
    assert out and out[0]["question"] == "how do I claim a damaged laptop?"


def test_stored_example_records_its_owner():
    col = MagicMock()
    with patch.object(fewshot, "_get_collection", return_value=col):
        fewshot.add_example("fewshot_1", "q", "a", "finance", 9, "mallory@example.com")
    meta = col.upsert.call_args.kwargs["metadatas"][0]
    assert meta["email"] == "mallory@example.com", (
        "an example with no recorded owner cannot be scoped on read"
    )
