"""重算结论比较：说明两版结论之间的差异来自数据变化还是规则变化。

一版结论由三项共同确定：输入摘要、规则版本、处理参数（见 app/manifest.py
的 digest）。输入摘要不同记为“数据变化”；规则版本或任一处理参数不同记为
“规则变化”。比较只读取两条已保存的运行记录，不重新计算，也不改写任何
历史，因此被替代版本当时看到的差异随时可以回查。
"""
import hashlib
import json
from decimal import Decimal

from app.config import MODULES

MAX_RECORD_DETAILS = 100
MAX_METRIC_CHANGES = 100
IGNORED_FIELDS = {"note", "reason", "detail"}
RAW_FIELDS = (
    "claim_id",
    "item",
    "period",
    "cumulative_qty",
    "previous_qty",
    "change_id",
    "reversal",
    "reported",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def manifest_of(run):
    return (run or {}).get("manifest") or {}


def fingerprint(manifest):
    """结论指纹：输入摘要 + 规则版本 + 参数。"""
    manifest = manifest or {}
    material = "|".join(
        [
            str(manifest.get("input_sha256", "")),
            str(manifest.get("rule_version", "")),
            canonical(manifest.get("parameters") or {}),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def stages_of(run):
    return (run or {}).get("stages") or []


def final_stage(run):
    stages = stages_of(run)
    return stages[-1] if stages else {}


def valuation_stage(run):
    """进度对账阶段是结论口径所在；阶段缺失时退回最后一阶段。"""
    stages = stages_of(run)
    if len(stages) > 5:
        return stages[5]
    return stages[-1] if stages else {}


def summary_of(run):
    metrics = valuation_stage(run).get("metrics") or {}
    return {
        "expected_total": metrics.get("expected_total"),
        "reported_total": metrics.get("reported_total"),
        "differences": metrics.get("differences"),
        "records": metrics.get("output_records"),
        "balanced": metrics.get("balanced"),
    }


def _record_key(row):
    claim = row.get("claim_id")
    if claim not in (None, ""):
        return str(claim)
    sources = row.get("_sources") or []
    return "line:%s" % (sources[0] if sources else row.get("_line", "?"))


def _source(row):
    """来源签名：只看输入原值，忽略解析时附加的位置信息。"""
    raw = row.get("_raw")
    if isinstance(raw, list):
        return canonical(raw)
    return canonical({key: row.get(key) for key in RAW_FIELDS})


def merge(rows):
    merged = {}
    for row in rows:
        merged[_record_key(row)] = row
    return merged


def quarantine_by_claim(run):
    return {
        str(row["claim_id"]): row
        for row in final_stage(run).get("cumulative_quarantine") or []
        if row.get("claim_id") not in (None, "")
    }


def _field_changes(old_row, new_row):
    changes = []
    for key in sorted(set(old_row) | set(new_row)):
        if key.startswith("_") or key in IGNORED_FIELDS:
            continue
        before, after = old_row.get(key), new_row.get(key)
        if canonical(before) == canonical(after):
            continue
        changes.append({"field": key, "before": jsonable(before), "after": jsonable(after)})
    return changes


def _metric_cause(data_changed, rule_changed):
    if data_changed and rule_changed:
        return "both"
    if data_changed:
        return "data"
    if rule_changed:
        return "rule"
    return "none"


def baseline(run):
    """首版结论没有可比对的上一版，明确记为基线。"""
    manifest = manifest_of(run)
    return {
        "baseline": True,
        "from_run_id": None,
        "to_run_id": (run or {}).get("run_id"),
        "generated_at": manifest.get("generated_at"),
        "data_changed": False,
        "rule_changed": False,
        "rule_version_changed": False,
        "causes": [],
        "data": {"changed": False, "input_sha256": {"before": None, "after": manifest.get("input_sha256")}},
        "rules": {
            "changed": False,
            "rule_version": {"before": None, "after": manifest.get("rule_version")},
            "parameters": {"changed": False, "added": [], "removed": [], "changed": [], "values": {}},
        },
        "records": {
            "counts": {"added": 0, "removed": 0, "changed": 0},
            "by_cause": {"data": 0, "rule": 0, "both": 0},
            "details": [],
            "details_truncated": 0,
        },
        "metrics": {"changes": [], "truncated": False},
        "from_summary": None,
        "summary": summary_of(run),
    }


def compare(previous, current):
    """比较前后两版结论，返回可存档、可回查的差异说明。"""
    old_manifest, new_manifest = manifest_of(previous), manifest_of(current)
    old_params = old_manifest.get("parameters") or {}
    new_params = new_manifest.get("parameters") or {}

    data_changed = old_manifest.get("input_sha256") != new_manifest.get("input_sha256")
    version_changed = old_manifest.get("rule_version") != new_manifest.get("rule_version")
    added_params = sorted(set(new_params) - set(old_params))
    removed_params = sorted(set(old_params) - set(new_params))
    changed_params = [
        key
        for key in sorted(set(old_params) & set(new_params))
        if canonical(old_params[key]) != canonical(new_params[key])
    ]
    parameters_changed = bool(added_params or removed_params or changed_params)
    rule_changed = version_changed or parameters_changed

    causes = []
    if data_changed:
        causes.append("data")
    if rule_changed:
        causes.append("rules")

    old_rows, new_rows = merge(final_stage(previous).get("rows") or []), merge(final_stage(current).get("rows") or [])
    old_quarantine, new_quarantine = quarantine_by_claim(previous), quarantine_by_claim(current)

    counts = {"added": 0, "removed": 0, "changed": 0}
    by_cause = {"data": 0, "rule": 0, "both": 0}
    details = []
    dropped = 0

    def attribute(data_side):
        """逐条归因：本行原值变了记数据侧；原值没变而结论翻转记规则侧。

        两侧都动过时，本行原值变过的记 both（派生值还可能受新规则影响）；
        原值没变的仍记 rule，不把别处数据变化算到它头上。只改了数据时，
        因跨行规则（如同周期重复）受影响的行仍归数据。
        """
        if not rule_changed:
            return "data"
        return "both" if data_side else "rule"

    def note(claim, change, cause, fields=None, sources=None):
        counts[change] += 1
        by_cause[cause] += 1
        nonlocal dropped
        if len(details) >= MAX_RECORD_DETAILS:
            dropped += 1
            return
        entry = {"claim_id": claim, "change": change, "cause": cause}
        if sources:
            entry["source_lines"] = sources
        if fields:
            entry["fields"] = fields
        details.append(entry)

    for claim in sorted(set(old_rows) - set(new_rows)):
        other = new_quarantine.get(claim)
        same_evidence = other is not None and _source(other) == _source(old_rows[claim])
        note(claim, "removed", attribute(not same_evidence), sources=(old_rows[claim].get("_sources") or []))
    for claim in sorted(set(new_rows) - set(old_rows)):
        other = old_quarantine.get(claim)
        same_evidence = other is not None and _source(other) == _source(new_rows[claim])
        note(claim, "added", attribute(not same_evidence), sources=(new_rows[claim].get("_sources") or []))
    for claim in sorted(set(old_rows) & set(new_rows)):
        old_row, new_row = old_rows[claim], new_rows[claim]
        fields = _field_changes(old_row, new_row)
        if not fields:
            continue
        note(
            claim,
            "changed",
            attribute(_source(old_row) != _source(new_row)),
            fields=fields,
            sources=(new_row.get("_sources") or []),
        )

    metric_cause = _metric_cause(data_changed, rule_changed)
    metric_changes = []
    truncated = False
    for index, (old_stage, new_stage) in enumerate(zip(stages_of(previous), stages_of(current))):
        old_metrics = old_stage.get("metrics") or {}
        new_metrics = new_stage.get("metrics") or {}
        for key in sorted(set(old_metrics) | set(new_metrics)):
            if canonical(old_metrics.get(key)) == canonical(new_metrics.get(key)):
                continue
            if len(metric_changes) >= MAX_METRIC_CHANGES:
                truncated = True
                break
            metric_changes.append(
                {
                    "stage": index,
                    "stage_name": MODULES[index][1] if index < len(MODULES) else "阶段%s" % index,
                    "metric": key,
                    "before": jsonable(old_metrics.get(key)),
                    "after": jsonable(new_metrics.get(key)),
                    "cause": metric_cause,
                }
            )

    return {
        "baseline": False,
        "from_run_id": (previous or {}).get("run_id"),
        "to_run_id": (current or {}).get("run_id"),
        "generated_at": new_manifest.get("generated_at"),
        "data_changed": data_changed,
        "rule_changed": rule_changed,
        "rule_version_changed": version_changed,
        "causes": causes,
        "data": {
            "changed": data_changed,
            "input_sha256": {"before": old_manifest.get("input_sha256"), "after": new_manifest.get("input_sha256")},
        },
        "rules": {
            "changed": rule_changed,
            "rule_version": {"before": old_manifest.get("rule_version"), "after": new_manifest.get("rule_version")},
            "parameters": {
                "changed": parameters_changed,
                "added": added_params,
                "removed": removed_params,
                "changed": changed_params,
                "values": {
                    key: {"before": jsonable(old_params.get(key)), "after": jsonable(new_params.get(key))}
                    for key in changed_params
                },
            },
        },
        "records": {"counts": counts, "by_cause": by_cause, "details": details, "details_truncated": dropped},
        "metrics": {"changes": metric_changes, "truncated": truncated},
        "from_summary": summary_of(previous),
        "summary": summary_of(current),
    }
