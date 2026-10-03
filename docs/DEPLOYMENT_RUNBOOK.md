# CompanyAgent 部署手册：从本地到自动化上线

按顺序做，每一步都有**验证方法**，验证通过再做下一步。

| 阶段 | 内容 | 预计时间 | 线上会中断吗 |
|---|---|---|---|
| 0 | 本地收尾（目录改名、本地验证） | 20 分钟 | 否 |
| 1 | 建 GitHub 私有仓库并推送 | 15 分钟 | 否 |
| 2 | 配置 GitHub 密钥和变量 | 20 分钟 | 否 |
| 3 | 服务器一次性迁移（echomind → companyagent） | 15 分钟 | **是**，从这一步开始 |
| 4 | 第一次自动部署 | 10 分钟 | 是，到这一步结束 |
| 5 | 验证线上 | 5 分钟 | 否 |
| 6 | 建立 Eval 基线 | 10 分钟 | 否 |
| 7–10 | 可选：监控栈、回滚演练、Terraform、K8s | — | 否 |

**阶段 3 和 4 要连着做**，线上中断大约 15–25 分钟，建议挑没人用的时间。

下文的约定：
- `PS>` 表示在你 Windows 电脑的 **PowerShell** 里执行
- `vm$` 表示在 **Azure VM** 的 SSH 会话里执行
- `<你的GitHub用户名>` 这类尖括号内容需要替换成你自己的值

---

## 阶段 0：本地收尾

### 0.1 改完剩下的 3 个目录名

代码里的名字都已经改成 CompanyAgent，但 VS Code 占用着 3 个目录，所以目录本身没改成功。

1. **完全关闭 VS Code**（所有窗口），以及其他可能打开了这个项目的程序
2. 执行：

```powershell
PS> cd 'C:\Users\szllw\Desktop\EchoMind所有代码+简历4___\EchoMind所有代码+简历'
PS> Rename-Item EchoMind CompanyAgent
PS> Rename-Item EchoMindJava CompanyAgentJava
PS> Rename-Item EchoMindFrontend CompanyAgentFrontend
PS> Get-ChildItem -Directory | Select-Object Name
```

**验证：** 列出的目录应该是 `CompanyAgent`、`CompanyAgentDotnet`、`CompanyAgentFrontend`、`CompanyAgentJava`、`deploy`、`docs`、`infra`，以及隐藏的 `.github`。

> 如果提示"拒绝访问"：说明还有程序占用这个目录，任务管理器里结束 `Code.exe` 和 `java.exe` 后重试。
>
> （可选）最外面两层文件夹名 `EchoMind所有代码+简历4___` 和 `EchoMind所有代码+简历` 也可以改，但改了之后本文档里的路径要相应替换。

### 0.2 换一个有效的 LLM API Key

本地 `CompanyAgent\.env` 里的 DeepSeek key 已经失效（调用返回 401）。现在改用 Claude 官方 API。

1. 在 Claude Console（console.anthropic.com）生成一个 API key
2. 用记事本打开 `CompanyAgent\.env`，把这三行改成：
   ```env
   ANTHROPIC_API_KEY=sk-ant-...
   ANTHROPIC_BASE_URL=https://api.anthropic.com
   ANTHROPIC_MODEL=claude-sonnet-5-5
   ```
   服务器上的 `~/companyagent/CompanyAgent/.env` 也要同样改：线上容器读的是那个文件，GitHub 里的 secret 不会同步到服务器
3. 在同一个文件里检查有没有以 `ECHOMIND_` 开头的变量。有的话改成 `COMPANYAGENT_` 开头。代码现在只认新的变量名

### 0.3 本地跑一遍（Docker Desktop 要先启动）

```powershell
PS> cd .\CompanyAgent
PS> docker compose up -d --build
PS> Start-Sleep 30; curl.exe -s http://localhost:8000/health
```

**验证：**
- `health` 返回 `{"status":"ok",...}`
- 打开 http://localhost:8000/docs，调一次 `POST /chat`，响应头里应该有 `x-trace-id`
- 打开 http://localhost:16686（Jaeger），Service 选 `companyagent-api`，能看到刚才那次请求的 trace 树
- 打开 http://localhost:3000（Grafana，账号密码都是 admin），在 Dashboards → CompanyAgent 里能看到看板

验证完关掉：

```powershell
PS> docker compose down
PS> cd ..
```

> 本地还有一套之前的 `deploy` 容器，已经被我停掉了（stop，没有删除）。它用的是旧名字，不用管，也可以执行 `docker compose -p deploy down` 删掉。

---

## 阶段 1：建 GitHub 私有仓库

### 1.1 把 git 仓库提升到根目录

现在有 3 个子目录各自是独立的 git 仓库：
- `CompanyAgent\`：12 次提交，没有远程仓库
- `CompanyAgentJava\`：已经推到 GitHub 上单独的仓库
- `CompanyAgentFrontend\`：已经推到 GitHub 上单独的仓库

如果不处理，git 会把 Java 和前端目录当成"嵌套仓库"，只记录一个指针而不收录代码，结果 CI 会构建失败。做法是：
- 把 `CompanyAgent` 的 `.git` 提升到根目录，保留它的历史
- 另外两个的 `.git` 移出去备份（它们的历史仍然保存在 GitHub 上原来的那两个仓库里）

```powershell
PS> cd 'C:\Users\szllw\Desktop\EchoMind所有代码+简历4___\EchoMind所有代码+简历'
PS> New-Item -ItemType Directory ..\git-backup | Out-Null
PS> Move-Item -Force .\CompanyAgentJava\.git ..\git-backup\CompanyAgentJava.git
PS> Move-Item -Force .\CompanyAgentFrontend\.git ..\git-backup\CompanyAgentFrontend.git
PS> Move-Item -Force .\CompanyAgent\.git .\.git
PS> Get-ChildItem -Recurse -Force -Directory -Filter .git -Depth 2 | Select-Object FullName
```

**验证：** 最后一条命令只能列出**根目录下的那一个** `.git`。

```powershell
PS> git add -A
PS> git status --short | Select-String -Pattern '\.env|\.pem|tfvars|tfstate'
```

**验证（很重要）：** 最后一条命令只能列出 `.env.example`、`.env.production.example` 这类示例文件。
**如果出现了真正的 `.env`、`.pem`、`terraform.tfvars`，停下来，不要提交**，先把它加进根目录的 `.gitignore`，再执行 `git reset` 后重新 `git add -A`。

```powershell
PS> git -c user.name="<你的名字>" -c user.email="<你的邮箱>" commit -m "Rename to CompanyAgent; add CI/CD, observability, eval gate, Terraform and K8s"
```

> 这次提交里也会包含你之前没提交的改动，比如删除的 wiki 和面经文件。如果其中有你想保留在仓库里的，先执行 `git restore --staged <文件>`，再提交。
>
> GitHub 上原来的 `EchoMindJava`、`EchoMindFrontend` 两个仓库如果是**公开**的，里面还是旧名字和旧代码。建议到各自的 Settings 里改成 Private，或者 Archive。

### 1.2 在 GitHub 上建仓库

1. 打开 https://github.com/new
2. Repository name 填 `companyagent`，选 **Private**，**不要**勾选 README、.gitignore 或 license
3. 点 Create repository

### 1.3 推送

```powershell
PS> git branch -M main
PS> git remote add origin https://github.com/Lwindsy/companyagent.git
PS> git push -u origin main
```

第一次推送时会弹出浏览器让你登录 GitHub。

**验证：** 打开仓库的 **Actions** 页面，会看到 `CI` 和 `Deploy` 两个 workflow 在运行：
- `CI`：4 个 job（Python、Terraform、Kubernetes + Compose、4 个镜像构建）全部变绿，大约 8–15 分钟
- `Deploy`：`ci` 和 `push-images` 变绿，`deploy` job 显示 **skipped**。这是正常的，因为还没设置 `AZURE_VM_HOST`，deploy job 不会碰服务器

再打开你 GitHub 主页的 **Packages** 标签，应该能看到 4 个镜像：`companyagent-backend`、`companyagent-java`、`companyagent-dotnet`、`companyagent-frontend`。

> 如果 CI 有红的：点进去看日志。常见原因见文末"排错"。

---

## 阶段 2：配置 GitHub

### 2.1 生成一把部署专用的 SSH key

不要把你现在用的 `zhenlin-companyagent.pem` 交给 GitHub，单独生成一把只给 CI 用的。哪天泄露了，删掉这一把就行，不影响你自己登录。

```powershell
PS> ssh-keygen -t ed25519 -C "github-actions-deploy" -f $HOME\.ssh\companyagent_deploy
```

提示输入 passphrase 时**直接按两次回车**（CI 没法输密码）。

把公钥加到 VM 上：

```powershell
PS> $pem  = "$HOME\Desktop\zhenlin-companyagent.pem"
PS> $vm   = "azureuser@zhenlin-companyagent.belgiumcentral.cloudapp.azure.com"
PS> $pub  = Get-Content "$HOME\.ssh\companyagent_deploy.pub"
PS> ssh -i $pem $vm "echo '$pub' >> ~/.ssh/authorized_keys"
```

**验证：** 用新 key 能登录：

```powershell
PS> ssh -i "$HOME\.ssh\companyagent_deploy" $vm "echo deploy-key-ok"
```

应该输出 `deploy-key-ok`。

### 2.2 添加 Secrets 和 Variables

打开仓库 → **Settings** → **Secrets and variables** → **Actions**。

**Secrets** 标签 → New repository secret，逐个添加：

| Name | 值怎么获得 |
|---|---|
| `AZURE_VM_SSH_KEY` | `PS> Get-Content -Raw "$HOME\.ssh\companyagent_deploy" \| Set-Clipboard`，然后粘贴。注意是**没有 .pub 后缀**的那个文件，内容以 `-----BEGIN OPENSSH PRIVATE KEY-----` 开头 |
| `AZURE_VM_KNOWN_HOSTS` | `PS> ssh-keyscan zhenlin-companyagent.belgiumcentral.cloudapp.azure.com 2>$null \| Set-Clipboard`，然后粘贴 |
| `ANTHROPIC_API_KEY` | 阶段 0.2 里的 Claude API key（只给 CI 评测用，每次评测会产生少量费用） |

**Variables** 标签 → New repository variable：

| Name | Value |
|---|---|
| `AZURE_VM_USER` | `azureuser` |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` |
| `LLM_PRICING_JSON` | （可选）单位是美元/百万 token：`{"claude-sonnet-5-5": [2, 10]}`。不填的话成本面板显示 0 |

> ⚠️ **先不要添加 `AZURE_VM_HOST`**，它是部署的开关，阶段 4 再加。

### 2.3 确认 VM 的 SSH 端口 GitHub 能连上

GitHub 的 runner 每次的 IP 都不一样。打开 Azure Portal → 你的 VM → **Networking**（网络设置），找到端口 **22** 的入站规则，看 Source（源）：

- **Source 是 Any**：不用改。你的 VM 只允许 key 登录，可以用这条命令确认：

  ```powershell
  PS> ssh -i $pem $vm "sudo sshd -T | grep -i passwordauthentication"
  ```

  输出应该是 `passwordauthentication no`。
- **Source 是你自己的 IP**：GitHub 连不上。二选一：
  - **简单做法：** 把 Source 改成 Any，前提是上面那条命令输出 `no`
  - **规范做法：** 让 workflow 部署时临时放行 runner 的 IP，部署完自动删除。见附录 A

---

## 阶段 3：服务器一次性迁移

服务器上的目录、volume 和配置文件里都还是旧名字，需要一次性迁移过来。**从 3.3 开始线上会中断，到阶段 4 部署完成后恢复。**

### 3.1 登录并查看现状

```powershell
PS> ssh -i "$HOME\Desktop\zhenlin-companyagent.pem" azureuser@zhenlin-companyagent.belgiumcentral.cloudapp.azure.com
```

```bash
vm$ cd ~/echomind
vm$ docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml ps
vm$ docker volume ls | grep deploy_
vm$ df -h / && free -h
vm$ which rsync || sudo apt-get install -y rsync
```

记下 `docker volume ls` 列出的 volume 名，下一步会用到。磁盘剩余空间至少要有 5 GB。

### 3.2 备份（此时线上仍在运行）

```bash
vm$ mkdir -p ~/backup && cd ~
vm$ tar czf ~/backup/echomind-files-$(date +%F).tgz --exclude='*/node_modules' echomind
vm$ for v in deploy_echomind_eval deploy_echomind_java_data deploy_echomind_dotnet_data deploy_redis_data deploy_chromadb_data deploy_caddy_data; do
      docker volume inspect $v >/dev/null 2>&1 || { echo "skip $v (不存在)"; continue; }
      docker run --rm -v $v:/v:ro -v ~/backup:/b alpine tar czf /b/$v.tgz -C /v .
      echo "backed up $v"
    done
vm$ ls -lh ~/backup
```

**验证：** `~/backup` 下有文件备份和每个 volume 的 `.tgz`，大小都不是 0。

### 3.3 停掉旧服务（⚠️ 线上从这里开始中断）

```bash
vm$ cd ~/echomind
vm$ docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml down
```

> 只执行 `down`，**不要加 `-v`**。加了 `-v` 会删除所有数据 volume。

### 3.4 目录改名

```bash
vm$ mv ~/echomind ~/companyagent
vm$ cd ~/companyagent
vm$ for d in EchoMind EchoMindJava EchoMindDotnet EchoMindFrontend; do [ -d "$d" ] && mv "$d" "${d/EchoMind/CompanyAgent}"; done
vm$ ls
```

**验证：** 能看到 `CompanyAgent`、`CompanyAgentJava`、`CompanyAgentDotnet`、`CompanyAgentFrontend`、`deploy`。

### 3.5 更新配置文件

```bash
vm$ grep -n -i echomind CompanyAgent/.env deploy/.env.production
```

如果有以 `ECHOMIND_` 开头的**变量名**，执行下面这条把它们改成新名字（只改变量名，不改值）：

```bash
vm$ sed -i 's/^ECHOMIND_/COMPANYAGENT_/' CompanyAgent/.env deploy/.env.production
```

顺便设置管理员密码。代码里没有默认密码，不设置的话管理后台无法登录：

```bash
vm$ grep -q '^ADMIN_PASSWORD=' CompanyAgent/.env || echo "ADMIN_PASSWORD=$(openssl rand -base64 18)" >> CompanyAgent/.env
vm$ grep '^ADMIN_PASSWORD=' CompanyAgent/.env
```

把输出的密码保存到你的密码管理器里，以后登录管理后台要用它。

### 3.6 迁移数据 volume

新的 compose 文件用的是新 volume 名。下面把旧 volume 的数据复制过去。旧 volume 原样保留，作为第二份备份。

```bash
vm$ for v in eval java_data dotnet_data; do
      old=deploy_echomind_$v; new=deploy_companyagent_$v
      docker volume inspect $old >/dev/null 2>&1 || { echo "skip $old (不存在)"; continue; }
      docker volume create --label com.docker.compose.project=deploy \
                           --label com.docker.compose.volume=companyagent_$v $new >/dev/null
      docker run --rm -v $old:/from:ro -v $new:/to alpine sh -c 'cp -a /from/. /to/'
      echo "$old -> $new: $(docker run --rm -v $new:/v alpine sh -c 'find /v -type f | wc -l') files"
    done
```

**验证：** 每个 volume 都打印出文件数，而且和旧 volume 的文件数一致。可以用 `docker run --rm -v deploy_echomind_java_data:/v alpine find /v -type f | wc -l` 对照旧 volume。

> `redis_data`、`chromadb_data`、`caddy_data` 这几个 volume 名字没变，不用迁移。Caddy 的 HTTPS 证书也在里面，所以不用重新申请证书。

### 3.7 退出 VM

```bash
vm$ exit
```

---

## 阶段 4：第一次自动部署

1. 回到 GitHub 仓库 → Settings → Secrets and variables → Actions → **Variables** → 添加：

   | Name | Value |
   |---|---|
   | `AZURE_VM_HOST` | `zhenlin-companyagent.belgiumcentral.cloudapp.azure.com` |

2. 打开 **Actions** → 左侧选 **Deploy** → 右侧 **Run workflow** → Branch 选 `main` → **Run workflow**

3. 点进正在运行的那次，看 `deploy` job 的日志，依次会经过：
   - `Sync compose files and mounted config`：把 compose 文件、Caddyfile、config、skills 传到 `~/companyagent`
   - `Pull images and restart`：登录 GHCR → 拉取 4 个镜像 → `up -d --no-build`
   - `Smoke test`：输出 `Python API healthy after XXs`

**全部变绿，线上就恢复了。**

> 第一次部署的 `Roll back` 步骤即使执行了也会失败，因为服务器上还没有"上一个版本"的记录。之后的部署都会有。
>
> **如果冒烟测试失败**，SSH 上去看日志：
> ```bash
> vm$ cd ~/companyagent
> vm$ docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml ps
> vm$ docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml logs --tail 100 companyagent
> ```
> 最常见的原因是 `.env` 里缺少变量，或者 3.6 的 volume 没迁移成功。实在不行可以用附录 B 回到改名前的状态。

---

## 阶段 5：验证线上

```powershell
PS> $site = "https://zhenlin-companyagent.belgiumcentral.cloudapp.azure.com"
PS> curl.exe -s "$site/api/python/health"
PS> '{"message":"Where is my order #12345?","user_id":"smoke"}' | Set-Content -Encoding ascii body.json
PS> curl.exe -s -D - -o NUL -X POST "$site/api/python/chat" -H "Content-Type: application/json" --data-binary "@body.json" | Select-String "HTTP/|x-trace-id"
PS> Remove-Item body.json
```

> JSON 先写进文件再交给 curl：直接在命令行里写 `-d '{\"message\":...}'`，PowerShell 7.3 以后会把 `\"` 原样传给 curl，服务器收到的不是合法 JSON，返回 422。

**验证：**
- [ ] health 返回 `"status":"ok"`
- [ ] chat 返回 `HTTP/1.1 200`，并且带 `x-trace-id`
- [ ] 浏览器打开网站，前端正常，分别切到 Python、Java、.NET 后端各发一条消息
- [ ] 用 3.5 设置的密码登录管理后台
- [ ] Java 后端的知识库条目还在，说明 volume 迁移成功
- [ ] 服务器上的日志已经是 JSON 格式：
  ```bash
  vm$ cd ~/companyagent && docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml logs --tail 5 companyagent
  ```

**从现在开始，日常发布只需要：改代码 → `git push` → 等 Deploy 变绿。** 原来的 `deploy-upload-changed.ps1` 保留，作为 GitHub 出问题时的手动备用方案。

---

## 阶段 6：建立 Eval 基线

Eval gate 需要一个基线，才能判断"有没有比上次差"。

1. Actions → **Eval gate** → Run workflow → 勾选 **update_baseline** → Run
2. 运行结束后，点进去看 **Summary**：有一张表列出各项指标（意图准确率、相关性等）、调用次数、token 数和花费
3. 如果分数可以接受，在页面底部 **Artifacts** 下载 `eval-report`，解压后把 `evaluation/baseline.json` 复制到本地的 `CompanyAgent\evaluation\baseline.json`
4. 提交：

```powershell
PS> git add CompanyAgent/evaluation/baseline.json
PS> git commit -m "Add eval baseline"
PS> git push
```

**之后：** 每个修改 Agent、prompt 或 skills 的 PR 都会自动跑评测，分数掉了 PR 就会变红；每天 UTC 05:00（北京时间 13:00）也会自动跑一次。

> 如果第一次跑就没通过阈值（例如意图准确率低于 0.80），说明阈值定得比模型当前水平高。你可以改进 prompt，也可以在 `CompanyAgent/evaluation/thresholds.json` 里调低阈值。两种做法都是合理的工程决策，面试时都可以讲。

---

## 阶段 7（可选）：开启生产监控栈

监控栈（Prometheus、Grafana、Jaeger）大约占 600 MB 内存。先看看服务器还剩多少：

```bash
vm$ free -h
```

`available` 小于 1 GB 的话不建议开，可以先升级 VM 规格（4 GB → 8 GB，例如 Standard_B2ms），或者只在需要时临时开一下。

```bash
vm$ cd ~/companyagent
vm$ cat >> deploy/.env.production <<'EOF'
COMPOSE_PROFILES=observability
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318
GRAFANA_ADMIN_PASSWORD=<换成一个强密码>
EOF
vm$ docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --no-build
```

`COMPOSE_PROFILES` 写在 `.env.production` 里，之后 CI 每次部署也会带上监控栈，不会把它当成多余的容器删掉。想关掉的话，删除这一行，再执行一次 `up -d --remove-orphans`。

监控栈的端口只绑定在服务器本机上，你要通过 SSH 隧道在自己电脑上访问：

```powershell
PS> ssh -i "$HOME\Desktop\zhenlin-companyagent.pem" -L 3000:localhost:3000 -L 16686:localhost:16686 -L 9090:localhost:9090 azureuser@zhenlin-companyagent.belgiumcentral.cloudapp.azure.com
```

隧道连着的时候，在浏览器打开：Grafana http://localhost:3000 · Jaeger http://localhost:16686 · Prometheus http://localhost:9090/alerts

---

## 阶段 8（可选）：回滚演练

面试时能讲"我亲手验证过自动回滚"，比只说"我配置了回滚"有说服力得多。

1. 新建分支，故意让健康检查失败：把 `CompanyAgent/api/main.py` 里 `/health` 的 `return {"status": "ok", ...}` 改成 `raise HTTPException(500, "drill")`
2. 合并到 main 并 push
3. 观察 Deploy：`Smoke test` 会在 5 分钟后失败 → `Roll back to previous release` 执行 → 整个 workflow 变红，**线上自动回到上一个版本**
4. 用 `git revert` 撤销这次改动，再 push

---

## 阶段 9（可选）：Terraform

详细说明在 `infra/terraform/README.md`。最安全的第一步是**只执行 plan，不 apply**：

```powershell
PS> winget install Hashicorp.Terraform Microsoft.AzureCLI
# 装完重新打开 PowerShell
PS> az login
PS> cd infra\terraform
PS> Copy-Item terraform.tfvars.example terraform.tfvars
PS> notepad terraform.tfvars     # 填 subscription_id、SSH 公钥、你的 IP
PS> terraform init
PS> terraform plan
```

`plan` 只是读取信息，不会创建任何资源，也不花钱。要不要 `apply`（会新建一台 VM 并开始计费），以及怎么接管现有 VM，见 README 里的"蓝绿迁移 / 导入"两种方案。

---

## 阶段 10（可选）：本地 Kubernetes

详细说明在 `deploy/k8s/README.md`。需要安装 kind：`winget install Kubernetes.kind`。一共 5 条命令，全部在你自己的电脑上执行，不花钱。

---

## 收尾（一周后，确认一切正常）

```bash
vm$ docker volume rm deploy_echomind_eval deploy_echomind_java_data deploy_echomind_dotnet_data
vm$ docker image prune -a      # 删除旧的本地构建镜像，释放磁盘空间
vm$ rm -rf ~/backup            # 也可以先把备份下载到本地再删
```

---

## 附录 A：NSG 只放行自己 IP 时的规范做法（OIDC）

workflow 用 Azure 的联合身份登录（不需要保存任何长期密钥），部署时临时给 runner 加一条 SSH 规则，部署结束后（包括失败时）删除。

```powershell
PS> az login --tenant <租户ID>          # 租户要求 MFA 时，不指定租户会登录失败并显示 No subscriptions found
PS> $rg  = "<资源组名>"
PS> $nic = az vm show -g $rg -n <VM名> --query "networkProfile.networkInterfaces[0].id" -o tsv
PS> az network nic show --ids $nic --query "networkSecurityGroup.id" -o tsv   # 最后一段就是 NSG 名
PS> $nsg = "<上一行输出的 NSG 名>"
PS> $subject = "<subject，见下方说明>"
PS> $sub = az account show --query id -o tsv
PS> $tenant = az account show --query tenantId -o tsv
PS> $app = az ad app create --display-name companyagent-github-deploy --query appId -o tsv
PS> az ad sp create --id $app | Out-Null
PS> @{name="gh-production"; issuer="https://token.actions.githubusercontent.com"; subject=$subject; audiences=@("api://AzureADTokenExchange")} | ConvertTo-Json | Set-Content cred.json
PS> az ad app federated-credential create --id $app --parameters "@cred.json"
PS> az role assignment create --assignee $app --role "Network Contributor" --scope "/subscriptions/$sub/resourceGroups/$rg"
PS> Remove-Item cred.json
PS> "AZURE_CLIENT_ID=$app"; "AZURE_TENANT_ID=$tenant"; "AZURE_SUBSCRIPTION_ID=$sub"
```

把最后输出的 3 个值加到 GitHub **Secrets**，再把 `AZURE_RESOURCE_GROUP=$rg` 和 `AZURE_NSG_NAME=$nsg` 加到 **Variables**。

- **NSG 一定要用 VM 网卡上挂的那个。** 资源组里可能有多个 NSG（创建 VM 时可能多生成一个），名字最像的不一定是生效的那个。规则加错 NSG 时，workflow 会在 `Allow runner IP through NSG` 这一步等 2 分钟后报 `Port 22 still unreachable`。
- **subject 要和 GitHub 实际发来的逐字一致。** 新仓库的 subject 带用户 ID 和仓库 ID，形如 `repo:Lwindsy@98681410/companyagent@1402425682:environment:production`，不是 `repo:Lwindsy/companyagent:...`。最稳妥的做法：先随便填一个，跑一次 Deploy，从 `Azure login (OIDC)` 步骤日志的 `subject claim` 那一行复制真实值，再用 `az ad app federated-credential update --id $app --federated-credential-id gh-production --parameters "@cred.json"` 更新。

> workflow 临时规则的优先级是 300。如果你的 NSG 里已经有优先级为 300 的规则，把 `.github/workflows/deploy.yml` 里的 `--priority 300` 改成一个没被占用的数字。

## 附录 B：紧急退回改名前的状态

阶段 3 之后如果出了问题，又一时查不出原因，可以这样恢复旧版本：

```bash
vm$ cd ~/companyagent
vm$ docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml down   # 不加 -v
vm$ cd ~ && mv ~/companyagent ~/companyagent-failed
vm$ mkdir -p ~/restore && tar xzf ~/backup/echomind-files-*.tgz -C ~/restore && mv ~/restore/echomind ~/echomind
vm$ cd ~/echomind && docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --build
```

旧的 volume（`deploy_echomind_*`）在阶段 3 里没有被修改，所以数据都还在。恢复后，再在 GitHub 上删掉 `AZURE_VM_HOST` 变量，防止 CI 再次部署。

## 排错

| 现象 | 原因 / 解决 |
|---|---|
| CI 的 Python job 报 `ModuleNotFoundError` | `requirements.txt` 和代码不一致，本地执行 `pytest` 复现 |
| CI 的 `images (java)` 构建失败 | 本地执行 `docker build CompanyAgentJava` 复现，看 Maven 编译错误 |
| Deploy 的 `Sync` 步骤报 `Permission denied (publickey)` | `AZURE_VM_SSH_KEY` 粘贴得不完整（要包含 BEGIN/END 两行），或者公钥没加到 VM |
| `Host key verification failed` | `AZURE_VM_KNOWN_HOSTS` 为空或已过期，重新执行 `ssh-keyscan` |
| `Connection timed out` | NSG 拦截了 22 端口，见 2.3 |
| `AADSTS700213: No matching federated identity record` | 联合凭据的 subject 和日志里的 `subject claim` 不一致，见附录 A |
| 服务器上手动 `up` 报 `failed to resolve reference ... not found` | `.env.production` 里的 `IMAGE_REGISTRY` / `IMAGE_TAG` 是部署时写入的，检查它们是否被手动改过；重新跑一次 Deploy 即可恢复 |
| `pull` 时报 `denied` 或 `unauthorized` | 镜像是私有的，workflow 的 `packages: read` 权限没生效。到 GitHub → Packages → 每个镜像 → Package settings → Manage Actions access → 把仓库加进去 |
| 冒烟测试超时，但容器在运行 | Python 后端首次启动要加载 Chroma 模型，可能超过 5 分钟。看 `logs companyagent`；必要时调大 `deploy.yml` 里 `seq 1 30` 的次数 |
| Eval gate 报 `judge failure rate` | LLM API 限流或者 key 无效，看日志里的 401/429 |
| 网站打开 502 | Caddy 找不到上游服务，执行 `ps` 看哪个容器没启动起来 |
