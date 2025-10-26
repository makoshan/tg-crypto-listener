# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**tg-crypto-listener** is a Telegram message monitoring and intelligent signal forwarding service for cryptocurrency markets. It listens to Telegram channels, filters and deduplicates messages, translates content, performs AI analysis (via Gemini/Claude), and forwards actionable signals to target channels.

This is a Python 3.9+ project using Telethon for Telegram integration, with optional AI/translation features and Supabase persistence.

## Development Commands

### Running the Listener

```bash
# Using uvx (recommended - auto-manages dependencies)
uvx --with-requirements requirements.txt python -m src.listener

# Manual virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.listener
```

### PM2 Process Management (Production)

```bash
npm run start      # Start listener with PM2
npm run stop       # Stop the process
npm run restart    # Restart the process
npm run status     # Check process status
npm run logs       # View logs
npm run monitor    # Real-time monitoring
```

PM2 configuration is in `ecosystem.config.js`, which runs `uvx --with-requirements requirements.txt python -m src.listener`.

### Testing Utilities

```bash
# Test Gemini API
python scripts/gemini_stream_example.py

# Database verification
python verify_supabase.py
python check_db.py

# Memory system testing
python test_memory_retrieval.py
python diagnose_memory.py
```

## Architecture Overview

### Core Flow

1. **Message Ingestion** (`src/listener.py`): Telethon monitors `SOURCE_CHANNELS`, receives new messages
2. **Keyword Filtering**: Messages filtered by keywords from `keywords.txt` (gitignored) or `FILTER_KEYWORDS` env var
3. **Deduplication** (4-tier system):
   - In-memory window dedup (`MessageDeduplicator`)
   - Database hash dedup (`hash_raw`)
   - Semantic vector dedup (`embedding` + PostgreSQL RPC `find_similar_events`)
4. **Translation** (`src/ai/translator.py`): Multi-provider aggregator (DeepL, Azure, Google, Amazon, Baidu, Alibaba, Tencent, Huawei, Volcano, NiuTrans)
5. **AI Signal Analysis** (`src/ai/signal_engine.py`):
   - Fast analysis with Gemini/OpenAI-compatible models (90% of messages)
   - Optional deep analysis with Claude/Gemini Function Calling (high-value signals, confidence >= 0.75)
6. **Signal Deduplication** ✨ (`SignalMessageDeduplicator`): Detects similar AI-generated signals to prevent duplicate forwarding when different sources report the same event
7. **Memory Context** (`src/memory/`): Retrieves historical similar events to inject into AI prompts
8. **Message Forwarding** (`src/forwarder.py`): Sends formatted messages to `TARGET_CHAT_ID`
9. **Persistence** (`src/db/repositories.py`): Stores events and signals in Supabase

### Dual-Engine AI Analysis

**AiSignalEngine** (`src/ai/signal_engine.py:279`) orchestrates a two-tier analysis system:

- **Primary Engine**: Fast analysis with Gemini Flash or OpenAI-compatible models
- **Deep Analysis Engine**: Triggered for high-value signals (confidence >= `HIGH_VALUE_CONFIDENCE_THRESHOLD`)
  - Provider configured via `DEEP_ANALYSIS_PROVIDER` (claude/gemini)
  - Optional fallback via `DEEP_ANALYSIS_FALLBACK_PROVIDER`
  - Rate-limited via `DEEP_ANALYSIS_MIN_INTERVAL` (default 25s)
  - See `docs/deep_analysis_engine_switch_plan.md` for architecture

### Memory System

The memory system provides historical context to AI analysis:

- **Backends**: Local (keyword-based), Supabase (vector similarity), or Hybrid (Supabase with local fallback)
- **Configuration**: `MEMORY_BACKEND`, `MEMORY_ENABLED`, `MEMORY_MAX_NOTES`, `MEMORY_SIMILARITY_THRESHOLD`
- **Implementation**: `src/memory/factory.py` creates appropriate repository
- **Claude Integration**: Memory tool handler (`src/memory/memory_tool_handler.py`) manages long-term memory via Claude's memory tool API
- See `docs/memory_architecture.md` for detailed design

### LangGraph Pipeline (Experimental)

- **Toggle**: `USE_LANGGRAPH_PIPELINE=true` enables graph-based processing
- **Implementation**: `src/pipeline/langgraph_pipeline.py`
- **Purpose**: Explicit state modeling for filter → AI → memory → forward → persist flow
- **Status**: Shadow mode available, not production-ready
- See `docs/langgraph_migration_plan.md` for migration roadmap

## Configuration

All configuration is in `.env` (see README.md for comprehensive list). Key variables:

### Core Settings
- `TG_API_ID`, `TG_API_HASH`, `TG_PHONE`: Telegram credentials
- `SOURCE_CHANNELS`: Comma-separated list of source channels
- `TARGET_CHAT_ID`: Destination channel for forwarded messages
- `FILTER_KEYWORDS_FILE`: Path to keywords file (default: `keywords.txt`)

### AI Configuration
- `AI_ENABLED`: Enable/disable AI analysis
- `AI_PROVIDER`: Provider selection (gemini/openai/deepseek/qwen)
- `AI_MODEL_NAME`: Model identifier
- `AI_MAX_CONCURRENCY`: Concurrent AI request limit

### Deep Analysis
- `DEEP_ANALYSIS_ENABLED`: Enable deep analysis engine
- `DEEP_ANALYSIS_PROVIDER`: Primary provider (claude/gemini)
- `DEEP_ANALYSIS_FALLBACK_PROVIDER`: Fallback provider
- `CLAUDE_API_KEY`, `CLAUDE_MODEL`: Claude configuration
- `GEMINI_DEEP_MODEL`: Gemini Function Calling model for deep analysis

### Memory
- `MEMORY_ENABLED`: Enable memory context injection
- `MEMORY_BACKEND`: Backend type (local/supabase/hybrid)
- `MEMORY_MAX_NOTES`: Max historical entries per query

### Signal Deduplication ✨
- `SIGNAL_DEDUP_ENABLED`: Enable signal-level deduplication (default: true)
- `SIGNAL_DEDUP_WINDOW_MINUTES`: Time window in minutes (default: 360 = 6 hours)
- `SIGNAL_DEDUP_SIMILARITY`: Text similarity threshold 0.0-1.0 (default: 0.68)
- `SIGNAL_DEDUP_MIN_COMMON_CHARS`: Minimum common characters (default: 10)

### Database
- `ENABLE_DB_PERSISTENCE`: Enable Supabase persistence
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`: Supabase credentials
- `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL`: For semantic deduplication

## Important Implementation Details

### Deduplication Strategy (4-tier) ✨

1. **In-memory window**: `MessageDeduplicator` checks last N hours of messages (`DEDUP_WINDOW_HOURS`)
2. **Hash-based**: `compute_sha256(text)` → check `news_events.hash_raw`
3. **Semantic vector**: `compute_embedding()` → PostgreSQL RPC `find_similar_events()` with cosine similarity threshold
4. **Signal-level** ✨: `SignalMessageDeduplicator` detects similar AI-generated signals based on:
   - Normalized summary text (removes URLs, numbers, punctuation)
   - Metadata matching (action, direction, event_type, asset)
   - Text similarity threshold (default 0.68) using SequenceMatcher
   - Character set overlap (minimum 10 common characters)
   - Time window (default 6 hours)

**Critical**:
- Embedding dedup happens at both pre-processing (before AI) and persistence stages to prevent duplicates. See `src/listener.py:337-379` and `src/listener.py:836-856`.
- Signal dedup happens after AI analysis but before message formatting to prevent duplicate forwarding when different sources report the same event. See `src/listener.py:773-788` and `src/pipeline/langgraph_pipeline.py:872-894`.
- Full documentation: `docs/signal_deduplication.md`

### AI Response Parsing

`AiSignalEngine._parse_response_text()` (line 626) expects JSON with fields:
- `summary`: Chinese summary
- `event_type`: One of ALLOWED_EVENT_TYPES (listing/hack/regulation/etc)
- `asset`: Crypto asset code (2-10 chars, uppercase) or "NONE"
- `action`: buy/sell/observe
- `confidence`: 0.0-1.0
- `risk_flags`: Array of validation flags

**Asset validation** (line 695-720): Filters out NO_ASSET_TOKENS, non-matching patterns, stock tickers (TSLA, SPX, etc).

### Media Handling

`TelegramListener._extract_media()` (line 938) downloads photos/images as base64 for multimodal AI:
- Max inline size: 4MB (`MAX_INLINE_MEDIA_BYTES`)
- Supports photos, image documents
- Base64 encoded for Gemini multimodal prompts

### Translation Fallback Chain

`Translator` (`src/ai/translator.py`) tries providers in order specified by `TRANSLATION_PROVIDERS`:
1. Attempt primary provider
2. On failure/quota exhaustion, try next provider
3. If all fail, return original text with warning

Quota tracking per provider in memory, configurable via `TRANSLATION_PROVIDER_QUOTAS`.

### Persistence Flow

`TelegramListener._persist_event()` (line 775):
1. Compute hashes and embeddings if missing
2. Check exact hash duplicate → return if exists
3. Check semantic duplicate → return if exists
4. Insert `news_events` record
5. If AI successful, insert `ai_signals` record

**Critical**: This happens after forwarding decision to avoid persisting skipped messages.

## Code Organization

```
src/
├── listener.py              # Main entry point, Telethon event handler
├── config.py                # Configuration loader with validation
├── forwarder.py            # Message forwarding to Telegram
├── utils.py                 # Deduplicator, hashing, embedding utils
├── ai/
│   ├── signal_engine.py    # AI orchestration, dual-engine routing
│   ├── gemini_client.py    # Gemini API client
│   ├── gemini_function_client.py  # Gemini Function Calling for deep analysis
│   ├── anthropic_client.py # Claude API client
│   ├── translator.py       # Multi-provider translation aggregator
│   ├── translation_providers.py   # Provider-specific implementations
│   └── deep_analysis/      # Deep analysis engine abstraction
│       ├── base.py         # Abstract base class
│       ├── claude.py       # Claude implementation
│       ├── gemini.py       # Gemini Function Calling implementation
│       └── factory.py      # Engine creation factory
├── db/
│   ├── supabase_client.py  # Supabase async client wrapper
│   ├── repositories.py     # NewsEventRepository, AiSignalRepository
│   └── models.py           # Data models (NewsEventPayload, AiSignalPayload)
├── memory/
│   ├── factory.py          # Memory backend factory
│   ├── repository.py       # Supabase memory repository
│   ├── local_memory_store.py   # Local file-based memory
│   ├── hybrid_repository.py    # Hybrid backend with fallback
│   └── memory_tool_handler.py  # Claude Memory Tool integration
└── pipeline/
    └── langgraph_pipeline.py   # LangGraph experimental pipeline
```

## Common Gotchas

- **Keywords file**: `keywords.txt` is gitignored. Copy from `keywords.sample.txt` to get started
- **Session persistence**: Telegram session stored in `./session/tg_session` (gitignored)
- **Gemini 503 errors**: Reduce `AI_MAX_CONCURRENCY` or increase `AI_RETRY_BACKOFF_SECONDS`
- **LibreSSL warnings on macOS**: Use OpenSSL-based Python or suppress with `urllib3.disable_warnings`
- **Duplicate messages**: If seeing duplicates in DB, check embedding dedup is enabled (`OPENAI_API_KEY` set) and RPC function exists
- **Memory retrieval**: Different backends require different inputs - Local uses keywords, Supabase uses embeddings, Hybrid tries both

## Testing

No formal test suite currently. Key verification scripts:
- `verify_supabase.py`: Test Supabase connection and RPC functions
- `test_memory_retrieval.py`: Validate memory repository operations
- `test_context_management.py`: Test memory context editing for Claude
- `diagnose_memory.py`: Debug memory backend issues

## Documentation

Core docs in `docs/`:
- `ai_signal_plan_cn.md`: AI signal planning (Chinese)
- `aisignalengine_implementation.md`: AiSignalEngine implementation guide
- `deep_analysis_engine_switch_plan.md`: Dual-engine architecture
- `memory_architecture.md`: Memory system design
- `langgraph_migration_plan.md`: LangGraph migration roadmap
- `EMBEDDING_SETUP.md`: OpenAI embedding deduplication setup
