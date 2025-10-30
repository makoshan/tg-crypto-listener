"""Gemini API key rotation manager for bypassing rate limits."""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import List

logger = logging.getLogger(__name__)


class GeminiKeyRotator:
    """Manages rotation of multiple Gemini API keys to distribute load."""

    def __init__(self, api_keys: List[str]) -> None:
        """Initialize the key rotator.

        Args:
            api_keys: List of Gemini API keys to rotate through
        """
        if not api_keys:
            raise ValueError("至少需要提供一个 Gemini API key")

        # Filter out empty keys
        self._keys = [key.strip() for key in api_keys if key.strip()]

        if not self._keys:
            raise ValueError("至少需要提供一个有效的 Gemini API key")

        self._current_index = 0
        self._lock = threading.Lock()
        self._usage_counter = Counter()

        logger.info(f"🔑 Gemini Key Rotator 已初始化，共 {len(self._keys)} 个 API keys")

    def get_next_key(self) -> str:
        """Get the next API key in rotation.

        Returns:
            The next API key to use
        """
        with self._lock:
            current_idx = self._current_index
            key = self._keys[current_idx]
            self._usage_counter[current_idx] += 1
            usage_count = self._usage_counter[current_idx]

            # Move to next key for next call
            self._current_index = (self._current_index + 1) % len(self._keys)

            logger.debug(
                "🔑 Gemini API Key 轮换: 使用 key[%d/%d] (使用次数: %d) %s...",
                current_idx + 1,
                len(self._keys),
                usage_count,
                key[:8],
            )

            return key

    def get_current_key(self) -> str:
        """Get the current API key without rotating.

        Returns:
            The current API key
        """
        with self._lock:
            return self._keys[self._current_index]

    def mark_key_failed(self, key: str) -> None:
        """Mark a key as failed (for potential future blacklisting).

        Args:
            key: The API key that failed
        """
        key_index = None
        with self._lock:
            try:
                key_index = self._keys.index(key) + 1
            except ValueError:
                pass

        if key_index:
            logger.warning(
                "⚠️ Gemini API key[%d/%d] 调用失败: %s... (已标记失败)",
                key_index,
                len(self._keys),
                key[:8],
            )
        else:
            logger.warning(f"⚠️ Gemini API key 调用失败: {key[:8]}... (已标记失败)")

    def get_usage_stats(self) -> dict[int, int]:
        """Get usage statistics for all keys.

        Returns:
            Dictionary mapping key index to usage count
        """
        with self._lock:
            return dict(self._usage_counter)

    def get_key_index(self, key: str) -> int | None:
        """Get the index of a key.

        Args:
            key: The API key to find

        Returns:
            The 1-based index of the key, or None if not found
        """
        with self._lock:
            try:
                return self._keys.index(key) + 1
            except ValueError:
                return None

    def reset_stats(self) -> None:
        """Reset usage statistics."""
        with self._lock:
            self._usage_counter.clear()
            logger.info("🔄 Gemini Key Rotator 使用统计已重置")

    @property
    def key_count(self) -> int:
        """Get the number of available keys."""
        return len(self._keys)
