# EchoMind 前端 React 重构设计

## 目标

将 `CompanyAgentFrontend` 从单文件 Vue 应用重构为 React 19 + TypeScript + Vite 应用，同时不改变 Python、Java、.NET 三个服务端的 HTTP 契约。新界面定位为 **EchoMind Support Command Center**：客户在一个低认知负担的对话空间中获得帮助；管理员在一个可观测、可操作的控制台中管理知识和服务连接。

## 设计原则

- **渐进披露**：客户默认只看到对话需要的信息；管理能力、后端选择与诊断信息只在受控的管理员工作区出现。
- **状态可解释**：请求中的加载、失败、服务健康度、当前后端与知识库搜索结果都必须有可见状态。
- **契约优先**：由 `api/backend.ts` 统一适配 snake_case 与 camelCase，组件不可直接拼接 API 请求。
- **本地优先且不泄露凭证**：配置存储在 `localStorage`；管理员会话按后端放在 `sessionStorage`；会话记录仅在浏览器当前会话保存。
- **无障碍与响应式**：所有图标按钮带名称，键盘可完成核心流程，布局在窄屏退化为单栏而不是缩放桌面 UI。

## 信息架构

```text
AppShell
├── SupportWorkspace       客户对话、建议卡、会话历史
├── GuideWorkspace         面向客户的产品与技术指南
├── AdminWorkspace         服务健康、设置、知识库、诊断输出
└── LoginDialog            管理员登录

状态 / 领域层
├── api/backend.ts         多后端 HTTP 适配与响应归一化
├── hooks/useConversations.ts  会话读写、选择与追加
├── hooks/useAdminSession.ts   会话隔离、跨后端登录、过期处理
└── content/customerDocuments.ts 静态指南内容及类型
```

## 视觉系统

- **色彩**：午夜蓝黑作为背景；靛蓝/青绿渐变表达可操作与系统健康；暖珊瑚仅用于高注意状态。
- **排版**：`Manrope` 用于界面文字，`Fraunces` 用于品牌和关键标题；系统字体为回退，避免字体加载失败影响体验。
- **组件语言**：玻璃质感面板、1px 半透明描边、16–24px 圆角、数据卡使用紧凑的标签/数值/说明三层结构。
- **动效**：只在 `prefers-reduced-motion: no-preference` 时启用入场与呼吸状态，避免装饰性动效影响阅读。

## 前端边界与数据流

```text
用户操作 → Workspace / Dialog → App 状态编排 → api/backend.ts
                                         ↓
sessionStorage/localStorage ← hook 持久化 ← HTTP 响应归一化
```

所有外部响应在领域层规范化；UI 只使用 `ChatResponse`、`BackendSettings` 等类型。错误不会向客户泄露服务端原文，而是转为下一步明确的界面提示；管理员仍可看到诊断结果。

## 交付切片与提交计划

| 切片 | 范围 | 是否独立提交 | 建议分支 | 建议提交信息 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | 本设计文档与迁移边界 | 是 | `docs/frontend-react-architecture` | `docs(frontend): define React migration architecture` | 完成，待提交 |
| 2 | React/TypeScript/Vite 基座与领域类型、API 适配 | 是 | `feat/react-foundation` | `build(frontend): migrate foundation to React and TypeScript` | 进行中 |
| 3 | 会话 Hook、客户对话工作区与响应式视觉系统 | 是 | `feat/support-workspace` | `feat(frontend): add support conversation workspace` | 未开始 |
| 4 | 指南工作区、管理员登录与运营控制台 | 是 | `feat/admin-command-center` | `feat(frontend): add guide and admin command center` | 未开始 |
| 5 | 构建验证、README 更新与迁移清理 | 是 | `chore/frontend-verification` | `docs(frontend): document React runtime and verification` | 未开始 |

### 分支隔离规则

切片 2 只拥有构建配置、类型和 `api/`；切片 3 只拥有 `hooks/`、`components/support/` 和主题样式；切片 4 只拥有 `components/admin/`、`components/guides/`；切片 5 不修改运行时代码。这样避免多个提交反复争抢同一个巨型根组件，也让每个分支可以干净地从上一切片 rebase 或 cherry-pick。

> 当前工作树会按以上切片实现；在每个切片完成时，本表会更新验证结论与建议提交内容。没有发现用户未提交的工作树改动，因此可以安全地创建这些原子提交。

## 验证准则

1. `npm run build` 完成 TypeScript 检查与生产构建。
2. 聊天请求、会话保存/切换/删除、新建会话和建议卡流程可用。
3. 管理员可切换后端、登录、刷新状态、搜索和导入知识。
4. 375px、768px、1440px 视口都不出现横向溢出；键盘可触达主要控件。
