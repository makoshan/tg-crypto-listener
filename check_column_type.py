#!/usr/bin/env python3
"""Check the actual column type of the embedding field"""

import asyncio
import os
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://woxbgotwkbbtiaerzrqu.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndveGJnb3R3a2JidGlhZXJ6cnF1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTkzNTU3MzMsImV4cCI6MjA3NDkzMTczM30.oS0b-N1l7midTEZ1qlD8qovPB_IkeJM5cYele7AZ10M")

async def main():
    print("Checking embedding column type in news_events table...")
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Query the database schema using SQL via RPC
    # We'll check information_schema to see the actual column type
    sql_query = """
    SELECT
        column_name,
        data_type,
        udt_name,
        character_maximum_length
    FROM information_schema.columns
    WHERE table_name = 'news_events'
    AND column_name = 'embedding'
    """

    try:
        # Execute raw SQL query
        result = client.rpc('execute_sql', {'query': sql_query}).execute()
        print(f"✅ Schema info: {result.data}")
    except Exception as e:
        print(f"❌ RPC execute_sql not available: {e}")
        print("\nTrying alternative method...")

        # Alternative: check by querying pg_typeof on a sample record
        try:
            result = client.table("news_events").select("id,embedding").not_.is_("embedding", "null").limit(1).execute()
            if result.data and len(result.data) > 0:
                embedding = result.data[0].get('embedding')
                print(f"Embedding field type in Python: {type(embedding).__name__}")
                if isinstance(embedding, str):
                    print("⚠️  Column appears to be TEXT type (returns as string)")
                    print(f"Sample value length: {len(embedding)}")
                elif isinstance(embedding, list):
                    print("✅ Column appears to be VECTOR type (returns as list)")
                    print(f"Dimensions: {len(embedding)}")
        except Exception as e2:
            print(f"❌ Alternative check failed: {e2}")

if __name__ == "__main__":
    asyncio.run(main())
