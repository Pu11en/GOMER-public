"""Deterministic facts and proof gates for a manual Signal & Story Guide."""

import json
import re
from datetime import date, timedelta

from trends import UNIT_ALIASES, analyze_trend
from correction_rules import classify_option, coverage_status, select_correction_targets

ELIGIBLE_STATUSES = {"final", "amended", "corrected"}
PATIENT_SOURCE_CLASSES = {"Chart", "Narrative", "Notes", "Visit"}
EXTERNAL_SOURCE_CLASSES = {"Corpus", "Web"}
WEAK_EVIDENCE_GRADES = {"", "indirect", "snippet", "unknown"}
APPLICABILITY_DIMENSIONS = {
    "current-medications", "treatment-phase", "kidney-function", "liver-function",
    "allergies", "prior-reactions", "immune-status", "source-population",
}
DIAGNOSIS_LANGUAGE = re.compile(r"\b(?:you have|this proves|this means you have|diagnosed with)\b", re.I)
GROUP_TERMS = {
    "blood-counts": ("hemoglobin", "hematocrit", "platelet", "white blood", "wbc", "neutrophil", "anc", "lymphocyte", "alc"),
    "kidney": ("creatinine", "egfr", "bun", "urea"),
    "liver": ("ast", "alt", "bilirubin", "albumin", "alkaline phosphatase"),
    "response-markers": ("kappa", "lambda", "m-spike", "m protein", "immunofixation", "igg", "iga", "igm", "beta-2 microglobulin"),
    "metabolic": ("calcium", "glucose", "sodium", "potassium", "magnesium", "phosphorus"),
    "coagulation": ("inr", "prothrombin", "ptt", "fibrinogen"),
}
GROUP_EVIDENCE_TERMS = {
    "blood-counts": ("cytopen", "neutroph", "platelet", "hemoglobin", "blood count"),
    "kidney": ("kidney", "renal", "creatinine", "egfr"),
    "liver": ("liver", "hepatic", "bilirubin", "transaminase"),
    "response-markers": ("response", "light chain", "m-protein", "immunofix", "immunoglobulin", "marrow"),
    "metabolic": ("calcium", "electrolyte", "glucose", "metabolic"),
    "coagulation": ("coagulation", "bleeding", "inr", "fibrinogen"),
    "other": ("laboratory", "result"),
}
TREATMENT_TERMS = ("car-t", "car t", "cell therapy", "ciltacabtagene", "idecabtagene")


def classify_result_group(name):
    normalized = str(name or "").casefold().replace("-", " ")

    def matches(term):
        return bool(re.search(rf"\b{re.escape(term)}\b", normalized)) if len(term) <= 3 else term in normalized

    return next(
        (group for group, terms in GROUP_TERMS.items() if any(matches(term) for term in terms)),
        "other",
    )


def evidence_query(group):
    label = group.replace("-", " ")
    return f"post CAR-T {label} interpretation recovery patient guidance"


def assess_group_evidence(group, hits):
    terms = GROUP_EVIDENCE_TERMS.get(group, GROUP_EVIDENCE_TERMS["other"])
    for hit in hits:
        text = f"{hit.get('title', '')} {hit.get('content', '')}".casefold()
        if (
            (hit.get("trust_tier") or 3) <= 2
            and any(term in text for term in terms)
            and any(term in text for term in TREATMENT_TERMS)
        ):
            source_id = str(hit.get("id"))
            source_id = source_id if source_id.startswith("corpus-") else f"corpus-{source_id}"
            return {
                "group": group, "kind": "external-evidence", "status": "answered",
                "reason": "Direct trusted post-CAR-T evidence is available.",
                "source_ids": [source_id], "publication_date_missing": not bool(hit.get("published")),
            }
    return {
        "group": group, "kind": "external-evidence", "status": "research-needed",
        "reason": "No direct trusted post-CAR-T source answers this result group.",
        "source_ids": [], "publication_date_missing": False,
    }


def build_repeated_result_candidates(rows, results_date, treatment_dates=None):
    """Find meaningful three-point patterns ending on the selected results day."""
    end = date.fromisoformat(results_date)
    anchors = []
    for value in treatment_dates or []:
        try:
            anchor = date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
        if anchor <= end:
            anchors.append(anchor)
    start = max(anchors) if anchors else end - timedelta(days=90)
    eligible = []
    for row in rows:
        row_date = _date(row)
        if (
            row.get("status") not in ELIGIBLE_STATUSES
            or row.get("numeric_value") is None
            or not row_date
            or not start.isoformat() <= row_date <= results_date
        ):
            continue
        eligible.append({**row, "date": row_date, "unit": UNIT_ALIASES.get(row.get("unit"), row.get("unit"))})

    series = {}
    for row in eligible:
        series.setdefault((_identity(row), row.get("unit")), []).append(row)

    singles = []
    for values in series.values():
        values.sort(key=_date)
        if len(values) < 3 or _date(values[-1]) != results_date:
            continue
        analysis = analyze_trend(values)
        if analysis["classification"] != "meaningful":
            continue
        singles.append({
            "kind": "single",
            "group": classify_result_group(values[-1].get("name")),
            "label": values[-1].get("name") or "Laboratory result",
            "direction": analysis["direction"],
            "reason": analysis["reason"],
            "points": [
                {
                    "label": row.get("name") or "Laboratory result",
                    "value": str(row["numeric_value"]), "unit": row.get("unit") or "",
                    "date": _date(row), "source_id": row.get("resource_id"),
                }
                for row in values if row.get("resource_id")
            ],
            "source_ids": [row.get("resource_id") for row in values if row.get("resource_id")],
        })
    singles.sort(key=lambda item: (item["group"], item["label"], item["direction"]))

    grouped, used = [], set()
    group_labels = {
        "blood-counts": "Blood and immune recovery", "kidney": "Kidney function",
        "liver": "Liver function", "response-markers": "Myeloma response markers",
        "metabolic": "Metabolic and electrolyte balance", "coagulation": "Clotting",
    }
    for group in sorted(group_labels):
        for direction in ("down", "up"):
            matching = [item for item in singles if item["group"] == group and item["direction"] == direction]
            if len(matching) < 2:
                continue
            used.update(id(item) for item in matching)
            grouped.append({
                "kind": "grouped", "group": group, "label": group_labels[group],
                "direction": direction,
                "reason": f"{len(matching)} related results moved {direction} repeatedly.",
                "points": [point for item in matching for point in item["points"]],
                "source_ids": list(dict.fromkeys(
                    source_id for item in matching for source_id in item["source_ids"]
                )),
            })
    result = grouped + [item for item in singles if id(item) not in used]
    for index, item in enumerate(result, 1):
        item["id"] = f"trend-{index}"
    return result


def build_result_groups(comparisons):
    grouped = {}
    for item in comparisons:
        group = classify_result_group(item["label"])
        item["result_group"] = group
        change = None
        if item["comparison"] == "compared":
            prior = float(item["prior_value"])
            current = float(item["value"])
            change = abs(current - prior) / abs(prior) if prior else float(current != prior)
        item["material"] = bool(
            item["flag"] or item["comparison"] != "compared"
            or (change is not None and change >= 0.20)
        )
        grouped.setdefault(group, []).append(item)
    return [
        {
            "name": name,
            "finding_source_ids": [item["source_id"] for item in items],
            "material": any(item["material"] for item in items),
        }
        for name, items in grouped.items()
    ]


def _date(row):
    return str(row.get("date") or "")[:10]


def _identity(row):
    if row.get("code"):
        return (str(row.get("system") or ""), str(row["code"]))
    return (str(row.get("name") or "").casefold(),)


def _history_points(rows, current, results_date):
    points = [
        row for row in rows
        if row.get("status") in ELIGIBLE_STATUSES
        and row.get("numeric_value") is not None
        and _date(row) <= results_date
        and _identity(row) == _identity(current)
        and row.get("unit") == current.get("unit")
        and row.get("resource_id")
    ]
    points.sort(key=lambda row: (_date(row), str(row["resource_id"])))
    return [
        {
            "label": current.get("name") or "Laboratory result",
            "value": str(row["numeric_value"]),
            "numeric_value": float(row["numeric_value"]),
            "unit": row.get("unit") or "",
            "date": _date(row),
            "source_id": row["resource_id"],
        }
        for row in points
    ]


def _flag(row):
    value, low, high = row.get("numeric_value"), row.get("reference_low"), row.get("reference_high")
    if value is None:
        return ""
    if low is not None and float(value) < float(low):
        return "LOW"
    if high is not None and float(value) > float(high):
        return "HIGH"
    return ""


def _point_source(point):
    return {
        "id": point["source_id"], "source_class": "Chart",
        "title": f"{point['label']} {point['value']} {point['unit']} on {point['date']}".strip(),
        "organization": "Case Chart", "published": point["date"], "url": "",
        "evidence_grade": "patient-fact",
    }


def _chart_source(row, prior=False):
    value = row["prior_value"] if prior else row["value"]
    date = row["prior_date"] if prior else row["date"]
    source_id = row["prior_source_id"] if prior else row["source_id"]
    return {
        "id": source_id, "source_class": "Chart",
        "title": f"{row['label']} {value} {row['unit']} on {date}".strip(),
        "organization": "Case Chart", "published": date, "url": "",
        "evidence_grade": "patient-fact",
    }


def build_comparisons(rows, results_date):
    """Compare selected-date completed numeric results to their nearest valid prior."""
    eligible = [
        row for row in rows
        if row.get("status") in ELIGIBLE_STATUSES and row.get("numeric_value") is not None and _date(row)
    ]
    comparisons = []
    for current in sorted((row for row in eligible if _date(row) == results_date), key=lambda row: (str(row.get("name")), str(row.get("resource_id")))):
        identity = _identity(current)
        earlier = [row for row in eligible if _date(row) < results_date and _identity(row) == identity]
        same_unit = [row for row in earlier if row.get("unit") == current.get("unit")]
        prior = max(
            same_unit,
            key=lambda row: (str(row.get("date") or ""), str(row.get("resource_id") or "")),
            default=None,
        )
        comparison = "compared" if prior else ("cannot-compare" if earlier else "no-prior")
        before = None if prior is None else float(prior["numeric_value"])
        now = float(current["numeric_value"])
        arrow = "" if before is None else "↑" if now > before else "↓" if now < before else "→"
        comparisons.append({
            "label": current.get("name") or "Laboratory result",
            "value": str(current["numeric_value"]),
            "unit": current.get("unit") or "",
            "date": _date(current),
            "trend": arrow,
            "source_id": current.get("resource_id"),
            "prior_value": "—" if prior is None else str(prior["numeric_value"]),
            "prior_date": "" if prior is None else _date(prior),
            "prior_source_id": "" if prior is None else prior.get("resource_id"),
            "prior_unit": "" if prior is None else prior.get("unit") or "",
            "comparison": comparison,
            "reference_low": current.get("reference_low"),
            "reference_high": current.get("reference_high"),
            "flag": _flag(current),
            "status": current.get("status"),
            "identity": identity,
            "history_points": _history_points(rows, current, results_date),
        })
    return comparisons


def _context_source(source_id, title, published, excerpt, source_class="Chart"):
    return {
        "id": source_id, "source_class": source_class, "title": title,
        "organization": "Case Narrative" if source_class == "Narrative" else "Case Chart",
        "published": published or "", "url": "", "evidence_grade": "patient-fact",
        "excerpt": excerpt,
    }


def build_context_facts(narrative, medications, allergies, procedures, reports):
    facts, sources = [], []

    def add(lane, source_id, label, value, date, source_class="Chart"):
        excerpt = " ".join(str(value or "").split())
        if not source_id or not excerpt:
            return
        facts.append({
            "lane": lane, "label": label, "value": excerpt,
            "date": str(date or "")[:10], "source_id": source_id,
        })
        sources.append(_context_source(source_id, label, str(date or "")[:10], excerpt, source_class))

    for entry in narrative.get("entries", []):
        if entry.get("entry_type") in {"current-status", "treatment-timeline"}:
            add(
                "treatment-phase", f"narrative-{entry.get('id')}",
                entry.get("title") or "Treatment context", entry.get("content"),
                entry.get("entry_date"), "Narrative",
            )
    for item in medications:
        if item.get("status") in {"active", "on-hold", "unknown"}:
            value = f"{item.get('medication', 'Medication')} status {item.get('status')}"
            add("current-medications", item.get("resource_id"), item.get("medication") or "Medication", value, item.get("authored"))
    for item in allergies:
        if item.get("status") not in {"inactive", "resolved", "entered-in-error"}:
            value = f"Allergy or intolerance: {item.get('allergy', 'unknown')}"
            add("allergies", item.get("resource_id"), item.get("allergy") or "Allergy", value, "")
            add("prior-reactions", item.get("resource_id"), item.get("allergy") or "Prior reaction", value, "")
    for item in procedures[:10]:
        value = f"Procedure: {item.get('procedure', 'unknown')} ({item.get('status', 'status unknown')})"
        add("recent-procedures", item.get("resource_id"), item.get("procedure") or "Procedure", value, item.get("date"))
    for item in reports[:10]:
        value = item.get("conclusion") or item.get("report_text")
        add("diagnostic-reports", item.get("resource_id"), item.get("report") or "Diagnostic report", value, item.get("date"))
    return facts, sources


def build_lab_context(rows, results_date):
    lane_groups = {
        "kidney-function": {"kidney"},
        "liver-function": {"liver"},
        "immune-status": {"blood-counts", "response-markers"},
    }
    eligible = [
        row for row in rows
        if row.get("status") in ELIGIBLE_STATUSES and row.get("numeric_value") is not None
        and _date(row) and _date(row) <= results_date
    ]
    facts, sources = [], []
    for lane, groups in lane_groups.items():
        candidates = [row for row in eligible if classify_result_group(row.get("name")) in groups]
        latest = {}
        for row in candidates:
            identity = _identity(row)
            if identity not in latest or _date(row) > _date(latest[identity]):
                latest[identity] = row
        for row in sorted(latest.values(), key=_date, reverse=True)[:5]:
            value = f"{row.get('name', 'Laboratory result')} {row['numeric_value']} {row.get('unit') or ''} on {_date(row)}".strip()
            source_id = row.get("resource_id")
            if not source_id:
                continue
            facts.append({"lane": lane, "label": row.get("name") or "Laboratory result", "value": value, "date": _date(row), "source_id": source_id})
            sources.append(_context_source(source_id, value, _date(row), value))
    return facts, sources


def build_signal_story_packet(question, rows, results_date, context=None):
    context = context or {}
    comparisons = build_comparisons(rows, results_date)
    result_groups = build_result_groups(comparisons)
    treatment_dates = [
        entry.get("entry_date")
        for entry in context.get("narrative", {}).get("entries", [])
        if entry.get("entry_type") == "treatment-timeline" and entry.get("entry_date")
    ]
    repeated_candidates = build_repeated_result_candidates(rows, results_date, treatment_dates)
    sources = {}
    for item in comparisons:
        sources[item["source_id"]] = _chart_source(item)
        if item["prior_source_id"]:
            sources[item["prior_source_id"]] = _chart_source(item, prior=True)
        for point in item["history_points"]:
            sources[point["source_id"]] = _point_source(point)
    for candidate in repeated_candidates:
        for point in candidate["points"]:
            sources[point["source_id"]] = _point_source(point)
    context_facts, context_sources = build_context_facts(
        context.get("narrative", {}), context.get("medications", []),
        context.get("allergies", []), context.get("procedures", []), context.get("reports", []),
    )
    lab_facts, lab_sources = build_lab_context(rows, results_date)
    context_facts.extend(lab_facts)
    context_sources.extend(lab_sources)
    sources.update({source["id"]: source for source in context_sources})
    missing = []
    for item in comparisons:
        if item["comparison"] == "no-prior":
            missing.append(f"No earlier comparable result for {item['label']}.")
        elif item["comparison"] == "cannot-compare":
            missing.append(f"Earlier {item['label']} result uses a different unit and was not compared.")
    available_lanes = {fact["lane"] for fact in context_facts}
    required_lanes = {
        "treatment-phase", "current-medications", "allergies", "prior-reactions",
        "kidney-function", "liver-function", "immune-status",
    }
    evidence_needs = [
        {"kind": "missing-context", "lane": lane, "status": "missing", "reason": f"Current {lane.replace('-', ' ')} is unavailable."}
        for lane in sorted(required_lanes - available_lanes)
    ]
    claims = [
        {
            "text": f"{item['label']} changed from {item['prior_value']} to {item['value']}.",
            "source_ids": [item["prior_source_id"], item["source_id"]], "status": "supported",
        }
        for item in comparisons if item["comparison"] == "compared"
    ]
    claims.extend({
        "text": f"{candidate['label']} has a repeated {candidate['direction']} trend through {results_date}.",
        "source_ids": candidate["source_ids"], "status": "supported",
    } for candidate in repeated_candidates)
    packet = {
        "title": "Signal & Story Guide",
        "direct_answer": "The selected results are compared with the nearest earlier comparable results.",
        "direct_answer_source_ids": [],
        "as_of_date": results_date,
        "chart_findings": comparisons,
        "timeline_events": [],
        "possible_explanations": [],
        "medical_options": [],
        "patient_actions": [],
        "monitoring_options": [],
        "nutrition_options": [],
        "unlikely_or_unsupported_options": [],
        "urgent_signs": [],
        "doctor_questions": [],
        "missing_evidence": missing,
        "conflicts": [],
        "claims": claims,
        "sources": list(sources.values()),
        "context_facts": context_facts,
        "result_groups": result_groups,
        "brain_leads": [],
        "brain_status": "not-checked",
        "evidence_needs": evidence_needs,
        "question_evidence": [],
        "repeated_result_candidates": repeated_candidates,
        "repeated_result_warnings": [],
        "correction_targets": [],
        "research_ledgers": [],
        "correction_dispositions": [],
        "correct_course": [],
        "deliverable_metadata": {
            "requested": True, "audience": "signal-story", "results_date": results_date,
        },
    }
    packet["correction_targets"] = select_correction_targets(packet)
    return packet


def build_correction_dispositions(packet, correction_result, promotion_ledger):
    """Give every target one source-bound terminal state without inventing a fix."""
    admitted = {
        target_id
        for action in correction_result.get("actions", [])
        for target_id in action.get("target_ids", [])
    }
    hint_by_id = {}
    for item in promotion_ledger or []:
        if not isinstance(item, dict) or not item.get("target_id"):
            continue
        hint = dict(item)
        if hint.get("disposition") not in {"grouped", "improving", "threshold-not-met", "research-open"}:
            if hint.get("status") in {"promoted", "reused"}:
                continue
            hint["disposition"] = "research-open"
        hint_by_id[hint["target_id"]] = hint
    findings = {item.get("label"): item for item in packet.get("chart_findings", [])}
    dispositions = []
    for target in packet.get("correction_targets", []):
        target_id = target.get("target_id")
        finding = findings.get(target.get("label"), {})
        if target_id in admitted:
            status, reason = "admitted", "An evidence-backed correction qualified."
        elif target_id in hint_by_id:
            hint = hint_by_id[target_id]
            status = hint["disposition"]
            reason = hint.get("reason") or f"The host classified this target as {status}."
        elif (
            finding.get("flag") == "HIGH" and finding.get("trend") == "↓"
        ) or (
            finding.get("flag") == "LOW" and finding.get("trend") == "↑"
        ):
            status, reason = "improving", "The latest value moved toward its recorded range."
        else:
            status, reason = "research-open", "No specific correction cleared every evidence and Case-fit gate."
        dispositions.append({
            "target_id": target_id,
            "label": target.get("label", ""),
            "status": status,
            "reason": reason,
            "source_ids": list(dict.fromkeys(target.get("source_ids", []))),
        })
    return dispositions


def _action_rank(action):
    parts = action.get("rank_components", {})
    return (
        int(parts.get("impact", 0)), int(parts.get("evidence", 0)),
        int(parts.get("targets_helped", 0)), int(parts.get("practicality", 0)),
        str(action.get("id", "")),
    )


def _finding_change_score(finding):
    try:
        current, prior = float(finding.get("value")), float(finding.get("prior_value"))
    except (TypeError, ValueError):
        return 0
    return abs(current - prior) / abs(prior) if prior else float(current != prior)


def _goal_direction(finding):
    if finding.get("flag") == "HIGH":
        return "down"
    if finding.get("flag") == "LOW":
        return "up"
    return {"↑": "down", "↓": "up"}.get(finding.get("trend"), "change")


def _deterministic_success(finding, direction):
    current, prior = finding.get("value"), finding.get("prior_value")
    if not current or not prior or prior == "—" or direction not in {"up", "down"}:
        return {"text": "Show improvement on the next test.", "source_ids": [finding.get("source_id")]}
    comparator = "above" if direction == "up" else "below"
    return {
        "text": f"{finding.get('label', 'The result')} {comparator} {current} on the next test, then moving toward {prior}.",
        "source_ids": list(filter(None, [finding.get("source_id"), finding.get("prior_source_id")])),
    }


def assemble_correct_course_issues(packet, correction_result, promotion_ledger):
    """Project admitted Actions into stable, host-owned report issues."""
    actions = [action for action in correction_result.get("actions", []) if action.get("target_ids")]
    components = []
    for action in actions:
        target_ids = set(action["target_ids"])
        joined = [item for item in components if item["target_ids"] & target_ids]
        if not joined:
            components.append({"target_ids": target_ids, "actions": [action]})
            continue
        merged = {"target_ids": set(target_ids), "actions": [action]}
        for item in joined:
            merged["target_ids"].update(item["target_ids"])
            merged["actions"].extend(item["actions"])
            components.remove(item)
        components.append(merged)

    target_order = {
        target.get("target_id"): index
        for index, target in enumerate(packet.get("correction_targets", []))
    }
    target_by_id = {
        target.get("target_id"): target for target in packet.get("correction_targets", [])
    }
    finding_by_label = {
        finding.get("label"): finding for finding in packet.get("chart_findings", [])
    }
    issues = []
    for component in sorted(components, key=lambda item: min(target_order.get(value, 10**6) for value in item["target_ids"])):
        target_ids = sorted(component["target_ids"], key=lambda value: target_order.get(value, 10**6))
        findings = [
            finding_by_label.get(target_by_id.get(target_id, {}).get("label"))
            for target_id in target_ids
        ]
        findings = [finding for finding in findings if finding]
        if not findings:
            continue
        primary = max(findings, key=lambda item: (_finding_change_score(item), item.get("label", "")))
        ranked = sorted(component["actions"], key=_action_rank, reverse=True)
        direction = _goal_direction(primary)
        source_ids = list(dict.fromkeys(
            source_id
            for action in ranked
            for source_id in (
                list(action.get("why", {}).get("target_source_ids", []))
                + list(action.get("why", {}).get("fit_source_ids", []))
                + [
                    ref.get("source_id")
                    for refs in action.get("proofs", {}).values()
                    for ref in (refs if isinstance(refs, list) else [])
                    if isinstance(ref, dict)
                ]
                + list(action.get("challenge", {}).get("source_ids", []))
            )
            if source_id
        ))
        finding_source_ids = list(dict.fromkeys(
            source_id
            for target_id in target_ids
            for source_id in target_by_id.get(target_id, {}).get("source_ids", [])
            if source_id
        ))
        item = ranked[0].get("item") or "the supported correction"
        title = f"{primary.get('label', 'This result')} needs to go {direction}"
        issues.append({
            "schema_version": 1,
            "issue_id": f"issue-{len(issues) + 1}",
            "target_ids": target_ids,
            "finding_source_ids": finding_source_ids,
            "primary_finding": primary,
            "history_points": primary.get("history_points", []),
            "goal_direction": direction,
            "success": _deterministic_success(primary, direction),
            "primary_action": ranked[0],
            "steps": ranked[1:],
            "source_ids": source_ids,
            "evidence_current_through": packet.get("as_of_date", ""),
            "copy": {
                "title": title,
                "term_explanation": f"{primary.get('label', 'This')} is the medical name for the result shown here.",
                "thesis_title": f"{item} is the strongest supported correction",
                "reasons": [
                    {"label": "Case fit", "text": target_by_id[target_ids[0]].get("reason", "The current result needs correction.")},
                    {"label": "Human evidence", "text": ranked[0].get("mechanism", "Human evidence supports the correction.")},
                ],
                "uncertainty": "The evidence does not prove this is the only cause.",
                "complete_idea": f"Use the supported correction and confirm improvement with the next {primary.get('label', 'result')} test.",
            },
        })
    return issues


def _issue_allowed_numbers(issue):
    finding = issue.get("primary_finding", {})
    values = [
        finding.get(field) for field in (
            "value", "date", "prior_value", "prior_date", "reference_low", "reference_high",
        )
    ]
    values.extend(
        point.get(field) for point in issue.get("history_points", []) for field in ("date", "value")
    )
    values.append(issue.get("success", {}).get("text"))
    for action in [issue.get("primary_action", {})] + list(issue.get("steps", [])):
        values.extend(action.get(field) for field in (
            "do_this", "amount", "timing", "frequency", "duration", "preparation",
        ))
        values.extend(action.get("expected", {}).values())
    values.extend(issue.get("copy_number_allowlist", []))
    return {
        number for value in values if value not in (None, "")
        for number in re.findall(r"\b\d+(?:\.\d+)?\b", str(value))
    }


def apply_correct_course_copy(packet, copy_result, evidence_text=""):
    """Merge only safe wording into locked issues; invalid copy leaves host text intact."""
    issue_by_id = {
        issue.get("issue_id"): issue for issue in packet.get("correct_course_issues", [])
        if isinstance(issue, dict) and issue.get("issue_id")
    }
    for candidate in copy_result.get("issues", []) if isinstance(copy_result, dict) else []:
        if not isinstance(candidate, dict) or candidate.get("issue_id") not in issue_by_id:
            continue
        issue = issue_by_id[candidate["issue_id"]]
        required = (
            "title", "term_explanation", "thesis_title", "reason_labels",
            "reason_texts", "uncertainty", "complete_idea",
        )
        if any(not candidate.get(field) for field in required):
            continue
        labels, texts = candidate["reason_labels"], candidate["reason_texts"]
        if not isinstance(labels, list) or not isinstance(texts, list) or not 1 <= len(labels) == len(texts) <= 3:
            continue
        visible = " ".join(
            str(candidate[field]) for field in required if field not in {"reason_labels", "reason_texts"}
        ) + " " + " ".join(map(str, labels + texts))
        if DIAGNOSIS_LANGUAGE.search(visible) or re.search(r"\b(?:her|hers|she|his|him|he)\b", visible, re.I):
            continue
        if any(number not in _issue_allowed_numbers(issue) for number in re.findall(r"\b\d+(?:\.\d+)?\b", visible)):
            continue
        issue["copy"] = {
            "title": candidate["title"],
            "term_explanation": candidate["term_explanation"],
            "thesis_title": candidate["thesis_title"],
            "reasons": [
                {"label": label, "text": text} for label, text in zip(labels, texts)
            ],
            "uncertainty": candidate["uncertainty"],
            "complete_idea": candidate["complete_idea"],
        }
    return packet


def _trusted_external(source):
    return (
        source.get("source_class") in EXTERNAL_SOURCE_CLASSES
        and str(source.get("evidence_grade", "")).casefold() not in WEAK_EVIDENCE_GRADES
    )


def sanitize_signal_story_reasoning(packet, evidence_text=""):
    """Remove unsupported model reasoning while retaining a useful, honest Guide."""
    source_by_id = {
        source.get("id"): source for source in packet.get("sources", [])
        if isinstance(source, dict) and source.get("id")
    }

    def supported(source_ids):
        classes = {source_by_id.get(source_id, {}).get("source_class") for source_id in source_ids}
        return bool(classes & PATIENT_SOURCE_CLASSES) and any(
            _trusted_external(source_by_id.get(source_id, {})) for source_id in source_ids
        )

    group_needs = {
        need.get("group"): need.get("status")
        for need in packet.get("evidence_needs", [])
        if need.get("kind") == "external-evidence" and need.get("group")
    }
    missing = list(packet.get("missing_evidence", []))
    kept_explanations = []
    for item in packet.get("possible_explanations", []):
        if not isinstance(item, dict):
            continue
        group = str(item.get("group") or "").casefold().replace(" ", "-")
        item["group"] = group
        complete = all(isinstance(item.get(field), list) and item[field] for field in ("supporting", "against", "missing"))
        text = " ".join(str(item.get(field, "")) for field in ("label", "uncertainty", "supporting", "against", "missing"))
        if group_needs.get(group) == "research-needed" or not complete or not supported(item.get("source_ids", [])) or DIAGNOSIS_LANGUAGE.search(text):
            missing.append(f"{group.replace('-', ' ').title()} interpretation remains unknown because directly relevant trusted evidence is unavailable.")
            continue
        kept_explanations.append(item)
    packet["possible_explanations"] = kept_explanations

    explained_groups = {item["group"] for item in kept_explanations}
    material_groups = {group.get("name") for group in packet.get("result_groups", []) if group.get("material")}
    unexplained_groups = material_groups - explained_groups
    for group in sorted(unexplained_groups):
        missing.append(f"{group.replace('-', ' ').title()} interpretation remains unknown because directly relevant trusted evidence is unavailable.")

    question_evidence = {
        item.get("question"): item for item in packet.get("question_evidence", [])
        if isinstance(item, dict) and item.get("question")
    }
    kept_questions, kept_question_evidence = [], []
    for question in packet.get("doctor_questions", []):
        item = question_evidence.get(question)
        if item and supported(item.get("source_ids", [])):
            kept_questions.append(question)
            kept_question_evidence.append(item)
        else:
            missing.append("A care-team question was withheld because it lacked both current patient and directly relevant trusted external evidence.")
    packet["doctor_questions"] = kept_questions
    packet["question_evidence"] = kept_question_evidence

    kept_urgent = []
    for item in packet.get("urgent_signs", []):
        source_ids = [ref.get("source_id") for ref in item.get("evidence", []) if isinstance(ref, dict)]
        if supported(source_ids):
            kept_urgent.append(item)
        else:
            missing.append("An urgent line was withheld because its patient and trusted external evidence was incomplete.")
    packet["urgent_signs"] = kept_urgent

    kept_actions = []
    for item in packet.get("patient_actions", []):
        applicability = item.get("applicability", []) if isinstance(item, dict) else []
        dimensions = {entry.get("dimension") for entry in applicability if isinstance(entry, dict)}
        source_ids = [ref.get("source_id") for ref in item.get("evidence", []) if isinstance(ref, dict)]
        complete = dimensions == APPLICABILITY_DIMENSIONS and all(
            entry.get("state") in {"match", "not-relevant"}
            and entry.get("reason") and entry.get("source_ids")
            for entry in applicability
        )
        if complete and supported(source_ids):
            kept_actions.append(item)
        else:
            missing.append("A direct action was withheld because its evidence or patient-fit checks were incomplete.")
    packet["patient_actions"] = kept_actions

    candidate_sources = {
        item.get("id"): set(item.get("source_ids", []))
        for item in packet.get("repeated_result_candidates", []) if isinstance(item, dict)
    }
    kept_warnings = []
    for item in packet.get("repeated_result_warnings", []):
        applicability = item.get("applicability", []) if isinstance(item, dict) else []
        dimensions = {entry.get("dimension") for entry in applicability if isinstance(entry, dict)}
        source_ids = [ref.get("source_id") for ref in item.get("evidence", []) if isinstance(ref, dict)]
        patient_ids = {
            source_id for source_id in source_ids
            if source_by_id.get(source_id, {}).get("source_class") in PATIENT_SOURCE_CLASSES
        }
        complete = (
            item.get("candidate_id") in candidate_sources
            and item.get("action_kind") in {"DO", "DON'T", "GET HELP NOW"}
            and all(item.get(field) for field in ("headline", "why", "action"))
            and dimensions == APPLICABILITY_DIMENSIONS
            and all(
                entry.get("state") in {"match", "not-relevant"}
                and entry.get("reason") and entry.get("source_ids")
                for entry in applicability
            )
            and bool(patient_ids & candidate_sources.get(item.get("candidate_id"), set()))
            and supported(source_ids)
        )
        text = " ".join(str(item.get(field, "")) for field in ("headline", "why", "action"))
        quoted = " ".join(
            str(ref.get("quote", "")) for ref in item.get("evidence", []) if isinstance(ref, dict)
        )
        numbers_supported = all(number in quoted for number in re.findall(r"\b\d+(?:\.\d+)?\b", text))
        if (
            complete and numbers_supported and not DIAGNOSIS_LANGUAGE.search(text)
            and not re.search(r"\bask (?:your )?(?:doctor|care team)\b", text, re.I)
        ):
            kept_warnings.append(item)
    packet["repeated_result_warnings"] = kept_warnings

    target_by_id = {
        item.get("target_id"): item for item in packet.get("correction_targets", [])
        if isinstance(item, dict) and item.get("target_id")
    }
    ledger_by_id = {
        item.get("target_id"): item for item in packet.get("research_ledgers", [])
        if isinstance(item, dict) and item.get("target_id")
    }
    kept_courses = []
    for course in packet.get("correct_course", [])[:3]:
        if not isinstance(course, dict) or course.get("target_id") not in target_by_id:
            continue
        target = target_by_id[course["target_id"]]
        coverage = coverage_status(ledger_by_id.get(course["target_id"], {}))
        categorized = {"do_now": [], "check_first": [], "dont": []}
        actions = [action for key in categorized for action in course.get(key, [])]
        for action in actions:
            if not isinstance(action, dict):
                continue
            applicability = action.get("applicability", [])
            dimensions = {entry.get("dimension") for entry in applicability if isinstance(entry, dict)}
            source_ids = [ref.get("source_id") for ref in action.get("evidence", []) if isinstance(ref, dict)]
            patient_ids = {
                source_id for source_id in source_ids
                if source_by_id.get(source_id, {}).get("source_class") in PATIENT_SOURCE_CLASSES
            }
            exact_quotes = all(
                isinstance(ref, dict) and ref.get("source_id") in source_by_id
                and str(ref.get("quote", "")).strip()
                and str(ref.get("quote", "")).casefold() in " ".join((
                    str(source_by_id[ref["source_id"]].get("title", "")),
                    str(source_by_id[ref["source_id"]].get("excerpt", "")),
                )).casefold()
                for ref in action.get("evidence", [])
            ) if evidence_text else True
            complete = (
                dimensions == APPLICABILITY_DIMENSIONS
                and all(
                    entry.get("state") in {"match", "missing", "not-relevant"}
                    and entry.get("reason") and entry.get("source_ids")
                    for entry in applicability
                )
                and bool(patient_ids & set(target.get("source_ids", [])))
                and supported(source_ids) and exact_quotes
            )
            decision = classify_option(action.get("evidence_profile", {}), coverage)
            if not complete or decision == "EXCLUDE":
                continue
            action["decision"] = decision
            key = {"DO NOW": "do_now", "CHECK FIRST": "check_first", "DON'T": "dont"}[decision]
            categorized[key].append(action)
        total = sum(len(items) for items in categorized.values())
        if not total:
            missing.append(f"No evidence-backed correction action qualified for {target.get('label', 'this result')}.")
            continue
        kept = {**course, **categorized}
        kept["source_ids"] = list(dict.fromkeys(
            source_id for items in categorized.values() for action in items
            for source_id in [ref.get("source_id") for ref in action.get("evidence", []) if isinstance(ref, dict)]
        ))
        kept_courses.append(kept)
    packet["correct_course"] = kept_courses

    kept_options = []
    for item in packet.get("medical_options", []):
        if isinstance(item, dict) and supported(item.get("source_ids", [])):
            kept_options.append(item)
        else:
            missing.append("A care-team option was withheld because it lacked patient and directly relevant trusted external evidence.")
    packet["medical_options"] = kept_options

    direct_ids = packet.get("direct_answer_source_ids", [])
    if not supported(direct_ids) or unexplained_groups:
        patient_ids = [
            item.get("source_id") for item in packet.get("chart_findings", [])
            if item.get("source_id") in source_by_id
        ]
        packet["direct_answer"] = (
            "The selected results contain material changes, but directly relevant trusted evidence "
            "is not sufficient to interpret every result group safely."
        )
        packet["direct_answer_source_ids"] = list(dict.fromkeys(patient_ids))[:8]
    packet["missing_evidence"] = list(dict.fromkeys(missing))
    return packet


def validate_correct_course_issues(packet):
    errors = []
    source_by_id = {
        source.get("id"): source for source in packet.get("sources", [])
        if isinstance(source, dict) and source.get("id")
    }
    source_ids = set(source_by_id)
    target_ids = {
        target.get("target_id") for target in packet.get("correction_targets", [])
        if isinstance(target, dict) and target.get("target_id")
    }
    dispositions = {
        item.get("target_id"): item for item in packet.get("correction_dispositions", [])
        if isinstance(item, dict) and item.get("target_id")
    }
    seen = set()
    seen_issue_targets = set()
    for issue in packet.get("correct_course_issues", []):
        if not isinstance(issue, dict):
            errors.append("Correct course issue must be an object")
            continue
        issue_id = issue.get("issue_id")
        if not issue_id or issue_id in seen:
            errors.append("Correct course issue IDs must be unique")
        seen.add(issue_id)
        issue_targets = set(issue.get("target_ids", []))
        if not issue_targets or not issue_targets <= target_ids:
            errors.append(f"Correct course issue has unknown targets: {issue_id}")
        if seen_issue_targets & issue_targets:
            errors.append(f"Correct course target appears in more than one issue: {issue_id}")
        seen_issue_targets.update(issue_targets)
        if issue.get("primary_finding", {}).get("source_id") not in issue.get("finding_source_ids", []):
            errors.append(f"Correct course primary finding is not linked to the issue: {issue_id}")
        for target_id in issue.get("target_ids", []):
            if dispositions.get(target_id, {}).get("status") not in {"admitted", "grouped"}:
                errors.append(f"Correct course issue target is not admitted: {target_id}")
        if not issue.get("history_points"):
            errors.append(f"Correct course issue lacks graph history: {issue_id}")
        for point in issue.get("history_points", []):
            if point.get("source_id") not in source_ids:
                errors.append(f"Correct course graph has unknown source: {point.get('source_id')}")
        success = issue.get("success", {})
        if not success.get("text") or not success.get("source_ids"):
            errors.append(f"Correct course issue lacks a source-bound success target: {issue_id}")
        elif not set(success["source_ids"]) <= source_ids:
            errors.append(f"Correct course success has unknown source: {issue_id}")
        actions = [issue.get("primary_action")] + list(issue.get("steps", []))
        for action in actions:
            if not isinstance(action, dict):
                errors.append(f"Correct course issue has invalid Action: {issue_id}")
                continue
            if not set(action.get("target_ids", [])) <= issue_targets:
                errors.append(f"Correct course Action is linked to another issue: {action.get('id')}")
            if action.get("kind") in {"medication", "fluid", "test", "treatment"} and action.get("control") != "clinician":
                errors.append(f"Correct course clinical Action lacks clinician control: {action.get('id')}")
        copy = issue.get("copy", {})
        visible = " ".join(
            str(value) for value in copy.values() if not isinstance(value, list)
        ) + " " + " ".join(
            str(value) for reason in copy.get("reasons", []) if isinstance(reason, dict)
            for value in (reason.get("label", ""), reason.get("text", ""))
        )
        if DIAGNOSIS_LANGUAGE.search(visible):
            errors.append(f"Correct course issue copy uses diagnosis language: {issue_id}")
        if re.search(r"\b(?:her|hers|she|his|him|he)\b", visible, re.I):
            errors.append(f"Correct course issue copy uses gendered patient language: {issue_id}")
        allowlist_text = " ".join(
            str(source_by_id.get(source_id, {}).get(field, ""))
            for source_id in issue.get("source_ids", [])
            for field in ("title", "excerpt")
        )
        for number in map(str, issue.get("copy_number_allowlist", [])):
            if number not in re.findall(r"\b\d+(?:\.\d+)?\b", allowlist_text):
                errors.append(f"Correct course copy number allowlist lacks source proof: {number}")
        allowed_numbers = _issue_allowed_numbers(issue)
        for number in re.findall(r"\b\d+(?:\.\d+)?\b", visible):
            if number not in allowed_numbers:
                errors.append(f"Correct course issue copy has unsupported number: {number}")
    if set(dispositions) != target_ids:
        errors.append("Every correction target needs one terminal disposition")
    return list(dict.fromkeys(errors))


def validate_signal_story_reasoning(packet, evidence_text=""):
    if "correct_course_issues" in packet:
        return validate_correct_course_issues(packet)
    errors = []
    source_by_id = {
        source.get("id"): source for source in packet.get("sources", [])
        if isinstance(source, dict) and source.get("id")
    }

    direct_answer = str(packet.get("direct_answer", ""))
    direct_ids = packet.get("direct_answer_source_ids", [])
    direct_classes = {source_by_id.get(source_id, {}).get("source_class") for source_id in direct_ids}
    unknown_bottom_line = "trusted evidence is not sufficient" in direct_answer.casefold()
    if not direct_classes & PATIENT_SOURCE_CLASSES:
        errors.append("Signal & Story bottom line requires current patient evidence")
    if not unknown_bottom_line and not any(
        _trusted_external(source_by_id.get(source_id, {})) for source_id in direct_ids
    ):
        errors.append("Signal & Story bottom line requires trusted external evidence")
    if DIAGNOSIS_LANGUAGE.search(direct_answer):
        errors.append("Signal & Story bottom line uses diagnosis language")
    if evidence_text:
        for number in re.findall(r"\b\d+(?:\.\d+)?\b", direct_answer):
            if number not in evidence_text:
                errors.append(f"Signal & Story bottom line has unsupported number: {number}")
    explanations_by_group = {}
    reasoning_text = [direct_answer]
    for item in packet.get("possible_explanations", []):
        if not isinstance(item, dict):
            continue
        group = item.get("group")
        explanations_by_group.setdefault(group, []).append(item)
        missing = {
            "group", "label", "fit", "uncertainty", "supporting", "against", "missing", "source_ids",
        } - set(item)
        if missing:
            errors.append(f"Signal & Story explanation missing: {','.join(sorted(missing))}")
            continue
        if not all(isinstance(item[field], list) and item[field] for field in ("supporting", "against", "missing")):
            errors.append(f"Signal & Story explanation needs supporting, contrary, and missing evidence: {item.get('label', '')}")
        source_ids = item.get("source_ids", [])
        classes = {source_by_id.get(source_id, {}).get("source_class") for source_id in source_ids}
        if not classes & PATIENT_SOURCE_CLASSES:
            errors.append(f"Signal & Story explanation requires current patient evidence: {item.get('label', '')}")
        if not any(_trusted_external(source_by_id.get(source_id, {})) for source_id in source_ids):
            errors.append(f"Signal & Story explanation requires trusted external evidence: {item.get('label', '')}")
        text = " ".join(str(item.get(field, "")) for field in ("label", "uncertainty", "supporting", "against", "missing"))
        reasoning_text.append(text)
        if DIAGNOSIS_LANGUAGE.search(text):
            errors.append(f"Signal & Story explanation uses diagnosis language: {item.get('label', '')}")

    for group, items in explanations_by_group.items():
        if group and len(items) > 3:
            errors.append(f"Signal & Story has more than three explanations for {group}")
    unknown_text = " ".join(str(item).casefold() for item in packet.get("missing_evidence", []))
    for group in packet.get("result_groups", []):
        name = group.get("name", "")
        if group.get("material") and not explanations_by_group.get(name):
            readable = name.replace("-", " ").casefold()
            if readable not in unknown_text:
                errors.append(f"Signal & Story material group lacks an explanation or named unknown: {name}")

    for action in packet.get("patient_actions", []):
        applicability = action.get("applicability", []) if isinstance(action, dict) else []
        dimensions = {item.get("dimension") for item in applicability if isinstance(item, dict)}
        if dimensions != APPLICABILITY_DIMENSIONS:
            errors.append("Signal & Story patient action requires all eight applicability dimensions")
            continue
        for item in applicability:
            if item.get("state") not in {"match", "not-relevant"}:
                errors.append(f"Signal & Story patient action has unresolved applicability: {item.get('dimension')}")
            if not item.get("reason") or not item.get("source_ids"):
                errors.append(f"Signal & Story applicability lacks reason or evidence: {item.get('dimension')}")
            for source_id in item.get("source_ids", []):
                if source_id not in source_by_id:
                    errors.append(f"Signal & Story applicability has unknown source: {source_id}")

    candidate_by_id = {
        item.get("id"): item for item in packet.get("repeated_result_candidates", [])
        if isinstance(item, dict) and item.get("id")
    }
    evidence_blocks = {}
    if evidence_text:
        matches = list(re.finditer(r"\[source:([^\]]+)\]", evidence_text))
        evidence_blocks = {
            match.group(1): evidence_text[match.end():(matches[index + 1].start() if index + 1 < len(matches) else len(evidence_text))]
            for index, match in enumerate(matches)
        }
    for warning in packet.get("repeated_result_warnings", []):
        if not isinstance(warning, dict):
            errors.append("repeated-result warning must be an object")
            continue
        required = {"candidate_id", "headline", "why", "action_kind", "action", "evidence", "applicability"}
        missing = required - set(warning)
        if missing:
            errors.append(f"repeated-result warning missing: {','.join(sorted(missing))}")
            continue
        candidate = candidate_by_id.get(warning.get("candidate_id"))
        if not candidate:
            errors.append("repeated-result warning has unknown candidate")
        if warning.get("action_kind") not in {"DO", "DON'T", "GET HELP NOW"}:
            errors.append("repeated-result warning has invalid action kind")
        if not all(str(warning.get(field, "")).strip() for field in ("headline", "why", "action")):
            errors.append("repeated-result warning has empty visible text")
        applicability = warning.get("applicability", [])
        dimensions = {item.get("dimension") for item in applicability if isinstance(item, dict)}
        if dimensions != APPLICABILITY_DIMENSIONS:
            errors.append("repeated-result warning requires all eight applicability dimensions")
        for item in (applicability if isinstance(applicability, list) else []):
            if not isinstance(item, dict) or item.get("state") not in {"match", "not-relevant"}:
                errors.append("repeated-result warning has unresolved applicability")
                continue
            if not item.get("reason") or not item.get("source_ids"):
                errors.append("repeated-result warning applicability lacks reason or evidence")
            for source_id in item.get("source_ids", []):
                if source_id not in source_by_id:
                    errors.append(f"repeated-result warning applicability has unknown source: {source_id}")
        refs = warning.get("evidence", [])
        if not isinstance(refs, list) or any(
            not isinstance(ref, dict) or not {"source_id", "quote"} <= set(ref) for ref in refs
        ):
            errors.append("repeated-result warning evidence requires source_id and quote")
            refs = []
        source_ids = [ref["source_id"] for ref in refs]
        patient_ids = {
            source_id for source_id in source_ids
            if source_by_id.get(source_id, {}).get("source_class") in PATIENT_SOURCE_CLASSES
        }
        if not patient_ids:
            errors.append("repeated-result warning requires current patient evidence")
        elif candidate and not patient_ids & set(candidate.get("source_ids", [])):
            errors.append("repeated-result warning patient evidence does not prove its candidate")
        if not any(_trusted_external(source_by_id.get(source_id, {})) for source_id in source_ids):
            errors.append("repeated-result warning requires trusted external evidence")
        quoted = " ".join(str(ref.get("quote", "")) for ref in refs)
        for ref in refs:
            source_id, quote = ref.get("source_id"), str(ref.get("quote", "")).strip()
            if source_id not in source_by_id:
                errors.append(f"repeated-result warning has unknown source: {source_id}")
            if not quote:
                errors.append(f"repeated-result warning has empty quote: {source_id}")
            elif evidence_text and quote.casefold() not in evidence_blocks.get(source_id, "").casefold():
                errors.append(f"repeated-result warning quote is absent from source: {source_id}")
        visible = " ".join(str(warning.get(field, "")) for field in ("headline", "why", "action"))
        reasoning_text.append(visible)
        if DIAGNOSIS_LANGUAGE.search(visible):
            errors.append("repeated-result warning uses diagnosis language")
        if re.search(r"\bask (?:your )?(?:doctor|care team)\b", visible, re.I):
            errors.append("repeated-result warning asks a doctor instead of giving an action")
        for number in re.findall(r"\b\d+(?:\.\d+)?\b", visible):
            if number not in quoted:
                errors.append(f"repeated-result warning has unsupported number: {number}")

    target_by_id = {
        item.get("target_id"): item for item in packet.get("correction_targets", [])
        if isinstance(item, dict) and item.get("target_id")
    }
    ledger_by_id = {
        item.get("target_id"): item for item in packet.get("research_ledgers", [])
        if isinstance(item, dict) and item.get("target_id")
    }
    courses = packet.get("correct_course", [])
    if len(courses) > 3:
        errors.append("Correct course has more than three targets")
    for course in courses[:3]:
        if not isinstance(course, dict):
            errors.append("Correct course target must be an object")
            continue
        target = target_by_id.get(course.get("target_id"))
        if not target:
            errors.append("Correct course has an unknown target")
            continue
        coverage = coverage_status(ledger_by_id.get(course["target_id"], {}))
        action_groups = (("do_now", "DO NOW"), ("check_first", "CHECK FIRST"), ("dont", "DON'T"))
        actions = [action for key, _ in action_groups for action in course.get(key, [])]
        if len(actions) > 3:
            errors.append("Correct course target has more than three actions")
        for key, listed_decision in action_groups:
            for action in course.get(key, []):
                if not isinstance(action, dict):
                    errors.append("Correct course action must be an object")
                    continue
                expected = classify_option(action.get("evidence_profile", {}), coverage)
                if action.get("decision") != listed_decision or action.get("decision") != expected:
                    errors.append("Correct course action does not match the host decision")
                applicability = action.get("applicability", [])
                dimensions = {item.get("dimension") for item in applicability if isinstance(item, dict)}
                if dimensions != APPLICABILITY_DIMENSIONS:
                    errors.append("Correct course action requires all eight applicability dimensions")
                for item in applicability if isinstance(applicability, list) else []:
                    if not isinstance(item, dict) or item.get("state") not in {"match", "missing", "not-relevant"}:
                        errors.append("Correct course action has invalid applicability")
                        continue
                    if not item.get("reason") or not item.get("source_ids"):
                        errors.append("Correct course applicability lacks reason or evidence")
                    for source_id in item.get("source_ids", []):
                        if source_id not in source_by_id:
                            errors.append(f"Correct course applicability has unknown source: {source_id}")
                refs = action.get("evidence", [])
                if not isinstance(refs, list) or any(
                    not isinstance(ref, dict) or not {"source_id", "quote"} <= set(ref) for ref in refs
                ):
                    errors.append("Correct course evidence requires source_id and quote")
                    refs = []
                source_ids = [ref.get("source_id") for ref in refs]
                patient_ids = {
                    source_id for source_id in source_ids
                    if source_by_id.get(source_id, {}).get("source_class") in PATIENT_SOURCE_CLASSES
                }
                if not patient_ids & set(target.get("source_ids", [])):
                    errors.append("Correct course action lacks target-specific Chart evidence")
                if not any(_trusted_external(source_by_id.get(source_id, {})) for source_id in source_ids):
                    errors.append("Correct course action lacks trusted external evidence")
                quoted = " ".join(str(ref.get("quote", "")) for ref in refs)
                for ref in refs:
                    source_id, quote = ref.get("source_id"), str(ref.get("quote", "")).strip()
                    if source_id not in source_by_id:
                        errors.append(f"Correct course action has unknown source: {source_id}")
                    elif not quote:
                        errors.append(f"Correct course action has empty quote: {source_id}")
                    elif evidence_text and quote.casefold() not in evidence_blocks.get(source_id, "").casefold():
                        errors.append(f"Correct course quote is absent from source: {source_id}")
                visible = " ".join(str(action.get(field, "")) for field in ("text", "why"))
                if DIAGNOSIS_LANGUAGE.search(visible):
                    errors.append("Correct course action uses diagnosis language")
                if key != "dont" and re.search(
                    r"\b(?:start|stop|change|adjust|increase|decrease|order|schedule)\b.{0,30}\b(?:prescription|medication|drug|dose|treatment|therapy|test|scan)\b",
                    visible, re.I,
                ):
                    errors.append("Correct course action contains a forbidden prescription, treatment, or testing change")
                for number in re.findall(r"\b\d+(?:\.\d+)?\b", visible):
                    if number not in quoted:
                        errors.append(f"Correct course action has unsupported number: {number}")

    for urgent in packet.get("urgent_signs", []):
        refs = urgent.get("evidence", []) if isinstance(urgent, dict) else []
        classes = {source_by_id.get(ref.get("source_id"), {}).get("source_class") for ref in refs if isinstance(ref, dict)}
        if not classes & PATIENT_SOURCE_CLASSES:
            errors.append("urgent line requires current patient evidence")
        if not any(_trusted_external(source_by_id.get(ref.get("source_id"), {})) for ref in refs if isinstance(ref, dict)):
            errors.append("urgent line requires trusted external evidence")

    question_evidence = {
        item.get("question"): item for item in packet.get("question_evidence", [])
        if isinstance(item, dict) and item.get("question")
    }
    for question in packet.get("doctor_questions", []):
        item = question_evidence.get(question)
        if not item:
            errors.append(f"Signal & Story care-team question lacks evidence: {question}")
            continue
        source_ids = item.get("source_ids", [])
        classes = {source_by_id.get(source_id, {}).get("source_class") for source_id in source_ids}
        if not classes & PATIENT_SOURCE_CLASSES or not any(
            _trusted_external(source_by_id.get(source_id, {})) for source_id in source_ids
        ):
            errors.append(f"Signal & Story care-team question requires patient and external evidence: {question}")

    has_three_point_trend = any(
        "trend" in str(claim.get("text", "")).casefold()
        and len([source_id for source_id in claim.get("source_ids", []) if source_by_id.get(source_id, {}).get("source_class") == "Chart"]) >= 3
        for claim in packet.get("claims", []) if isinstance(claim, dict)
    )
    if not has_three_point_trend and any(re.search(r"\btrend\b", text, re.I) for text in reasoning_text):
        errors.append("Signal & Story calls a two-point change a trend")
    return errors


def validate_comparisons(packet):
    """Return proof failures for Guide facts before a model or renderer can use them."""
    errors = []
    findings = packet.get("chart_findings", [])
    sources = {source.get("id"): source for source in packet.get("sources", [])}
    if not findings:
        errors.append("Signal & Story requires at least one completed result on the selected date")
    for item in findings:
        if not item.get("value") or not item.get("date") or not item.get("source_id"):
            errors.append("Signal & Story finding is missing a current value, date, or Chart source")
        if item.get("status") not in ELIGIBLE_STATUSES:
            errors.append(f"Signal & Story includes non-final result: {item.get('label', 'Laboratory result')}")
        if sources.get(item.get("source_id"), {}).get("source_class") != "Chart":
            errors.append(f"Signal & Story current result lacks a Chart source: {item.get('label', 'Laboratory result')}")
        comparison = item.get("comparison")
        if comparison not in {"compared", "no-prior", "cannot-compare"}:
            errors.append(f"Signal & Story has invalid comparison state: {comparison}")
        if comparison == "compared":
            if not item.get("prior_source_id") or not item.get("prior_date") or item.get("prior_value") in {None, "—"}:
                errors.append(f"Signal & Story comparison lacks a prior Chart fact: {item.get('label', 'Laboratory result')}")
            if item.get("unit") != item.get("prior_unit"):
                errors.append(f"Signal & Story comparison uses mismatched units: {item.get('label', 'Laboratory result')}")
            if sources.get(item.get("prior_source_id"), {}).get("source_class") != "Chart":
                errors.append(f"Signal & Story prior result lacks a Chart source: {item.get('label', 'Laboratory result')}")
        elif item.get("prior_value") != "—":
            errors.append(f"Signal & Story non-comparison retained a prior value: {item.get('label', 'Laboratory result')}")
    return errors
