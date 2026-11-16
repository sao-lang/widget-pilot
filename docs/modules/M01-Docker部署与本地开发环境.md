# M01 · Docker 部署与本地开发环境（v1.2 增补模块，原文 §12.2）

> **文档类型**：可独立交付 / 可独立验收的模块设计文档（补充模块，与 M18/M19/M20 同批 v1.2 增补）
> **递进层定位**：**L1 工程基建（全集第一层）**——为 M02–M20 全部模块供给可复现的开发与验收环境；本层出口未达成（`make dev-up` 全绿 + seed 完成）不得进入后续层联调（00 总览 §2.3）
> **来源**：`设计方案.md` **v1.2 §12.2（本模块的原文增补章节）**、§7.1/§7.2/§7.3（工程结构与部署形态）、§5 D5 / M08 §2.3/§3.6（一次性沙箱镜像与 seccomp/cgroups，本模块只引用）、00 总览 §6（monorepo 代码落位）；本模块是拆分评审发现的**能力缺口补充**——原文档集只有 M08 的一次性沙箱镜像（`infra/sandbox`）与一句生产编排（§7.1 `infra/deploy` Helm/terraform），没有本地开发容器化方案、没有各服务镜像构建标准、没有 CI 容器策略
> **版本**：v1.1　|　**拆分日期**：2026-11-16　|　**状态**：设计定稿（含 7 项待决，见 §9；v1.1 增补 §11 全模块技术栈汇总与镜像分层构建标准）　|　**最近修订**：2026-11-16（v1.3，ADR-001 同步）
> **对应编号**：无独立 FR —— 支撑全部 FR 的**工程底座**；承载 D-5「保留的通用基础设施」中 Postgres / Redis / 对象存储 / OTel 的**开发期实例供给**
> **主责目录**：`docker-compose.dev.yml`、`Makefile`、`infra/docker/`（Dockerfile 与 .dockerignore）、`infra/dev/`（initdb / seed / 端口检测）、`.env.example`、`.github/workflows/ci-containers.yml`（CI 容器策略）
> **一句话定义**：提供「开发时 redis/db 等直连 docker、应用服务跑本机」的本地开发栈（compose profiles），定义全部 7 个服务 + web 的镜像构建标准（multi-stage / 非 root / HEALTHCHECK / 层缓存 / tag 策略），约定 CI 起真实依赖跑集成测试的容器策略，并保证 dev 与生产 Helm 配置同构、镜像一次构建多环境部署。

---

## 0. 模块速览（零基础可读）

### 0.1 项目在做什么（本模块所需的最小背景）

WidgetPilot 是一个「用自然语言生成手机桌面控件」的 AI Agent 产品：规格可选预填（六种 grid，可跳过），缺省由系统询问补全，系统不推断【v1.3 ADR-001 D-6】；用户输入描述、可传附件，系统生成 TSX + React 代码，经编译与一次性沙箱测试后产出控件包。后端是 5 个 Python 服务（control-plane / orchestrator / guardrails / retriever / sandbox-runner / eval-runner），前端是 React SPA（apps/web），运行时依赖 **PostgreSQL（状态基座）、Redis（队列 / SSE 扇出 / 限流）、S3 兼容对象存储（大对象 / 证据）、OTel + Grafana + Loki（观测）**。

这些依赖在开发机上不该一个一个手动安装。本模块回答四个此前无人认领的问题：

1. **开发时依赖从哪来**：`docker-compose.dev.yml` 一条命令拉起 Postgres / Redis / MinIO（/ 观测栈），**应用服务不在容器里**——开发者在本机跑 uvicorn / vite，直连 docker 里的 db / redis / minio（用户的明确要求）。
2. **镜像怎么构建**：7 个服务 + web 的 Dockerfile 统一标准（multi-stage、非 root、HEALTHCHECK、uv / pnpm 层缓存、git sha + semver 双 tag）。
3. **CI 怎么跑集成测试**：M04 的租约并发、M08 的沙箱、M05 的对象存储测试**必须**起真实容器，策略与资源约束在本模块定义。
4. **dev 与生产怎么对齐**：同一套 env schema，镜像一次构建，dev / staging / prod 只换配置不换镜像。

### 0.2 端到端 14 步链路中的位置

```
① 登录 → 新建会话（grid 可选预填，缺省由系统询问补全【v1.3 ADR-001 D-6】）
② 输入描述 + 上传附件
③ 语义层校验
④ 进 LLM 护栏
⑤ 意图与规格确定
⑥ 上下文装配
⑦ 生成回路
⑧ 出 LLM 护栏
⑨ 契约校验
⑩ 一次性沙箱
⑪ 生成 WidgetBundle
⑫ 控件墙渲染卡片
⑬ 评测合格 → 语料回流
⑭ 全流程按 seq 写事件流与只追加审计
```

本模块**不参与任何业务步骤**，是纯工程底座：14 步的每一次落库（①⑭）、每一次队列投递、每一个证据对象写入（⑩⑪）、每一条 span 上报（⑭），在开发机上都要经由本模块供给的容器实例完成。它是「其他 17 个模块能否在自己机器上跑起来并独立提测」的前置条件。

### 0.3 为什么必须有这个模块（缺口在哪）

| 已有的 | 模块 | 缺的 |
|---|---|---|
| 一次性沙箱镜像 / seccomp / cgroups | M08（`infra/sandbox`） | 只覆盖**沙箱这一个镜像**；6 个常驻服务与 web 的镜像构建无任何标准 |
| 生产编排一句承诺（Helm / terraform / 网络策略 / CSP 模板） | 原文 §7.1（`infra/deploy`） | 没有本地开发容器化方案；开发者无从起依赖 |
| 各服务运行时依赖清单（Postgres / Redis / 对象存储 / OTel） | M04 / M08 / M11 §2.3 | 依赖「存在」，但**开发期实例如何供给、种子数据如何播种**无定义 |
| monorepo 代码落位（00 总览 §6） | 00 总览 | `infra/` 下只列了 sandbox / deploy / observability，**没有 docker / dev 目录** |

没有本模块：每个开发者手工装 Postgres + Redis + MinIO + OTel，版本各不相同，种子数据各自为政，「M04–M20 各模块独立验收」在第一步（前置条件）就不可复现；CI 里集成测试没有真实依赖，M04 的租约并发、M08 的沙箱断言只能用 mock 骗过自己。

### 0.4 上下游邻居（mermaid）

```mermaid
flowchart LR
  subgraph DEPS["本模块供给的运行时依赖"]
    PG[("postgres:16")]
    RDS[("redis:7")]
    MIO[("minio (S3 兼容)")]
    OBS[("otel-collector\n+ grafana + loki")]
  end
  subgraph APPS["应用服务（默认本机进程，--profile full 时容器化）"]
    CP["control-plane"]
    ORC["orchestrator"]
    GRD["guardrails"]
    RET["retriever"]
    SBX["sandbox-runner"]
    EVL["eval-runner"]
    WEB["apps/web (vite/nginx)"]
  end
  CI["CI 容器策略\n(compose services / testcontainers)"] --> DEPS
  DEPS --> APPS
  M08["M08 一次性沙箱镜像\ninfra/sandbox（不归本模块）"] -.镜像名与资源约束被引用.-> SBX
  HELM["生产编排 infra/deploy\nHelm / terraform（不归本模块）"] -.同一 env schema / 同一镜像.-> APPS
```

- **上游（被消费）**：M04–M20 全部模块的开发与验收环境；CI 的集成测试 job。
- **下游（消费谁）**：官方镜像 `postgres:16` / `redis:7` / `minio` / `otelcol-contrib` / `grafana` / `loki`；M08 的沙箱镜像（只引用名字与约束，不定义）。
- **平行**：生产编排（原文 §7.1 `infra/deploy`）——本模块保证「配置同构、镜像同一」，集群侧 Helm values / 网络策略 / CSP 模板不归本模块。
- **横切**：无 X* 承载；D-5「保留的通用基础设施」的开发期实例供给落在本模块。

### 0.5 原文对照

| 本模块内容 | 原文章节 / 编号 |
|---|---|
| monorepo 目录与 `infra/*` 落位 | 00 总览 §6（本模块补齐 `infra/docker/`、`infra/dev/`） |
| 服务划分（8 个服务清单、状态与伸缩依据） | §7.1（本模块按同一服务清单构建镜像） |
| 一次性沙箱镜像 / seccomp / cgroups | §5 D5、M08 §2.3/§3.6（**只引用**：镜像名 `wp-sandbox`、资源约束由 cgroups 限定） |
| 生产部署 Helm / terraform | §7.1 `infra/deploy`（M11 §2.4，**不动**；本模块保证 env schema 同构） |
| 运行时依赖：Postgres / Redis / 对象存储 / OTel | M04 §2.2、M08 §2.3、M11 §2.3（本模块给出开发期实例） |
| 大对象对象存储 key 前缀（checkpoint/audit/evidence/code/spec/trace-export） | 01 基线 §9（MinIO 初始化按该前缀建桶与目录约定） |
| 12-factor 风格配置 | §7.3（工程结构隐含；env 清单为本模块【契约约定】） |

---

## 1. 模块定位与职责边界

### 1.1 为什么必须有

三个不可替代的职责：

1. **可复现的开发环境**：M04–M20 每个模块的 §8 验收都要求「真实 Postgres + Redis（测试容器）+ 本地对象存储」。本模块让这条前置条件变成 `make dev-up` 一条命令，且版本、健康判定、种子数据全仓一致。
2. **镜像即交付物**：§7.1 的 8 个服务要上生产 Helm，必须有统一构建标准；镜像 tag（git sha）是 M11 审计与回滚（RollbackController）可指向的不可变工件。
3. **CI 的真实性**：「确定性门禁高于模型自律」意味着护栏 / 租约 / 沙箱测试必须在**真实依赖**上验证——`UPDATE ... WHERE lease_until < now()` 的并发语义在 mock 里根本不存在。

### 1.2 做什么（职责清单）

| # | 职责 | 说明 |
|---|---|---|
| R1 | `docker-compose.dev.yml` 本地依赖栈 | postgres:16 / redis:7 / minio / otel-collector + grafana + loki，healthcheck 与 named volume 全配 |
| R2 | compose profiles：`core` / `observability` / `full` / `sandbox` | 日常开发最小集默认只起 core；观测可选；全容器模式与沙箱依赖按需 |
| R3 | 应用服务**默认不在容器里** | 本机 uvicorn / vite 直连 docker 依赖；`--profile full` 提供全容器热重载模式 |
| R4 | `.env.example` 与配置注入 | 与生产同一 env schema、不同值（12-factor）；密钥不入库 |
| R5 | Makefile 开发工作流 | dev-up / dev-down / dev-reset / seed / logs / ps / migrate / port-check |
| R6 | 种子数据 `seed` | 测试账号、公共语料样本、评测 fixture、示例会话——支撑 M04–M20 独立验收桩 |
| R7 | 7 服务 + web 的 Dockerfile 构建标准 | multi-stage、非 root、HEALTHCHECK、uv / pnpm 层缓存、.dockerignore、tag 策略 |
| R8 | CI 容器策略 | 哪些集成测试必须容器、testcontainers vs compose services、沙箱特权约束、测试数据隔离 |
| R9 | Windows 开发路径 | Docker Desktop + WSL2、代码放 WSL 文件系统、LF 行尾、端口占用检测 |
| R10 | dev 与生产同构性 | 同一 env schema；镜像一次构建，多环境只换配置；schema 迁移（alembic【推导·需评审】） |

### 1.3 不做什么（明确排除，边界切割点）

| 不做 | 归属 | 边界说明 |
|---|---|---|
| 生产集群编排：Helm chart / terraform / 网络策略 / CSP 模板 / 副本数与 HPA | 原文 §7.1 `infra/deploy`（M11 §2.4） | 本模块只保证「env schema 同构、镜像同一」；chart 与 values 不在此定义 |
| 一次性沙箱镜像内容、seccomp profile、cgroups 限额数值 | M08（`infra/sandbox`） | 本模块只在 compose / CI 里**引用**镜像名 `wp-sandbox:{tag}` 与「超时强制销毁、资源限额」约束 |
| 各服务业务配置项语义（预算上限、护栏阈值、kill switch） | M07 / M08 / M11 | 本模块只负责把这些配置**注入**容器（env），不定义其取值 |
| schema 与表结构本身 | M04 / M11 等拥有者 | 本模块负责**执行迁移**（`make migrate`），不拥有 DDL |
| OTel collector 采样策略 / Grafana 看板内容 / 告警规则 | M11（`infra/observability`） | dev compose 挂载该目录现成配置；本模块不新增看板 |
| 前端构建管线（Vite / Vitest / Playwright 配置） | M12 | 本模块只给 web 镜像的构建外壳（pnpm 缓存、nginx 静态） |
| 真实模型 API 接入 | M07 / M03 | dev 种子与本地栈不内置任何模型密钥；模型调用在本地开发走 Fake DecisionSource（M04 §8.1 桩） |

### 1.4 输入 / 输出与上下游契约

| 方向 | 内容 | 契约形式 |
|---|---|---|
| 入 | 开发者命令：`make dev-up` 等（§4.2）；`.env`（由 `.env.example` 复制，§4.3） | Makefile + dotenv |
| 入 | 官方基础镜像（版本钉死，§4.4）；M08 沙箱镜像 `wp-sandbox`（只引用） | compose `image:` 字段 |
| 入 | M11 `infra/observability/` 的 collector / grafana 配置文件 | bind mount（只读） |
| 出 | 一组 healthy 的依赖端点：pg:5432 / redis:6379 / minio:9000 / otel:4317+4318 | 端口 + healthcheck（§4.1） |
| 出 | 8 个不可变镜像：`ghcr.io/widgetpilot/{service}:{git_sha}` | 镜像清单契约（§4.4） |
| 出 | 种子数据集（账号 / 语料 / fixture / 示例会话） | seed 脚本（§6.3） |
| 出 | CI 集成测试容器策略（必容器测试清单 + 隔离规则） | workflow 约定（§3.4） |

### 1.5 承载的横切能力

- 无 X* 承载。本模块是 D-5「保留的通用基础设施」（Postgres / Redis / 对象存储 / OTel SDK 所连的 collector）的**开发期供给方**；X11（可观测）的数据采集归 M11，本模块只保证 collector 在 dev 可达（`observability` profile）。

### 1.6 关键设计原则

1. **依赖容器化、应用本机化**：db / redis / minio / otel 进容器（版本统一、一键起停、随时销毁重建）；应用服务在本机跑（断点调试、秒级热重载、不用重新 build 镜像）。这是用户的明确要求，也是开发迭代速度的最优解。
2. **12-factor 配置同构**：dev compose 与生产 Helm 注入**同一组变量名**（§4.3 全表），只换值；禁止代码里出现「if dev then 读别的配置键」。
3. **镜像一次构建、多环境部署**：dev/staging/prod 运行同一 `:git_sha` 镜像，差异只在 env 与挂载配置；禁止「dev 镜像里塞种子数据」。
4. **破坏性操作显式确认**：`dev-reset` 清 volume，数据不可恢复，必须 `CONFIRM=dev-reset` 显式确认（§5.2）。
5. **健康判定交给 healthcheck**：`depends_on: condition: service_healthy`，不做「sleep 10 再试」式时序赌注。
6. **最小权限与最小暴露面**：端口默认绑 `127.0.0.1`；容器内非 root；`.env` 不入库；seed 拒绝在非 dev 环境执行。

---

## 2. 技术栈

| 层 | 选型 | 版本 / 约束 | 为什么 | 明确不用什么 |
|---|---|---|---|---|
| 容器编排（dev） | Docker Compose v2 | `docker compose`（单文件 `docker-compose.dev.yml`） | 声明式依赖栈 + profiles + healthcheck，一条命令 | 不用 k3d/kind 当日常开发栈（生产才用 Helm） |
| 数据库 | `postgres:16-alpine`（镜像摘要钉死） | 16.x；named volume `pgdata` | 与 M04/M11 选型「PostgreSQL 14+」对齐，dev 直接用 16 | 不用 SQLite 降级替身（租约并发语义不可模拟） |
| 缓存 / 队列 | `redis:7-alpine` | 7.x；`--appendonly yes`；volume `redis-data` | Redis Streams 队列 / SSE 扇出 / 限流（M04/M11） | 不用内存 mock |
| 对象存储 | `minio/minio` + `minio/mc` 初始化 | RELEASE 钉死；9000 API / 9001 Console；volume `minio-data` | S3 兼容，M04/M08/M11 的大对象 / 证据 / WORM 语义开发替代 | 不用本地裸文件系统当 S3（key 前缀与签名语义缺失） |
| 观测栈 | `otelcol-contrib` + `grafana` + `loki` | 版本钉死；4317(gRPC)/4318(HTTP)/3000/3100 | M11 的 OTel→表→看板链路在 dev 可视；配置复用 `infra/observability/` | 不引入 LangFuse 等 LLM 观测平台（D-5） |
| Python 构建 | `uv`（构建缓存层）+ `python:3.12-slim-bookworm` | `uv sync --frozen`，cache mount | 依赖层缓存最快、lockfile 确定性 | 不用 pip + 手写 requirements 分层 |
| 前端构建 | `node:22-alpine` + corepack pnpm + pnpm store cache mount | lockfile 驱动 | 层缓存 + 构建可复现 | 不用 npm/yarn 混用 |
| web 运行时 | `nginx:1.27-alpine`（SPA 静态 + /healthz） | 非 root（uid 101） | 产物静态化，生产可换 CDN（§7.1 web 行） | 不用 node 常驻跑静态 |
| 容器内进程用户 | 专用 uid `10001`（app） | `USER 10001`，无 shell | 最小权限 | 不用 root 跑应用 |
| 镜像 tag | `{service}:{git_sha_short}`（不可变）+ `{service}:{semver}`（release）+ `{service}:dev`（仅本地） | CI 构建时注入 | M11 回滚可指向确定版本 | 禁止 `:latest` 上生产 |
| CI 集成测试 | testcontainers-python（单测进程内起容器）+ compose services（端到端 job） | 每 job 独立 project name / template database | 隔离、并行、即起即毁 | 不用共享的常驻 CI 数据库 |
| schema 迁移 | Alembic【推导·需评审：原文未提迁移工具】 | `alembic upgrade head`；版本脚本入 `services/control_plane/migrations/` | 版本化、可回滚、CI 可断言升级链 | 不用「initdb 全量 SQL + 手工改表」 |

### 2.1 硬约束

- **D-5 兼容**：本模块引入的全是「通用基础设施」的运行实例，不是 Agent 框架；镜像内不装 LangChain / Temporal 等任何被禁依赖。
- **密钥不入库**：`.env` 在 `.gitignore`；`.env.example` 只含占位值；dev 默认密码标注「仅限本地」。
- **门禁常量在代码里**：镜像与 compose 不提供任何「关闭护栏 / 关闭校验」的环境开关（§1.7 #8）。
- **seed 只进 dev**：seed 脚本启动即断言 `ENV=dev`，否则 exit 7（§5.1）；生产数据永不导入 dev 栈。
- **沙箱边界不动**：compose / CI 对 `wp-sandbox` 镜像只引用；seccomp / cgroups 内容修改必须走 M08 评审。

### 2.2 运行时依赖（本模块自身的依赖）

| 依赖 | 用途 | 不可用时的行为 |
|---|---|---|
| Docker Engine 24+ / Docker Desktop（Windows 需 WSL2 backend） | 运行 compose 栈与镜像构建 | `make dev-up` 启动前探测，失败 exit 6 并提示安装指引 |
| Compose v2 插件 | profiles / `--wait` | 同上 |
| Make（Windows 走 WSL2 内的 make / 或 git-bash + make） | 工作流入口 | 提供 `bash infra/dev/*.sh` 等价直调脚本 |
| M11 `infra/observability/` 配置文件 | collector / grafana / loki 配置 | 缺失时 `observability` profile 启动失败并明确报缺文件（不静默用内置兜底配置） |
| M08 `infra/sandbox/` 镜像 | `sandbox` profile | 镜像缺失时提示 `make sandbox-build`（调用 M08 构建入口），本模块不代建 |

### 2.3 代码落位（补齐 00 总览 §6 的 infra 目录）

```
widgetpilot/
  docker-compose.dev.yml        # ★ 本模块：本地依赖栈 + full/sandbox 模式
  Makefile                      # ★ 本模块：dev-up/dev-down/dev-reset/seed/logs/ps/migrate/port-check
  .env.example                  # ★ 本模块：env 契约（§4.3）
  .dockerignore                 # ★ 本模块：根级（各服务可覆写）
  .gitattributes                # ★ 本模块：LF 行尾（§3.5）
  infra/
    docker/
      control-plane.Dockerfile  # ★ 各服务构建标准实例（§3.3）
      orchestrator.Dockerfile   # ★
      guardrails.Dockerfile     # ★
      retriever.Dockerfile      # ★
      sandbox_runner.Dockerfile # ★（运行时外壳；沙箱镜像本体在 infra/sandbox，归 M08）
      eval_runner.Dockerfile    # ★
      web.Dockerfile            # ★ node 构建 + nginx 静态
      nginx-spa.conf            # ★ SPA fallback + /healthz
    dev/
      initdb/01-extensions.sql  # ★ postgres 初始化（扩展与 role）
      port_check.sh             # ★ 端口占用检测（§3.5）
      seed/run_seed.py          # ★ 种子数据入口（§6.3）
      wait_deps.sh              # ★ 依赖健康等待兜底（CI 用）
  infra/sandbox/                # M08（不归本模块，只引用镜像名）
  infra/observability/          # M11 配置（本模块挂载）
  infra/models/                 # 【v1.3 契约约定】L2 语义路由本地 embedding 模型权重与意图样本库的容器挂载（v1.3 ADR-001 D-8）：只读 bind mount 供给 M06，镜像不打包权重，保持「镜像一次构建」原则
  infra/deploy/                 # 生产 Helm / terraform（不归本模块）
  scripts/dev/                  # 辅助脚本（数据库快照导入导出等）
```

---

## 3. 设计架构

### 3.1 内部组件与职责

```mermaid
flowchart TB
  subgraph COMPOSE["docker-compose.dev.yml（profiles 分层）"]
    subgraph CORE["profile: core（日常开发最小集）"]
      PG["postgres:16\n5432 · pgdata · pg_isready"]
      RDS["redis:7\n6379 · appendonly · ping"]
      MIO["minio\n9000/9001 · minio-data"]
      MC["createbuckets（one-shot）\nmc mb 初始化桶与前缀"]
    end
    subgraph OBS["profile: observability（可选）"]
      OTC["otel-collector\n4317/4318"]
      GRAF["grafana\n3000"]
      LOKI["loki\n3100 · loki-data"]
    end
    subgraph FULL["profile: full（全容器热重载）"]
      CP["control-plane :8000"]
      ORC["orchestrator（无对外端口）"]
      GRD["guardrails :8100"]
      RET["retriever :8200"]
      SBX["sandbox-runner :8300"]
      EVL["eval-runner（作业型）"]
      WEB["web :5173（vite dev）"]
    end
    subgraph SBXP["profile: sandbox（沙箱依赖）"]
      SBIMG["wp-sandbox 镜像引用\n（本体归 M08）"]
    end
  end
  MK["Makefile\ndev-up / dev-reset / seed / migrate"] --> COMPOSE
  ENV[".env（.env.example 复制）"] --> COMPOSE
  HOST["开发者本机进程\nuvicorn / vite（默认模式）"] -->|"直连"| PG & RDS & MIO & OTC
  CI["CI job\n(testcontainers / compose services)"] --> PG & RDS & MIO
```

| 组件 | 职责 | 关键不变量 |
|---|---|---|
| `core` 组 | 供给 PG / Redis / MinIO 三大必需依赖 + 桶初始化 | 三者全部 healthcheck 就绪才算 up 成功；volume 持久化跨 `dev-down` 保留 |
| `observability` 组 | 供给 OTLP 接收端与看板 | collector 配置来自 `infra/observability/`，本模块零自定义 |
| `full` 组 | 应用服务容器化 + bind mount 热重载 | 与本机模式消费**同一组 env**；挂载只读源码 + `--reload` |
| `sandbox` 组 | 沙箱镜像与渲染依赖供给 | 镜像与 seccomp/cgroups 语义归 M08，本模块只引用与起停 |
| `Makefile` | 唯一官方入口 | 破坏性目标必须确认；所有目标可脚本化（无交互阻塞） |
| `.env` | 唯一配置注入口 | 变量名与生产 schema 一致；缺失关键变量 exit 2 |
| `seed` | 播种可复现数据 | 幂等（重复执行不产生重复行）；仅在 ENV=dev 可执行 |
| `port_check` | 起栈前端口冲突前置检测 | 冲突时 exit 3 并指出占用的本机端口 |

### 3.2 处理流程与数据流（开发者一天的标准动线）

1. **首次初始化**：`cp .env.example .env` → 按需改端口与密码（默认值即可跑）→ `make dev-up`。
2. `make dev-up` 内部顺序：`port_check`（冲突即退出）→ `docker compose --profile core up -d --wait`（healthcheck 全绿才算成功）→ `make migrate`（alembic 到 head）→ 输出依赖端点清单。
3. **本机起应用**：开发者另开终端 `uv run uvicorn services.control_plane.api.main:app --reload`（连 `127.0.0.1:5432` 的 docker PG）；前端 `pnpm dev`（连 `127.0.0.1:8000` 的本机 API）。此为**默认开发模式**：应用不在容器里。
4. **要观测**：`make dev-up PROFILES="core observability"` → OTLP `127.0.0.1:4318` 可达，Grafana `127.0.0.1:3000` 看 M11 看板。
5. **要播种**：`make seed` → 写入测试账号 / 语料样本 / 评测 fixture / 3 个示例会话（幂等，可反复执行）。
6. **要全容器联调**（模拟部署形态 / 验证 Dockerfile）：`make dev-up PROFILES="core full observability"` → 应用服务在容器内跑，bind mount 源码 + `--reload` / vite 热更新，行为与本机模式一致。
7. **要跑沙箱**：`make dev-up PROFILES="core sandbox"` → 引用 M08 `wp-sandbox` 镜像与渲染依赖。
8. **收工**：`make dev-down`（停容器保 volume）；**环境坏了**：`CONFIRM=dev-reset make dev-reset`（清 volume → 重起 → migrate → seed，回到出厂可复现状态）。
9. **CI 里**：单测 job 用 testcontainers 进程内起 PG/MinIO 跑集成测试；端到端 job 用 compose 起 core 组跑冒烟（§3.4）。

### 3.3 各服务 Dockerfile 构建标准（R7）

统一标准（全部服务适用）：

| 规则 | 要求 |
|---|---|
| multi-stage | `builder` 阶段装依赖与构建，`runtime` 阶段只拷贝产物；runtime 不含编译工具链、不含 .git |
| 非 root | runtime `USER 10001`（web 用 nginx 镜像自带 uid 101）；不挂载 docker.sock 到业务服务 |
| HEALTHCHECK | 每个**有端口的**服务必须内置 `HEALTHCHECK` 打自身 `/healthz`；无端口 worker（orchestrator / eval-runner）用进程级探针脚本 |
| 依赖层缓存 | Python：先 `COPY pyproject.toml uv.lock` → `uv sync --no-install-project`（依赖层）→ 再拷源码；前端：先拷 lockfile → `pnpm install --frozen-lockfile`（store cache mount）→ 再拷源码 |
| 镜像 tag | CI 构建双 tag：`{git_sha_short}`（不可变，唯一事实）+ `{semver}`（release 时指向）；本地 `make dev-up PROFILES=full` 用 `:dev` |
| .dockerignore | 见 §6.4 清单；保证 build context 不含 .git / node_modules / .venv / 种子数据 |
| web 产物 | vite build → nginx alpine 静态托管（SPA fallback + /healthz）；生产可原样推 CDN（§7.1） |

**Python 服务标准 Dockerfile（control-plane 实例，其余同构）**：

```dockerfile
# infra/docker/control-plane.Dockerfile
# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=0 UV_LINK_MODE=copy
# 依赖层：先只拷锁文件，最大化缓存命中
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
# 源码层：packages 先于 services（packages 变更频率低）
COPY packages/ packages/
COPY services/ services/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /app/packages /app/packages
COPY --from=builder --chown=10001:10001 /app/services /app/services
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"
CMD ["uvicorn", "services.control_plane.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**web 标准 Dockerfile（node 构建 + nginx 静态）**：

```dockerfile
# infra/docker/web.Dockerfile
# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS builder
RUN corepack enable
WORKDIR /app
COPY apps/web/pnpm-lock.yaml apps/web/package.json ./
RUN --mount=type=cache,id=pnpm-store,target=/root/.pnpm-store \
    pnpm install --frozen-lockfile
COPY apps/web/ .
RUN pnpm build

FROM nginx:1.27-alpine AS runtime
COPY infra/docker/nginx-spa.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
USER 101
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1:80/healthz >/dev/null 2>&1 || exit 1
EXPOSE 80
```

### 3.4 CI 容器策略（R8）

**分层原则**：单元测试不起容器（快）；集成测试用 **testcontainers-python**（测试进程内起一次性容器，随测试结束销毁）；端到端冒烟用 **compose services**（复用 `docker-compose.dev.yml` 的 core 组 + full 组，CI 与开发者行为一致）。

| 测试类别 | 覆盖模块 | 是否必须真实容器 | 供给方式 |
|---|---|---|---|
| 纯函数 / 单元（decide()、护栏判据、reducer） | M03 / M08 / M17 | 否 | 进程内 mock |
| checkpoint / 租约并发、kill 续跑（M04 §8.2 A3/A4/A5） | M04 | **必须 Postgres**（`UPDATE ... WHERE lease_until < now()` 并发语义） | testcontainers：postgres:16，每 worker 一个 template database |
| 对象存储读写 / key 前缀 / hash 校验（M05 上传、M08 证据写入） | M05 / M08 | **必须 S3 兼容** | testcontainers：minio 容器 |
| 沙箱编译 / 渲染 / 断言（M08 阶段 C） | M08 | **必须沙箱镜像** | compose job：`wp-sandbox` 镜像 + seccomp/cgroups 约束（下述） |
| 审计 hash 链 / 只追加语义（M11） | M11 | 必须 Postgres（验证禁止 UPDATE 的约束与链重算） | testcontainers |
| 检索与入库门禁（M10） | M10 | 需要 PG（+ 向量能力【推导·需评审：pgvector 扩展或独立容器，随 M10 定稿】） | testcontainers |
| 端到端冒烟（提交 → 事件流 → 终态） | M04–M09 串联 | compose core + Fake DecisionSource 应用进程 | compose services（full 组或本机进程） |

**沙箱测试在 CI 的特权 / 资源约束处理**【推导·需评审】：

- 优先使用带 **gVisor（runsc）** runtime 的 self-hosted runner（与 M08 生产形态一致）；不可得时降级为普通 Docker runtime + seccomp 默认 profile + cgroups v2 限额，并在测试报告标注「隔离强度降级」。
- 沙箱 job 设并发 = 1（防资源互踩）；单容器 `cpus=2, memory=2g, pids-limit=256`；超时由 M08 `SandboxLifecycle` 强制销毁，CI 侧再兜底 job 级 timeout。
- CI runner 不挂载 docker.sock 进沙箱容器；不申请 `privileged: true`。

**测试数据隔离**：

1. 每 CI job 设独立 `COMPOSE_PROJECT_NAME=ci-{run_id}-{job}`，容器与 volume 天然隔离，job 结束 `compose down -v`。
2. testcontainers 模式：每测试进程起独立容器（Ryuk 自动回收）；PG 场景用 **template database**——起一个空库打迁移到 head 后 `CREATE DATABASE wp_test_{worker} TEMPLATE wp_template`，worker 间零串扰。
3. 种子数据在 CI 使用 `seed --ci` 最小集（只账号 + fixture，不含示例会话的长时间事件流），控制 job 时长。

### 3.5 Windows 开发注意（用户环境：Windows + bash）

| 项 | 要求 / 做法 |
|---|---|
| Docker Desktop backend | 必须启用 **WSL2 backend**（Settings → General → Use the WSL 2 based engine）；Hyper-V backend 不支持本模块的 cgroups v2 语义 |
| 代码放哪 | 仓库 clone 到 **WSL 文件系统内**（如 `~/work/widgetpilot`），不要放 `/mnt/e/...`——跨 9P 协议的 bind mount 会使 vite / uvicorn 文件监听与 pnpm 安装慢一个数量级；在 WSL 内用 `make`，Windows 侧用 `\\wsl$\Ubuntu\home\...` 访问 |
| 行尾 | 根 `.gitattributes`：`* text=auto eol=lf` + `*.ps1 eol=crlf` + 二进制类型 `-text`；shell 脚本若被 checkout 成 CRLF 会在容器内报 `\r: command not found`，`port_check.sh` 启动时自检并给出 `git add --renormalize .` 提示 |
| 端口占用 | Windows 有 Hyper-V / 保留端口段问题：`port_check.sh` 除探测「端口被进程监听」外，还提示 `netsh int ipv4 show excludedportrange protocol=tcp` 检查系统保留段；占用即 exit 3 |
| 路径 | compose 内一律容器内路径；env 里的宿主路径（如 bind mount）统一正斜杠；`COMPOSE_CONVERT_WINDOWS_PATHS=1` 写入 `.env.example` 注释 |
| make | 在 WSL2 内 `sudo apt install make`（推荐）；git-bash 直调 `bash infra/dev/*.sh` 为等价后备 |

### 3.6 部署形态与伸缩

| 场景 | 形态 | 说明 |
|---|---|---|
| 本机日常开发 | core（容器）+ 应用（本机进程） | 默认；资源占用最小，热重载最快 |
| 本机联调 / 部署形态演练 | core + full + observability（全容器） | 与生产拓扑同构的缩小版；单副本 |
| CI | job 级临时栈 | testcontainers / compose，即起即毁 |
| staging / prod | 原文 §7.1 `infra/deploy`（Helm / terraform） | **同一镜像**（`:git_sha`）+ **同一 env schema**，只换 values；伸缩（QPS / 队列深度 / 并发沙箱数）归原文 §7.1，本模块不定义 |

---

## 4. 接口契约

> 本模块的「接口」不是 REST 业务 API，而是**脚本与环境契约**：compose 服务端点、Makefile 目标、env 变量、镜像清单。业务 API 的契约见 M04–M11 与 01 基线。

### 4.1 compose 服务与端口 / 健康检查表

| 服务 | 镜像 | profile | 宿主端口（默认绑 127.0.0.1） | healthcheck | volume | 依赖 |
|---|---|---|---|---|---|---|
| `postgres` | `postgres:16-alpine`（digest 钉死） | core | 5432 | `pg_isready -U $user -d $db`，interval 5s / retries 10 / start_period 10s | `pgdata` + initdb 挂载（只读） | — |
| `redis` | `redis:7-alpine` | core | 6379 | `redis-cli ping`，5s/5/5s | `redis-data` | — |
| `minio` | `minio/minio`（RELEASE 钉死） | core | 9000 API / 9001 Console | `mc ready local`，10s/5/15s | `minio-data` | — |
| `createbuckets` | `minio/mc`（one-shot，无重启） | core | — | —（任务完成即 exit 0） | — | `minio: service_healthy` |
| `otel-collector` | `otelcol-contrib`（版本钉死） | observability | 4317 gRPC / 4318 HTTP | `/` HTTP 探针（collector 自带 health extension），10s/5/10s | — | — |
| `grafana` | `grafana/grafana` | observability | 3000 | `/api/health`，10s/5/30s | `grafana-data` | otel-collector（soft） |
| `loki` | `grafana/loki` | observability | 3100 | `/ready`，10s/5/15s | `loki-data` | — |
| `control-plane` | 本地构建 `:dev` | full | 8000 | 镜像内置 `/healthz` 探针 | 源码 bind mount（只读） | postgres/redis/minio healthy |
| `orchestrator` | 同上 | full | 无对外端口 | worker 进程探针（`pgrep -f orchestrator` + 最近心跳文件） | 同上 | postgres/redis/minio healthy |
| `guardrails` | 同上 | full | 8100 | `/healthz` | 同上 | postgres healthy |
| `retriever` | 同上 | full | 8200 | `/healthz` | 同上 | postgres/minio healthy |
| `sandbox-runner` | 同上 | full | 8300 | `/healthz` | 同上 | minio healthy；沙箱镜像由 M08 供给 |
| `eval-runner` | 同上 | full | 无常驻端口（作业型，`command` 覆写为具体回放任务） | 进程探针 | 同上 | postgres/minio healthy |
| `web` | 本地构建 `:dev` | full | 5173（vite dev，full 模式跑 vite 而非 nginx） | TCP 探针 | 源码 bind mount（只读） | control-plane（soft，启动顺序用） |

**约定**：
- 所有 `depends_on` 一律 `condition: service_healthy`（one-shot 的 `createbuckets` 用 `service_completed_successfully`）。
- `restart: unless-stopped` 适用于有状态依赖与应用服务；one-shot 任务禁用 restart。
- 端口冲突时不是 compose 报错，而是 `port_check` 前置拦截（exit 3，§5.1）。
- full 组的服务命令为开发形态（uvicorn `--reload` / vite dev）；**运行镜像与生产 CMD 不同属预期**（镜像 CMD 是生产形态），镜像一次构建原则不变。

### 4.2 Makefile 目标契约（输入 / 输出 / 副作用）

| 目标 | 输入 | 输出 | 副作用 | 幂等 |
|---|---|---|---|---|
| `make dev-up` | `PROFILES`（默认 `core`） | 全部目标服务 healthy；stdout 打端点清单 | 起容器；执行 `migrate`；创建 network | 是（重复执行收敛到同一状态） |
| `make dev-down` | — | 容器停止 | 停容器与 network；**保留全部 named volume** | 是 |
| `make dev-reset` | `CONFIRM=dev-reset`（必需） | 出厂态栈（volume 清空 + migrate + seed 完成） | **清空 pgdata/redis-data/minio-data/loki-data**，数据不可恢复 | 是 |
| `make seed` | `SEED_SET`（默认 `full`；`--ci` 为最小集） | 测试账号 / 语料 / fixture / 示例会话入库 | 写 PG + MinIO；幂等 upsert | 是 |
| `make migrate` | migrations 目录 | schema 至 `head` | 写 `alembic_version` 表 | 是（已 head 则 no-op） |
| `make logs` | `SVC`（可选服务名） | 跟踪日志流（`--tail=100`） | 无 | — |
| `make ps` | — | 服务 / 状态 / 端口表 | 无 | — |
| `make port-check` | `.env` 端口清单 | 全部可用 / exit 3 报冲突端口 | 无 | 是 |
| `make sandbox-build` | `infra/sandbox/` | `wp-sandbox:{git_sha}` 镜像 | 调用 M08 构建入口（本模块只透传） | 是 |

**约定**：
- 所有目标可脚本化执行（CI 可用），禁止交互式 prompt；需要确认的破坏性操作用环境变量确认（§5.2）。
- 目标失败即非零退出码（§5.1），`dev-up` 使用 `set -euo pipefail`。
- `dev-reset` 不经确认执行时 exit 5，不产生任何副作用（先确认后动手）。

### 4.3 env 变量契约表（`.env.example` 全量）

> 变量名与生产 Helm values 同 schema【契约约定】：dev 与 prod 只允许值不同。类型列是注入形态（全部以字符串进容器，由消费方解析）。

| 变量 | 类型 | dev 默认值 | 被谁消费 |
|---|---|---|---|
| `COMPOSE_PROFILES` | profile 列表 | `core` | compose CLI（决定起哪组） |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | str | `widgetpilot` / `widgetpilot_dev`（仅限本地）/ `widgetpilot` | postgres 容器 + `DATABASE_URL` 拼装 |
| `POSTGRES_PORT` | int | `5432` | compose 端口映射 |
| `DATABASE_URL` | URL | `postgresql+asyncpg://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot`（本机模式） | M04/M05/M09/M10/M11 服务（本机进程读此值；full 模式由 compose `environment` 覆写为 `postgres:5432` 主机名） |
| `REDIS_URL` | URL | `redis://127.0.0.1:6379/0` | M04（队列 / SSE 扇出）、M11（限流） |
| `S3_ENDPOINT` | URL | `http://127.0.0.1:9000` | M04/M05/M08/M09/M11 的 S3 客户端 |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | str | `wpdev` / `wpdev-secret`（仅限本地） | 同上 + minio 容器 |
| `S3_BUCKET` | str | `wp-dev` | 同上（桶由 `createbuckets` 初始化） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | URL | `http://127.0.0.1:4318`（observability 未启动时服务需容忍不可达，M11 §2.3 行为） | 全部服务的 OTel SDK |
| `JWT_SECRET` | str | dev 随机占位（`change-me-dev-only`，文件内标注禁止上生产） | M04 JWT 签发/校验 |
| `JWT_ACCESS_TTL` / `JWT_REFRESH_TTL` | duration | `15m` / `7d`（M04 §2.1 契约约定值） | M04 |
| `ENV` | 枚举 | `dev` | seed 守卫（非 dev 即拒绝）、日志格式 |
| `LOG_LEVEL` | 枚举 | `INFO` | 全部服务 |
| `VITE_API_BASE` | URL | `http://127.0.0.1:8000` | apps/web 开发代理 |
| `COMPOSE_CONVERT_WINDOWS_PATHS` | bool | `1`（Windows 注释项） | compose |
| 模型 API 密钥类 | — | **不提供**（本地开发用 Fake DecisionSource，M04 §8.1） | — |

### 4.4 镜像清单契约

| 镜像 | 构建上下文 | Dockerfile | 基础镜像 | 依赖（构建期） |
|---|---|---|---|---|
| `ghcr.io/widgetpilot/control-plane` | 仓库根 | `infra/docker/control-plane.Dockerfile` | `python:3.12-slim-bookworm` | `packages/contracts`、`packages/agent_core`、`packages/sdk`(py) |
| `ghcr.io/widgetpilot/orchestrator` | 仓库根 | `infra/docker/orchestrator.Dockerfile` | 同上 | `packages/agent_core`、`packages/prompts` |
| `ghcr.io/widgetpilot/guardrails` | 仓库根 | `infra/docker/guardrails.Dockerfile` | 同上 | `packages/agent_core`、`packages/contracts` |
| `ghcr.io/widgetpilot/retriever` | 仓库根 | `infra/docker/retriever.Dockerfile` | 同上 | `packages/contracts` |
| `ghcr.io/widgetpilot/sandbox-runner` | 仓库根 | `infra/docker/sandbox_runner.Dockerfile` | 同上 | `packages/contracts`；运行时依赖 `wp-sandbox` 镜像（M08） |
| `ghcr.io/widgetpilot/eval-runner` | 仓库根 | `infra/docker/eval_runner.Dockerfile` | 同上 | `packages/contracts` |
| `ghcr.io/widgetpilot/web` | 仓库根 | `infra/docker/web.Dockerfile` | `nginx:1.27-alpine` | `packages/sdk`(ts)、`packages/widget_bridge`、`packages/contracts` |
| `wp-sandbox` | `infra/sandbox/` | M08 拥有 | — | **只引用**：本模块的 `sandbox` profile 与 CI 沙箱 job 按此名取镜像 |

**约定**：上下文一律仓库根（monorepo 共享依赖可 COPY）；由 `.dockerignore` 控制体积；全部非 root；tag 规范见 §6.4。

### 4.5 约定（健康检查超时、重启策略、资源限额）

| 项 | 约定 |
|---|---|
| healthcheck 参数基线 | interval `5–10s`、timeout `3s`、retries `3–10`、`start_period` 给慢启动组件（grafana 30s、postgres 10s）；`--wait` 全绿超时统一 `120s`，超时 exit 4 |
| 重启策略 | 依赖与应用服务 `unless-stopped`；one-shot 任务（createbuckets / seed 容器）不重启 |
| dev 资源限额 | compose 默认不设上限（本机资源自管）；**沙箱容器例外**：沿用 M08 cgroups 约束（cpus/memory/pids-limit），防本地死循环控件拖垮开发机 |
| 端口绑定 | 默认 `127.0.0.1` 前缀（`127.0.0.1:5432:5432`）；需要局域网访问时在 `.env` 显式改绑并自担风险 |
| 日志 | 全部容器 `json-file` driver，`max-size=10m, max-file=3`，防盘满 |
| 时区 / 时间 | 容器一律 UTC（与库内 timestamptz 口径一致，M04 §6.1）；不注入 TZ |
| one-shot 语义 | `createbuckets` 完成即 exit 0；`service_completed_successfully` 门禁后续 |

### 4.6 接口状态表（契约状态 / 运行态）

| 接口 | 契约状态 | 运行态 | 说明 |
|---|---|---|---|
| compose 服务清单与端口（§4.1） | 定稿（本模块 v1.0） | 常驻 / one-shot | 端口改动走 `.env`，不变更契约 |
| Makefile 目标（§4.2） | 定稿 | 同步脚本 | 新目标须补本表与 §5.1 退出码 |
| env 变量表（§4.3） | 定稿骨架；`JWT_TTL` 等【契约约定】值随 M02 定稿 | — | 新增变量必须进 `.env.example` 并标注消费方 |
| 镜像清单（§4.4） | 定稿 | CI 构建 | 新服务必须同时补镜像行与 Dockerfile |
| Alembic 迁移 | **【推导·需评审】**原文未提迁移工具 | `migrate` 目标 | 定稿前以「initdb 基线 SQL + alembic 增量」双轨，见 §9 |
| 向量库供给（M10） | **【推导·需评审】**随 M10 定稿 | 可选 compose 服务 | initdb 预置 `CREATE EXTENSION IF NOT EXISTS vector` 兜底 |
| 沙箱镜像引用（§4.4 末行） | 依赖外部（M08） | `sandbox` profile | 镜像名 / 约束以 M08 为准 |

---

## 5. 状态、原因码与 API 状态

### 5.1 本模块拥有的状态与枚举（完整定义）

**A. 容器 healthcheck 状态机**（compose 层唯一需要 developers 理解的状态机）：

```mermaid
stateDiagram-v2
  [*] --> created: compose up
  created --> starting: 容器启动,healthcheck 开始
  starting --> healthy: 探针连续成功且达 start_period 后
  starting --> unhealthy: 连续 retries 次失败
  healthy --> unhealthy: 运行中探针连续失败
  unhealthy --> healthy: 探针恢复
  unhealthy --> exited: compose down / 崩溃
  healthy --> exited: compose down / 崩溃
  exited --> [*]
```

- `starting` 期间不视为就绪：`depends_on: service_healthy` 的下游会等待；`--wait` 超时（120s）判定为失败并 exit 4。
- `unhealthy` 的容器由 `restart: unless-stopped` 自动重启；连续重启风暴（1 分钟内 ≥5 次）应人工介入，`make logs SVC=postgres` 是第一入口。
- one-shot 任务的状态是 `created → running → exited(0)`；exit 非 0 = 初始化失败（如桶创建失败），`service_completed_successfully` 门禁会阻断下游。

**B. 脚本退出码约定**（Makefile / infra/dev 脚本统一）【契约约定】：

| 退出码 | 语义 | 场景 |
|---|---|---|
| `0` | 成功 | 目标完成 |
| `1` | 通用失败 | 命令内部错误 |
| `2` | 配置缺失 | `.env` 不存在或关键变量（`DATABASE_URL` / `JWT_SECRET` 等）为空 |
| `3` | 端口冲突 | `port_check` 检出占用或系统保留段 |
| `4` | 依赖不健康 | `--wait` 超时 / healthcheck 未达 green |
| `5` | 破坏性操作未确认 | `dev-reset` 缺 `CONFIRM=dev-reset` |
| `6` | Docker 不可用 | daemon 未启动 / 版本过低 / WSL2 backend 未启用 |
| `7` | seed 环境守卫 | `seed` 在 `ENV != dev` 下被调用 |
| `8` | 迁移失败 | alembic 报错 / 版本链断裂 |
| `130` | 用户中断 | SIGINT |

**C. 与业务 ReasonCode 的关系**：本模块**无业务 API，不产出也不消费 14 个 `ReasonCode`**——那些码属于 M04–M11 的会话 / 校验 / 治理语义（01 基线 §7）。容器与栈层的异常通过**退出码（上表）+ compose 事件 + 容器日志**表达；应用服务内部的业务失败仍按各自模块的 ReasonCode 写库与事件流，本模块只负责把日志收集好（`make logs` / observability profile）。

### 5.2 状态流转表（栈生命周期操作）

| 触发 | 前态 | 后态 | 副作用 | 说明 |
|---|---|---|---|---|
| `dev-up`（首次） | 无栈 | core healthy | 起 3 容器 + 桶初始化 + migrate | 首次会执行 initdb 脚本（仅 volume 为空时） |
| `dev-up`（已有栈） | 部分运行 | 目标组 healthy | 补起缺失容器 | 幂等收敛 |
| `dev-down` | running | exited（volume 保留） | 停容器 | 数据不丢，下次 up 直接连回 |
| `dev-reset`（有确认） | 任意 | 出厂 healthy + 种子完整 | **volume 全清 + 重播种** | 数据不可恢复；确认机制见下 |
| `dev-reset`（无确认） | 任意 | **前态不变** | 无（exit 5） | 先确认后动手 |
| 容器 unhealthy | healthy | 重启 / 风暴 | restart 策略生效 | 排查走 `make logs` |
| `migrate`（落后） | schema rev N | rev head | 执行迁移脚本 | alembic 版本链可 downgrade |
| `seed` | 任意 | 种子完整 | 幂等 upsert | 可在任意时刻重跑 |

**dev-reset 破坏性确认语义**：`CONFIRM=dev-reset make dev-reset`；确认值必须逐字等于目标名（防「顺手确认」）。Makefile 内实现：

```make
dev-reset:
	@if [ "$(CONFIRM)" != "dev-reset" ]; then \
	  echo "ERROR: destructive operation. Run: CONFIRM=dev-reset make dev-reset"; exit 5; fi
	@$(COMPOSE) down -v --remove-orphans
	@$(MAKE) dev-up
	@$(MAKE) seed
	@echo "dev stack reset to factory state."
```

### 5.3 本模块产出 / 消费的 `ReasonCode`

不适用：本模块无业务 API。见 §5.1C——栈层异常用退出码 0–8 / 130 表达；业务 ReasonCode（14 个，01 基线 §7）由 M04–M11 在应用层产出，与本模块无映射关系。**禁止**在 compose / Makefile 层发明新的「伪 ReasonCode」。

### 5.4 HTTP 状态码与业务码映射

不适用（本模块无 HTTP 业务接口）。本模块涉及的 HTTP 仅两类探测：容器 healthcheck（200 = 健康）与应用 `/healthz`（200 = 健康；503 = 依赖不可达，由应用按 M04/M11 语义上报）。

### 5.5 接口的运行态约定

- **`dev-up` 是同步收敛操作**：命令返回即代表目标 profile 全部 healthy（`--wait`），不返回「大概起来了」。
- **`dev-down` 保留状态、`dev-reset` 摧毁状态**：两者的数据语义必须在 README 与 `make` 输出中显式区分；volume 清单见 §6.1。
- **seed 幂等可重跑**：任何时刻 `make seed` 都把数据收敛到「种子完整」态；它与业务测试数据（开发者手造的会话）互不清理——清理业务数据只有 `dev-reset` 一条路。
- **迁移是单向前进**：日常只 `upgrade head`；downgrade 仅用于迁移脚本本身的缺陷回退，属破坏性操作（可能丢列），须人工评审后执行【契约约定】。
- **observability 缺席不是错误**：`core` 模式下 `OTEL_EXPORTER_OTLP_ENDPOINT` 不可达，服务按 M11 §2.3「本地缓冲 span」降级，栈不算不健康。

---

## 6. 核心字段与元数据

### 6.1 compose 关键字段定义

| 字段 | 取值 / 规则 | 含义 |
|---|---|---|
| `name` | `widgetpilot-dev` | compose project 名；CI 覆写为 `ci-{run_id}-{job}` 实现隔离 |
| `services.*.profiles` | `["core"]` / `["observability"]` / `["full"]` / `["sandbox"]` | 分组开关；`.env` 的 `COMPOSE_PROFILES=core` 为默认集 |
| `services.*.healthcheck` | `{test, interval, timeout, retries, start_period}` | §4.1 表逐项给定；禁止省略 |
| `services.*.depends_on` | `{service: {condition: service_healthy}}` | 时序正确性唯一手段；one-shot 用 `service_completed_successfully` |
| `volumes`（named） | `pgdata` / `redis-data` / `minio-data` / `grafana-data` / `loki-data` | 持久化清单；`dev-down` 保留、`dev-reset` 全清 |
| `services.*.volumes`（bind） | `./services:/app/services:ro` 等 | 仅 full 组；只读挂载 + 容器内 `--reload` |
| `services.*.ports` | `"127.0.0.1:${HOST_PORT:-默认}:容器端口"` | 默认仅回环可达 |
| `services.*.logging` | `json-file, max-size=10m, max-file=3` | 防盘满 |
| `x-app-env`（YAML anchor） | `&app-env` 统一定义应用 env，full 组 `<<: *app-env` 复用 | 保证容器化应用与本机进程读到同一组变量 |

### 6.2 env 全表与注入路径

- **本机模式（默认）**：应用进程直接读 shell 导出的 `.env`（`uv run --env-file .env ...`），`DATABASE_URL` 指向 `127.0.0.1`。
- **full 模式**：compose 的 `x-app-env` anchor 把同一变量名的**主机名部分**覆写为容器网络名（`postgres:5432` / `redis:6379` / `minio:9000` / `otel-collector:4317`）；变量名不增不减。
- 变量 → 消费方完整映射见 §4.3 表；该表是唯一事实来源，`.env.example` 注释与本表不一致时以本表为准并修 `.env.example`。

### 6.3 种子数据清单（seed 拥有，完整定义）

| 种子集 | 内容 | 用途（支撑哪些模块的验收桩） |
|---|---|---|
| `accounts` | 2 个账号：`dev-user@widgetpilot.local`（role=user，password=`Dev-123456`，argon2 hash）、`dev-curator@widgetpilot.local`（role=curator） | M04 登录 / 角色 / 越权用例（A1/A2） |
| `corpus_samples` | 10 条脱敏公共语料样本（六种规格至少各 1 条：spec + TSX 代码 + 质量分 + 归属声明） | M10 检索 / 硬过滤 / RRF / 入库撤回（M18 语料溯源亦可） |
| `eval_fixtures` | 评测回放集 20 case：成功 10 / 回边 4 / 护栏命中 3 / 预算触顶 2 / 沙箱失败 1 | M10 离线回放与回归门禁、M11 指标口径验证 |
| `demo_sessions` | 3 个示例会话：① COMPLETED（含 bundle 与卡片引用）；② FAILED（原因码 + evidenceRef 指向 MinIO 证据对象）；③ PAUSED（含 checkpoint rev 与租约过期快照） | M13 会话中心、M14 异常态、M17 回放、M18 trace 视图 |
| `demo_events` | 上述 3 会话的 `agent_event` 序列（18 种 kind 至少各出现 1 次，含 v1.3 新增 `ask_user`【v1.3 ADR-001 D-7】）、`trace_span` 树、`cost_ledger` 行 | M15/M17/M18/M11 的非空数据前提 |
| `objects` | 上述会话对应的对象存储对象：`checkpoint/`、`evidence/`、`spec/`、`code/` 前缀下带 `#sha256:` hash 的最小对象 | M04 checkpoint 恢复、M08 证据钻取、M09 包引用 |

**约定**：seed 全部走应用层 API/仓储函数写入（不走裸 SQL 直插业务表），保证 hash 链、seq 连续性与真实写入路径一致；幂等键为种子集名 + 实体自然键，重复执行 upsert 不产生重复行。

### 6.4 镜像 tag 规范与 .dockerignore

**tag 规范**【契约约定】：

```
ghcr.io/widgetpilot/{service}:{git_sha_short}    # 不可变，CI 每次构建产出，唯一事实
ghcr.io/widgetpilot/{service}:{semver}           # 仅在 release tag 时由 CI 追加，指向同一 sha
ghcr.io/widgetpilot/{service}:dev                # 仅本地 full 模式，不入仓库
```

- 镜像 OCI label 必填：`org.opencontainers.image.revision={git_sha}`、`.source`、`.created`——M11 审计与回滚按 revision 定位。
- 禁止 `:latest` 出现在任何部署配置（dev compose 也不用于业务镜像）。

**`.dockerignore` 基线清单**（仓库根，服务级可覆写追加）：

```
.git
.env*
!.env.example
**/__pycache__
**/.venv
**/node_modules
**/dist
docs/
tests/          # 生产镜像不装测试依赖；testcontainers 测试在 CI 仓库根跑
docs/modules/
*.md            # 按需保留 README 例外
infra/dev/seed/ # 种子不进业务镜像（镜像一次构建原则）
.github/
```

### 6.5 数据与状态（volume、迁移、升级语义）

| 主题 | 约定 |
|---|---|
| named volume 清单 | `pgdata`（PG 全部业务数据）/ `redis-data`（appendonly AOF）/ `minio-data`（对象）/ `grafana-data` / `loki-data`（观测，可丢） |
| volume 语义 | `dev-down` 保留全部；`dev-reset` 清全部并重播种；观测类 volume 允许单独 `docker volume rm` 不影响业务 |
| schema 迁移 | 【推导·需评审】Alembic：迁移脚本入 `services/control_plane/migrations/versions/`；initdb 仅放扩展与 role（`01-extensions.sql`），**业务表一律走迁移**（首次 `migrate` 从零建到 head）；M04 §6.2 的 `checkpoint` / `side_effect` 原文 SQL 作为基线迁移脚本内容 |
| 版本升级 | 拉新代码后 `make dev-up`（内含 migrate）；alembic 报「分支 / 冲突」时 exit 8，开发者需先解决版本链 |
| dev-reset 触发场景 | 迁移出现不可回退破坏性变更、种子结构升级、依赖大版本升级（如 pg16→17 换镜像需 reset）；`docker-compose.dev.yml` 中镜像版本变更的 PR 必须在描述中标注「需 dev-reset」 |

---

## 7. 安全、威胁面与合规

| 不可信输入 | 攻击路径 | 本模块控制点 | 明确禁止 |
|---|---|---|---|
| `.env` 凭据 | 密钥误提交 / dev 密码上生产 | `.env` 在 `.gitignore`；`.env.example` 仅占位；dev 密码注释「仅限本地」；`JWT_SECRET` 生产值只存在于生产密钥管理（Helm secret，归 §7.1） | 禁止把 dev 默认密码写进任何代码常量或生产 values |
| 暴露端口 | 局域网内他人直连开发机 PG / MinIO | 端口默认绑 `127.0.0.1`（§4.5）；改绑公网接口需显式修改并自担风险 | 禁止 compose 默认 `0.0.0.0` |
| seed 数据 | 种子账号被当作真实用户流入生产 | seed 守卫 `ENV != dev` → exit 7；种子邮箱固定 `*.widgetpilot.local` 域 | 禁止向 dev 栈导入生产数据（含脱敏后的） |
| docker.sock | 容器逃逸 | 业务服务一律不挂载 docker.sock；sandbox-runner 与沙箱的交互按 M08 的受控出口设计 | 禁止为图省事挂 `/var/run/docker.sock` 进业务容器 |
| 基础镜像供应链 | 镜像投毒 / 漂移 | 基础镜像版本钉死 + CI 记录 digest；CI 镜像扫描（Trivy）高危项阻断【推导·需评审：阈值随评审定】 | 禁止 `:latest` / 未钉版本的基础镜像 |
| 沙箱边界 | 开发机沙箱配置被放宽后混入生产 | 本模块对 `wp-sandbox` 只引用不定义；seccomp/cgroups 变更必须走 M08 评审 | 禁止在 compose/CI 里覆写沙箱镜像的安全参数 |
| dev 观测数据 | 本地 trace / 日志含真实用户描述 | 本地栈只处理种子与开发者手造数据；不接生产采集通道 | 禁止把生产 Loki / Grafana 数据源指回 dev |

**审计留证**：本模块自身无业务审计义务（AuditEvent 归 M04/M11）；CI 的镜像构建与扫描结果作为供应链留证保存在 CI 产物中。**合规**：dev 栈永不承载真实用户数据（PII 铁律在开发环境同样成立——种子语料已脱敏）。

---

## 8. 独立验收方案（可脱离其他模块单独提测）

### 8.1 验收前置条件与上下游桩

| 依赖 | 本模块验收时的桩 | 说明 |
|---|---|---|
| M04–M11 应用服务 | full 模式用**最小健康壳**：各服务临时实现 `/healthz`（或使用仅含健康路由的占位应用） | 验证的是容器 / 端口 / 依赖注入，不是业务逻辑 |
| M08 沙箱镜像 | 本地 `make sandbox-build` 产出 `wp-sandbox:dev`（用 M08 仓库当前定义） | 只验证「可被引用与起停」，镜像内容归 M08 §8 |
| M11 观测配置 | 仓库内 `infra/observability/` 现成文件（collector / grafana provisioning 最小集） | 缺文件时报错路径即验收断言之一 |
| 种子数据 | `infra/dev/seed/` 自带全部 fixture | 不依赖任何业务模块先完成 |
| 业务模块验收的复用 | M04 §8.1「真实 Postgres + Redis（测试容器）」直接由本模块供给 | 本模块通过后，M04–M20 的前置条件随之满足 |

> 有了 compose 栈 + 种子 + 健康壳，本模块**不需要任何业务模块完成即可独立提测**；反之，本模块未过，其余模块的验收环境不成立。

### 8.2 可断言的验收用例清单

| # | 用例（Given / When / Then） | 判据 | 断言方式 |
|---|---|---|---|
| D1 | Given 全新 clone + `cp .env.example .env`；When `make dev-up`；Then core 三服务 + createbuckets 全部 healthy，命令退出码 0 | 一条命令全绿 | `docker compose ps --format json` 断言 health=healthy；exit code 断言 |
| D2 | Given core 栈 healthy；When 本机起 control-plane（uvicorn 连 `127.0.0.1:5432/6379/9000`）并跑 M04 单测 + 集成测试套件；Then 全绿 | 直连模式可用 | pytest exit 0；`/healthz` 200 |
| D3 | Given `make seed` 已执行；When 查库；Then 账号 2 条 / 语料 10 条 / fixture 20 case / 示例会话 3 个，且 `agent_event` 覆盖 18 种 kind（v1.3 含 `ask_user`，ADR-001） | 种子完整 | SQL count 断言 + kind 集合断言 |
| D4 | Given 重复执行 `make seed` 两次；Then 各表行数不变 | seed 幂等 | 前后 count 对比 = 0 差异 |
| D5 | Given core + seed；When `CONFIRM=dev-reset make dev-reset`；Then volume 清空后栈 healthy 且 D3 的全部计数再次成立 | reset 后种子完整 | D3 断言复跑 |
| D6 | Given 未带 CONFIRM；When `make dev-reset`；Then exit 5 且容器与 volume 无任何变化 | 破坏性守卫 | exit code + `docker volume ls` 前后对比 |
| D7 | Given `PROFILES="core full"`；When `make dev-up`；Then 7 个应用容器 healthy、web 5173 返回 200、经 web→control-plane `/healthz` 链路可达 | full 模式端到端冒烟 | curl 链路断言 + compose ps |
| D8 | Given 任一业务镜像；When `docker inspect`；Then `User` ≠ root（web 为 101，其余 10001）且 `Healthcheck` 非空 | 非 root + HEALTHCHECK | inspect JSON 断言（8 镜像逐一） |
| D9 | Given `5432` 被本机进程占用；When `make dev-up`；Then exit 3 且输出占用端口清单，compose 未被拉起 | 端口冲突前置拦截 | exit code + `docker compose ps` 为空 |
| D10 | Given testcontainers 集成测试；When 跑 M04 A4（两 worker 并发抢租约）；Then 恰 1 个成功，断言基于真实 PG | 必容器测试真实执行 | pytest 断言 affected rows；容器创建日志存在 |
| D11 | Given testcontainers MinIO；When 跑 M05 上传 + hash 校验用例；Then 对象可读且 hash 一致 | S3 语义真实 | pytest 断言 |
| D12 | Given CI 沙箱 job（seccomp + cgroups 限额 cpus=2/mem=2g）；When 跑 M08 一个编译→渲染→断言用例与一个死循环控件；Then 前者 passed、后者被超时强制销毁且无残留容器 | 沙箱约束生效 | 容器列表断言 + 用例结论断言 |
| D13 | Given observability profile；When 本机服务向 `127.0.0.1:4318` 发测试 span；Then Grafana Loki/Datasource 可查到该 trace 标识 | OTLP 链路通 | collector 日志 + Grafana API 查询断言 |
| D14 | Given 迁移链 rev1→rev2→rev3；When `make migrate` 两次；Then 第二次 no-op 且 `alembic_version` = head | 迁移幂等 | alembic current 断言 |
| D15 | Given CI 构建流水线；Then 产出 8 个镜像，tag 均为 `{git_sha_short}` 且含 `revision` label；任何镜像不含 `:latest` tag | tag 规范 | CI 产物清单断言 + inspect label |
| D16 | Given `.env` 被删除；When `make dev-up`；Then exit 2 且提示复制 `.env.example` | 配置守卫 | exit code + stderr 文案 |
| D17 | Given Windows 11 + Docker Desktop（WSL2 backend）+ 代码在 WSL 文件系统；When 完成 D1/D2/D7 冒烟；Then 全部通过且 vite 热更新生效（改动文件 ≤2s 触发重载） | Windows 路径可用 | 人工冒烟清单 + 热更新计时 |
| D18 | Given CI 并行 2 个 job；Then 两者的 PG 数据互不可见（独立 project name / template db） | 测试数据隔离 | 跨 job 行数断言 |

### 8.3 量化指标与阈值

| 指标 | 目标 | 测量点 |
|---|---|---|
| 冷启动 `make dev-up`（首次含镜像拉取除外） | 依赖栈 healthy ≤ 120s（`--wait` 硬顶） | D1 计时 |
| 二次 `dev-up` | ≤ 30s | D1 复跑 |
| 镜像构建（CI，含缓存） | 单 Python 服务 ≤ 3 min；web ≤ 5 min | CI 计时 |
| build context 体积 | 任一服务 ≤ 50MB（.dockerignore 生效） | `docker build` 输出 |
| dev-reset 全程（含 seed） | ≤ 3 min | D5 计时 |
| seed 执行 | ≤ 60s（`--ci` 最小集 ≤ 20s） | D3 计时 |
| CI 集成测试容器回收 | job 结束后无残留容器 / volume | `docker ps -a` / `volume ls` 断言 |

### 8.4 出口条件（Definition of Done）

- §8.2 的 D1–D18 全部通过且可自动化复跑（D17 允许人工冒烟清单 + 录屏留证）；
- `docker-compose.dev.yml` / `Makefile` / `.env.example` / 8 个 Dockerfile / `.dockerignore` / `.gitattributes` 全部入库并通过评审；
- M04–M20 至少各 1 个模块用本栈完成一轮「独立验收前置条件」演练（默认 core + seed 即满足其 §8.1 基础设施行）;
- CI 容器策略落地：必容器测试清单（§3.4）在 CI 实跑，隔离规则生效；
- `GATE OK` = 本模块脚本测试全绿 + compose 配置快照一致 + full 模式端到端冒烟通过（§11.9 口径）。

### 8.5 对抗与回归用例

| 对抗用例 | 本模块断言 |
|---|---|
| 绕过确认执行 dev-reset | exit 5，零副作用（D6） |
| 在非 dev 环境跑 seed | exit 7，零写入（§5.1 / §7） |
| 删除/篡改 `.env` 关键变量 | exit 2 明确报错，不静默用默认值起栈（D16） |
| 死循环控件在本地沙箱跑 | cgroups 限额 + 超时销毁，开发机不被拖垮（D12，约束本体归 M08） |
| 依赖容器被杀（kill postgres） | 应用按 M04/M11 语义降级与恢复；栈恢复后 `dev-up` 幂等收敛 |
| 镜像被注入 `:latest` 或未钉版本基础镜像 | CI lint 阻断（D15 / §7） |

**回归触发**：compose 服务增删、端口表变更、env 变量增删、Dockerfile 标准变更、基础镜像版本升级、seed 结构变更任一发生 → 重跑 §8.2 全部自动化用例；基础镜像大版本升级额外触发「需 dev-reset」标注与全模块验收环境回归。

---

## 9. 依赖、风险与待决

| 项 | 说明 | 处理 / 责任建议 |
|---|---|---|
| 迁移工具未定 | 原文未提 schema 迁移工具；M04 §6.2 只给了建表 SQL | 【推导·需评审】建议 Alembic；与 M02/M04 评审定稿；定稿前 initdb 基线 + alembic 双轨（§6.5） |
| 向量库供给未定 | 架构图有「向量库」，M10 检索实现未定稿 | 【推导·需评审】initdb 预装 pgvector 兜底；若 M10 选独立向量库，compose 增服务并同步 env 表 |
| CI 沙箱隔离强度 | hosted runner 无 gVisor / 无 privileged | 【推导·需评审】self-hosted gVisor runner 优先；降级路径已在 §3.4 定义并要求报告标注 |
| Trivy 扫描阈值 | 高危阻断的具体 CVSS 阈值未定 | 【推导·需评审】先「HIGH+ 可修复即阻断，BASE 例外白名单」，影子期校准 |
| MinIO WORM 与生产对象锁差异 | dev 用普通桶，生产 WORM 语义（M11）更强 | dev 简化【契约约定】；M11 语义测试在 CI 用带 object-lock 的桶【推导·需评审】 |
| Windows 文件监听差异 | WSL2 内 inotify 与 Windows 侧编辑器（经 \\wsl$）配合偶发失灵 | 文档化建议：编辑器装 WSL 远程扩展（VS Code Remote-WSL）；D17 仅断言 ≤2s 基本可用 |
| grafana 3000 端口冲突 | 3000 是常见开发端口 | `.env` 可改 `GRAFANA_PORT`；port_check 覆盖 |
| 镜像仓库可用性 | ghcr.io 命名空间需项目开通 | 若不可用，tag 规范不变、registry 前缀替换为内部 registry；镜像名契约仅前缀变化 |

---

## 10. 术语速查与原文对照

| 术语 | 本模块语境下的含义 |
|---|---|
| `core` profile | 日常开发最小集：postgres / redis / minio（+ 一次性 createbuckets）；`.env` 默认 |
| `full` profile | 全部应用服务容器化 + bind mount 热重载的部署演练模式 |
| `observability` profile | otel-collector + grafana + loki 可选组 |
| `直连模式` | 默认开发形态：应用跑本机、依赖在容器，`DATABASE_URL` 等指向 `127.0.0.1` |
| `dev-reset` | 清空全部 named volume 并重播种的破坏性操作，需 `CONFIRM=dev-reset` |
| `seed` | 幂等播种：测试账号 / 语料样本 / 评测 fixture / 示例会话，支撑 M04–M20 验收桩 |
| `template database` | CI 隔离手段：迁移到 head 的模板库，每 worker `CREATE DATABASE ... TEMPLATE` |
| `testcontainers` | 测试进程内起一次性容器并自动回收的集成测试供给方式 |
| `:git_sha tag` | 不可变镜像标识，M11 回滚与审计可指向的唯一事实 |
| `wp-sandbox` | M08 拥有的一次性沙箱镜像；本模块只引用其名与资源约束 |
| `port_check` | 起栈前端口占用 / Windows 保留段前置检测，冲突 exit 3 |

**原文对照**：§7.1（服务划分与伸缩——本模块按同一清单构建镜像）、§7.2（工程结构——00 总览 §6 补齐 `infra/docker` / `infra/dev`）、§7.3（职责划分）、§5 D5 / M08 §2.3 §3.6（沙箱镜像与 seccomp/cgroups——只引用）、§11.4（checkpoint/side_effect SQL——作为迁移基线内容）、01 基线 §9（对象存储 key 前缀——MinIO 初始化约定）、M04/M08/M11 §2.3（运行时依赖清单——开发期实例供给）。

**延伸阅读**（非理解前提）：
- `00-总览与模块地图.md` —— monorepo 落位（§6）、模块地图、统一模板
- `01-共享契约基线.md` —— 对象存储 key 规范、REST/事件基线
- `M08-出护栏契约校验与沙箱测试域.md` —— 一次性沙箱镜像与 seccomp/cgroups 的拥有者（本模块引用方）
- `M11-治理观测与成本域.md` —— `infra/observability` 配置与生产部署（`infra/deploy`）的拥有者；本模块的观测栈配置与 Helm 同构对象
- `M04-账号会话与编排域.md` —— checkpoint/lease 表（迁移基线）与「真实 PG/Redis 测试容器」验收前置的直接消费方
- `M18-链路追踪可视化.md` —— 补充模块格式样板；`demo_sessions` 种子为其 §8.1 fixture 的现成数据源
- `M12-前端工程化与质量.md` —— web 构建管线的拥有者（本模块只给镜像外壳）

---

## 11. 全模块技术栈汇总（v1.1 增补，本模块汇总收口）

> **目的**：把 00 总览 / 01 基线 / M01–M20 各模块 §2「分层选型表」中声明的技术栈收口到一处，
> 作为**镜像构建与依赖锁定的单一事实来源**（本模块拥有镜像构建标准）。各模块只写「用谁」，版本钉死与镜像落位统一在此。
> **方法**：逐模块扫描 §2 技术栈表 + §2.x 保留的通用基础设施清单汇总；版本策略为「文档给约束、本表钉版本、`uv.lock`/`pnpm-lock.yaml` 锁定」。
> **硬约束**（00 总览 D-5，所有镜像与依赖必须遵守）：**零 Agent 框架**——禁用 LangChain / LangGraph / Temporal / AI SDK / CopilotKit / LangFuse / OPA / Casbin / MCP SDK 及任何 Agent/策略/编排框架。

### 11.1 语言运行时与工程工具链

| 层 | 技术 | 版本约束（来源） | 镜像 / 锁定落位 |
|---|---|---|---|
| 后端运行时 | Python | 3.11+（M04/M05/M06/M10/M11 §2.1）；**镜像钉 3.12**（本模块 §3.3） | `widgetpilot/python-base:dev`（`python:3.12-slim-bookworm`） |
| Python 包管理 | uv | `uv sync --frozen`，`uv.lock` 确定性（本模块 §3.3） | 根 `uv.lock`；基座层安装 uv |
| 前端运行时 | Node.js | 22 LTS（`node:22-alpine`，M12 React 19 / Vite 6 兼容基线） | `widgetpilot/node-base:dev` |
| 前端包管理 | pnpm | workspace（00 总览 §6 / M02 §2.4） | 根 `pnpm-lock.yaml`；`corepack enable` |
| Python 测试 | pytest + pytest-asyncio + testcontainers | M01 §3.4 CI 容器策略 | dev 依赖组 |
| 迁移 | Alembic【推导·需评审】 | M01 §6.5 定稿前双轨之一 | `services/control_plane/migrations/` |

### 11.2 后端服务栈（M04–M11、M18）

| 技术 | 版本 / 约束（来源） | 使用方模块 |
|---|---|---|
| FastAPI + Pydantic v2 + uvicorn | 原文 §11.1 保留清单；各服务 §2.1「Python 3.11+」 | M04 / M05 / M06 / M07 / M09 / M10 / M11 / M18 |
| SSE（服务端） | 流式事件（M02 §4.5；`EventSource` / `fetch ReadableStream` 对端） | M04 / M07 / M15 |
| httpx | 模型直连 chat-completions 兼容端点（M03 §2.1 `model/provider.py`、M20 ProviderAdapter） | M03 / M07 / M20 |
| tiktoken | **纯 token 计数工具库**（M03/M07/M10 保留清单；禁用其编排生态） | M03 / M07 / M10 |
| asyncpg + SQLAlchemy(core, alembic 用) | PG 驱动；连接串 `postgresql+asyncpg://`（M01 §4.3 env 契约） | 全部服务 |
| Alembic | 迁移基线（M01 §6.5） | control-plane |
| Pillow / libvips | 图片只读解码（M05 §2.1 R8） | M05 |
| ffmpeg | 视频关键帧 + 元信息（M05 §2.1；受限子进程） | M05 |
| OCR 引擎 | 版面定位提取（M05【推导·需评审】定稿前可桩） | M05 |
| 本地 embedding（BGE-small 级 / sentence-transformers 级） | L2 语义路由（M06 §2.1【推导·需评审】） | M06 |
| 正则引擎 + JSON Schema 校验器 | L1 输入护栏（M06 §2.1 保留清单） | M06 |
| pgvector / 独立向量库 | 向量检索（M10 §2.1【推导·需评审：M10 定稿】）；M01 initdb 预装兜底 | M10 |
| BM25 + 自研 RRF | 混合检索关键词路（M10 §2.1；禁 RAG 框架） | M10 |
| esbuild / tsc | TSX 编译（M08 §2.1 R7；编译只在一次性沙箱内） | M08 |
| headless 浏览器或 jsdom | 沙箱渲染断言（M08 §2.1 R8；不模拟真实设备） | M08 |
| gVisor / Kata 或容器 + seccomp/cgroups | 一次性沙箱隔离（M08 §2.1 R16；镜像归 M08 `infra/sandbox`） | M08 |
| OpenTelemetry SDK | 打 span + 自建 `agent_event`/`trace_span` 表（M03/M04/M11；禁 LangFuse） | 全部服务 |

### 11.3 前端栈（M12–M17，apps/web）

| 技术 | 版本 / 约束（来源） | 镜像落位 |
|---|---|---|
| React 19 + TypeScript 5.x + Vite 6.x SPA | M12 §2.1（原文 §6.8 未给版本，【契约约定】以 package.json 锁定为准） | `web-dev:dev` / `web:prod` |
| Tailwind CSS + shadcn/ui（Radix） | M12 §2.1 UI 层 | apps/web（M12 装配） |
| TanStack Query / Zustand / `@tanstack/react-virtual` | M12 §2.1 数据/状态/列表虚拟化 | apps/web |
| CodeMirror 6 / Monaco；Recharts / ECharts | M12 §2.1 代码展示 / 图表 | apps/web（M12 装配） |
| Zod + openapi-typescript | M02 生成链路（I3） | packages/contracts + sdk/ts |
| BFF | node + tsx（M02 §4.1.3【契约约定】路径；不做鉴权判定） | `bff:dev` |
| Vitest + Testing Library + MSW + Playwright + Storybook | M12 §2.1 测试层（事件序列回放测试） | dev 依赖（M12 装配） |
| Biome/ESLint + axe + Sentry + Lighthouse CI | M12 §2.1 质量门禁（fail-closed） | CI（M12 装配） |

### 11.4 契约与协议（M02）

| 技术 | 说明 |
|---|---|
| OpenAPI 3.1 | REST 单一事实来源 `packages/contracts/openapi.yaml` |
| JSON Schema draft 2020-12 | 4 份协议 schema（events / widget_spec / widget_bundle / widget_runtime） |
| Zod（TS）/ Pydantic v2（Py） | 运行时校验，均由契约源生成（I3） |
| enums 单一来源 | `enums.source.json` → `enums.ts` / `enums.py` 同源生成（I2） |
| CI 一致性 | `contracts:check` 每次 PR 必跑（§8 W6 行） |

### 11.5 基础设施与观测（本模块 compose 供给）

| 组件 | 版本钉死 | profile | 端口 | volume |
|---|---|---|---|---|
| PostgreSQL + pgvector | `pgvector/pgvector:pg16`（= postgres:16 + vector 扩展；镜像选择最终归 M10 定稿） | core | 127.0.0.1:5432 | `pgdata` |
| Redis | `redis:7-alpine`（appendonly） | core | 127.0.0.1:6379 | `redis-data` |
| MinIO + mc | RELEASE 钉死（本机镜像源受限时以同上游 latest 代替，CI 直连官方） | core | 127.0.0.1:9000/9001 | `minio-data` |
| otel-collector-contrib | 版本钉死 | observability | 4317/4318 | — |
| Grafana / Loki | 版本钉死（配置复用 M11 `infra/observability/`） | observability | 3000 / 3100 | `grafana-data` / `loki-data` |
| nginx | `nginx:1.27-alpine`（web prod 静态形态） | —（prod 镜像） | 80 | — |

### 11.6 镜像分层构建标准（v1.1 实现，替换 §3.3 单文件形态）

| 层 | 镜像 | 基座 | 内容 |
|---|---|---|---|
| L1 | `widgetpilot/python-base:dev` | `python:3.12-slim-bookworm` | uv + `uv sync`（全部 Python 依赖）+ 非 root 用户 10001 + ffmpeg 系统包 |
| L1 | `widgetpilot/node-base:dev` | `node:22-alpine` | corepack pnpm + npm registry 配置 |
| L2 | 7 个服务镜像 | FROM `python-base` | 仅源码层（packages/ + services/），worker 探针用心跳文件 mtime |
| L3 | `web-dev:dev` | FROM `node-base` | pnpm workspace 依赖 + apps/web 源码（vite 热重载） |
| L3 | `bff:dev` | FROM `node-base` | pnpm workspace 依赖 + apps/bff 源码（tsx） |
| L3 | `web:prod`（构建验证用） | node-base → `nginx:1.27-alpine` | vite build 产物静态托管 + SPA fallback |

> 与 §3.3 原文的差异：单文件 multi-stage Dockerfile → **分层基座形态**（基座与源码层分离，服务镜像共享依赖层）。
> 动机：7 个 Python 服务依赖完全同构，分层后改源码重建秒级完成、磁盘占用减半；§3.3 的其它标准（非 root / HEALTHCHECK / 层缓存 / tag 策略）全部保留。
> 国内构建约束：`ghcr.io/astral-sh/uv` 基座不可达，改为 `python:3.12-slim` + `pip install uv`（PyPI 走阿里云镜像，`--build-arg PIP_INDEX_URL` 可覆盖；npm 走 npmmirror）。

### 11.7 技术栈 → 镜像差异对照（本汇总定稿时的核对结论）

| 文档要求 | 镜像/依赖现状 | 结论 |
|---|---|---|
| Python 3.11+（镜像钉 3.12） | python-base = python:3.12-slim-bookworm | ✅ 一致 |
| Node 22 / React 19 / Vite 6 / TS 5.x | node-base = node:22-alpine；web = react 19 + vite 6 + ts 5.7 | ✅ 一致 |
| postgres:16 + pgvector 兜底 | pgvector/pgvector:pg16（postgres:16 同基 + vector） | ✅ 一致（M10 定稿前形态） |
| redis:7 / minio RELEASE 钉死 / otelcol+grafana+loki 版本钉死 | compose 已按 §4.1 配置 | ✅ 一致 |
| tiktoken / Pillow / jsonschema / ffmpeg 等文档明确的运行期基础库 | python-base 依赖层**缺 tiktoken、Pillow、jsonschema、ffmpeg** | ⚠️ 差异 → 本轮补入并重建 |
| M12 前端基座运行时库（TanStack Query / Zustand / react-virtual / Recharts） | apps/web **缺** | ⚠️ 差异 → 本轮补入并重建 web-dev |
| M08 esbuild/tsc（沙箱内编译）、M05 OCR/libvips、M06 embedding 权重、M10 向量库定稿 | 未定稿或归 M05/M06/M08/M10，不预装 | 📌 按各模块定稿时补入 |

