# 深度登记 商圈客流设备重叠与人数区间估计

规模：中型。核心规则：无法识别个人时只输出满足区域交集约束的人数上下界；矛盾观测隔离而非伪造精确人数。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 设备采信 | 查看并复算设备采信 | `/api/m1_ingest` | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py` |
| M2 区域映射 | 查看并复算区域映射 | `/api/m2_mapping` | `app/modules/m2_mapping/rules.py` | `tests/test_mapping.py` |
| M3 观测分桶 | 查看并复算观测分桶 | `/api/m3_versions` | `app/modules/m3_versions/rules.py` | `tests/test_versions.py` |
| M4 重叠约束 | 查看并复算重叠约束 | `/api/m4_segments` | `app/modules/m4_segments/rules.py` | `tests/test_segments.py` |
| M5 人数估界 | 查看并复算人数估界 | `/api/m5_calculate` | `app/modules/m5_calculate/rules.py` | `tests/test_calculate.py` |
| M6 日汇对账 | 查看并复算日汇对账 | `/api/m6_reconcile` | `app/modules/m6_reconcile/compare.py` | `tests/test_reconcile.py` |
| M7 报告归档 | 查看并复算报告归档 | `/api/m7_archive` | `app/modules/m7_archive/export_out.py`、`app/modules/m7_archive/review.py` | `tests/test_archive.py`、`tests/test_review.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | GB18030中文 | 固定UTF-8失败 | 按编码链回退 | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_legacy_encoding` |
| TRAP-02 | 表头BOM | 首列键失配 | 去除BOM | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_bom` |
| TRAP-03 | 引号内换行 | 分行误计 | csv逻辑记录 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_preserves_multiline_note` |
| TRAP-05 | 列数错位 | 静默补列 | WIDTH隔离 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_width_isolation` |
| TRAP-06 | 全角千分位 | float失败 | NFKC与Decimal | `app/modules/m2_mapping/coerce.py` | `tests/test_mapping.py::test_thousands` |
| TRAP-07 | 空值哨兵 | 空值当零 | MISSING隔离 | `app/modules/m2_mapping/coerce.py` | `tests/test_mapping.py::test_missing_numeric` |
| TRAP-08 | 前导零ID | 编码失真 | 字符串保形 | `app/modules/m2_mapping/rules.py` | `tests/test_mapping.py::test_identifier_zeroes` |
| TRAP-10 | 浮点误差 | 累计失准 | Decimal求和 | `app/modules/m2_mapping/coerce.py` | `tests/test_mapping.py::test_decimal_precision` |
| TRAP-14 | 同版冲突 | 最后覆盖 | 整组隔离 | `app/modules/m2_mapping/rules.py` | `tests/test_mapping.py::test_real_conflict_quarantine` |
| TRAP-18 | 三种空态 | 除零或伪成功 | 三个错误码 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_empty_three_states` |
| TRAP-26 | 旧版本复核 | 覆盖新结论 | 事务条件更新 | `app/store.py` | `tests/test_archive.py::test_stale_review` |
| TRAP-11 | 时区混用 | 同刻不同桶 | UTC归整 | `app/modules/m2_mapping/coerce.py` | `tests/test_versions.py::test_same_instant` |
| TRAP-19 | 三方约束矛盾 | 输出伪精确数 | 非负原子区域可行检查 | `app/modules/m5_calculate/rules.py` | `tests/test_calculate.py::test_three_way_contradiction` |
| TRAP-31 | 复核顺序单一维度 | 纯客流漏掉高收窄设备，纯不确定度放过高客流区 | 按预期可确认客次(可收窄×客流暴露)降序 | `app/modules/m7_archive/review.py` | `tests/test_review.py::test_order_by_expected_confirmed_customers` |
| TRAP-32 | 补录全量重算 | 无关时段被重判 | 只重判受影响时段，其余沿用旧结论，被取代隔离记录留存 | `app/modules/m7_archive/review.py` | `tests/test_review.py::test_supplement_rejudges_only_affected_bucket` |
| TRAP-33 | 重跑新增复核任务 | 任务膨胀重复 | 任务按设备+时段确定性主键幂等入库 | `app/store.py` | `tests/test_review.py::test_same_batch_creates_no_new_tasks` |
| TRAP-34 | 切换中途失败 | 新版本半写入 | 批次、任务、最新指针同事务，失败保留旧版 | `app/store.py` | `tests/test_review.py::test_failed_switch_keeps_old_version` |

## 对账规则

输入行数=采纳+隔离+空行+重复表头+完全重复；业务总数与计算证据独立求和。隔离行保留来源行、ID、原因码与原值。

## 已知不覆盖

不做多租户权限和外部服务联调。行业规则以项目数据字典为准。文件上限2MB，逐行解析后在上限内完成分组计算。
