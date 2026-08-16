from __future__ import annotations


def test_parallel_search_responses_are_identical(page) -> None:
    payload = {
        "venue": 12,
        "date_from": "2025-01-01",
        "date_to": "2025-03-31",
        "bet": {"type": "sanrentan", "first": 1, "second": 2, "third": 3},
    }
    responses = page.evaluate(
        """
        async ({payload}) => Promise.all(
          Array.from({length: 8}, async () => {
            const response = await fetch('/api/search', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(payload),
            });
            return {status: response.status, body: await response.json()};
          })
        )
        """,
        {"payload": payload},
    )
    assert {item["status"] for item in responses} == {200}
    assert all(item["body"] == responses[0]["body"] for item in responses[1:])


def test_saved_strategy_match_counts_equal_search_on_completed_date(page) -> None:
    conditions = {
        "venue": 12,
        "race_no": {"min": 7, "max": 12},
        "boats": {"1": {"motor_rate2": {"min": 35}}},
        "compare": [{"metric": "age", "boat": 1, "op": "le", "other": 2, "margin": 0}],
        "bet": {"type": "tansho", "first": 1},
    }
    result = page.evaluate(
        """
        async ({conditions}) => {
          const saved = await fetch('/api/strategies', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: 'round3-completed', conditions}),
          }).then(r => r.json());
          const matches = await fetch(`/api/strategies/${saved.id}/matches?date=2025-01-02`).then(r => r.json());
          const search = await fetch('/api/search', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({...conditions, date_from: '2025-01-02', date_to: '2025-01-02', fast: true}),
          }).then(r => r.json());
          return {matches, search};
        }
        """,
        {"conditions": conditions},
    )
    assert result["matches"]["counts"]["matched"] == result["search"]["n"] == 3
    assert result["matches"]["counts"]["pending"] == result["search"]["excluded"]["condition_null"] == 0


def test_pending_match_count_equals_step2_condition_null_count(page) -> None:
    conditions = {
        "venue": 12,
        "race_no": {"min": 7, "max": 12},
        "boats": {"1": {"motor_rate2": {"min": 35}, "ex_rank": {"max": 3}}},
        "compare": [{"metric": "ex_time", "boat": 1, "op": "le", "other": 2, "margin": 0}],
        "bet": {"type": "tansho", "first": 1},
    }
    result = page.evaluate(
        """
        async ({conditions}) => {
          const saved = await fetch('/api/strategies', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: 'round3-pending', conditions}),
          }).then(r => r.json());
          const matches = await fetch(`/api/strategies/${saved.id}/matches?date=2026-08-16`).then(r => r.json());
          const search = await fetch('/api/search', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({...conditions, date_from: '2026-08-16', date_to: '2026-08-16', fast: true}),
          }).then(r => r.json());
          return {matches, search};
        }
        """,
        {"conditions": conditions},
    )
    matched = result["matches"]["counts"]["matched"]
    pending = result["matches"]["counts"]["pending"]
    assert matched == result["search"]["n"]
    assert pending == result["search"]["excluded"]["condition_null"]
