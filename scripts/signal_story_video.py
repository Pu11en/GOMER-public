"""Create and verify the locked Video contract embedded in Signal & Story reports."""

import hashlib
import html
import json
import re
from copy import deepcopy
from datetime import date

MANIFEST_ID = "gomer-video-manifest"
SCHEMA_VERSION = 1
_MANIFEST_RE = re.compile(
    rf'<script id="{MANIFEST_ID}" type="application/json">(.*?)</script>', re.S
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:%|mg/dL|mL)?")


def _text(value):
    return " ".join(str(value or "").split())


def _spoken_date(value):
    parsed = date.fromisoformat(value)
    return f"{parsed.strftime('%B')} {parsed.day}"


def _plural(count, singular, plural=None):
    return singular if count == 1 else (plural or singular + "s")


def _finding_sentence(finding):
    label = _text(finding.get("label"))
    prior = _text(finding.get("prior_value"))
    current = _text(finding.get("value"))
    unit = _text(finding.get("unit"))
    if not prior or prior == "—":
        return f"{label} was {current} {unit}.".replace("  ", " ")
    return f"{label} moved from {prior} to {current} {unit}.".replace("  ", " ")


def _history_sentence(issue):
    points = issue.get("history_points", [])
    if len(points) < 3:
        return ""
    values = [
        f"{_text(point.get('value'))} on {_spoken_date(point['date'])}"
        for point in points
    ]
    return "The complete sequence was " + ", then ".join(values) + "."


def _source_card(source):
    organization = _text(source.get("organization"))
    title = re.sub(r"\s+-\s+PubMed$", "", _text(source.get("title")))
    title = re.split(r"\s+[—–]\s+", title, maxsplit=1)[0]
    prefix = f"{organization}: " if organization else ""
    available = max(32, 96 - len(prefix))
    if len(title) > available:
        title = title[: available - 1].rsplit(" ", 1)[0] + "…"
    published = _text(source.get("published"))
    label = prefix + title
    if published and published not in label:
        label += f" ({published[:4]})"
    return {
        "source_id": source["id"],
        "label": label,
        "url": source.get("url", ""),
    }


def _issue_segment(issue, finding_by_id, source_by_id):
    copy = issue.get("copy", {})
    findings = [
        finding_by_id[source_id]
        for source_id in issue.get("finding_source_ids", [])
        if source_id in finding_by_id
    ]
    action = issue.get("primary_action", {})
    steps = [action, *issue.get("steps", [])]
    control = (
        "CLINICIAN REVIEW"
        if any(step.get("control") == "clinician" for step in steps)
        else "SUPPORTED ACTION"
    )
    reasons = [
        f"{_text(reason.get('label'))}. {_text(reason.get('text'))}"
        for reason in copy.get("reasons", [])
    ]
    history = _history_sentence(issue)
    narration = [
        _text(copy.get("title")),
        " ".join(_finding_sentence(finding) for finding in findings),
        history,
        _text(copy.get("thesis_title")),
        " ".join(reasons),
        f"What is not proven: {_text(copy.get('uncertainty'))}",
        (
            "The report calls for clinician review."
            if control == "CLINICIAN REVIEW"
            else "The report includes a supported action."
        ),
        " ".join(_text(step.get("do_this")) for step in steps),
        f"Success means {_text(issue.get('success', {}).get('text'))}",
    ]
    required = [
        _text(copy.get("title")),
        *[
            _text(finding.get(field))
            for finding in findings
            for field in ("label", "prior_value", "value", "unit")
            if finding.get(field) not in (None, "", "—")
        ],
        *[
            _text(point.get(field))
            for point in issue.get("history_points", [])
            for field in ("date", "value")
        ],
        _text(copy.get("thesis_title")),
        *[
            _text(reason.get(field))
            for reason in copy.get("reasons", [])
            for field in ("label", "text")
        ],
        control,
        *[_text(step.get("do_this")) for step in steps],
        _text(issue.get("success", {}).get("text")),
        _text(copy.get("uncertainty")),
    ]
    source_cards = [
        _source_card(source_by_id[source_id])
        for source_id in issue.get("source_ids", [])
        if source_id in source_by_id and source_by_id[source_id].get("url")
    ]
    return {
        "id": f"issue-{_text(issue.get('issue_id'))}",
        "role": "issue",
        "issue_id": _text(issue.get("issue_id")),
        "narration": " ".join(part for part in narration if part),
        "required_facts": list(dict.fromkeys(part for part in required if part)),
        "source_cards": source_cards,
    }


def build_video_manifest(packet):
    issues = packet.get("correct_course_issues", [])
    findings = packet.get("chart_findings", [])
    source_by_id = {
        source.get("id"): source
        for source in packet.get("sources", [])
        if source.get("id")
    }
    finding_by_id = {
        finding.get("source_id"): finding
        for finding in findings
        if finding.get("source_id")
    }
    day = _spoken_date(packet["as_of_date"])
    issue_count = len(issues)
    if issue_count:
        result = (
            f"{issue_count} connected {_plural(issue_count, 'pattern')} "
            "earned an evidence-backed correction."
        )
    else:
        result = "No finding earned an evidence-backed correction."
    opening = {
        "id": "opening",
        "role": "opening",
        "narration": (
            f"On {day}, Signal & Story compared {len(findings)} completed laboratory "
            f"{_plural(len(findings), 'result')} with the closest matching earlier tests. "
            f"{result}"
        ),
        "required_facts": [str(len(findings)), str(issue_count), result],
        "source_cards": [],
    }
    segments = [
        opening,
        *[
            _issue_segment(issue, finding_by_id, source_by_id)
            for issue in issues
        ],
    ]
    close = {
        "id": "close",
        "role": "close",
        "narration": (
            "Findings without a qualified correction were not guessed. "
            "That does not mean every other result was normal."
        ),
        "required_facts": [
            "Findings without a qualified correction were not guessed."
        ],
        "source_cards": [],
    }
    segments.append(close)
    cards = [card for segment in segments for card in segment["source_cards"]]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "report_digest": "",
        "testing_day": packet["as_of_date"],
        "result_count": len(findings),
        "issue_count": issue_count,
        "segments": segments,
        "source_cards": list(
            {card["source_id"]: card for card in cards}.values()
        ),
        "template_labels": [
            "SIGNAL & STORY",
            "THE PATIENT SIGNAL",
            "THE SUPPORTING PROOF",
            "SUCCESS",
        ],
    }
    manifest["required_facts"] = manifest_required_facts(manifest)
    manifest["allowed_numbers"] = sorted(manifest_allowed_numbers(manifest))
    return manifest


def manifest_script(manifest):
    return "\n\n".join(segment["narration"] for segment in manifest["segments"])


def manifest_required_facts(manifest):
    return list(
        dict.fromkeys(
            fact
            for segment in manifest["segments"]
            for fact in segment.get("required_facts", [])
            if fact
        )
    )


def manifest_allowed_numbers(manifest):
    material = " ".join(
        [
            manifest_script(manifest),
            *manifest_required_facts(manifest),
            *[card["label"] for card in manifest.get("source_cards", [])],
        ]
    )
    return set(_NUMBER_RE.findall(material))


def _payload(manifest):
    return (
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _report_digest(document, manifest):
    unbound = {**manifest, "report_digest": ""}
    material = json.dumps(
        unbound, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(f"{document}\0{material}".encode("utf-8")).hexdigest()


def embed_video_manifest(document, manifest):
    if _MANIFEST_RE.search(document):
        raise ValueError("report already contains a Video Manifest")
    bound = deepcopy(manifest)
    bound["report_digest"] = _report_digest(document, bound)
    block = (
        f'<script id="{MANIFEST_ID}" type="application/json">'
        f"{_payload(bound)}</script>"
    )
    if "</head>" not in document:
        raise ValueError("report has no closing head")
    return document.replace("</head>", block + "</head>", 1)


def extract_video_manifest(document):
    match = _MANIFEST_RE.search(document)
    if not match:
        raise ValueError("approved report has no Video Manifest")
    manifest = json.loads(html.unescape(match.group(1)))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported Video Manifest schema")
    segments = manifest.get("segments")
    if (
        not isinstance(segments, list)
        or not segments
        or any(
            not isinstance(segment, dict) or not _text(segment.get("narration"))
            for segment in segments
        )
    ):
        raise ValueError("Video Manifest has empty locked narration")
    base = document[: match.start()] + document[match.end() :]
    digest = _report_digest(base, manifest)
    if manifest.get("report_digest") != digest:
        raise ValueError("Video Manifest report digest mismatch")
    if manifest.get("required_facts") != manifest_required_facts(manifest):
        raise ValueError("Video Manifest fact list mismatch")
    if set(manifest.get("allowed_numbers", [])) != manifest_allowed_numbers(manifest):
        raise ValueError("Video Manifest number allowlist mismatch")
    return manifest
