"""Explainable small-series trend rules; no claim of clinical diagnosis."""

UNIT_ALIASES = {
    "K/uL": "10^9/L",
    "10*3/uL": "10^9/L",
    "x10^3/uL": "10^9/L",
    "thousand/uL": "10^9/L",
}


def normalize_points(rows):
    points = []
    for row in rows:
        if row.get("numeric_value") is None or not row.get("date"):
            continue
        item = dict(row)
        item["unit"] = UNIT_ALIASES.get(item.get("unit"), item.get("unit"))
        points.append(item)
    return sorted(points, key=lambda row: row["date"])


def analyze_trend(rows):
    points = normalize_points(rows)
    units = {row.get("unit") for row in points if row.get("unit")}
    if len(units) > 1:
        return {
            "classification": "incomparable", "direction": "unknown",
            "reason": "Units differ and were not normalized.", "points": points,
            "crossed_reference": False, "missing": ["unit normalization"],
        }
    if len(points) < 3:
        return {
            "classification": "insufficient", "direction": "unknown",
            "reason": "At least three comparable results are required.", "points": points,
            "crossed_reference": False, "missing": ["more results"],
        }

    values = [float(row["numeric_value"]) for row in points]
    deltas = [right - left for left, right in zip(values, values[1:])]
    downward = sum(delta < 0 for delta in deltas)
    upward = sum(delta > 0 for delta in deltas)
    direction = "down" if downward == len(deltas) else "up" if upward == len(deltas) else "mixed"
    first, last = points[0], points[-1]
    low = last.get("reference_low")
    high = last.get("reference_high")
    crossed = bool(
        (low is not None and values[0] >= float(low) > values[-1])
        or (high is not None and values[0] <= float(high) < values[-1])
    )
    relative = abs(values[-1] - values[0]) / max(abs(values[0]), 1e-9)
    meaningful = direction in {"up", "down"} and (crossed or relative >= 0.20)
    if crossed:
        reason = "Repeated movement crossed a reference boundary."
    elif direction in {"up", "down"}:
        reason = f"Repeated movement changed {relative:.0%} from baseline."
    else:
        reason = "Results did not move repeatedly in one direction."
    return {
        "classification": "meaningful" if meaningful else "possible",
        "direction": direction,
        "reason": reason,
        "points": points,
        "crossed_reference": crossed,
        "missing": [] if low is not None or high is not None else ["reference range"],
    }
