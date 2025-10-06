#!/bin/bash
# Supabase Embedding 格式验证脚本（使用 curl）

SUPABASE_URL="https://woxbgotwkbbtiaerzrqu.supabase.co"
SUPABASE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndveGJnb3R3a2JidGlhZXJ6cnF1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTkzNTU3MzMsImV4cCI6MjA3NDkzMTczM30.oS0b-N1l7midTEZ1qlD8qovPB_IkeJM5cYele7AZ10M"

echo "================================================================================"
echo "Supabase Embedding 格式验证工具"
echo "================================================================================"

echo ""
echo "[步骤 1] 检查最新 5 条记录的 embedding 格式"
echo "--------------------------------------------------------------------------------"

# 查询最新的 5 条记录
curl -s -H "apikey: $SUPABASE_KEY" \
  "$SUPABASE_URL/rest/v1/news_events?select=id,created_at,embedding&order=id.desc&limit=5" | \
python3 -c '
import sys
import json

try:
    data = json.load(sys.stdin)

    if not data:
        print("❌ 没有找到记录")
        sys.exit(1)

    print(f"✅ 找到 {len(data)} 条记录\n")

    string_count = 0
    vector_count = 0
    null_count = 0

    for idx, row in enumerate(data, 1):
        event_id = row.get("id")
        created_at = row.get("created_at", "")[:19]
        embedding = row.get("embedding")

        print(f"记录 #{idx} (ID: {event_id}, 时间: {created_at})")

        if embedding is None:
            print(f"  ⚠️  NULL embedding")
            null_count += 1
        elif isinstance(embedding, str):
            print(f"  ❌ 格式错误：存储为字符串")
            print(f"  字符串长度: {len(embedding)}")
            print(f"  前 100 字符: {embedding[:100]}")
            string_count += 1

            # 尝试解析
            try:
                parsed = json.loads(embedding)
                if isinstance(parsed, list):
                    print(f"  解析后维度: {len(parsed)} 维")
            except:
                pass

        elif isinstance(embedding, list):
            print(f"  ✅ 格式正确：vector 类型（API 返回为列表）")
            print(f"  维度: {len(embedding)}")
            print(f"  前 3 个值: {embedding[:3]}")
            vector_count += 1
        else:
            print(f"  ⚠️  未知类型: {type(embedding).__name__}")

        print()

    print("="*80)
    print("统计结果：")
    print(f"  字符串格式（错误）: {string_count}")
    print(f"  Vector 格式（正确）: {vector_count}")
    print(f"  NULL: {null_count}")
    print("="*80)

except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
'

echo ""
echo "[步骤 2] 测试向量查询 RPC 函数"
echo "--------------------------------------------------------------------------------"

# 创建测试 embedding（1536 维，全 0）
TEST_EMBEDDING="[$(python3 -c 'print(",".join(["0.0"]*1536))'))]"

echo "正在调用 search_memory_events RPC..."

curl -s -H "apikey: $SUPABASE_KEY" \
  -H "Content-Type: application/json" \
  "$SUPABASE_URL/rest/v1/rpc/search_memory_events" \
  -d "{
    \"query_embedding\": $TEST_EMBEDDING,
    \"match_threshold\": 0.1,
    \"match_count\": 3,
    \"min_confidence\": 0.0,
    \"time_window_hours\": 168
  }" | python3 -c '
import sys
import json

try:
    data = json.load(sys.stdin)

    if isinstance(data, dict) and "code" in data:
        print(f"❌ RPC 调用失败: {data.get(\"message\", \"未知错误\")}")
        print(f"   错误代码: {data.get(\"code\")}")
        print(f"   详情: {data.get(\"details\", \"无\")}")
        sys.exit(1)

    if isinstance(data, list):
        print(f"✅ RPC 调用成功")
        print(f"返回结果数量: {len(data)}")

        if len(data) == 0:
            print("\n⚠️  警告：返回 0 条结果")
            print("   这说明向量查询功能无法正常工作")
            print("   原因：embedding 存储格式不正确（字符串而非 vector）")
        else:
            print("\n✅ 成功检索到记忆！")
            for i, mem in enumerate(data[:3], 1):
                print(f"  [{i}] similarity={mem.get(\"similarity\", 0):.3f}, confidence={mem.get(\"confidence\", 0):.2f}")
    else:
        print(f"⚠️  未知响应格式: {type(data).__name__}")
        print(data)

except Exception as e:
    print(f"❌ 错误: {e}")
    import traceback
    traceback.print_exc()
'

echo ""
echo "[步骤 3] 诊断和修复建议"
echo "--------------------------------------------------------------------------------"

cat << 'EOF'

📋 如果检测到问题，请按以下步骤修复：

┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. 代码已修复 ✅                                                              │
│    文件：src/db/repositories.py                                              │
│    新写入的数据将使用正确的 vector 格式                                        │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. 迁移旧数据（在 Supabase Dashboard → SQL Editor 中执行）                   │
└─────────────────────────────────────────────────────────────────────────────┘

先检查列类型：

  SELECT
    column_name,
    data_type,
    udt_name
  FROM information_schema.columns
  WHERE table_name = 'news_events'
  AND column_name = 'embedding';

如果是 USER-DEFINED (vector)，执行方案 A：

  -- 转换字符串为 vector
  UPDATE news_events
  SET embedding = embedding::text::vector(1536)
  WHERE embedding IS NOT NULL;

如果是 text，执行方案 B：

  -- 先修改列类型
  ALTER TABLE news_events
  ALTER COLUMN embedding TYPE vector(1536)
  USING embedding::text::vector(1536);

┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. 创建向量索引（提升查询性能）                                               │
└─────────────────────────────────────────────────────────────────────────────┘

  CREATE INDEX IF NOT EXISTS idx_news_events_embedding
  ON news_events USING ivfflat(embedding vector_cosine_ops)
  WITH (lists = 100);

┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. 重新运行此脚本验证                                                         │
└─────────────────────────────────────────────────────────────────────────────┘

  bash verify_embedding.sh

EOF

echo ""
echo "================================================================================"
echo "验证完成"
echo "================================================================================"
