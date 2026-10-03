# 部署踩坑记录

第一次按 `DEPLOYMENT_RUNBOOK.md` 把 CompanyAgent 部署到 Azure VM、接入 CI/CD 与可观测性、并在本地 kind 上跑通 K8s 的过程中遇到的问题。
每条都写了**现象 → 原因 → 处理**；标了 commit 的已经在代码里修掉，其余已写进 runbook / README 对应步骤。

## 速查

| # | 阶段 | 现象（关键词） | 状态 |
|---|---|---|---|
| 1 | 代码 | 管理员密码写死在代码里 | 已修复 |
| 2 | 代码 | 新一代 Claude 模型返回 400 / 回答为空 | 已修复 `b76d5ca` |
| 3 | 2.2 | `ssh-keyscan` 没有输出 | 文档已改 |
| 4 | 附录 A | `az login` 显示 No subscriptions found | 文档已改 |
| 5 | 附录 A | `AADSTS700213: No matching federated identity record` | 文档已改 |
| 6 | 4 | `az network nsg rule create` 报 `_DeadlockError` | 已修复 `b815f63` |
| 7 | 4 | 部署 SSH `Connection timed out` | 文档已改，workflow 会提示 `b815f63` |
| 8 | 5 | `/chat` 返回 422 | 文档已改 |
| 9 | 6 | Eval 显示 `0 tokens`，所有分数 0.500 | 文档已改 |
| 10 | 7 | 手动 `up` 报 `failed to resolve reference ... not found` | 已修复 `14e1d33` |
| 11 | 7 | Jaeger 一直 `Restarting` | 已修复 `397dd76` |
| 12 | 8 | 回滚演练时没有触发回滚 | 已修复 `cc3bbf1` |
| 13 | 9 | `terraform plan` 好几分钟没有输出 | 文档已改 |
| 14 | 9 | 查到的公网 IP 是 IPv6 | 文档已改 |
| 15 | 10 | `kind load` 报 `short read` / `content digest not found` | 文档已改 |
| 16 | 10 | Pod 一直 `ContainerCreating` | 文档已改 |
| 17 | 10 | Ingress 报 `failed calling webhook ... connection refused` | 文档已改 |
| 18 | 10 | `kubectl` 崩溃：`runtime: cannot allocate memory` | 文档已改 |

---

## 代码

### 1. 管理员密码写死在代码里

- **现象**：提交前扫描发现 Python、Java 的 `ADMIN_PASSWORD` 都有一个硬编码的默认值，一旦提交就会永久留在 git 历史里。
- **处理**：Python / Java 改成和 .NET 一致：没有默认值，未配置 `ADMIN_PASSWORD` 时拒绝管理员登录；`.env.example` 加上这一项。去掉默认值的版本直接放进首次提交，而不是事后再删——事后删除，密码仍然留在历史里。
- **教训**：提交前用 `git grep --cached` 扫一遍密钥和密码；"先提交再删"挡不住历史泄露。

### 2. 新一代 Claude 模型返回 400 / 回答为空（`b76d5ca`）

- **现象**：模型换成 `claude-sonnet-5-5` 后，Python 和 Java 后端的调用报 400；Java 偶尔返回空字符串。
- **原因**：
  - 新一代模型拒绝自定义 `temperature`，而代码每次都带；
  - 新模型会先思考，思考 token 计入 `max_tokens`，256 的预算可能把 JSON 答案截断；
  - Spring AI 把 thinking 块也映射成一条 `Generation`，代码取第一条，拿到的是空的思考内容。
- **处理**：Python 新增 `claude_request_kwargs()` 按模型代际生成参数（去掉 `temperature`、`max_tokens` 下限 4096、设置 effort）；Java 对新模型发 API 默认值 `temperature=1.0`（Spring AI 不发 null，会回落到 0.8），并只拼接正文 Generation。DeepSeek 和旧模型的请求参数不变。

## 阶段 2：配置 GitHub

### 3. `ssh-keyscan` 没有输出

- **现象**：在 Windows 上执行 `ssh-keyscan <host>`，有时只有几行 `#` 注释，有时完全没有输出，`AZURE_VM_KNOWN_HOSTS` 复制到的是空内容。
- **原因**：关键在 `choose_kex: unsupported KEX method sntrup761x25519-sha512@openssh.com`。Windows 自带的 `ssh-keyscan` 版本较旧，会提出一个自己并不支持的密钥交换算法，服务器（OpenSSH 9.6）恰好选中它，握手失败。那几行 `#` 是 stderr 的注释，不是公钥。
- **处理**：让服务器自己扫，再把 `localhost` 换成域名（见 runbook 2.2）。GitHub runner 是 Ubuntu，不受影响。

## 附录 A：OIDC 临时放行 SSH

### 4. `az login` 显示 No subscriptions found

- **原因**：租户要求 MFA，`az login` 默认登录所有租户，在这个租户上因 MFA 被拒，于是一个订阅都没拿到。
- **处理**：`az login --tenant <租户ID>`，按提示完成 MFA；窗口异常时加 `--use-device-code`。

### 5. `AADSTS700213: No matching federated identity record`

- **现象**：workflow 的 `Azure login (OIDC)` 步骤失败。
- **原因**：联合凭据登记的 subject 是 `repo:<owner>/<repo>:environment:production`，而 GitHub 实际发来的是带用户 ID 和仓库 ID 的新格式 `repo:<owner>@<ownerId>/<repo>@<repoId>:environment:production`。Azure 要求逐字一致。
- **处理**：从该步骤日志的 `subject claim` 行复制真实值，`az ad app federated-credential update` 更新。runbook 附录 A 已改为以日志为准。

### 6. `az network nsg rule create` 报 `_DeadlockError`（`b815f63`）

- **原因**：runner 上的 Azure CLI 跑在 Python 3.14，长操作的后台轮询线程与主线程第一次 `import requests` 争用模块锁，偶发死锁。是 Azure CLI 自身的问题，和配置无关。
- **处理**：创建规则前先执行一次同步的 `az network nsg show`，让依赖在主线程加载完；创建和删除各重试 3 次。

### 7. 部署 SSH `Connection timed out`

- **原因**：资源组里有两个 NSG，`AZURE_NSG_NAME` 填了名字更像的那个，但它没有挂在任何网卡或子网上；临时放行规则加到了不生效的 NSG 上。
- **排查**：`az vm show` → 网卡 ID → `az network nic show --query networkSecurityGroup.id`，再查子网有没有 NSG、VM 上 `ufw` 是否开启。
- **处理**：`AZURE_NSG_NAME` 改为网卡上实际挂的 NSG。workflow 也改为规则创建后等待 22 端口可达（最多 2 分钟），超时时提示检查 NSG，不再让 rsync 直接超时。
- **教训**：Azure 门户创建 VM 时可能多生成资源，"名字像"不等于"在生效"，要从 VM 反查。

## 阶段 5–7：验证与运维

### 8. `/chat` 返回 422

- **原因**：`-d '{\"message\":...}'` 这种转义只在 Windows PowerShell 5.1 有效；PowerShell 7.3+ 会把 `\"` 原样传给 `curl.exe`，服务器收到的不是合法 JSON。
- **处理**：先把 JSON 写进文件，再 `curl.exe --data-binary "@body.json"`，与 PowerShell 版本无关。
- **顺带**：响应头里有 `X-Trace-Id`，说明请求已经过 Caddy 到达 FastAPI，服务本身是好的——先看链路是否通，再看请求体。

### 9. Eval 显示 `0 tokens`，所有分数都是 0.500

- **现象**：`46 LLM calls · 0 tokens`，judge 失败率 100%，四项质量分都正好是 0.500。
- **原因**：0.500 是 judge 调用失败时的默认分，调用次数统计的是"发起"，token 只统计成功返回，所以是全部失败。日志里的报错 `Authentication Fails, Your api key: ****xxxx is invalid` 是 **DeepSeek** 的报错格式（Anthropic 的是 `invalid x-api-key`），key 末 4 位与 Claude key 一致——Claude key 被发到了 DeepSeek：GitHub Variable `ANTHROPIC_BASE_URL` 还是 DeepSeek 的地址。
- **处理**：`ANTHROPIC_BASE_URL` 改为 `https://api.anthropic.com`（或删除）。
- **教训**：key、base URL、模型名三者必须属于同一家；GitHub 上的值要和服务器 `.env` 一致，否则评测测的不是线上模型。

### 10. 手动 `up` 报 `failed to resolve reference ... not found`（`14e1d33`）

- **原因**：镜像地址 `${IMAGE_REGISTRY}/...:${IMAGE_TAG}` 只在 CI 部署时临时 `export`，服务器上手动 `up` 时没有这两个值（或 runbook 里写了过期的镜像仓库地址），拉取不存在的镜像，执行到一半失败，部分容器已被停掉。
- **处理**：部署和回滚成功后，workflow 把 `IMAGE_REGISTRY` / `IMAGE_TAG` 写进服务器的 `deploy/.env.production`，手动 `up --no-build` 直接使用当前线上版本，不再需要 `export`。

### 11. Jaeger 一直 `Restarting`（`397dd76`）

- **现象**：开启监控栈后 Jaeger 反复重启，日志 `mkdir /badger/key: permission denied`。
- **原因**：Jaeger 镜像以 UID 10001 运行；镜像里没有 `/badger` 目录，所以新建的命名 volume 是属于 root 的空目录（镜像里有该目录时 Docker 才会连同权限复制进 volume，Grafana / Prometheus 因此没出问题）。本地开发用内存存储不挂 volume，这段生产配置在阶段 7 之前从没真正运行过。
- **处理**：新增一次性容器 `jaeger-init`，启动前 `chown -R 10001:10001 /badger`，Jaeger `depends_on` 它成功完成。

## 阶段 8：回滚演练

### 12. 回滚演练时没有触发回滚（`cc3bbf1`）

- **现象（演练前分析发现）**：让 `/health` 返回 500 后，Python 后端被判定 unhealthy；Caddy `depends_on` 它 `service_healthy`，`up` 直接报 `dependency failed to start`，`Pull images and restart` 失败，冒烟测试被跳过。而回滚条件只写了 `steps.smoke.outcome == 'failure'`，于是不会回滚，线上停在坏版本。
- **处理**：重启步骤加 `id: restart`，回滚条件改为重启或冒烟测试任一失败。演练结果：`Pull images and restart` 失败 → `Roll back to previous release` 成功 → 线上回到上一版本。
- **教训**：回滚只覆盖"冒烟测试不过"是不够的，"新版本根本起不来"更常见；回滚逻辑要靠故障注入真正跑一次才算验证过。

## 阶段 9：Terraform

### 13. `terraform plan` 好几分钟没有输出

- **原因**：没有数据源、也没有 state 时，plan 要算完才一次性打印；azurerm 4.x 首次连接订阅时还会自动注册一批资源提供程序，这一步没有任何输出。期间目录里出现的 0 字节 `terraform.tfstate` 和 `.terraform.tfstate.lock.info` 是 plan 的 state 锁，不代表创建过资源。
- **处理**：等待（首次可能 5–10 分钟）；仍无输出时 `$env:TF_LOG="INFO"` 重跑看卡在哪；中断后锁未释放用 `terraform force-unlock <ID>`。
- **相关**：plan 结尾 "You didn't use the -out option" 只是提示；要保证执行的就是审查过的计划，用 `plan -out tfplan` + `apply tfplan`。`dns_label` 填现有值只适合 plan / 导入，真正 apply 新环境要换名字，同区域内不能重复。

### 14. 查到的公网 IP 是 IPv6

- **原因**：`curl https://ifconfig.me` 走了 IPv6；但 VM 只有 IPv4 公网地址，SSH 时 NSG 看到的是 IPv4 来源，填 IPv6 的规则永远匹配不上。
- **处理**：`curl.exe -4 -s https://ifconfig.me`，或 `curl.exe -s https://api.ipify.org`。

## 阶段 10：本地 Kubernetes（kind）

### 15. `kind load` 报 `short read` / `content digest ... not found`

- **原因**：Docker Desktop 开启了 containerd 镜像存储。`kind load` 内部用 `docker save` 导出、再 `ctr import --all-platforms` 导入：
  - 多个镜像一起导出时管道中途断开 → `short read`；
  - 从 Docker Hub 拉的多平台镜像（如 `redis:7-alpine`）本地只有 amd64 的层，但导出的清单引用了其他平台 → `content digest not found`。
- **处理**：镜像逐个加载；多平台镜像用一行 Dockerfile 重建成单平台、同名覆盖后再加载（`"FROM redis:7-alpine" | docker build -t redis:7-alpine -`）。较新的 Docker 可以直接 `docker save --platform linux/amd64` + `kind load image-archive`；也可以关闭 containerd 镜像存储（之后要重新构建镜像）。

### 16. Pod 一直 `ContainerCreating`

- **原因**：卡住的都是要从外网拉镜像的 Pod（Redis、Chroma、ingress-nginx）；用 `kind load` 预加载的自建镜像都正常 Running。kind 节点在自己的容器里拉镜像，不走 Docker Desktop 的缓存，也不显示进度，慢或卡住时看起来像死了。`describe pod` 的 Events 停在 `Pulling image` 即可确认。
- **处理**：在本机 `docker pull` 后按第 15 条加载进节点。

### 17. Ingress 报 `failed calling webhook "validate.nginx.ingress.kubernetes.io" ... connection refused`

- **原因**：ingress-nginx 控制器还没就绪（本次是镜像拉取卡住，见第 16 条），创建 Ingress 时的校验 webhook 无人响应。
- **处理**：先 `kubectl -n ingress-nginx wait --for=condition=ready pod -l app.kubernetes.io/component=controller` 再 apply；重复 apply 是安全的。Ingress 不影响应用本身，`port-forward` 可以直接访问前端。

### 18. `kubectl` 崩溃：`runtime: cannot allocate memory`

- **原因**：不是集群的问题，是 Windows 的**可提交内存**耗尽（当时 93 GB 上限只剩 0.1 GB）——物理内存虽有空闲，但新进程申请不到内存，`kubectl` / `docker` 启动即崩或卡住，也拖慢了节点内的镜像拉取。
- **处理**：关掉不用的程序、用完 `kind delete cluster`；仍不行就重启电脑（集群在 Docker 里，重启 Docker Desktop 后会恢复）。
- **相关**：`.NET` 后端没有 K8s 清单，前端切到 .NET 在 kind 里会失败，属预期。
