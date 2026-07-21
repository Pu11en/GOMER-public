"""Validated clinical Answer Packets and deterministic presentation helpers."""
import json
import re

REQUIRED_FIELDS = {
    "title", "direct_answer", "as_of_date", "chart_findings", "timeline_events",
    "possible_explanations", "medical_options", "patient_actions", "monitoring_options",
    "nutrition_options", "unlikely_or_unsupported_options", "urgent_signs",
    "doctor_questions", "missing_evidence", "conflicts", "claims", "sources",
    "deliverable_metadata",
}
LIST_FIELDS = REQUIRED_FIELDS - {"title", "direct_answer", "as_of_date", "deliverable_metadata"}
ALLOWED_FRAMING = {"ask-care-team", "abstain"}
ALLOWED_SOURCE_CLASSES = {"Chart", "Corpus", "Narrative", "Notes", "Web", "Visit"}
OBJECT_FIELDS = {
    "chart_findings": {"label", "value", "date", "source_id"},
    "timeline_events": {"date", "label", "source_id"},
    "possible_explanations": {"label", "fit", "uncertainty", "source_ids"},
    "medical_options": {"option", "why", "fit", "framing", "source_ids"},
    "patient_actions": {"action", "why", "when", "evidence"},
    "nutrition_options": {"option", "limits", "source_ids"},
    "urgent_signs": {"signs", "action", "evidence"},
    "claims": {"text", "source_ids", "status"},
    "sources": {"id", "source_class", "title", "organization", "published", "url", "evidence_grade"},
}
STRING_LIST_FIELDS = LIST_FIELDS - set(OBJECT_FIELDS)
PATIENT_SOURCE_CLASSES = {"Chart", "Narrative", "Notes", "Visit"}
EXTERNAL_SOURCE_CLASSES = {"Corpus", "Web"}
VAGUE_ACTION = re.compile(
    r"\b(?:monitor closely|seek care if concerned|follow up|ask (?:your )?(?:doctor|care team)|as needed)\b",
    re.I,
)


def _evidence_blocks(text):
    matches = list(re.finditer(r"\[source:([^\]]+)\]", text))
    return {
        match.group(1): text[match.end():(matches[index + 1].start() if index + 1 < len(matches) else len(text))]
        for index, match in enumerate(matches)
    }


def _evidence_refs(item):
    return item.get("evidence", []) if isinstance(item, dict) else []


def parse_packet(text):
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
    candidate = fenced.group(1) if fenced else text[text.find("{"):text.rfind("}") + 1]
    if not candidate:
        raise ValueError("No Answer Packet JSON found")
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("Answer Packet must be an object")
    return value


def validate_packet(packet, evidence_text=""):
    if not isinstance(packet, dict):
        return ["Answer Packet must be an object"]

    errors = []
    errors.extend(f"missing field: {name}" for name in sorted(REQUIRED_FIELDS - set(packet)))
    for name in sorted(LIST_FIELDS):
        if name in packet and not isinstance(packet[name], list):
            errors.append(f"{name} must be a list")
    if not isinstance(packet.get("deliverable_metadata"), dict):
        errors.append("deliverable_metadata must be an object")
    if not isinstance(packet.get("direct_answer"), str) or not packet.get("direct_answer", "").strip():
        errors.append("direct_answer must be non-empty")
    for field in STRING_LIST_FIELDS:
        items = packet.get(field) if isinstance(packet.get(field), list) else []
        if any(not isinstance(item, str) for item in items):
            errors.append(f"{field} items must be strings")
    for field, required in OBJECT_FIELDS.items():
        items = packet.get(field) if isinstance(packet.get(field), list) else []
        for item in items:
            if not isinstance(item, dict):
                errors.append(f"{field} item must be an object")
                continue
            missing_item_fields = required - set(item)
            if missing_item_fields:
                errors.append(f"{field} item missing: {','.join(sorted(missing_item_fields))}")
            if "source_ids" in item and not isinstance(item["source_ids"], list):
                errors.append(f"{field} source_ids must be a list")

    sources = packet.get("sources") if isinstance(packet.get("sources"), list) else []
    valid_sources = [source for source in sources if isinstance(source, dict)]
    source_by_id = {source.get("id"): source for source in valid_sources if source.get("id")}
    source_ids = set(source_by_id)
    evidence_blocks = _evidence_blocks(evidence_text)
    evidence_ids = set(evidence_blocks)
    for source in sources:
        if not isinstance(source, dict):
            errors.append("source must be an object")
            continue
        source_id = source.get("id")
        if not source_id:
            errors.append("source id must be non-empty")
        elif evidence_text and source_id not in evidence_ids:
            errors.append(f"source id not present in evidence: {source_id}")
        if source.get("source_class") not in ALLOWED_SOURCE_CLASSES:
            errors.append(f"invalid source class: {source.get('source_class')}")
        url = source.get("url", "")
        if evidence_text and url and url not in evidence_text:
            errors.append(f"source url not present in evidence: {url}")

    referenced_ids = []
    for field in ("claims", "medical_options", "nutrition_options", "possible_explanations"):
        items = packet.get(field) if isinstance(packet.get(field), list) else []
        for item in items:
            if isinstance(item, dict):
                referenced_ids.extend(item.get("source_ids", []))
    for field in ("patient_actions", "urgent_signs"):
        items = packet.get(field) if isinstance(packet.get(field), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            refs = _evidence_refs(item)
            if not refs:
                errors.append(f"{field} evidence must be non-empty")
                continue
            if any(not isinstance(ref, dict) or not {"source_id", "quote"} <= set(ref) for ref in refs):
                errors.append(f"{field} evidence items require source_id and quote")
                continue
            ref_ids = [ref["source_id"] for ref in refs]
            referenced_ids.extend(ref_ids)
            classes = {source_by_id.get(source_id, {}).get("source_class") for source_id in ref_ids}
            external_refs = [
                source_by_id.get(source_id, {}) for source_id in ref_ids
                if source_by_id.get(source_id, {}).get("source_class") in EXTERNAL_SOURCE_CLASSES
            ]
            trusted_external = any(
                source.get("evidence_grade") not in {"", "indirect", "snippet", "unknown"}
                for source in external_refs
            )
            if field == "patient_actions":
                if not classes & PATIENT_SOURCE_CLASSES:
                    errors.append("patient action requires patient evidence")
                if not external_refs:
                    errors.append("patient action requires external evidence")
                elif not trusted_external:
                    errors.append("patient action requires trusted external evidence")
                if VAGUE_ACTION.search(item.get("action", "")):
                    errors.append("vague patient action")
            elif not external_refs:
                errors.append("urgent sign requires external evidence")
            elif not trusted_external:
                errors.append("urgent sign requires trusted external evidence")
            for ref in refs:
                source_id, quote = ref["source_id"], str(ref["quote"]).strip()
                block = evidence_blocks.get(source_id, "")
                source = source_by_id.get(source_id, {})
                if not quote:
                    errors.append(f"empty evidence quote: {source_id}")
                elif evidence_text and quote.casefold() not in block.casefold():
                    errors.append(f"evidence quote not present for source: {source_id}")
                if evidence_text and source.get("source_class") in EXTERNAL_SOURCE_CLASSES:
                    grade = str(source.get("evidence_grade", ""))
                    if "evidence_grade" not in block or grade.casefold() not in block.casefold():
                        errors.append(f"external evidence grade not present for source: {source_id}")
            numbers = re.findall(r"\b\d+(?:\.\d+)?\b", " ".join(str(item.get(key, "")) for key in item if key != "evidence"))
            quoted = " ".join(str(ref.get("quote", "")) for ref in refs if isinstance(ref, dict))
            for number in numbers:
                if number not in quoted:
                    errors.append(f"unsupported action threshold: {number}")
    for field in ("chart_findings", "timeline_events"):
        items = packet.get(field) if isinstance(packet.get(field), list) else []
        for item in items:
            if isinstance(item, dict) and item.get("source_id"):
                referenced_ids.append(item["source_id"])
    for source_id in referenced_ids:
        if source_id not in source_ids:
            errors.append(f"unknown source id: {source_id}")

    options = packet.get("medical_options") if isinstance(packet.get("medical_options"), list) else []
    for option in options:
        if not isinstance(option, dict):
            errors.append("medical option must be an object")
            continue
        framing = option.get("framing")
        if framing not in ALLOWED_FRAMING:
            errors.append(f"invalid medical option framing: {framing}")
    return errors


def is_report_request(message):
    return bool(re.search(
        r"\b(create|make|prepare|generate|attach|send)\b.{0,35}\b(html|report|brief|file|deliverable)\b",
        message,
        re.I | re.S,
    ))


def _lines(items, formatter=str):
    return "\n".join(f"• {formatter(item)}" for item in items if item)


def _trim_words(text, limit):
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]).rstrip(".,;:") + "…"


def _render_sections(sections):
    return "\n\n".join(f"{heading}\n{body}" for heading, body in sections if body)


def _fits(text, max_chars, max_words):
    return len(text) <= max_chars and len(text.split()) <= max_words


def render_telegram(packet, max_chars=1200, max_words=120):
    """Render only evidence-bound actions; keep clinician decisions separate."""
    cited_ids = [
        source_id
        for field in ("claims", "medical_options")
        for item in packet[field]
        for source_id in item.get("source_ids", [])
    ]
    cited_ids.extend(
        ref["source_id"]
        for field in ("patient_actions", "urgent_signs")
        for item in packet[field]
        for ref in item.get("evidence", [])
    )
    cited_ids = list(dict.fromkeys(cited_ids))
    source_numbers = {source_id: index for index, source_id in enumerate(cited_ids, 1)}

    def citation(source_ids):
        numbers = [source_numbers[source_id] for source_id in dict.fromkeys(source_ids) if source_id in source_numbers]
        return f" [{','.join(map(str, numbers))}]" if numbers else ""

    claim_ids = [source_id for claim in packet["claims"] for source_id in claim.get("source_ids", [])]
    bottom = _trim_words(packet["direct_answer"].strip(), 42) + citation(claim_ids)
    used_ids = set(claim_ids)

    actions = []
    for item in packet["patient_actions"][:3]:
        source_ids = [ref["source_id"] for ref in item["evidence"]]
        used_ids.update(source_ids)
        actions.append(_trim_words(f"{item['action']} — {item['why']} {item['when']}", 30) + citation(source_ids))

    decisions = []
    for item in packet["medical_options"][:1]:
        used_ids.update(item["source_ids"])
        label = item["option"] if re.match(r"(?i)^ask\b", item["option"]) else "Ask whether " + item["option"]
        decisions.append(_trim_words(f"{label} — {item['why']}", 28) + citation(item["source_ids"]))

    urgent = []
    for item in packet["urgent_signs"]:
        source_ids = [ref["source_id"] for ref in item["evidence"]]
        used_ids.update(source_ids)
        urgent.append(_trim_words(f"{item['signs']}: {item['action']}", 40) + citation(source_ids))

    unknown = [_trim_words(item, 24) for item in packet["missing_evidence"] + packet["conflicts"] if item][:2]

    def evidence_lines():
        return _lines([
            f"[{source_numbers[source['id']]}] "
            f"{source.get('organization') or source['source_class']} — {source['title']}"
            for source in packet["sources"] if source["id"] in used_ids
        ])

    def sections():
        value = [("Bottom line", bottom)]
        if actions:
            value.append(("What to do", _lines(actions)))
        if decisions:
            value.append(("Care-team decision", _lines(decisions)))
        if urgent:
            value.append(("Get urgent help", _lines(urgent)))
        if unknown:
            value.append(("What is still unknown", _lines(unknown)))
        evidence = evidence_lines()
        if evidence:
            value.append((f"Evidence current through {packet['as_of_date']}", evidence))
        return value

    text = _render_sections(sections())
    while decisions and not _fits(text, max_chars, max_words):
        decisions.pop()
        text = _render_sections(sections())
    while unknown and not _fits(text, max_chars, max_words):
        unknown.pop()
        text = _render_sections(sections())
    while len(actions) > 1 and not _fits(text, max_chars, max_words):
        actions.pop()
        text = _render_sections(sections())
    if not _fits(text, max_chars, max_words):
        bottom = _trim_words(packet["direct_answer"].strip(), 24) + citation(claim_ids)
        text = _render_sections(sections())
    if not _fits(text, max_chars, max_words) and not urgent:
        return text[:max_chars].rstrip() + "…"
    return text
