"""Render a Hermes brief as a safe, print-ready standalone HTML file.

Files are written to deliverables/ and sent to Telegram as documents.

NOTE: deliverables/ contents contain patient data and are gitignored.
"""
import html
import os
import re
import shutil
import subprocess
import time
from datetime import date
from urllib.parse import urlparse
from html.parser import HTMLParser

from signal_story_video import build_video_manifest, embed_video_manifest

HERE = os.path.dirname(__file__)
DELIV_DIR = os.path.join(HERE, "..", "deliverables")

TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --ink:#1a1a1a; --muted:#6b6b6b; --accent:#7c2d12; --line:#e5e5e5; --bg:#faf8f5; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:'Georgia','Times New Roman',serif; color:var(--ink); background:var(--bg);
         margin:0; padding:48px 24px; line-height:1.7; }}
  .sheet {{ max-width:760px; margin:0 auto; background:#fff; padding:56px 64px;
            box-shadow:0 1px 24px rgba(0,0,0,.06); border-radius:6px; }}
  .eyebrow {{ font-family:system-ui,sans-serif; font-size:12px; letter-spacing:.18em;
              text-transform:uppercase; color:var(--accent); margin:0 0 8px; font-weight:600; }}
  h1 {{ font-size:30px; margin:0 0 6px; line-height:1.25; }}
  .meta {{ font-family:system-ui,sans-serif; font-size:13px; color:var(--muted); margin:0 0 32px;
           padding-bottom:20px; border-bottom:1px solid var(--line); }}
  h2 {{ font-size:19px; margin:32px 0 10px; color:var(--accent); font-family:system-ui,sans-serif;}}
  p, li {{ font-size:16px; }}
  ul {{ padding-left:20px; }} li {{ margin:6px 0; }}
  strong {{ color:var(--ink); }}
  .brief {{ white-space:pre-wrap; font-size:16px; }}
  .lead {{ font-size:18px; line-height:1.55; }}
  .summary {{ background:#eef6f1; border-radius:10px; padding:18px 20px; }}
  .urgent {{ background:#fff3df; border-left:4px solid #b45309; padding:14px 18px; }}
  table {{ width:100%; border-collapse:collapse; font-family:system-ui,sans-serif; font-size:13px; }}
  th, td {{ border-bottom:1px solid var(--line); padding:9px 7px; text-align:left; vertical-align:top; }}
  a {{ color:#175b48; overflow-wrap:anywhere; }}
  @media (max-width:640px) {{
    body {{ padding:12px; }}
    .sheet {{ padding:28px 20px; }}
    h1 {{ font-size:24px; }}
    table {{ display:block; overflow-x:auto; }}
  }}
  .sources {{ margin-top:36px; padding-top:20px; border-top:1px solid var(--line);
              font-family:system-ui,sans-serif; font-size:13px; color:var(--muted);}}
  .sources h2 {{ font-size:14px; }}
  .footer {{ font-family:system-ui,sans-serif; font-size:11px; color:var(--muted);
             text-align:center; margin-top:40px; }}
  code {{ font-family:ui-monospace,monospace; background:#f3f0ec; padding:1px 5px; border-radius:3px; font-size:14px;}}
</style></head>
<body><div class="sheet">
<p class="eyebrow">GOMER · Case Brief</p>
<h1>{title}</h1>
<p class="meta">{meta}</p>
{body}
<div class="footer">This brief is decision support, not a substitute for clinical judgment. Always confirm with the care team.</div>
</div></body></html>"""

SIGNAL_STORY_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  @page {{ size:Letter portrait; margin:0; }}
  :root {{ --ink:#111; --muted:#595959; --line:#d7d7d7; --paper:#fff; --bg:#e9e9e9; --ok:#cfcfcf; --edge:#eee; --green:#111; --amber:#111; --red:#111; }}
  * {{ box-sizing:border-box; }}
  html,body {{ margin:0; color:var(--ink); background:#fff; font-family:system-ui,sans-serif; }}
  .page {{ width:8.5in; height:11in; margin:20px auto; padding:.34in .38in .3in; overflow:hidden; background:var(--paper); box-shadow:0 8px 30px rgba(20,50,35,.09); break-after:page; page-break-after:always; }}
  .page:last-child {{ break-after:auto; page-break-after:auto; }}
  .page-head {{ display:flex; align-items:end; justify-content:space-between; margin-bottom:9px; border-bottom:2px solid var(--ink); padding-bottom:7px; }}
  .eyebrow {{ margin:0 0 2px; color:var(--green); font-size:7.5px; font-weight:900; letter-spacing:.14em; text-transform:uppercase; }}
  h1 {{ margin:0; font-size:25px; line-height:1; letter-spacing:-.03em; }}
  .meta {{ margin:0; color:var(--muted); font-size:8px; font-weight:650; }}
  .results {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:5px; align-content:start; }}
  .result {{ min-height:55px; padding:5px 7px 4px; border:1px solid var(--line); border-radius:7px; break-inside:avoid; overflow:hidden; }}
  .result-name {{ display:flex; align-items:baseline; gap:4px; min-width:0; }}
  .result h2 {{ margin:0; overflow:hidden; font-size:8.8px; line-height:1.08; text-overflow:ellipsis; white-space:nowrap; }}
  .medical-name {{ overflow:hidden; color:var(--muted); font-size:5.8px; line-height:1; text-overflow:ellipsis; white-space:nowrap; }}
  .comparison {{ display:flex; align-items:baseline; gap:3px; margin-top:3px; white-space:nowrap; }}
  .comparison strong {{ font-size:12.5px; line-height:1; }}
  .comparison .arrow {{ color:var(--green); font-size:10px; font-weight:900; }}
  .unit {{ color:var(--muted); font-size:5.8px; font-weight:600; }}
  .dates {{ display:flex; justify-content:space-between; margin-top:1px; color:var(--muted); font-size:5.5px; }}
  .range {{ margin-top:3px; }}
  .range-track {{ position:relative; height:3px; border-radius:9px; background:linear-gradient(90deg,var(--edge) 0 25%,var(--ok) 25% 75%,var(--edge) 75% 100%); }}
  .range-marker {{ position:absolute; top:50%; left:var(--pos); width:7px; height:7px; border:1.5px solid #fff; border-radius:50%; background:var(--ink); transform:translate(-50%,-50%); }}
  .range.no-reference .range-track {{ background:#e8ece9; }}
  .range-labels {{ display:grid; grid-template-columns:1fr 2fr 1fr; margin-top:1px; color:var(--muted); font-size:4.8px; font-weight:900; text-align:center; }}
  .key {{ margin:7px 0 0; color:var(--muted); font-size:6.5px; text-align:right; }}
  .course-intro {{ margin:0 0 12px; max-width:610px; color:var(--muted); font-size:10px; line-height:1.35; }}
  .course-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }}
  .course-count-1 {{ grid-template-columns:1fr; }}
  .course-count-2 {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .course-target {{ padding:12px; border:1px solid var(--line); border-top:5px solid var(--green); border-radius:10px; break-inside:avoid; }}
  .target-kicker {{ margin:0 0 3px; color:var(--green); font-size:7px; font-weight:900; letter-spacing:.1em; text-transform:uppercase; }}
  .course-target h2 {{ margin:0; font-size:15px; line-height:1.1; }}
  .trigger {{ margin:5px 0 2px; font-size:9px; font-weight:800; }}
  .target-why {{ margin:0 0 8px; color:var(--muted); font-size:8px; line-height:1.3; }}
  .action-lane {{ margin-top:7px; padding-top:7px; border-top:1px solid var(--line); }}
  .lane-label {{ margin:0 0 4px; font-size:7px; font-weight:950; letter-spacing:.09em; }}
  .lane-check-first .lane-label {{ color:var(--amber); }}
  .lane-dont .lane-label {{ color:var(--red); }}
  .course-action {{ margin:0 0 6px; }}
  .course-action strong {{ display:block; font-size:9px; line-height:1.2; }}
  .course-action p {{ margin:2px 0 0; color:var(--muted); font-size:7.5px; line-height:1.3; }}
  .proof-mark {{ color:var(--green); font-size:6px; vertical-align:super; }}
  .course-date {{ margin:10px 0 0; color:var(--muted); font-size:6.5px; }}
  .proofs {{ margin-top:13px; padding-top:8px; border-top:1px solid var(--line); }}
  .proofs h2 {{ margin:0 0 4px; font-size:9px; }}
  .proofs ol {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:2px 18px; margin:0; padding-left:18px; }}
  .proofs li {{ color:var(--muted); font-size:6.5px; line-height:1.25; }}
  .empty-course {{ margin:1.2in auto 0; max-width:430px; padding:24px; border:1px solid var(--line); border-radius:12px; text-align:center; }}
  .empty-course h2 {{ margin:0 0 7px; font-size:18px; }}
  .empty-course p {{ margin:0; color:var(--muted); font-size:10px; }}
  .page-before-now {{ position:relative; }}
  .page-before-now .key {{ position:absolute; right:.38in; bottom:.16in; margin:0; }}
  .result.abnormal {{ border:2px solid #111; padding:4px 6px 3px; }}
  .result.course-linked {{ position:relative; border:2.5px solid #111; box-shadow:inset 4px 0 #111; }}
  .result.course-linked .result-name {{ padding-left:3px; padding-right:47px; }}
  .result.course-linked::after {{ content:"CORRECT COURSE →"; position:absolute; top:3px; right:4px; padding:2px 3px; border-radius:2px; color:#fff; background:#111; font-size:4.7px; line-height:1; font-weight:950; }}
  .comparison .arrow {{ margin:0 1px; font-size:16px; line-height:.65; color:#111; }}
  .range-marker {{ width:9px; height:9px; border-width:2px; box-shadow:0 0 0 1px #111; }}
  .range.no-reference .range-track {{ display:none; }}
  .no-range-label {{ color:var(--muted); font-size:4.8px; font-weight:900; }}
  .range.no-reference .range-labels {{ display:block; text-align:left; }}
  .range.no-reference .range-labels span {{ display:none; }}
  .range.no-reference .range-labels::after {{ content:"NO RECORDED RANGE"; color:var(--muted); font-size:4.8px; font-weight:900; }}
  .cc-count {{ margin:0 0 8px; padding:6px 9px; border:1px solid #111; border-radius:7px; font-size:9px; line-height:1.3; }}
  .cc-top {{ display:grid; grid-template-columns:.96fr 1.04fr; gap:11px; align-items:start; }}
  .cc-kicker {{ margin:0 0 2px; font-size:7px; font-weight:950; letter-spacing:.11em; text-transform:uppercase; }}
  .cc-title {{ margin:0 0 4px; font-size:25px; line-height:1.02; letter-spacing:-.035em; }}
  .cc-plain {{ margin:0 0 7px; color:var(--muted); font-size:10px; line-height:1.35; }}
  .cc-chart {{ padding:7px 8px 4px; border:1px solid var(--line); border-radius:8px; }}
  .cc-chart-title {{ margin:0; font-size:8px; font-weight:850; }}
  .cc-chart-note {{ margin:1px 0 0; color:var(--muted); font-size:5.7px; }}
  .cc-success,.cc-lead,.cc-action {{ margin-top:6px; padding:8px 10px; border:2px solid #111; border-radius:8px; }}
  .cc-success small,.cc-action small,.cc-step small {{ display:block; margin-bottom:2px; font-size:6px; font-weight:900; letter-spacing:.08em; text-transform:uppercase; }}
  .cc-success strong {{ font-size:10.5px; }}
  .cc-lead h3,.cc-action h3 {{ margin:3px 0 7px; font-size:16px; line-height:1.08; }}
  .cc-proof-row {{ display:grid; grid-template-columns:18px 1fr; gap:6px; margin:5px 0; font-size:9.5px; line-height:1.34; }}
  .cc-number {{ width:17px; height:17px; display:flex; align-items:center; justify-content:center; border-radius:50%; color:#fff; background:#111; font-size:8px; font-weight:950; }}
  .cc-truth,.cc-action p {{ margin:5px 0 0; color:var(--muted); font-size:8px; line-height:1.32; }}
  .cc-action {{ margin-top:9px; text-align:center; }}
  .cc-action h3 {{ margin:2px 0; font-size:23px; text-transform:uppercase; }}
  .cc-steps {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; margin-top:9px; }}
  .cc-step {{ padding:8px 9px; border:1px solid #111; border-radius:8px; }}
  .cc-step h3 {{ margin:0 0 4px; font-size:11px; }}
  .cc-step p {{ margin:0; color:#333; font-size:8.7px; line-height:1.36; }}
  .cc-thesis {{ margin-top:7px; padding:8px 10px; background:#f1f1f1; border-radius:8px; }}
  .cc-thesis h3 {{ margin:0 0 3px; font-size:11px; }}
  .cc-thesis p {{ margin:0; font-size:9px; line-height:1.35; }}
  .cc-clinical {{ margin-top:7px; padding-top:6px; border-top:1px solid var(--line); color:var(--muted); font-size:6.8px; line-height:1.32; }}
  .cc-links {{ display:flex; gap:4px; flex-wrap:wrap; margin-top:5px; }}
  .cc-links a {{ padding:3px 5px; border:1px solid #999; border-radius:999px; color:#111; font-size:6.3px; font-weight:800; text-decoration:none; }}
  @media (max-width:700px) {{ body {{ background:#fff; }} .page {{ width:100%; height:auto; min-height:100vh; margin:0; padding:24px 16px; box-shadow:none; }} .results,.cc-top,.cc-steps {{ grid-template-columns:1fr; }} .result {{ min-height:auto; }} }}
  @media print {{ body {{ background:#fff; }} .page {{ margin:0; box-shadow:none; }} }}
</style></head>
<body>{pages}</body></html>"""

PLAIN_RESULT_NAMES = {
    "absolute neutrophil count": "Infection fighters: count",
    "alt": "Liver check 1",
    "ast": "Cell and liver check",
    "albumin level": "Main blood protein",
    "alkaline phosphatase": "Liver and bone check",
    "anion gap": "Blood acid balance",
    "bun": "Kidney waste check",
    "basophil %": "Allergy cells: share",
    "basophil abs": "Allergy cells: count",
    "bilirubin total": "Yellow waste check",
    "co2": "Blood acid balance helper",
    "crp-hs": "Inflammation signal",
    "calcium level total": "Calcium",
    "chloride": "Salt balance",
    "creatinine": "Kidney cleanup",
    "eosinophil %": "Allergy-related cells: share",
    "eosinophil abs": "Allergy-related cells: count",
    "ferritin level": "Stored iron",
    "free kappa light chains": "Myeloma protein marker",
    "glucose level": "Blood sugar",
    "hematocrit": "Blood made of red cells",
    "hemoglobin": "Oxygen-carrying protein",
    "ldh": "Cell damage signal",
    "lymphocyte %": "Immune cells: share",
    "lymphocyte abs": "Immune cells: count",
    "magnesium level": "Magnesium",
    "mean cell hemoglobin": "Red-cell oxygen amount",
    "mean cell hemoglobin concentration": "Red-cell oxygen concentration",
    "mean cell volume": "Red-cell size",
    "mean platelet volume": "Clotting-cell size",
    "monocyte %": "Cleanup immune cells: share",
    "monocyte abs": "Cleanup immune cells: count",
    "neutrophil %": "Infection fighters: share",
    "neutrophil abs": "Infection fighters: count",
    "phosphorus level": "Phosphorus",
    "platelet": "Clotting cells",
    "potassium level": "Potassium",
    "rdw-sd": "Red-cell size spread",
    "red blood cell": "Oxygen-carrying cells",
    "red cell diameter width": "Red-cell size variation",
    "sodium level": "Sodium",
    "tot protein": "Blood proteins",
    "uric acid": "Cell-breakdown waste",
    "white blood cell": "Immune cells",
    "egfr": "Kidney filtering",
}


def _e(value):
    return html.escape(str(value or ""), quote=True)


def _list(items):
    return "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in items) + "</ul>"


def _finding_rows(findings):
    return "".join(
        "<tr>"
        f"<td>{_e(item.get('label'))}</td>"
        f"<td>{_e(item.get('value'))} {_e(item.get('unit'))}</td>"
        f"<td>{_e(item.get('date'))}</td><td>{_e(item.get('trend'))}</td>"
        "</tr>"
        for item in findings
    )


def _safe_url(value):
    parsed = urlparse(str(value or ""))
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _write_pdf(html_path):
    """Render an HTML report to PDF via the system weasyprint CLI."""
    binary = shutil.which("weasyprint")
    if not binary:
        return None
    pdf_path = html_path[:-5] + ".pdf" if html_path.endswith(".html") else html_path + ".pdf"
    subprocess.run([binary, html_path, pdf_path], check=True, capture_output=True)
    return pdf_path


def _pdf_page_count(pdf_path):
    binary = shutil.which("mutool")
    if not binary or not pdf_path:
        return None
    result = subprocess.run([binary, "info", pdf_path], check=True, capture_output=True, text=True)
    match = re.search(r"Pages:\s*(\d+)", result.stdout)
    return int(match.group(1)) if match else None


def _plain_result_name(label):
    return PLAIN_RESULT_NAMES.get(str(label or "").casefold(), str(label or "Result"))


def _direction(arrow):
    return {"↑": "↑ up", "↓": "↓ down", "→": "→ same"}.get(arrow, "")


def _range(item):
    low, high = item.get("reference_low"), item.get("reference_high")
    labels = '<div class="range-labels"><span>LOW</span><span>OK</span><span>HIGH</span></div>'
    if low is None or high is None or float(high) <= float(low):
        return '<div class="range no-reference" aria-label="No recorded reference range"><span class="no-range-label">NO RECORDED RANGE</span></div>'
    low, high, value = float(low), float(high), float(item["value"])
    span = high - low
    position = max(2, min(98, 100 * (value - (low - span / 2)) / (span * 2)))
    status = "LOW" if value < low else "HIGH" if value > high else "OK"
    label = f"{item['label']} is {status.lower()}; recorded range {low:g} to {high:g} {item.get('unit', '')}".strip()
    return (
        f'<div class="range" role="img" aria-label="{_e(label)}">'
        f'<div class="range-track"><span class="range-marker" style="--pos:{position:.1f}%"></span></div>{labels}</div>'
    )


def _signal_card(item, course_linked=False):
    prior = "—" if item.get("prior_value") in (None, "", "—") else _e(item["prior_value"])
    unit = _e(item.get("unit"))
    before_date = _e(item.get("prior_date") or "No earlier result")
    try:
        abnormal = float(item["value"]) < float(item["reference_low"]) or float(item["value"]) > float(item["reference_high"])
    except (KeyError, TypeError, ValueError):
        abnormal = False
    classes = "result" + (" abnormal" if abnormal else "") + (" course-linked" if course_linked else "")
    return (
        f'<article class="{classes}">'
        f'<div class="result-name"><h2>{_e(_plain_result_name(item["label"]))}</h2>'
        f'<small class="medical-name">{_e(item["label"])}</small></div>'
        f'<div class="comparison"><strong>{prior}</strong><span class="unit">{unit}</span>'
        f'<span class="arrow">{_e(item.get("trend") or "→")}</span><strong>{_e(item["value"])}</strong><span class="unit">{unit}</span></div>'
        f'<div class="dates"><span>BEFORE · {before_date}</span><span>NOW · {_e(item.get("date"))}</span></div>'
        f'{_range(item)}</article>'
    )


def _history_graph(issue):
    points = issue.get("history_points", [])
    if not points:
        return ""
    parsed = []
    for point in points:
        try:
            parsed.append((date.fromisoformat(point["date"]), float(point["value"]), point))
        except (KeyError, TypeError, ValueError):
            continue
    if not parsed:
        return ""
    parsed.sort(key=lambda item: item[0])
    start, end = parsed[0][0], parsed[-1][0]
    elapsed = max(1, (end - start).days)
    values = [item[1] for item in parsed]
    low, high = min(values), max(values)
    span = high - low or max(abs(high) * .1, 1)
    low -= span * .15
    high += span * .15
    coords = [
        (45 + 395 * ((when - start).days / elapsed), 140 - 120 * ((value - low) / (high - low)), point)
        for when, value, point in parsed
    ]
    path = " ".join(("M" if index == 0 else "L") + f"{x:.1f} {y:.1f}" for index, (x, y, _) in enumerate(coords))
    circles = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6"/>' for x, y, _ in coords
    )
    labels = "".join(
        f'<text x="{x:.1f}" y="{max(10, y - 10):.1f}" text-anchor="middle" fill="#111" stroke="none" font-size="10" font-weight="700">{_e(point["value"])}</text>'
        f'<text x="{x:.1f}" y="165" text-anchor="middle" fill="#111" stroke="none" font-size="8" font-weight="700">{_e(point["date"])}</text>'
        for x, y, point in coords
    )
    label = ", ".join(f'{point["value"]} on {point["date"]}' for _, _, point in coords)
    return (
        f'<div class="cc-chart"><p class="cc-chart-title">{_e(issue["primary_finding"].get("label"))} trend · {_e(issue["primary_finding"].get("unit"))}</p>'
        f'<svg viewBox="0 0 500 175" width="100%" role="img" aria-label="{_e(label)}">'
        '<rect x="45" y="20" width="395" height="120" fill="#fff"/><g stroke="#ddd" stroke-width="1"><line x1="45" y1="20" x2="440" y2="20"/><line x1="45" y1="80" x2="440" y2="80"/><line x1="45" y1="140" x2="440" y2="140"/></g>'
        f'<path d="{path}" fill="none" stroke="#111" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<g fill="#fff" stroke="#111" stroke-width="3">{circles}</g>{labels}</svg>'
        '<p class="cc-chart-note">Dates use their real spacing. Values come directly from the Chart.</p></div>'
    )


def _issue_page(issue, page_number, issue_count, source_by_id):
    copy = issue.get("copy", {})
    reasons = "".join(
        f'<div class="cc-proof-row"><span class="cc-number">{index}</span><span><strong>{_e(reason.get("label"))}.</strong> {_e(reason.get("text"))}</span></div>'
        for index, reason in enumerate(copy.get("reasons", []), 1)
    )
    action = issue.get("primary_action", {})
    control = "CLINICIAN REVIEW" if action.get("control") == "clinician" else "SUPPORTED ACTION"
    steps = "".join(
        f'<article class="cc-step"><small>Step {index}</small><h3>{_e(step.get("item") or step.get("kind"))}</h3><p>{_e(step.get("do_this"))}</p></article>'
        for index, step in enumerate(issue.get("steps", []), 1)
    )
    links = []
    for source_id in issue.get("source_ids", []):
        source = source_by_id.get(source_id)
        if not source:
            continue
        url = _safe_url(source.get("url"))
        label = _e(source.get("title") or source_id)
        if url:
            links.append(f'<a data-source="{_e(source_id)}" href="{_e(url)}">{label} ↗</a>')
    clinical = " · ".join(filter(None, [
        action.get("do_this"),
        f"Expected direction: {action.get('expected', {}).get('direction')}" if action.get("expected", {}).get("direction") else "",
        action.get("population"), action.get("treatment_context"),
    ]))
    count_word = "finding" if issue_count == 1 else "findings"
    return f'''<section class="page page-correct-course" id="{_e(issue.get('issue_id'))}" data-page="{page_number}">
<header class="page-head"><div><p class="eyebrow">GOMER · Signal &amp; Story</p><h1>Correct course</h1></div><p class="meta">Evidence current through · {_e(issue.get('evidence_current_through'))}</p></header>
<p class="cc-count"><strong>{issue_count} {count_word} earned an evidence-backed correction.</strong> Unsupported findings were omitted rather than guessed. This is correction {page_number - 1} of {issue_count}.</p>
<div class="cc-top"><div><p class="cc-kicker">What needs to turn around</p><h2 class="cc-title">{_e(copy.get('title'))}</h2><p class="cc-plain">{_e(copy.get('term_explanation'))}</p>{_history_graph(issue)}<div class="cc-success"><small>Success on the next test</small><strong>{_e(issue.get('success', {}).get('text'))}</strong></div></div>
<div class="cc-lead"><p class="cc-kicker">Why this is the strongest lead</p><h3>{_e(copy.get('thesis_title'))}</h3>{reasons}<p class="cc-truth"><strong>What is not proven:</strong> {_e(copy.get('uncertainty'))}</p></div></div>
<div class="cc-action"><small>{control}</small><h3>{_e(action.get('item'))}</h3><p>{_e(action.get('do_this'))}</p></div>
{f'<div class="cc-steps">{steps}</div>' if steps else ''}
<div class="cc-thesis"><h3>The complete idea</h3><p><strong>{_e(copy.get('complete_idea'))}</strong></p></div>
<div class="cc-clinical"><strong>Clinical detail:</strong> {_e(clinical)}<div class="cc-links">{"".join(links)}</div></div>
</section>'''


def _render_signal_story(packet, generated_at):
    issues = packet.get("correct_course_issues", [])
    linked_source_ids = {
        source_id
        for issue in issues if isinstance(issue, dict)
        for source_id in issue.get("finding_source_ids", [])
    }
    before = (
        '<section class="page page-before-now" data-page="1">'
        '<header class="page-head"><div><p class="eyebrow">GOMER · Signal &amp; Story</p><h1>Before and now</h1></div>'
        f'<p class="meta">New results · {_e(packet["as_of_date"])}</p></header>'
        f'<div class="results">{"".join(_signal_card(item, item.get("source_id") in linked_source_ids) for item in packet["chart_findings"])}</div>'
        '<p class="key">Arrows show movement only · Thick outline = outside the recorded range · CORRECT COURSE → = evidence-backed correction · LOW · OK · HIGH uses the recorded range.</p></section>'
    )
    source_by_id = {source.get("id"): source for source in packet.get("sources", []) if source.get("id")}
    pages = before + "".join(
        _issue_page(issue, index + 2, len(issues), source_by_id)
        for index, issue in enumerate(issues)
    )
    return SIGNAL_STORY_TEMPLATE.format(title=_e(packet["title"]), pages=pages)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def verify_signal_story_html(document, packet):
    parser = _TextParser()
    parser.feed(document)
    text = " ".join(parser.parts)
    issues = packet.get("correct_course_issues", [])
    expected_pages = 1 + len(issues)
    errors = [] if "Before and now" in text else ["missing guide heading: Before and now"]
    if len(issues) != text.count("Correct course"):
        errors.append("Correct course page count does not match admitted issues")
    if document.count('class="page ') != expected_pages or document.count('data-page="') != expected_pages:
        errors.append(f"Signal & Story must contain {expected_pages} portrait pages")
    if re.search(r"<(?:img|iframe)\b", document, re.I):
        errors.append("Signal & Story must not embed images or pages")
    for finding in packet["chart_findings"]:
        for value in (finding["label"], finding["value"], finding["date"], finding["prior_value"], finding["prior_date"]):
            if value and value != "—" and str(value) not in text:
                errors.append(f"missing guide fact: {value}")
    source_by_id = {source.get("id"): source for source in packet.get("sources", []) if source.get("id")}
    for issue in issues:
        copy = issue.get("copy", {})
        for value in (
            copy.get("title"), copy.get("term_explanation"), copy.get("thesis_title"),
            copy.get("uncertainty"), copy.get("complete_idea"),
            issue.get("success", {}).get("text"), issue.get("evidence_current_through"),
            issue.get("primary_action", {}).get("item"), issue.get("primary_action", {}).get("do_this"),
        ):
            if value and str(value) not in text:
                errors.append(f"missing guide fact: {value}")
        for reason in copy.get("reasons", []):
            for value in (reason.get("label"), reason.get("text")):
                if value and str(value) not in text:
                    errors.append(f"missing guide fact: {value}")
        for point in issue.get("history_points", []):
            for value in (point.get("date"), point.get("value")):
                if value not in (None, "") and str(value) not in text:
                    errors.append(f"missing guide graph fact: {value}")
        for step in issue.get("steps", []):
            if step.get("do_this") and str(step["do_this"]) not in text:
                errors.append(f"missing guide fact: {step['do_this']}")
        for source_id in issue.get("source_ids", []):
            source = source_by_id.get(source_id)
            if not source:
                errors.append(f"missing guide source: {source_id}")
                continue
            url = _safe_url(source.get("url"))
            if url and source.get("title") and str(source["title"]) not in text:
                errors.append(f"missing guide proof: {source['title']}")
            if url and f'href="{_e(url)}"' not in document:
                errors.append(f"missing guide link: {source_id}")
    if "Warnings from repeated results" in text:
        errors.append("legacy repeated-result warnings must not render in Correct course")
    return list(dict.fromkeys(errors))


def _validated_signal_story_document(packet, generated_at):
    document = _render_signal_story(packet, generated_at)
    errors = verify_signal_story_html(document, packet)
    if errors:
        raise ValueError("; ".join(errors))
    return embed_video_manifest(document, build_video_manifest(packet))


def render_signal_story_html(packet, generated_at=None):
    """Return validated deterministic Signal & Story HTML without writing files."""
    generated_at = generated_at or time.strftime("%Y-%m-%d %H:%M")
    return _validated_signal_story_document(packet, generated_at)


def render_packet_save(packet, generated_at=None):
    """Render one validated Answer Packet as a semantic two-audience report."""
    os.makedirs(DELIV_DIR, exist_ok=True)
    generated_at = generated_at or time.strftime("%Y-%m-%d %H:%M")
    if packet.get("deliverable_metadata", {}).get("audience") == "signal-story":
        from signal_story import validate_correct_course_issues
        errors = validate_correct_course_issues(packet) if "correct_course_issues" in packet else []
        if errors:
            raise ValueError("; ".join(errors))
        document = render_signal_story_html(packet, generated_at)
        slug = re.sub(r"[^a-z0-9]+", "-", packet["title"].lower()).strip("-")[:40] or "report"
        path = os.path.join(DELIV_DIR, f"{time.strftime('%Y-%m-%d')}-{slug}.html")
        pdf_path = path[:-5] + ".pdf"
        temporary_html = path[:-5] + ".tmp.html"
        temporary_pdf = temporary_html[:-5] + ".pdf"
        try:
            with open(temporary_html, "w", encoding="utf-8") as handle:
                handle.write(document)
            if not _write_pdf(temporary_html):
                raise ValueError("Signal & Story requires the PDF renderer")
            page_count = _pdf_page_count(temporary_pdf)
            if page_count is None:
                raise ValueError("Signal & Story requires PDF page-count validation")
            expected_pages = 1 + len(packet.get("correct_course_issues", []))
            if page_count != expected_pages:
                raise ValueError(f"Signal & Story PDF must be exactly {expected_pages} pages, got {page_count}")
            os.replace(temporary_html, path)
            os.replace(temporary_pdf, pdf_path)
        finally:
            for temporary in (temporary_html, temporary_pdf):
                if os.path.exists(temporary):
                    os.remove(temporary)
        return {"html": path, "pdf": pdf_path}
    options = [
        f"{item['option'] if re.match(r'(?i)^ask\b', item['option']) else 'Ask about ' + item['option']}: {item['why']} {item['fit']}"
        for item in packet["medical_options"]
    ]
    actions = [
        f"{item['action']} — {item['why']} {item['when']}"
        for item in packet["patient_actions"]
    ]
    nutrition = [
        f"{item['option']}: {item['limits']}"
        for item in packet["nutrition_options"]
    ]
    urgent = [
        f"{item['signs']}: {item['action']}"
        for item in packet["urgent_signs"]
    ]
    explanations = [
        f"{item['label']} — {item['fit']}. {item['uncertainty']}"
        for item in packet["possible_explanations"]
    ]
    timeline = [
        f"{item['date']}: {item['label']}"
        for item in packet["timeline_events"]
    ]
    source_items = []
    for source in packet["sources"]:
        organization = f"{source['organization']} — " if source.get("organization") else ""
        label = f"{organization}{source['title']} ({source['evidence_grade']})"
        url = _safe_url(source.get("url"))
        source_items.append(f'<a href="{_e(url)}">{_e(label)}</a>' if url else _e(label))
    body = f"""
<section class="summary"><h2>For review</h2><p class="lead">{_e(packet['direct_answer'])}</p><p>Evidence current through {_e(packet['as_of_date'])}.</p></section>
<section><h2>What changed</h2><table><thead><tr><th>Finding</th><th>Value</th><th>Date</th><th>Trend</th></tr></thead><tbody>{_finding_rows(packet['chart_findings'])}</tbody></table></section>
<section><h2>Relevant timeline</h2>{_list(timeline)}</section>
<section><h2>What may explain it</h2>{_list(explanations)}</section>
<section><h2>What to do now</h2>{_list(actions)}</section>
<section><h2>Care-team decisions</h2>{_list(options + packet['monitoring_options'])}</section>
<section><h2>Food and supportive care</h2>{_list(nutrition)}</section>
<section><h2>What probably will not help</h2>{_list(packet['unlikely_or_unsupported_options'])}</section>
<section class="urgent"><h2>When to contact the care team</h2>{_list(urgent)}</section>
<section><h2>For the care team</h2>{_list(packet['doctor_questions'])}<h3>Missing or conflicting evidence</h3>{_list(packet['missing_evidence'] + packet['conflicts'])}</section>
<section class="sources"><h2>Evidence</h2><ul>{''.join(f'<li>{item}</li>' for item in source_items)}</ul></section>
"""
    document = TEMPLATE.format(
        title=_e(packet["title"]),
        meta=f"Generated {_e(generated_at)} · For review by the care team",
        body=body,
    )
    slug = re.sub(r"[^a-z0-9]+", "-", packet["title"].lower()).strip("-")[:40] or "report"
    path = os.path.join(DELIV_DIR, f"{time.strftime('%Y-%m-%d')}-{slug}.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(document)
    return {"html": path, "pdf": _write_pdf(path)}
