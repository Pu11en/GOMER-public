"""Pure correction-target and evidence-decision rules."""

SAFETY_DIMENSIONS = {
    "current-medications", "treatment-phase", "kidney-function", "liver-function",
    "allergies", "prior-reactions", "immune-status", "source-population",
}
REQUIRED_LANES = {
    "systematic", "human", "population", "safety", "contradiction", "citations", "identity",
}
BAD_DIRECTIONS = {
    "bun": "up", "creatinine": "up", "phosphorus": "up", "potassium": "either",
    "sodium": "either", "calcium": "either", "glucose": "up", "rdw": "up",
    "hemoglobin": "down", "hematocrit": "down", "platelet": "down",
    "neutrophil": "down", "white blood": "down", "egfr": "down",
}
GROUP_PRIORITY = {
    "kidney": 5, "blood-counts": 5, "metabolic": 4, "liver": 4,
    "coagulation": 4, "response-markers": 2, "other": 1,
}


def _bad_direction(label, direction):
    name = str(label or "").casefold()
    expected = next((value for term, value in BAD_DIRECTIONS.items() if term in name), None)
    return expected == "either" or expected == direction


def select_correction_targets(packet, limit=3):
    findings = {item.get("label"): item for item in packet.get("chart_findings", [])}
    candidates = {item.get("label"): item for item in packet.get("repeated_result_candidates", [])}
    rows = []
    for label, finding in findings.items():
        candidate = candidates.get(label)
        flag = finding.get("flag", "")
        repeated_bad = candidate and _bad_direction(label, candidate.get("direction"))
        if not flag and not repeated_bad:
            continue
        group = candidate.get("group", "other") if candidate else "other"
        score = GROUP_PRIORITY.get(group, 1) + (4 if candidate else 0) + (3 if flag else 0)
        rows.append({
            "target_id": "", "label": label, "group": group,
            "value": finding.get("value"), "unit": finding.get("unit", ""),
            "date": finding.get("date"), "flag": flag,
            "direction": candidate.get("direction", finding.get("trend", "")) if candidate else finding.get("trend", ""),
            "reason": candidate.get("reason", "Current result is outside the recorded range.") if candidate else "Current result is outside the recorded range.",
            "source_ids": list(dict.fromkeys(
                (candidate.get("source_ids", []) if candidate else []) + [finding.get("source_id")]
            )),
            "score": score,
        })
    rows.sort(key=lambda item: (-item["score"], item["label"]))
    selected = rows if limit is None else rows[:limit]
    for index, item in enumerate(selected, 1):
        item["target_id"] = f"correct-{index}"
    return selected


def coverage_status(ledger):
    lanes = ledger.get("lanes", {})
    missing = [f"lane:{name}" for name in sorted(REQUIRED_LANES) if lanes.get(name) != "complete"]
    checks = ledger.get("safety_checks", {})
    missing.extend(
        f"safe:{name}" for name in sorted(SAFETY_DIMENSIONS)
        if checks.get(name) not in {"pass", "missing", "not-relevant"}
    )
    if int(ledger.get("saturation_passes", 0)) < 2:
        missing.append("saturation:two no-new-evidence passes")
    return {"complete": not missing, "missing": missing}


def classify_option(option, coverage):
    if option.get("harm") or option.get("strong_no_benefit") or option.get("patient_mismatch"):
        return "DON'T"
    if not coverage.get("complete") or option.get("conflict") or option.get("missing_safety"):
        return "CHECK FIRST"
    if option.get("human_similar_population") and option.get("all_fit_checks_pass"):
        return "DO NOW"
    if option.get("human_other_cancer_population"):
        return "CHECK FIRST"
    return "EXCLUDE"
