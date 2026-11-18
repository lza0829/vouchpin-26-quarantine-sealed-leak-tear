# Vouchpin（券钉簿）

骨架：券事件入湖 → 核销事实钉扎 → 过期/退回窗（后挖）→ 批次互证。

## 非法核销收口：隔离区

非法核销（无对应发放、金额非正、活动不存在/不一致、重复核销、业务日已钉扎）一律**不拒写、不入 open 事实，改入隔离区**，全局只有这一条路径：

- 事件仍入事件湖（带 `reason_code`），可审计；同笔事务内写 `redeem_quarantine` 行（`reason_code` + 中文原因）。
- 隔离行**不**物化为 `redeem_facts`，因此天然不进钉扎预览、不进 sealed 合计、不进总览 `redeem_total_today` / `fact_count`；总览单独给出 `illegal_redeem_count`、`quarantine_count` 等隔离计数。
- 晋升：`POST /api/quarantine/{id}/promote` 重新跑完整校验（活动存在、金额为正、有对应发放、未重复核销、当日未钉扎），通过后才物化为 open 事实。
- 丢弃：`POST /api/quarantine/{id}/discard`，终态，丢弃后不可晋升。
- 晋升与丢弃均写 `audit_log`（`GET /api/audit?target_type=quarantine&target_id=`），记录前后金额/活动、原因码与备注。
- 并发：晋升与钉扎都在 `BEGIN IMMEDIATE` 下执行，故晋升先提交则被钉入 sealed，钉扎先提交则晋升返回 409，不存在未晋升行被钉进 sealed 的中间态。
- 页面：`/quarantine` 隔离区（晋升/丢弃/审计轨迹）、`/redeem` 预览显示“隔离已排除”、`/events` 显示事件去向、总览显示隔离计数。

## 本地

- API: `cd backend && python -m uvicorn app.main:app --port 10800`
- Web: `cd frontend && npm install && npm run dev`（5800）
- 测试: `cd backend && pip install -r requirements-dev.txt && python -m pytest tests -q`
