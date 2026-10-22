# Support Workspace

React 19 + TypeScript + Vite 客服前端，兼容 Python、Java、.NET 三个后端。包含聊天、会话历史、客户指南和管理员运营工作区，支持明暗主题、快捷导航、响应式布局和安全 Markdown。

## 本地运行

使用 Node.js 24 LTS（与 Docker / CI 一致），在本目录执行：

```powershell
npm ci
npm run dev
```

默认访问 http://localhost:5173。无后端时仍可浏览首页、指南和设置；聊天或登录请求会显示实际连接错误，不会填入模拟业务数据。

## API 配置

| 服务 | 浏览器默认路径 | Vite 代理目标 |
| --- | --- | --- |
| Python | /api/python | http://localhost:8000 |
| Java | /api/java | http://localhost:8080 |
| .NET | /api/dotnet | http://localhost:8090 |

可在管理员工作区修改本浏览器的地址；未登录时也可展开连接修复区。地址只允许 HTTP(S) 或同源绝对路径。修改地址需要重新登录；已有聊天继续使用其创建时的端点。

VITE_PYTHON_API_URL、VITE_JAVA_API_URL、VITE_DOTNET_API_URL 同时用于 Vite 默认客户端地址与开发代理目标，传入绝对 URL 时浏览器可能直连并需要后端 CORS。它们是公开的构建配置，不可放入密钥。开发时优先使用默认同源路径和代理。

生产沿用现有 Dockerfile 和 docker/nginx.conf，三个 /api/* 前缀分别反代对应服务。无需修改后端 API。

## 常用命令

```powershell
npm run typecheck     # TypeScript strict
npm test              # API / 会话 / 认证 / 请求竞态
npm run build         # 类型检查 + 生产构建
npm run format:check  # 格式检查
npx playwright install chromium
npm run test:e2e      # Mock API 浏览器流程、布局、axe 检查与截图
```

如希望使用系统 Chrome：

```powershell
$env:PLAYWRIGHT_CHANNEL = 'chrome'
npm run test:e2e
```

Playwright 默认自动启动本地 Vite，使用隔离的浏览器上下文和模拟 API，不写入真实知识库。截图位于 test-results/visual，失败 trace 与 HTML 报告分别位于 test-results 和 playwright-report，均已忽略。

## 目录

```text
src/
  api/          # 三后端 HTTP 适配与边界校验
  components/
    layout/     # 导航、快捷面板、历史
    support/    # 对话、建议、Markdown
    guides/     # 指南、章节、搜索
    admin/      # 登录、运营、知识、设置
    ui/         # 品牌、对话框、错误边界
  hooks/        # 会话、认证、请求生命周期、URL 工作区
  lib/          # 存储与会话纯函数
  content/      # 类型化指南
  types.ts      # 领域契约
  theme.css     # 主题与基础视觉
  workspaces.css
  App.tsx
  main.tsx
```

## 行为说明

- Ctrl/Cmd+K 快捷导航；Enter 发送，Shift+Enter 换行。
- 设置和主题存在 localStorage；会话和 token 存在当前 tab 的 sessionStorage。
- 会话删除只删除浏览器历史；停止回复仅取消浏览器等待。
- token 按后端与具体端点隔离；登出清除全部本地管理员会话。
- 知识上传支持非空 TXT/MD/JSON，最大 5 MB。
- 运营缺失值显示破折号；不捏造健康数据或指标曲线。
- 字体加载使用 Google Fonts，失败时回退系统字体；离线部署可以自托管。

## 设计与交付文档

- [整体架构、设计细节与取舍](../docs/FRONTEND_REACT_REFACTOR.md)

React 迁移和员工入口已合入 main。业务升级在独立 feature 分支按功能提交；合入 main 将进入现有部署流程。

## 本地业务HTTP联调

`npm run test:e2e`继续运行原13条mock接口浏览器测试。`npm run test:integration:business`另起本地5174/8873服务，让真实页面调用业务router/workflow与临时SQLite；模型使用固定原句表，不调用API模型或生产后端。

启动前让BUSINESS_E2E_PYTHON指向Python3.12（可复用原仓库venv）；依赖见 `../CompanyAgent/requirements-browser-tests.txt`，与已有生产HTTP/schema直接pin一致。测试拒绝复用已有服务器，端口冲突时先核对所有者，不清理用户其他进程。报告在playwright-integration-report/，截图/trace在test-results/integration/；均不提交。

格式检查覆盖integration目录，TypeScript配置同时检查独立Playwright配置与测试。生产启动、真实管理员认证、模型质量和容器应分别验收。

## 生产镜像验收

`npm run test:acceptance:production`使用实际Nginx构建产物、`api.main`完整生命周期、Redis、Chroma和SQLite。仅模型由独立HTTP替身代替；不加载开发者env、不请求付费API。测试覆盖真实员工登录/审批、UID1000写卷、重建容器后原ID重放、关闭业务能力保留普通问答，再启用后保留原数据。

先在仓库根目录构建并启动独立测试项目：

~~~powershell
# Build the real production targets; these commands do not push images or deploy.
docker build --target production -t companyagent-closeout-backend:local CompanyAgent
docker build -t companyagent-closeout-frontend:local CompanyAgentFrontend
# The Compose file uses synthetic credentials and a dedicated named volume.
# Port 18874 is loopback-only; do not reuse an occupied port or a production project.
docker compose -p companyagent-closeout-local -f CompanyAgent/acceptance/docker-compose.yml up -d
Set-Location -LiteralPath CompanyAgentFrontend
# The test checks this project's Docker labels and port before sending requests.
$env:CLOSEOUT_COMPOSE_PROJECT = 'companyagent-closeout-local'
npm.cmd run test:acceptance:production
~~~

格式与类型检查同时覆盖`acceptance/`和对应配置。测试重建API后显式reload原Nginx配置，重新解析容器地址；这不代表修改了生产代理策略。报告/trace保存在忽略的`playwright-acceptance-report/`与`test-results/acceptance/`。测试结束只清理上述项目的容器和合成卷，保留原开发/部署项目；CI独立job会自动完成清理。真实模型语言质量由单独、有预算的捕获程序验收，不把HTTP替身分数当模型成绩。
