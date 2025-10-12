# Claude Code 用户偏好设置

本文件记录用户的偏好和习惯，Claude Code 应在所有对话中遵循这些偏好。

---

## Python 代码运行方式

**重要**: 始终使用 `uvx --with-requirements requirements.txt python -m` 来运行 Python 代码。

### 标准命令格式

```bash
uvx --with-requirements requirements.txt python -m <module_name>
```

### 常见用例

#### 运行测试
```bash
uvx --with-requirements requirements.txt python -m pytest <test_file>
```

#### 运行主程序
```bash
uvx --with-requirements requirements.txt python -m src.listener
```

#### 运行脚本
```bash
uvx --with-requirements requirements.txt python -m <script_module>
```

#### 运行单个 Python 文件
```bash
uvx --with-requirements requirements.txt python <script.py>
```

### 说明

- **不要使用**: `python -m` 或 `pip install` 单独运行
- **原因**: 项目使用 `uvx` 来自动管理依赖环境，确保所有依赖从 `requirements.txt` 正确加载
- **优势**:
  - 无需手动创建和激活虚拟环境
  - 自动同步依赖
  - 环境隔离

---

## 其他偏好

### 代码风格
- 遵循项目现有的代码风格
- 使用中文注释和日志（与项目保持一致）

### 文档语言
- 技术文档优先使用中文
- 代码注释使用中文

---

**最后更新**: 2025-10-12
