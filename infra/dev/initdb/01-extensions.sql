-- M01 §2.3 / §6.5：postgres 初始化——仅放扩展与 role；
-- 业务表一律走迁移（alembic），initdb 不建业务表。
-- 仅在 pgdata volume 为空时执行一次。

-- 向量能力兜底（M10 定稿前预装 pgvector，若镜像不含该扩展则跳过不阻塞）
CREATE EXTENSION IF NOT EXISTS vector;
