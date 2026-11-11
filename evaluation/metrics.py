"""Retrieval / impact metrics. Pure functions; nothing here invents numbers."""
from __future__ import annotations


def matches(item: dict, expected: dict) -> bool:
    """item: {path, symbol, start_line, end_line}; expected: {path, symbol?|start?,end?}."""
    if item["path"] != expected["path"]:
        return False
    if "symbol" in expected:
        return item["symbol"].split(" (")[0] == expected["symbol"]
    if "start" in expected:
        return item["start_line"] <= expected.get("end", expected["start"]) and item["end_line"] >= expected["start"]
    return True


def recall_at_k(ranked: list[dict], expected: list[dict], k: int) -> float:
    if not expected:
        return float("nan")
    hit = sum(1 for e in expected if any(matches(i, e) for i in ranked[:k]))
    return hit / len(expected)


def reciprocal_rank(ranked: list[dict], expected: list[dict]) -> float:
    for n, i in enumerate(ranked, 1):
        if any(matches(i, e) for e in expected):
            return 1.0 / n
    return 0.0


def precision_recall(predicted: set[str], expected: set[str]) -> tuple[float, float]:
    tp = len(predicted & expected)
    return (tp / len(predicted) if predicted else 0.0, tp / len(expected) if expected else float("nan"))


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))]


def mean(values: list[float]) -> float:
    vals = [v for v in values if v == v]
    return sum(vals) / len(vals) if vals else float("nan")
