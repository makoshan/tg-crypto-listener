"""Factory helpers for configuring memory backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.db.supabase_client import SupabaseError, get_supabase_client
from src.utils import setup_logger

from .hybrid_repository import HybridMemoryRepository
from .local_memory_store import LocalMemoryStore
from .memory_tool_handler import MemoryToolHandler
from .multi_source_repository import MultiSourceMemoryRepository
from .repository import MemoryRepositoryConfig, SupabaseMemoryRepository

logger = setup_logger(__name__)


@dataclass(slots=True)
class MemoryBackendBundle:
    """Container exposing memory components to AI engines."""

    provider: str = "disabled"
    handler: Optional[MemoryToolHandler] = None
    repository: Optional[Any] = None
    local_store: Optional[LocalMemoryStore] = None

    @property
    def enabled(self) -> bool:
        return self.provider != "disabled"


def create_memory_backend(config: Any) -> MemoryBackendBundle:
    """Instantiate memory backend components according to configuration."""

    memory_enabled = bool(getattr(config, "MEMORY_ENABLED", False))
    backend_name = getattr(config, "MEMORY_BACKEND", "supabase").strip().lower()
    base_path = getattr(config, "MEMORY_DIR", "./memories")

    bundle = MemoryBackendBundle(provider="disabled")
    if not memory_enabled:
        logger.info("记忆功能未启用，Memory backend 将保持禁用状态")
        return bundle

    repo_config = MemoryRepositoryConfig(
        max_notes=int(getattr(config, "MEMORY_MAX_NOTES", 3)),
        similarity_threshold=float(getattr(config, "MEMORY_SIMILARITY_THRESHOLD", 0.55)),
        lookback_hours=int(getattr(config, "MEMORY_LOOKBACK_HOURS", 72)),
        min_confidence=float(getattr(config, "MEMORY_MIN_CONFIDENCE", 0.6)),
    )

    local_store: Optional[LocalMemoryStore] = None
    repository: Optional[Any] = None
    provider_label = backend_name

    if backend_name in {"local", "filesystem"}:
        local_store = LocalMemoryStore(
            base_path=base_path,
            lookback_hours=repo_config.lookback_hours,
        )
        repository = local_store
        provider_label = "local"
        logger.info("Memory backend: LocalMemoryStore 已初始化")
    elif backend_name == "supabase":
        try:
            client = get_supabase_client(
                getattr(config, "SUPABASE_URL", ""),
                getattr(config, "SUPABASE_SERVICE_KEY", ""),
                timeout=float(getattr(config, "SUPABASE_TIMEOUT_SECONDS", 8.0)),
            )
        except SupabaseError as exc:
            logger.warning("Supabase backend 初始化失败，降级到本地存储: %s", exc)
            local_store = LocalMemoryStore(
                base_path=base_path,
                lookback_hours=repo_config.lookback_hours,
            )
            repository = local_store
            provider_label = "local-fallback"
        else:
            supabase_repo = SupabaseMemoryRepository(client, repo_config)
            repository = supabase_repo
            provider_label = "supabase"
            logger.info("Memory backend: SupabaseMemoryRepository 已初始化")

            secondary_enabled = bool(getattr(config, "SUPABASE_SECONDARY_ENABLED", False))
            if secondary_enabled:
                secondary_url = getattr(config, "SUPABASE_SECONDARY_URL", "")
                secondary_key = getattr(config, "SUPABASE_SECONDARY_SERVICE_KEY", "")
                if not secondary_url or not secondary_key:
                    logger.warning("副记忆库已启用，但缺少 URL 或 Service Key，忽略副库配置")
                else:
                    try:
                        secondary_client = get_supabase_client(
                            secondary_url,
                            secondary_key,
                            timeout=float(getattr(config, "SUPABASE_TIMEOUT_SECONDS", 8.0)),
                        )
                    except SupabaseError as exc:  # pragma: no cover - network failure
                        logger.warning("无法初始化副记忆库 Supabase client: %s", exc)
                    else:
                        repository = MultiSourceMemoryRepository(
                            primary=supabase_repo,
                            secondary_client=secondary_client,
                            secondary_table=getattr(config, "SUPABASE_SECONDARY_TABLE", "docs"),
                            config=repo_config,
                            secondary_similarity_threshold=float(
                                getattr(
                                    config,
                                    "SUPABASE_SECONDARY_SIMILARITY_THRESHOLD",
                                    repo_config.similarity_threshold,
                                )
                            ),
                            secondary_max_results=int(
                                getattr(
                                    config,
                                    "SUPABASE_SECONDARY_MAX_RESULTS",
                                    repo_config.max_notes,
                                )
                            ),
                        )
                        provider_label = "supabase-multi"
                        logger.info(
                            "Memory backend: MultiSourceMemoryRepository 已启用 "
                            "(primary=%s, secondary=%s)",
                            getattr(config, "SUPABASE_URL", "") or "<unset>",
                            secondary_url or "<unset>",
                        )
    elif backend_name == "hybrid":
        try:
            client = get_supabase_client(
                getattr(config, "SUPABASE_URL", ""),
                getattr(config, "SUPABASE_SERVICE_KEY", ""),
                timeout=float(getattr(config, "SUPABASE_TIMEOUT_SECONDS", 8.0)),
            )
        except SupabaseError as exc:
            logger.warning("混合记忆初始化失败（Supabase 不可用），降级为本地模式: %s", exc)
            local_store = LocalMemoryStore(
                base_path=base_path,
                lookback_hours=repo_config.lookback_hours,
            )
            repository = local_store
            provider_label = "local-fallback"
        else:
            supabase_repo = SupabaseMemoryRepository(client, repo_config)
            primary_repo: Any = supabase_repo

            secondary_enabled = bool(getattr(config, "SUPABASE_SECONDARY_ENABLED", False))
            if secondary_enabled:
                secondary_url = getattr(config, "SUPABASE_SECONDARY_URL", "")
                secondary_key = getattr(config, "SUPABASE_SECONDARY_SERVICE_KEY", "")
                if not secondary_url or not secondary_key:
                    logger.warning("副记忆库已启用，但缺少 URL 或 Service Key，忽略副库配置")
                else:
                    try:
                        secondary_client = get_supabase_client(
                            secondary_url,
                            secondary_key,
                            timeout=float(getattr(config, "SUPABASE_TIMEOUT_SECONDS", 8.0)),
                        )
                    except SupabaseError as exc:  # pragma: no cover - network failure
                        logger.warning("无法初始化副记忆库 Supabase client: %s", exc)
                    else:
                        primary_repo = MultiSourceMemoryRepository(
                            primary=supabase_repo,
                            secondary_client=secondary_client,
                            secondary_table=getattr(config, "SUPABASE_SECONDARY_TABLE", "docs"),
                            config=repo_config,
                            secondary_similarity_threshold=float(
                                getattr(
                                    config,
                                    "SUPABASE_SECONDARY_SIMILARITY_THRESHOLD",
                                    repo_config.similarity_threshold,
                                )
                            ),
                            secondary_max_results=int(
                                getattr(
                                    config,
                                    "SUPABASE_SECONDARY_MAX_RESULTS",
                                    repo_config.max_notes,
                                )
                            ),
                        )
                        logger.info(
                            "Hybrid memory: MultiSourceMemoryRepository 已启用副库 (secondary=%s)",
                            secondary_url or "<unset>",
                        )

            local_store = LocalMemoryStore(
                base_path=base_path,
                lookback_hours=repo_config.lookback_hours,
            )
            repository = HybridMemoryRepository(
                supabase_repo=primary_repo,
                local_store=local_store,
                config=repo_config,
            )
            provider_label = "hybrid-multi" if primary_repo is not supabase_repo else "hybrid"
            logger.info("Memory backend: HybridMemoryRepository 已初始化")
    else:
        logger.warning("未知的 MEMORY_BACKEND=%s，降级为本地模式", backend_name)
        local_store = LocalMemoryStore(
            base_path=base_path,
            lookback_hours=repo_config.lookback_hours,
        )
        repository = local_store
        provider_label = "local"

    handler = MemoryToolHandler(base_path=base_path, backend=repository)

    bundle.provider = provider_label
    bundle.handler = handler
    bundle.repository = repository
    bundle.local_store = local_store

    return bundle
