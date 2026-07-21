#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

PUBLIC_ROOT = Path(__file__).resolve().parents[2]
CODE_ROOT = PUBLIC_ROOT if (PUBLIC_ROOT / "scripts").is_dir() else PUBLIC_ROOT.parent
sys.path.insert(0, str(CODE_ROOT / "scripts"))

from deliverables import render_signal_story_html
from signal_story import build_signal_story_packet


def build(input_path, output_dir):
    source = json.loads(Path(input_path).read_text())
    packet = build_signal_story_packet(
        source["question"], source["rows"], source["results_date"],
    )
    packet["correction_targets"] = []
    packet["correction_dispositions"] = []
    packet["correct_course_issues"] = []
    packet["missing_evidence"].append(
        "Synthetic Beta was not interpreted because its earlier result uses a different unit."
    )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    packet_path = output_dir / "packet.json"
    report_path = output_dir / "report.html"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n")
    report_path.write_text(render_signal_story_html(packet, "2030-01-16 09:00"))
    return packet_path, report_path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(Path(__file__).with_name("input.json")))
    parser.add_argument("--output", default=str(Path(__file__).with_name("output")))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    packet_path, report_path = build(args.input, args.output)
    if args.check:
        packet = json.loads(packet_path.read_text())
        assert len(packet["chart_findings"]) == 2
        assert packet["chart_findings"][0]["comparison"] == "compared"
        assert packet["chart_findings"][1]["comparison"] == "cannot-compare"
        assert "Synthetic Preliminary" not in report_path.read_text()
    print("Synthetic demo ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
