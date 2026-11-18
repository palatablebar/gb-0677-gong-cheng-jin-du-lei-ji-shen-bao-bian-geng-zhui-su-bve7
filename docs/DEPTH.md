# 深度登记 工程进度累计申报与变更追溯

规模：中型。使用者：总包项目计量审核员。核心规则：累计申报必须减前期已审金额，变更按审批生效版本覆盖；负进度需关联冲回证据。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 申报采信 | 查看并运行申报采信 | `/api/claims` | `app/modules/m1_claims/rules.py` | `tests/test_m1.py` |
| M2 清单映射 | 查看并运行清单映射 | `/api/billitems` | `app/modules/m2_billitems/rules.py` | `tests/test_m2.py` |
| M3 变更定版 | 查看并运行变更定版 | `/api/changes` | `app/modules/m3_changes/rules.py` | `tests/test_m3.py` |
| M4 累计还原 | 查看并运行累计还原 | `/api/increment` | `app/modules/m4_increment/rules.py` | `tests/test_m4.py` |
| M5 扣回核算 | 查看并运行扣回核算 | `/api/deductions` | `app/modules/m5_deductions/rules.py` | `tests/test_m5.py` |
| M6 进度对账 | 查看并运行进度对账 | `/api/valuation` | `app/modules/m6_valuation/rules.py` | `tests/test_m6.py` |
| M7 签证归档 | 查看并运行签证归档 | `/api/certificates` | `app/modules/m7_certificates/rules.py` | `tests/test_m7.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码 | 默认解码失败 | 回退GB18030 | `app/modules/m1_claims/rules.py` | `tests/test_m1.py::test_legacy_encoding_real_sample` |
| TRAP-02 | BOM | 首列名多字符 | utf-8-sig | `app/modules/m1_claims/rules.py` | `tests/test_m1.py::test_bom_real_sample` |
| TRAP-03 | 带引号换行 | 拆成两条 | csv逻辑行 | `app/modules/m1_claims/rules.py` | `tests/test_m1.py::test_quoted_newline_is_one_record` |
| TRAP-05 | 列数不匹配 | 静默错列 | 隔离原行 | `app/modules/m1_claims/rules.py` | `tests/test_m1.py::test_bad_columns_real_sample` |
| TRAP-08 | 前导零ID | 编码变形 | 全程字符串 | `app/modules/m1_claims/rules.py` | `tests/test_m1.py::test_identifier_preserved_real_sample` |
| TRAP-14 | 重复键冲突 | 后写覆盖 | 保留首条并隔离冲突行 | `app/modules/m1_claims/rules.py` | `tests/test_m1.py::test_duplicate_conflict_real_sample` |
| TRAP-18 | 空态三态 | 一律成功 | 分别错误码 | `app/modules/m1_claims/rules.py` | `tests/test_m1.py::test_three_empty_states` |
| TRAP-26 | 旧版本提交 | 覆盖新结果 | 版本条件更新 | `app/store.py` | `tests/test_e2e.py::test_stale_review_409` |
| TRAP-06 | 全角累计量 | 转换失败 | Decimal归一 | `app/values.py` | `tests/test_m2.py::test_fullwidth_quantity` |
| TRAP-07 | 累计量空值 | 按零冲回 | 隔离缺失 | `app/values.py` | `tests/test_m2.py::test_real_null_not_zero` |
| TRAP-10 | 分币舍入 | 浮点误差 | 分项量化后净额守恒 | `app/modules/m5_deductions/rules.py` | `tests/test_m5.py::test_fraction_rounds_to_cent` |
| TRAP-23 | 变更生效边界 | 旧价覆盖 | 按周期选择已批版本 | `app/modules/m3_changes/rules.py` | `tests/test_m3.py::test_effective_boundary` |

## 对账规则

输入原始逻辑行=最终采纳来源行+隔离来源行+重复行+空白行+分段表头。派生记录用_sources保存完整来源，因此分组不丢行。业务预期合计与上游申报合计之差等于逐行差异之和。结果按输入摘要、规则版本及参数产生唯一批次，事务写入；同源重跑只复用已有批次。

## 结论重算与替代（TRAP-27）

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-27 | 补正后重算的结论 | 覆盖旧结果，旧结论与复核依据丢失 | 每版结论只改状态不改内容，同身份重跑复用 | `app/store.py`、`app/conclusion.py` | `tests/test_revisions.py` |

- 结论身份=`input_sha256`+`rule_version`+参数。同身份重跑命中已有记录，返回 `reused: true`，不新增版本、不移动现行结论。
- 新身份写入时，原现行结论在同一事务内转为 `superseded`；数据库部分唯一索引保证任一时刻只有一条 `current`。
- 差异分因：sha256 变化记“数据变化”，规则版本或参数变化记“规则变化”；逐条记录再按来源原值是否变化细分，指标变化随批次标出整体成因。
- `replaced_run_id` 记录本版最近替代了谁（随切换更新），`origin_run_id` 记录生成时的对照版本（不可变）。因此回看被替代版本时，差异依据与当时一致。
- 切换用单事务 + 条件更新：目标版本失效、并发现行已变化或批次缺失都整体回滚，原现行结论与结论内容保持不变。
- 复核、切换均追加审计记录，证据CSV含批次状态、复核记录、切换记录与差异明细。复核只接受现行结论，被替代版本保留历史复核记录但不再接受新签认。

## 已知不覆盖

4MB以上文件需预先按业务键分批；不模拟生产支付与外部回调。人工复核只变更批次状态，不改写原始记录。版本链按单机单库维护，不提供多用户同时切换的分布式锁。
