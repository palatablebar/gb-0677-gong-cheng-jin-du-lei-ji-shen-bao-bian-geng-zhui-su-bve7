import csv
import io
import json


def _cell(value):
    return "" if value is None else str(value)


def build_archive(run):
    """导出单个批次的完整证据：结果、隔离来源、复核记录与版本替代关系。"""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "run_id",
            "input_sha256",
            "rule_version",
            "kind",
            "source_lines",
            "reason",
            "evidence",
        ]
    )
    meta = run["manifest"]
    stage = run["stages"][-1]
    for row in stage["rows"]:
        writer.writerow(
            [
                run["run_id"],
                meta["input_sha256"],
                meta["rule_version"],
                "accepted",
                json.dumps(row.get("_sources", [])),
                row.get("status", ""),
                json.dumps(row, ensure_ascii=False),
            ]
        )
    for row in stage.get("cumulative_quarantine", []):
        writer.writerow(
            [
                run["run_id"],
                meta["input_sha256"],
                meta["rule_version"],
                "quarantine",
                json.dumps(row.get("_sources", [])),
                row.get("reason", ""),
                json.dumps(row, ensure_ascii=False),
            ]
        )
    writer.writerow([])
    writer.writerow(["批次状态"])
    writer.writerow(["run_id", "state", "version", "decision", "fingerprint", "replaced_run_id", "superseded_by"])
    writer.writerow(
        [
            run["run_id"],
            run.get("state", ""),
            run.get("version", ""),
            run.get("decision", ""),
            run.get("fingerprint", ""),
            _cell(run.get("replaced_run_id")),
            _cell(run.get("superseded_by")),
        ]
    )
    writer.writerow([])
    writer.writerow(["复核记录"])
    writer.writerow(["version", "decision", "reviewed_at"])
    for entry in run.get("reviews", []):
        writer.writerow([entry.get("version", ""), entry.get("decision", ""), entry.get("reviewed_at", "")])
    writer.writerow([])
    writer.writerow(["现行切换记录"])
    writer.writerow(["version", "superseded_run_id", "activated_at"])
    for entry in run.get("activations", []):
        writer.writerow(
            [entry.get("version", ""), _cell(entry.get("superseded_run_id")), entry.get("activated_at", "")]
        )
    comparison = run.get("comparison")
    if comparison:
        writer.writerow([])
        writer.writerow(["与上一版结论的差异"])
        writer.writerow(["baseline", "from_run_id", "to_run_id", "data_changed", "rule_changed"])
        writer.writerow(
            [
                comparison.get("baseline"),
                _cell(comparison.get("from_run_id")),
                _cell(comparison.get("to_run_id")),
                comparison.get("data_changed"),
                comparison.get("rule_changed"),
            ]
        )
        writer.writerow(["change", "claim_id", "cause", "fields"])
        for entry in comparison["records"]["details"]:
            writer.writerow(
                [
                    entry.get("change", ""),
                    entry.get("claim_id", ""),
                    entry.get("cause", ""),
                    json.dumps(entry.get("fields", []), ensure_ascii=False),
                ]
            )
        writer.writerow(["stage", "metric", "before", "after", "cause"])
        for entry in comparison["metrics"]["changes"]:
            writer.writerow(
                [
                    entry.get("stage_name", ""),
                    entry.get("metric", ""),
                    _cell(entry.get("before")),
                    _cell(entry.get("after")),
                    entry.get("cause", ""),
                ]
            )
    return "﻿" + stream.getvalue()
