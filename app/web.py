"""工作台页面：以现行结论为准，同时保留被替代版本与差异依据。"""
from flask import render_template, request

from app import store
from app.config import MODULES, TITLE, SHORT
from app.pipeline import execute, load_sample

STAGE_COLUMNS = [
    ["claim_id", "item", "period", "cumulative_qty", "previous_qty", "change_id", "reported"],
    ["claim_id", "item", "unit", "cumulative_qty", "previous_qty"],
    ["claim_id", "period", "approved_change", "unit_price"],
    ["claim_id", "period", "cumulative_qty", "approved_previous", "increment_qty", "gross_amount"],
    ["claim_id", "item", "gross_amount", "retention", "advance_recovery", "expected"],
    ["claim_id", "item", "expected", "reported", "difference", "status"],
    ["claim_id", "period", "expected", "status"],
]

LABELS = {
    "claim_id": "申报编号",
    "item": "清单项",
    "period": "申报周期",
    "cumulative_qty": "本次累计量",
    "previous_qty": "前审累计量",
    "change_id": "变更单",
    "reported": "申报净额",
    "unit": "单位",
    "approved_change": "已批变更",
    "unit_price": "单价 / 元",
    "approved_previous": "修订前审量",
    "increment_qty": "本期工程量",
    "gross_amount": "本期总额",
    "retention": "质保金",
    "advance_recovery": "预付款扣回",
    "expected": "实算净额",
    "difference": "净额差异",
    "status": "结论",
    "gross_total": "本期总额合计",
    "net_total": "实算净额合计",
    "accepted_inputs": "采纳来源行",
    "quarantine_inputs": "隔离来源行",
    "output_records": "结果记录",
    "input": "输入行",
    "excluded": "排除行",
    "balanced": "行数平衡",
    "expected_total": "预期合计",
    "reported_total": "申报合计",
    "differences": "差异项",
    "blank": "空行",
    "repeated_headers": "分段表头",
    "encoding": "编码",
    "duplicate": "重复行",
    "quarantine": "隔离行",
    "accepted": "采纳行",
    "sha256": "校验摘要",
    "ready": "可归档",
    "records": "归档记录",
    "source_lines": "来源行",
    "versions": "生效变更",
}

STATE_LABELS = {"current": "现行", "superseded": "被替代"}
CAUSE_LABELS = {"data": "数据变化", "rule": "规则变化", "both": "数据与规则均变化", "none": "无变化"}


def _workbench():
    """默认展示现行结论；指定 run_id 时回看任意一版，仍标注其现行或被替代状态。"""
    requested = request.args.get("run")
    if requested:
        return store.get(requested)
    run = store.current()
    if run is None:
        run = execute(load_sample("dirty.csv"))
    return run


def register_pages(app):
    def page(index):
        run = _workbench()
        stage = run["stages"][index]
        rows = stage["rows"]
        return render_template(
            f"m{index + 1}.html",
            title=TITLE,
            short=SHORT,
            modules=MODULES,
            index=index,
            run=run,
            stage=stage,
            rows=rows,
            columns=STAGE_COLUMNS[index][:12],
            history=store.history(),
            labels=LABELS,
            state_labels=STATE_LABELS,
            cause_labels=CAUSE_LABELS,
        )

    app.add_url_rule("/", view_func=lambda: page(4))
    for index, (slug, name) in enumerate(MODULES):
        app.add_url_rule("/" + slug, endpoint="page_" + slug, view_func=lambda index=index: page(index))
