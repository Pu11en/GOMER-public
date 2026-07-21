"""Deterministic contracts and matching for the GOMER Correction Engine."""

import re
import unicodedata
from datetime import date

EVIDENCE_LEVELS = (
    "human-same-population",
    "human-other-cancer",
    "human-general",
    "human-theory",
)
FIT_DIMENSIONS = {
    "current-medications", "treatment-phase", "kidney-function", "liver-function",
    "allergies", "prior-reactions", "immune-status", "source-population",
}
VISIBLE_PROOF_FIELDS = {
    "action.item", "action.form", "action.amount", "action.timing",
    "action.frequency", "action.duration", "action.preparation",
    "expected.direction", "mechanism", "population", "treatment_context", "safety",
}
PATIENT_ACTION_KINDS = {"food", "supplement", "activity", "meal-timing", "exposure"}
CLINICIAN_ACTION_KINDS = {"medication", "fluid", "test", "treatment"}
ALLOWED_KINDS = PATIENT_ACTION_KINDS | CLINICIAN_ACTION_KINDS
ALLOWED_CONTROLS = {"patient", "clinician"}
CLINICIAN_LANGUAGE = re.compile(
    r"\b(?:prescription|medic(?:ation|ine)|drug|dose|intravenous|iv|infusion|"
    r"laboratory|lab test|blood test|urine test|scan|imaging|radiation|chemotherapy|surgery)\b",
    re.I,
)
ALLOWED_OPS = {
    "present", "none-contain", "any-contain", "number-gte", "number-lte",
    "not-relevant", "evidence-at-least",
}


def normalize_key(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _field(recipe, path):
    value = recipe
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _quotes_valid(field, value, refs, source_by_id):
    if not isinstance(refs, list) or not refs:
        return f"missing proof: {field}"
    quoted = []
    for ref in refs:
        source = source_by_id.get(ref.get("source_id"), {}) if isinstance(ref, dict) else {}
        quote = str(ref.get("quote", "")).strip() if isinstance(ref, dict) else ""
        body = " ".join((
            str(source.get("title", "")),
            str(source.get("excerpt", "")),
            str(source.get("content", "")),
        ))
        if not source:
            return f"unknown proof source: {field}"
        if not quote or quote.casefold() not in body.casefold():
            return f"quote absent from source: {field}"
        quoted.append(quote)
    if field != "safety" and str(value).casefold() not in " ".join(quoted).casefold():
        return f"proof does not support field: {field}"
    return None


def validate_recipe(recipe, source_by_id):
    errors = []
    if recipe.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not normalize_key(recipe.get("id")):
        errors.append("recipe id is required")
    if not recipe.get("target_keys") or any(
        not normalize_key(value) for value in recipe.get("target_keys", [])
    ):
        errors.append("target_keys are required")
    action = recipe.get("action", {})
    if action.get("kind") not in ALLOWED_KINDS:
        errors.append("action kind is invalid")
    if action.get("control") not in ALLOWED_CONTROLS:
        errors.append("action control is invalid")
    if action.get("kind") in CLINICIAN_ACTION_KINDS and action.get("control") != "clinician":
        errors.append("clinician control is required")
    if action.get("control") == "patient" and CLINICIAN_LANGUAGE.search(" ".join(str(value) for value in action.values())):
        errors.append("clinical action language requires clinician control")
    if recipe.get("evidence_level") not in EVIDENCE_LEVELS:
        errors.append("evidence_level is invalid")
    try:
        reviewed = date.fromisoformat(str(recipe.get("reviewed_at", ""))[:10])
        expires = date.fromisoformat(str(recipe.get("expires_at", ""))[:10])
        if expires <= reviewed:
            errors.append("recipe expiration must follow review")
    except ValueError:
        errors.append("recipe review and expiration dates are invalid")
    for score in ("impact_score", "practicality_score"):
        if recipe.get(score) not in range(1, 6):
            errors.append(f"{score} must be 1 through 5")
    proofs = recipe.get("proofs", {})
    proof_fields = set(VISIBLE_PROOF_FIELDS)
    for optional in ("expected.magnitude", "expected.time_to_effect"):
        if str(_field(recipe, optional) or "").strip():
            proof_fields.add(optional)
    for field in sorted(proof_fields):
        value = _field(recipe, field)
        if field != "safety" and not str(value or "").strip():
            errors.append(f"missing recipe field: {field}")
            continue
        error = _quotes_valid(field, value, proofs.get(field), source_by_id)
        if error:
            errors.append(error)
    dimensions = set()
    for constraint in recipe.get("constraints", []):
        if not isinstance(constraint, dict):
            errors.append("constraint must be an object")
            continue
        dimension = constraint.get("dimension")
        # ponytail: one rule per dimension; add compound rules when a real recipe proves the need.
        if dimension in dimensions:
            errors.append(f"duplicate fit dimension: {dimension}")
        dimensions.add(dimension)
        op = constraint.get("op")
        if op not in ALLOWED_OPS:
            errors.append(f"invalid fit operation: {dimension}")
        elif op in {"none-contain", "any-contain"} and not constraint.get("values"):
            errors.append(f"{op} requires values: {dimension}")
        elif op in {"number-gte", "number-lte"} and (
            not constraint.get("lab") or not isinstance(constraint.get("value"), (int, float))
        ):
            errors.append(f"{op} requires a lab and number: {dimension}")
        elif op == "evidence-at-least" and constraint.get("value") not in EVIDENCE_LEVELS:
            errors.append(f"invalid evidence threshold: {dimension}")
    for dimension in sorted(FIT_DIMENSIONS - dimensions):
        errors.append(f"missing fit dimension: {dimension}")
    fit_claims = recipe.get("fit_claims", {})
    for dimension in sorted(FIT_DIMENSIONS):
        field = f"fit.{dimension}"
        claim = fit_claims.get(dimension)
        if not str(claim or "").strip():
            errors.append(f"missing fit claim: {dimension}")
            continue
        error = _quotes_valid(field, claim, proofs.get(field), source_by_id)
        if error:
            errors.append(error)
    if recipe.get("evidence_level") == "human-theory":
        if not proofs.get("mechanism"):
            errors.append("theory recipe lacks mechanism proof")
        if not proofs.get("action.amount"):
            errors.append("theory recipe lacks amount proof")
        if not proofs.get("safety"):
            errors.append("theory recipe lacks safety proof")
    return list(dict.fromkeys(errors))


def detect_correction_targets(packet):
    from research_coverage import select_correction_targets
    return select_correction_targets(packet, limit=None)


def _number(value):
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else None


def build_case_profile(packet):
    profile = {dimension: [] for dimension in FIT_DIMENSIONS - {"source-population"}}
    profile["labs"] = {}
    for fact in packet.get("context_facts", []):
        lane = fact.get("lane")
        if lane not in profile:
            continue
        profile[lane].append({
            "label": fact.get("label", ""),
            "value": fact.get("value", ""),
            "number": _number(fact.get("value")),
            "source_id": fact.get("source_id", ""),
        })
    for finding in packet.get("chart_findings", []):
        key = normalize_key(finding.get("label"))
        number = finding.get("numeric_value")
        if number is None:
            number = _number(finding.get("value"))
        profile["labs"].setdefault(key, []).append({
            "label": finding.get("label", ""),
            "value": finding.get("value", ""),
            "number": number,
            "source_id": finding.get("source_id", ""),
        })
    return profile


EVIDENCE_RANK = {level: index for index, level in enumerate(reversed(EVIDENCE_LEVELS), 1)}


def _facts_for(constraint, profile):
    dimension = constraint["dimension"]
    if dimension == "source-population":
        return []
    if constraint.get("lab"):
        return profile.get("labs", {}).get(normalize_key(constraint["lab"]), [])
    return profile.get(dimension, [])


def _contains_term(text, value):
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", text))


def _evaluate_constraint(constraint, profile, evidence_level):
    op = constraint["op"]
    facts = _facts_for(constraint, profile)
    if op == "not-relevant":
        return "not-relevant", []
    if op == "evidence-at-least":
        required = EVIDENCE_RANK.get(constraint.get("value"), 99)
        actual = EVIDENCE_RANK.get(evidence_level, 0)
        return ("match" if actual >= required else "mismatch"), []
    if not facts:
        return "missing", []
    text = " ".join(normalize_key(item.get("value")) for item in facts)
    values = [normalize_key(value) for value in constraint.get("values", [])]
    if op == "present":
        state = "match"
    elif op == "none-contain":
        state = "match" if not any(_contains_term(text, value) for value in values) else "mismatch"
    elif op == "any-contain":
        state = "match" if any(_contains_term(text, value) for value in values) else "mismatch"
    elif op in {"number-gte", "number-lte"}:
        numbers = [item.get("number") for item in facts if item.get("number") is not None]
        if not numbers:
            state = "missing"
        elif op == "number-gte":
            state = "match" if max(numbers) >= float(constraint["value"]) else "mismatch"
        else:
            state = "match" if min(numbers) <= float(constraint["value"]) else "mismatch"
    else:
        state = "mismatch"
    return state, [item.get("source_id") for item in facts if item.get("source_id")]


def match_recipe(recipe, profile):
    dimensions = {}
    for constraint in recipe.get("constraints", []):
        state, source_ids = _evaluate_constraint(
            constraint, profile, recipe.get("evidence_level"),
        )
        dimension = constraint["dimension"]
        dimensions[dimension] = {
            "state": state,
            "claim": recipe.get("fit_claims", {}).get(dimension, ""),
            "source_ids": source_ids,
        }
    qualified = set(dimensions) == FIT_DIMENSIONS and all(
        item["state"] in {"match", "not-relevant"}
        for item in dimensions.values()
    )
    return {"qualified": qualified, "dimensions": dimensions}


def _challenge_error(recipe, source_by_id):
    challenge = recipe.get("challenge", {})
    if challenge.get("status") != "passed":
        return "challenge failed"
    if not challenge.get("queries"):
        return "challenge has no query"
    if not challenge.get("source_ids"):
        return "challenge has no sources"
    if any(source_id not in source_by_id for source_id in challenge["source_ids"]):
        return "challenge has unknown source"
    if challenge.get("contradictions"):
        return "challenge has unresolved contradiction"
    conclusion = str(challenge.get("conclusion", "")).strip()
    if not conclusion:
        return "challenge has no conclusion"
    return _quotes_valid(
        "challenge", conclusion, challenge.get("evidence"), source_by_id,
    )


def _rank_components(recipe, target_count):
    return {
        "impact": recipe["impact_score"],
        "evidence": EVIDENCE_RANK[recipe["evidence_level"]],
        "practicality": recipe["practicality_score"],
        "targets_helped": target_count,
    }


def _rank_tuple(components):
    return (
        components["impact"],
        components["evidence"],
        components["targets_helped"],
        components["practicality"],
    )


def _action_text(recipe):
    action = recipe["action"]
    return f"Use {action['amount']} of {action['item']} {action['frequency']} {action['timing']}."


def build_correction_result(packet, recipes):
    targets = detect_correction_targets(packet)
    source_by_id = {
        item.get("id"): item for item in packet.get("sources", []) if item.get("id")
    }
    profile = build_case_profile(packet)
    actions, rejections, covered = [], [], set()
    for recipe in recipes:
        errors = validate_recipe(recipe, source_by_id)
        if errors:
            rejections.append({
                "id": recipe.get("id", ""),
                "reason": "; ".join(errors),
            })
            continue
        challenge_error = _challenge_error(recipe, source_by_id)
        if challenge_error:
            rejections.append({"id": recipe["id"], "reason": challenge_error})
            continue
        fit = match_recipe(recipe, profile)
        if not fit["qualified"]:
            rejections.append({
                "id": recipe["id"],
                "reason": "Case mismatch",
                "fit": fit,
            })
            continue
        recipe_keys = {normalize_key(value) for value in recipe["target_keys"]}
        matched = [
            item for item in targets if normalize_key(item["label"]) in recipe_keys
        ]
        if not matched:
            continue
        target_ids = [item["target_id"] for item in matched]
        covered.update(target_ids)
        components = _rank_components(recipe, len(target_ids))
        actions.append({
            "id": recipe["id"],
            "target_ids": target_ids,
            "target_labels": [item["label"] for item in matched],
            "do_this": _action_text(recipe),
            "control": recipe["action"]["control"],
            "kind": recipe["action"]["kind"],
            "item": recipe["action"]["item"],
            "form": recipe["action"]["form"],
            "amount": recipe["action"]["amount"],
            "timing": recipe["action"]["timing"],
            "frequency": recipe["action"]["frequency"],
            "duration": recipe["action"]["duration"],
            "preparation": recipe["action"]["preparation"],
            "expected": recipe["expected"],
            "mechanism": recipe["mechanism"],
            "population": recipe["population"],
            "treatment_context": recipe["treatment_context"],
            "why": {
                "target_source_ids": list(dict.fromkeys(
                    source_id
                    for item in matched
                    for source_id in item.get("source_ids", [])
                )),
                "fit_source_ids": list(dict.fromkeys(
                    source_id
                    for item in fit["dimensions"].values()
                    for source_id in item["source_ids"]
                )),
            },
            "evidence_level": recipe["evidence_level"],
            "proofs": recipe["proofs"],
            "challenge": recipe["challenge"],
            "fit": fit,
            "rank_components": components,
        })
    actions.sort(
        key=lambda item: (_rank_tuple(item["rank_components"]), item["id"]),
        reverse=True,
    )
    missing = [
        item["target_id"] for item in targets if item["target_id"] not in covered
    ]
    return {
        "schema_version": 1,
        "complete": not missing,
        "actions": actions,
        "missing_target_ids": missing,
        "rejections": rejections,
    }
