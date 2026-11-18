# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 结论版本

一版结论的身份=输入sha256+规则版本+处理参数（`app/conclusion.py` 的 `fingerprint`）。各项接口：

- `POST /api/runs`：`{sample|"csv", parameters}`。若同身份的结论已存在，直接返回该记录并带 `"reused": true`，不新增版本、不改版本号、不移动现行结论；否则写入新版本，原现行结论在同一事务内转为 `superseded`。响应包含 `run_id`、`version`、`state`、`is_current`、`origin_run_id`、`replaced_run_id`、`superseded_by`、`fingerprint`、`reviews`、`activations`、`comparison`。
- `GET /api/runs/current`：唯一现行结论；没有时为404。
- `GET /api/runs`：`{"runs":[...], "current": id}`，每条含 `state`/`is_current`/`replaced_run_id`/`superseded_by`/`created_at`。
- `GET /api/runs/<id>`：任一历史版本，`state` 标明 `current` 或 `superseded`；被替代版本的 `comparison` 仍是当时生成时的对照结果，不随时间改写。
- `GET /api/runs/<id>/comparison[?against=<id>]`：默认与 `origin_run_id` 比较；`against` 可指定任意版本做横向复核。首版无对照且未指定 `against` 时返回409 `NO_BASELINE`。
- `POST /api/runs/<id>/activate`（别名 `/restore`、`/revert`）：`{version?, expected_current?}`，把被替代版本切回现行。任一校验失败（版本失效409 `STALE_VERSION`、现行已变化409 `CURRENT_MOVED`、批次缺失404）整体回滚，原现行结论保持不变；目标已是现行时为空操作。
- `POST /api/runs/<id>/review`：`{version, decision}`，版本条件更新，回写复核人与时间到审计表。只接受现行结论；复核被替代版本返回409 `SUPERSEDED`，版本过期返回409 `STALE_VERSION`。被替代版本已有的复核记录原样保留。
- `GET /api/runs/<id>/archive`：证据CSV，依次输出结果行、隔离行、批次状态、复核记录、现行切换记录，以及本版与生成时上一版的差异（记录级新增/移出/变更及成因、指标级前后值及成因）。

## comparison 结构

```json
{
  "baseline": false,
  "from_run_id": "...", "to_run_id": "...",
  "data_changed": true,  "rule_changed": false,  "causes": ["data"],
  "data": {"changed": true, "input_sha256": {"before": "...", "after": "..."}},
  "rules": {"changed": false, "rule_version": {"before": "...", "after": "..."},
            "parameters": {"changed": false, "added": [], "removed": [], "changed": [], "values": {}}},
  "records": {"counts": {"added": 0, "removed": 0, "changed": 1},
              "by_cause": {"data": 1, "rule": 0},
              "details": [{"claim_id": "00002", "change": "changed", "cause": "data",
                           "source_lines": [4], "fields": [{"field": "cumulative_qty", "before": "125", "after": "130"}]}],
              "details_truncated": 0},
  "metrics": {"changes": [{"stage": 4, "stage_name": "扣回核算", "metric": "net_total",
                           "before": "68425.00", "after": "67620.00", "cause": "rule"}], "truncated": false},
  "from_summary": {"expected_total": "...", "reported_total": "...", "differences": 31, "records": 32, "balanced": true},
  "summary": {"expected_total": "...", "reported_total": "...", "differences": 32, "records": 32, "balanced": true}
}
```

成因判定：批次层面看清单——输入sha256不同→`data`，规则版本或任一参数不同→`rules`，同时成立则 `causes` 同时列出。逐条记录再按其自身来源原值判断：原值变了记 `data`（两个层面都动了记 `both`，因为派生值还可能受新规则影响）；原值没变而结论翻转记 `rule`；只改数据时因跨行规则连带变化的记录仍归 `data`。指标变化沿用批次层面的成因。明细最多保留100条记录、100项指标，超出部分在各 `truncated` 字段标出，计数不受截断影响。
