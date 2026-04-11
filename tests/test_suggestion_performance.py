from statistics import mean

from scripts.benchmark_suggestion_actions import run_benchmarks


def test_suggestion_accept_stays_under_500ms_average():
    result = run_benchmarks(iterations=15)

    accept_mean_ms = mean(result["accept_ms"])
    dismiss_mean_ms = mean(result["dismiss_ms"])

    assert accept_mean_ms < 500, f"accept mean was {accept_mean_ms:.2f}ms"
    assert dismiss_mean_ms < 500, f"dismiss mean was {dismiss_mean_ms:.2f}ms"
