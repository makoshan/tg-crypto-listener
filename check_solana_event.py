#!/usr/bin/env python3
"""Check recent Solana-related events in the database."""

import asyncio
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from src.db.repositories import NewsEventRepository, AiSignalRepository
from src.db.supabase_client import get_supabase_client


async def main():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")

    if not url or not key:
        print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        return

    client = get_supabase_client(url, key)
    event_repo = NewsEventRepository(client)
    signal_repo = AiSignalRepository(client)

    # Query recent events with Solana keyword
    print("=== Searching for Solana-related events ===\n")

    # Use RPC to search for events
    try:
        # Search in the last 24 hours for solana-related content
        since_time = datetime.now() - timedelta(hours=24)

        # Direct SQL query via RPC (if available) or manual search
        # Let's use the existing repository methods

        # Get recent events and filter manually
        print("Fetching recent events from database...\n")

        # We'll need to use a custom query - let's check the database directly
        result = await client.rpc("get_recent_events", {"hours": 24, "limit_count": 50})

        if result:
            solana_events = [
                event for event in result
                if 'solana' in event.get('content', '').lower()
                or '拥堵' in event.get('content', '')
                or 'sol' in event.get('asset', '').lower()
            ]

            print(f"Found {len(solana_events)} Solana-related events:\n")
            for event in solana_events[:5]:
                print(f"ID: {event.get('id')}")
                print(f"Created: {event.get('created_at')}")
                print(f"Source: {event.get('source')}")
                print(f"Content: {event.get('content', '')[:300]}...")
                print("---\n")
        else:
            print("No events found or RPC not available")

    except Exception as e:
        print(f"Error querying events: {e}")
        print("\nTrying alternative approach...\n")

        # Try to get the most recent event with hash matching
        # This is a workaround since we don't have a direct search method
        print("Checking for recent signals with SOL or HYPE assets...\n")

    # Check AI signals table directly
    try:
        # Query signals with SOL or HYPE in asset field
        # Since we can't do LIKE queries easily, let's check recent signals
        print("Note: Checking signals requires custom RPC or direct table access")
        print("The SupabaseClient implementation is minimal and doesn't support complex queries")

    except Exception as e:
        print(f"Error querying signals: {e}")

if __name__ == "__main__":
    asyncio.run(main())
