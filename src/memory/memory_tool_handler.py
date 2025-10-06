"""
Memory Tool Handler - Claude Memory Tool 实现

基于 Anthropic Memory & Context Management Cookbook
文档: docs/memory_cookbook.ipynb
"""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Union

from src.utils import setup_logger

logger = setup_logger(__name__)


class SecurityError(Exception):
    """安全相关错误（路径穿越、权限等）"""
    pass


class MemoryToolHandler:
    """
    Claude Memory Tool 处理器

    支持 6 个命令:
    - view: 查看目录或文件内容
    - create: 创建或覆盖文件
    - str_replace: 替换文件中的文本
    - insert: 在指定行插入文本
    - delete: 删除文件或目录
    - rename: 重命名或移动文件

    安全特性:
    - 路径验证（防止目录穿越攻击）
    - 审计日志（记录所有写操作）
    - 内容审查（可选，防止 Prompt Injection）
    """

    def __init__(self, base_path: str = "./memories", backend: Optional[Any] = None):
        """
        初始化 Memory Tool Handler

        Args:
            base_path: 记忆存储根目录（仅 Local 模式使用）
            backend: 存储后端（LocalMemoryStore / SupabaseMemoryRepository / HybridMemoryRepository）
        """
        self.base_path = Path(base_path).resolve()
        self.backend = backend

        # 确保基础目录存在
        if not self.backend:  # Local 模式
            self.base_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Memory Tool Handler 初始化 (Local 模式): {self.base_path}")
        else:
            logger.info(f"Memory Tool Handler 初始化 ({type(backend).__name__} 模式)")

    def execute_tool_use(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行 Claude Memory Tool 命令

        Args:
            tool_input: 工具输入参数（包含 command 字段）

        Returns:
            工具执行结果（success, message, data）
        """
        command = tool_input.get("command")

        try:
            if command == "view":
                return self._view(tool_input["path"])
            elif command == "create":
                return self._create(tool_input["path"], tool_input["file_text"])
            elif command == "str_replace":
                return self._str_replace(
                    tool_input["path"],
                    tool_input["old_str"],
                    tool_input["new_str"]
                )
            elif command == "insert":
                return self._insert(
                    tool_input["path"],
                    tool_input["insert_line"],
                    tool_input["insert_text"]
                )
            elif command == "delete":
                return self._delete(tool_input["path"])
            elif command == "rename":
                return self._rename(tool_input["old_path"], tool_input["new_path"])
            else:
                return {
                    "success": False,
                    "error": f"未知命令: {command}"
                }
        except SecurityError as e:
            logger.error(f"安全错误: {e}")
            return {"success": False, "error": f"安全错误: {str(e)}"}
        except Exception as e:
            logger.error(f"Memory Tool 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _validate_path(self, path: str) -> Path:
        """
        验证路径安全性（防止目录穿越攻击）

        Args:
            path: 待验证的路径

        Returns:
            验证后的绝对路径

        Raises:
            SecurityError: 路径不安全
        """
        # 移除前导斜杠，转为相对路径
        path = path.lstrip("/")

        # 构造绝对路径并解析
        full_path = (self.base_path / path).resolve()

        # 检查是否在 base_path 内
        if not full_path.is_relative_to(self.base_path):
            raise SecurityError(f"路径在 base_path 之外: {path}")

        return full_path

    def _view(self, path: str) -> Dict[str, Any]:
        """查看目录或文件内容"""
        full_path = self._validate_path(path)

        if full_path.is_dir():
            # 列出目录内容
            try:
                items = sorted(full_path.iterdir(), key=lambda x: x.name)
                files = [f.name for f in items if f.is_file()]
                dirs = [d.name for d in items if d.is_dir()]

                content = f"Directory: {path}\n"
                if not items:
                    content += "(empty)"
                else:
                    for d in dirs:
                        content += f"- {d}/\n"
                    for f in files:
                        content += f"- {f}\n"

                return {"success": True, "content": content.strip()}
            except Exception as e:
                return {"success": False, "error": f"读取目录失败: {e}"}

        elif full_path.is_file():
            # 读取文件内容（带行号，类似 cat -n）
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                # 限制显示行数（避免超大文件）
                max_lines = 500
                if len(lines) > max_lines:
                    content = f"File: {path} (showing first {max_lines} lines)\n"
                    lines = lines[:max_lines]
                else:
                    content = f"File: {path}\n"

                for i, line in enumerate(lines, 1):
                    content += f"{i:4}: {line}"

                return {"success": True, "content": content}
            except Exception as e:
                return {"success": False, "error": f"读取文件失败: {e}"}

        else:
            return {"success": False, "error": f"路径不存在: {path}"}

    def _create(self, path: str, content: str) -> Dict[str, Any]:
        """创建或覆盖文件"""
        full_path = self._validate_path(path)

        # 审计日志
        logger.warning(
            f"Memory 写入: {path} | "
            f"内容预览: {content[:200]}..." if len(content) > 200 else content
        )

        # 内容审查（可选，防止 Prompt Injection）
        sanitized_content = self._sanitize_content(content)

        # 确保父目录存在
        full_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(sanitized_content)

            logger.info(f"文件已创建: {path}")
            return {
                "success": True,
                "message": f"File created successfully at {path}"
            }
        except Exception as e:
            return {"success": False, "error": f"创建文件失败: {e}"}

    def _str_replace(self, path: str, old_str: str, new_str: str) -> Dict[str, Any]:
        """替换文件中的文本"""
        full_path = self._validate_path(path)

        if not full_path.is_file():
            return {"success": False, "error": f"文件不存在: {path}"}

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 检查 old_str 是否存在
            if old_str not in content:
                return {
                    "success": False,
                    "error": f"未找到要替换的文本: {old_str[:50]}..."
                }

            # 检查 old_str 是否唯一
            occurrences = content.count(old_str)
            if occurrences > 1:
                return {
                    "success": False,
                    "error": f"文本出现 {occurrences} 次，不唯一。请提供更多上下文或使用 replace_all"
                }

            # 执行替换
            new_content = content.replace(old_str, new_str, 1)

            # 审计日志
            logger.warning(f"Memory 修改: {path} | 替换: {old_str[:50]}... → {new_str[:50]}...")

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return {
                "success": True,
                "message": f"File {path} has been edited successfully"
            }
        except Exception as e:
            return {"success": False, "error": f"替换失败: {e}"}

    def _insert(self, path: str, line_number: int, text: str) -> Dict[str, Any]:
        """在指定行插入文本"""
        full_path = self._validate_path(path)

        if not full_path.is_file():
            return {"success": False, "error": f"文件不存在: {path}"}

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # 插入文本（行号从 1 开始）
            if line_number < 1 or line_number > len(lines) + 1:
                return {
                    "success": False,
                    "error": f"行号超出范围: {line_number} (文件共 {len(lines)} 行)"
                }

            lines.insert(line_number - 1, text + "\n")

            # 审计日志
            logger.warning(f"Memory 插入: {path}:{line_number} | 内容: {text[:50]}...")

            with open(full_path, "w", encoding="utf-8") as f:
                f.writelines(lines)

            return {
                "success": True,
                "message": f"Text inserted at line {line_number} in {path}"
            }
        except Exception as e:
            return {"success": False, "error": f"插入失败: {e}"}

    def _delete(self, path: str) -> Dict[str, Any]:
        """删除文件或目录"""
        full_path = self._validate_path(path)

        if not full_path.exists():
            return {"success": False, "error": f"路径不存在: {path}"}

        # 审计日志
        logger.warning(f"Memory 删除: {path}")

        try:
            if full_path.is_dir():
                shutil.rmtree(full_path)
                return {"success": True, "message": f"Directory {path} deleted"}
            else:
                full_path.unlink()
                return {"success": True, "message": f"File {path} deleted"}
        except Exception as e:
            return {"success": False, "error": f"删除失败: {e}"}

    def _rename(self, old_path: str, new_path: str) -> Dict[str, Any]:
        """重命名或移动文件"""
        old_full = self._validate_path(old_path)
        new_full = self._validate_path(new_path)

        if not old_full.exists():
            return {"success": False, "error": f"源路径不存在: {old_path}"}

        if new_full.exists():
            return {"success": False, "error": f"目标路径已存在: {new_path}"}

        # 审计日志
        logger.warning(f"Memory 重命名: {old_path} → {new_path}")

        try:
            # 确保目标目录存在
            new_full.parent.mkdir(parents=True, exist_ok=True)

            old_full.rename(new_full)
            return {
                "success": True,
                "message": f"Renamed {old_path} to {new_path}"
            }
        except Exception as e:
            return {"success": False, "error": f"重命名失败: {e}"}

    def _sanitize_content(self, content: str) -> str:
        """
        内容审查（防止 Prompt Injection）

        过滤危险模式:
        - <|.*?|> - Special tokens
        - ```.*system.*``` - System prompt injection
        - ignore previous - Instruction override
        """
        dangerous_patterns = [
            r"<\|.*?\|>",
            r"```.*?system.*?```",
            r"ignore\s+previous",
            r"disregard\s+all",
        ]

        sanitized = content
        for pattern in dangerous_patterns:
            sanitized = re.sub(pattern, "[filtered]", sanitized, flags=re.IGNORECASE | re.DOTALL)

        # 限制长度（防止超大文件）
        max_length = 50000
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length] + "\n\n[truncated]"

        return sanitized

    def clear_all_memory(self):
        """清空所有记忆（仅用于测试和演示）"""
        if self.base_path.exists():
            shutil.rmtree(self.base_path)
            self.base_path.mkdir(parents=True, exist_ok=True)
            logger.warning("所有记忆已清空")
