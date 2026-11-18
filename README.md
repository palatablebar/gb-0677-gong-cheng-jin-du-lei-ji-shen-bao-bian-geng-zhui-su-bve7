# 工程进度累计申报与变更追溯

供总包项目计量审核员核对原始记录、处理差异并封存证据。

## 启动

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

打开 http://localhost:18246 。首次进入自动运行仓内脏样例。工具条可重跑、切换样例或上传CSV。SQLite保存在命名卷records中，重建服务保留批次。

## 模块

- 1. 申报采信：`/claims`，`/api/claims`。
- 2. 清单映射：`/billitems`，`/api/billitems`。
- 3. 变更定版：`/changes`，`/api/changes`。
- 4. 累计还原：`/increment`，`/api/increment`。
- 5. 扣回核算：`/deductions`，`/api/deductions`。
- 6. 进度对账：`/valuation`，`/api/valuation`。
- 7. 签证归档：`/certificates`，`/api/certificates`。

## 核心规则

累计申报必须减前期已审金额，变更按审批生效版本覆盖；负进度需关联冲回证据。

## 结论重算

补正后重算不会覆盖旧结论，也不会让同一事实出现多个现行结论：

- 任一时刻只有一条现行结论；新结论写入时，旧版在同一事务内转为“被替代”，内容与复核依据原样保留。
- 相同输入与处理规则重跑直接复用已有结论，不新增版本。
- 每版结论注明与生成时上一版的差异来自数据变化、规则变化，还是两者都有，并给出逐条记录与指标的前后值。
- 工作台可回看任意被替代版本，并可在校验通过时切换回它；切换失败（版本失效、现行已变化、批次缺失）时旧版结论保持不变。

## 演示

1. 打开首页，核对当次计算、差异和隔离原因。
2. 切换模块查看源行及裁决依据。
3. 重跑相同样例，批次号与行数保持一致，版本链不增加。
4. 换一个样例或调整扣回参数，查看“结论版本”面板里的差异来源与明细。
5. 在“版本链”里点开任一被替代版本，核对当时的结论与复核记录，必要时切回为现行。
6. 复核后导出一份证据CSV。版本已变化的复核提交返回409。

## 输入与输出

输入列见 `docs/DATA-DICTIONARY.md`。项目包含dirty.csv、clean.csv、legacy.csv，统一走实际导入路径。上传限制4MB，拒绝不带时区的时间。业务键按字符串保留，Decimal参与计算，空值不会当零。

`POST /api/runs` 接收sample或csv；`GET /api/runs`返回历史批次与现行批次；`GET /api/runs/current`返回现行结论；`GET /api/runs/<id>/comparison`返回差异说明；`POST /api/runs/<id>/activate`切回被替代版本；`POST /api/runs/<id>/review`需要version与decision；`GET /api/runs/<id>/archive`返回唯一证据导出。

## 范围

样例均为虚构。单机复核工具不连接生产系统；不提供登录、远端数据库或自动支付。内存计算按4MB输入上限控制，SQLite事务保证结果完整写入。

## 目录

app为服务与规则，sample_data为输入，tests为测试，docs为定义与深度登记。
