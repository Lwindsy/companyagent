# CompanyAgent 部署与更新指南

公网地址：<https://zhenlin-companyagent.belgiumcentral.cloudapp.azure.com>

## 日常更新前端

1. 在本地修改 `CompanyAgentFrontend` 中的代码。

2. 可选：先在本地确认前端可以编译：

   ```powershell
   cd 'C:\Users\szllw\Desktop\CompanyAgent所有代码+简历4___\CompanyAgent所有代码+简历\CompanyAgentFrontend'
   npm run build
   ```

3. 在项目根目录执行上传脚本：

   ```powershell
   cd 'C:\Users\szllw\Desktop\CompanyAgent所有代码+简历4___\CompanyAgent所有代码+简历'
   powershell -ExecutionPolicy Bypass -File .\deploy-upload.ps1
   ```

   脚本会打包代码并上传到 Azure。它不会上传 `.env`、私钥、`node_modules`、Python 虚拟环境或 wiki 文档。

4. SSH 登录 Azure 后，更新并重新构建前端：

   ```bash
   cd ~/companyagent
   unzip -oq ~/companyagent-deploy.zip -d ~/companyagent
   docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --build frontend
   ```

5. 在浏览器刷新公网地址验证更新。

## 修改后端或部署配置

上传步骤相同；在 Azure 的最后一步改为重建全部服务：

```bash
cd ~/companyagent
unzip -oq ~/companyagent-deploy.zip -d ~/companyagent
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --build
```

## 增量更新（推荐，网速慢时用）

`deploy-upload-changed.ps1` 先通过 SSH 读取服务器上文件的 SHA-256（只读），和本地逐个比对，只打包上传内容不同的文件，并解压到 `~/companyagent`。最后会打印需要重建的服务。

```powershell
cd 'C:\Users\szllw\Desktop\CompanyAgent所有代码+简历4___\CompanyAgent所有代码+简历'
# 先看会上传哪些文件，不改动服务器
powershell -ExecutionPolicy Bypass -File .\deploy-upload-changed.ps1 -DryRun
# 实际上传
powershell -ExecutionPolicy Bypass -File .\deploy-upload-changed.ps1
```

然后在 Azure SSH 里执行脚本打印的 `docker compose ... up -d --build <服务>` 命令。如果 Caddyfile 有改动，还需要执行 `--force-recreate caddy`：Caddyfile 是单文件挂载，容器必须重建才能读到新文件。

脚本不会上传：`.env`、`.pem`、`.git`、`node_modules`、虚拟环境、`bin/obj/target/dist` 等编译产物、Java 的 `.mvn`、.NET 测试项目，以及文件名含中文的文件（会给出提示）。

## 首次上线 .NET 后端

.NET 后端（`CompanyAgentDotnet/`，服务名 `companyagent-dotnet`，端口 8090，公网路径 `/api/dotnet`）与 Python / Java 共用 `CompanyAgent/.env` 和 Redis。

1. .NET 后端没有内置管理员密码：在 Azure 上的 `~/companyagent/CompanyAgent/.env` 里加入 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD`。Python 和 Java 也会读取这两个变量，所以三者的管理员账号会保持一致。
2. 上传后重建相关服务（Caddyfile 新增了 `/api/dotnet` 路由，所以 caddy 也要重建）：

   ```bash
   cd ~/companyagent
   unzip -oq ~/companyagent-deploy.zip -d ~/companyagent
   docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --build companyagent-dotnet frontend caddy companyagent companyagent-java
   ```

3. 验证：

   ```bash
   curl -fsS https://zhenlin-companyagent.belgiumcentral.cloudapp.azure.com/api/dotnet/health
   ```

## 状态与排错

```bash
cd ~/companyagent
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml ps
curl -fsS https://zhenlin-companyagent.belgiumcentral.cloudapp.azure.com/api/python/health
curl -fsS https://zhenlin-companyagent.belgiumcentral.cloudapp.azure.com/api/dotnet/health
```

.NET 后端日志：

```bash
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml logs --tail=100 companyagent-dotnet
```

前端报错日志：

```bash
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml logs --tail=100 frontend
```

后端报错日志：

```bash
docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml logs --tail=100 companyagent
```

## 重要安全规则

- 不要把 `CompanyAgent/.env`、`deploy/.env.production` 或 `.pem` 私钥上传到 GitHub。
- 不要执行 `docker compose down -v`；`-v` 会删除 Redis / Chroma 的持久化数据。
- Azure 网络安全组只应向公网开放 `80` 和 `443`；SSH `22` 应限制为自己的 IP。
