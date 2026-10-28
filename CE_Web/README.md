# Counterexample Web 复现工程

基于论文 *Counterexample Web: Collective Self-Evolution of Web Agents Through Verifiable Failures* 第 3 节、公式 (1)–(20) 和 Algorithm 1，实现面向独立验证的反例驱动自进化流水线。

## 能力

- 基于独立环境状态检查识别来源失败，执行多轮重新初始化与重放认证。
- 使用操作语义、前提谓词、可恢复干预、后置条件、可观察事件以及来源证书表示可迁移契约。
- 将契约在不同站点的实体类型、操作与授权干预上进行匹配，禁止把来源结果当作目标结果。
- 对可选条件执行逐项消减，并在来源环境重新认证。
- 用包含认证、grounding、复现、修复和验收结果的证据图计算式 (11) 选择分数。
- 仅对接收端已经复现的失败生成有限候选策略，通过互不重叠的拟合任务与独立门控任务进行筛选。
- 在新挑战与既有任务上分别计算成对差值、单侧 bootstrap 下界，并同时满足修复门与回归门才持久化策略。
- 在冻结策略后运行独立 held-out 评测，导出 JSON 证据图、YAML 契约及策略。

## 环境与启动

推荐 Python 3.10 及以上。

```bash
python -m pip install -e .
python -m ceweb.cli validate --config configs/default.yaml
python -m ceweb.cli run --config configs/default.yaml --output runs/demo
```

`runs/demo/report.json`、`runs/demo/evidence_graph.json`、`runs/demo/policy.yaml` 与 `runs/demo/certified_contracts.yaml` 由运行时生成。

## 配置

- `configs/default.yaml`：随机种子、预算、认证重放次数与门槛、选择函数的先验与权重、候选修改空间、拟合和门控样本数、统计检验及代理配置。
- `configs/sites.yaml`：站点版本、语义角色、操作绑定、授权干预、重置与独立状态验证能力。
- `configs/contracts.yaml`：来源失败假说和声明式反例契约。

默认 `memory` 站点模拟三种不同系统的记录持久化行为：事件编辑、内容发布和工单提交。此设置是可重复的离线功能验证环境，不等同于论文使用的 BreakingWeb、WebArena-Verified 或 TimeWarp 数据集。新增有可控复位和独立状态验证的真实站点时，需要通过 `Environment` 接口提供执行、干预及验证适配器；没有独立验证的真实站点不能用于认证。

## 论文与实现边界

论文将基础模型参数视为固定，改变的主要是允许更新的代理策略组件。本文提供的是算法复现工程，不包含基础语言模型权重、论文尚未提供的原始实验数据或访问凭据，不能声称复现论文中报告的实验数值。

论文没有完整指定契约 DSL、图先验估计、策略搜索的离散空间或置信下界数值算法。本实现分别采用声明式谓词、Beta 平滑复现概率/代价经验估计、受约束枚举变异和成对 bootstrap 下置信界；这些具体工程选择均可通过配置调整，并非声称是论文作者的私有实现。

source URL as printed in paper: https://anonymous.4open.science/r/CE-BE84/

## 主要文件与论文对应

| 论文组成 | 工程实现 |
| --- | --- |
| 式 (4) 反例契约 | `ceweb/models.py`、`configs/contracts.yaml` |
| 式 (5) 违规指示器 | `ceweb/predicates.py` |
| 式 (6)–(7) 来源认证与缩减 | `ceweb/certification.py` |
| 式 (8) 目标站点 grounding | `ceweb/environment.py` |
| 式 (9)–(12) 证据图与选择函数 | `ceweb/graph.py` |
| 式 (13)–(16) 候选策略适配 | `ceweb/evolution.py` |
| 式 (17)–(20) 成对门控与永久更新 | `ceweb/evolution.py`、`ceweb/engine.py` |
| Algorithm 1 主循环与 held-out | `ceweb/engine.py` |

## 可替换的模型和站点

默认离线运行无需 API 密钥或网络连接。项目保留 `ceweb/llm.py` 与 `ceweb/http_environment.py`，用于接入模型服务和支持独立验证的外部站点。可直接在 YAML 中配置 `mutation.strategy: llm`、`mutation.model`，或在 `configs/sites.yaml` 中配置 `driver: http` 及服务地址、端点和鉴权环境变量；基础模型的候选策略仍受 `mutation.allowed_values` 与独立门控约束。

外部 HTTP 环境适配器使用 `/reset`、`/intervene`、`/write`、`/read` 和 `/inspect` 等可配置端点。外部系统需要支持隔离重置、可恢复干预及独立真实状态验证。示例配置文件已从本精简包中移除；核心实现和配置入口均保留。

## 已执行的验证

默认端到端运行会生成一条通过来源认证的反例、一次接收端复现、一次通过双门验收的策略更新；独立 held-out 测试显示在该合成条件下更新前的干预完成率为 0、更新后为 1，同时清洁任务完成率保持为 1。以上数字是示例环境的功能检查，不能解释为真实 Web benchmark 成绩。

本版本已移除 tests 目录、两份 *_example.yaml 配置、测试开发依赖及预生成运行结果；保留默认运行所需的 3 份 YAML 配置。
