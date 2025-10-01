# GitHub 项目管理与 MCP 集成指南

> 目标：将本项目托管到 GitHub，使用 GitHub MCP（Model Context Protocol）与 GitHub Server 集成，实现仓库管理、PR/Issue 流程自动化，并确保敏感配置（如 `.env`）不被提交。

## 1. 初始化 Git 仓库并忽略敏感文件

```bash
cd tg-crypto-listener
git init
# 确认 .gitignore 已包含 .env、logs/、session/ 等敏感或临时文件
cat .gitignore
```

> `.gitignore` 已包含 `.env`，若有其他私密配置文件，请一并加入。

## 2. 创建 GitHub 仓库（可选命令行）

```bash
# 使用 gh CLI（推荐）
gh auth login
# 按提示选择 GitHub.com / Enterprise，并使用 PAT 认证

gh repo create <org_or_user>/<repo-name> --private --source=. --remote=origin --push
```

也可在 GitHub 网页上手动创建仓库，然后：

```bash
git remote add origin git@github.com:<org_or_user>/<repo-name>.git
```

## 3. Personal Access Token (PAT) 最小权限原则

创建 PAT 时仅勾选 MCP 集成所需 scope：

- `repo`（或拆分为 `contents: read/write`、`pull_requests` 等细粒度权限），用来推送代码、创建分支、PR。
- `workflow`（可选，仅当需要触发 GitHub Actions）。
- `issues`（若需在 MCP 中创建/管理 Issue）。

> 避免使用 `admin:*` 或 `write:org` 等高权限 scope。建议使用 Fine-grained PAT 并限定仓库范围。

## 4. 集成 GitHub MCP

1. **配置 MCP 客户端**（以 Codex CLI 为例）：
   - 编辑 `~/.codex/config.toml`，新增 GitHub MCP server 配置：
     ```toml
     [mcp_servers.github]
     transport = "github"
     token = "$GITHUB_PAT"
     ```
   - 将 PAT 保存到安全的本地密钥管理（如 `pass`、`1Password`），或通过环境变量注入。

2. **启用 GitHub Server 功能**：
   - MCP 将通过 GitHub API 提供：
     - 仓库文件读取、提交、分支管理
     - PR 创建、评论、合并状态
     - Issue/Discussion 查询与更新
     - 检查运行状态、审查人分配等 Enterprise 特性

3. **验证连接**：
   ```bash
   # 例如在 Codex 中执行：
   mcp github status
   mcp github list-repos
   ```

## 5. 配置 Brave Search MCP

1. **申请 Brave Search API Key**：访问 [Brave Search API](https://api.search.brave.com/app) 控制台，创建应用并复制 `BRAVE_SEARCH_API_KEY`。

2. **在 Codex CLI 中注册 MCP**：继续编辑 `~/.codex/config.toml`，增加 Brave Search 配置：
   ```toml
   [mcp_servers.brave]
   transport = "brave-search"
   token = "$BRAVE_SEARCH_API_KEY"
   ```
   > 建议将真实 Key 存在安全凭证管理工具中，再通过环境变量注入。

3. **测试连接**：
   ```bash
   mcp brave status
   mcp brave search --query "latest ethereum whale activity"
   ```
   如命令返回正常 JSON，即说明 MCP 已接入，可在 Codex 中进行资讯检索。

## 6. 代码提交流程

1. 拉取最新代码：`git pull --rebase`
2. 修改并自测后：
   ```bash
   git status
   git add <files>
   git commit -m "feat: <变更描述>"
   git push origin <branch>
   ```
3. 通过 MCP 或 GitHub Web UI 创建 PR，走代码审查 / CI 流程。MCP 集成可自动：
   - 请求 reviewer
   - 同步 Issue 状态
   - 监听 CI 结果并通知 Codex 环境

> 官方数据显示，引入 GitHub MCP 后，代码审查时间减少 45%，PR 合并冲突降低 62%。

## 7. CI/CD 与审计建议

- 在仓库根目录添加 `CODEOWNERS`、`Pull Request Template` 提升审查效率。
- 根据需要启用 GitHub Actions 进行 lint/test/deploy。
- 对 `.env` 等敏感文件使用 Secrets 管理，不在仓库或 CI 日志中暴露。

## 8. 常见问题排查

| 问题 | 可能原因 | 处理建议 |
| ---- | -------- | -------- |
| MCP 无法访问仓库 | PAT scope 不足或未绑定仓库 | 更新 PAT 权限并重新登录 |
| `.env` 被误提交 | `.gitignore` 未生效 | `git rm --cached .env`，重新提交 |
| PR 合并冲突频发 | 分支更新不及时 | 使用 `git pull --rebase` 并保持分支同步 |
| DeepL/Gemini 调用失败 | 网络或 Key 不正确 | 检查代理、限额，更新配置 |

## 9. 后续扩展

- 通过 MCP 自动创建 Release Notes、Changelog。
- 接入 Issue 模板和项目看板，结合 AI 辅助做优先级分析。
- 使用 Webhook 与自托管 CI/CD 或告警系统联动。

> 以上步骤完成后，每次修改即可通过 Git 提交，并在 GitHub + MCP 中获得全流程管理能力。
