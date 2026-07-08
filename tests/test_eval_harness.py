"""CI regression tests over the eval harness (mock mode — no LLM).

Asserts the 10-case regression subset passes tool-selection scoring at 100%,
and that every adversarial case's write attempt is refused by run_sql inside
the eval pipeline itself.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.run_evals import (REGRESSION_IDS, load_golden, mock_agent,
                             score_tool_selection)


def test_regression_subset_has_ten_cases_all_categories():
    cases = load_golden("regression")
    assert len(cases) == 10
    assert {c["id"] for c in cases} == set(REGRESSION_IDS)
    assert sum(1 for c in cases if c.get("expect_refusal")) == 3


def test_regression_subset_tool_selection_100_percent():
    for case in load_golden("regression"):
        result = mock_agent(case)
        passed, note = score_tool_selection(case, result["tool_calls"])
        assert passed, f"{case['id']}: {note}"


def test_adversarial_write_attempts_are_refused_in_pipeline():
    adversarial = [c for c in load_golden(None) if c.get("expect_refusal")]
    assert len(adversarial) == 3
    for case in adversarial:
        result = mock_agent(case)  # internally asserts run_sql refused the write
        sql_calls = [c for c in result["tool_calls"] if c["name"] == "run_sql"]
        assert sql_calls and all("error" in c["result"] for c in sql_calls)
        assert "read-only" in result["answer"]


def test_scorer_fails_when_expected_tool_missing():
    case = {"id": "x", "category": "easy_sql", "question": "q",
            "reference_answer": "a", "expected_tools": ["run_sql"]}
    passed, note = score_tool_selection(case, [])
    assert not passed and "missing" in note


def test_scorer_fails_if_write_query_somehow_succeeded():
    case = {"id": "x", "category": "adversarial", "question": "q",
            "reference_answer": "a", "expected_tools": [], "expect_refusal": True}
    forged = [{"name": "run_sql", "input": {"query": "DELETE FROM trucks"},
               "result": {"rows": [], "columns": [], "row_count": 0, "truncated": False}}]
    passed, note = score_tool_selection(case, forged)
    assert not passed and "without refusal" in note
