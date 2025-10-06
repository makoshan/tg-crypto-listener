-- ========================================================================
-- Embedding 列类型修复迁移脚本
-- ========================================================================
-- 目的：将 news_events.embedding 从 TEXT 类型转换为 vector(1536) 类型
-- 日期：2025-10-06
-- ========================================================================

-- 步骤 1: 检查当前状态
-- ========================================================================
DO $$
BEGIN
    RAISE NOTICE '=== 步骤 1: 检查当前 embedding 列状态 ===';
END $$;

SELECT
    column_name,
    data_type,
    udt_name,
    CASE
        WHEN data_type = 'text' THEN '❌ 需要修复'
        WHEN udt_name = 'vector' THEN '✅ 已经是 vector 类型'
        ELSE '⚠️ 未知类型'
    END as status
FROM information_schema.columns
WHERE table_name = 'news_events'
AND column_name = 'embedding';

-- 检查有多少条记录有 embedding
SELECT
    COUNT(*) as total_records,
    COUNT(embedding) as records_with_embedding,
    COUNT(embedding)::float / NULLIF(COUNT(*), 0) * 100 as percentage
FROM news_events;

-- ========================================================================
-- 步骤 2: 转换列类型从 TEXT 到 vector(1536)
-- ========================================================================
DO $$
BEGIN
    RAISE NOTICE '=== 步骤 2: 转换 embedding 列类型 ===';
END $$;

-- 确保 pgvector 扩展已启用
CREATE EXTENSION IF NOT EXISTS vector;

-- 转换列类型
-- 这会将所有现有的 JSON 字符串（如 "[-0.002,0.017,...]"）转换为真正的 vector 类型
ALTER TABLE news_events
ALTER COLUMN embedding TYPE vector(1536)
USING (
    CASE
        WHEN embedding IS NULL THEN NULL
        WHEN embedding::text ~ '^\[.*\]$' THEN embedding::text::vector(1536)
        ELSE NULL
    END
);

-- ========================================================================
-- 步骤 3: 创建向量索引以提升查询性能
-- ========================================================================
DO $$
BEGIN
    RAISE NOTICE '=== 步骤 3: 创建向量索引 ===';
END $$;

-- 删除旧索引（如果存在）
DROP INDEX IF EXISTS idx_news_events_embedding;

-- 创建 IVFFlat 索引用于余弦相似度搜索
-- lists = 100 适合中小规模数据集（< 100K 记录）
-- 如果数据量很大，可以调整 lists 参数：
--   - 1K-10K 记录: lists = 10-50
--   - 10K-100K 记录: lists = 50-100
--   - 100K-1M 记录: lists = 100-1000
CREATE INDEX idx_news_events_embedding
ON news_events
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- ========================================================================
-- 步骤 4: 验证修复结果
-- ========================================================================
DO $$
BEGIN
    RAISE NOTICE '=== 步骤 4: 验证修复结果 ===';
END $$;

-- 检查列类型是否已更新
SELECT
    column_name,
    data_type,
    udt_name,
    CASE
        WHEN udt_name = 'vector' THEN '✅ 修复成功'
        ELSE '❌ 修复失败'
    END as status
FROM information_schema.columns
WHERE table_name = 'news_events'
AND column_name = 'embedding';

-- 检查索引是否已创建
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'news_events'
AND indexname = 'idx_news_events_embedding';

-- 测试向量查询（使用零向量）
WITH test_query AS (
    SELECT array_fill(0.0, ARRAY[1536])::vector(1536) as query_vector
)
SELECT
    ne.id,
    ne.created_at,
    ne.embedding <=> tq.query_vector as distance,
    'Vector query works! ✅' as status
FROM news_events ne, test_query tq
WHERE ne.embedding IS NOT NULL
ORDER BY ne.embedding <=> tq.query_vector
LIMIT 3;

-- 统计信息
SELECT
    COUNT(*) as total_records,
    COUNT(embedding) as records_with_embedding,
    COUNT(CASE WHEN pg_typeof(embedding)::text = 'vector' THEN 1 END) as vector_type_count,
    '✅ 迁移完成！' as status
FROM news_events;

-- ========================================================================
-- 完成
-- ========================================================================
DO $$
BEGIN
    RAISE NOTICE '=== ✅ 迁移完成 ===';
    RAISE NOTICE '下一步：运行 verify_embedding_issue.py 验证应用层面的功能';
END $$;
