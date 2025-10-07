#!/usr/bin/env python3
"""测试 context_management 参数修复"""

import sys
import ast


def test_context_management_parameter():
    """检查 anthropic_client.py 中是否正确传递 context_management 参数"""

    with open('src/ai/anthropic_client.py', 'r') as f:
        content = f.read()

    # 解析 AST
    tree = ast.parse(content)

    found_context_management = False
    found_betas = False

    # 查找 messages.create 调用
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # 检查是否是 messages.create 调用
            if (isinstance(node.func, ast.Attribute) and
                node.func.attr == 'create'):

                # 检查参数
                for keyword in node.keywords:
                    if keyword.arg == 'context_management':
                        found_context_management = True
                        print(f"✅ 找到 context_management 参数")
                    if keyword.arg == 'betas':
                        found_betas = True
                        print(f"✅ 找到 betas 参数")

    if found_context_management and found_betas:
        print("\n✅ 所有必需参数都已正确配置")
        return True
    else:
        print(f"\n❌ 缺少参数:")
        if not found_context_management:
            print("  - context_management")
        if not found_betas:
            print("  - betas")
        return False


if __name__ == '__main__':
    success = test_context_management_parameter()
    sys.exit(0 if success else 1)
