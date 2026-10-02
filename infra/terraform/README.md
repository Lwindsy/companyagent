# CompanyAgent Azure 基础设施（Terraform）

用代码描述生产环境：资源组、VNet/子网、NSG（只开 80/443，SSH 限定 IP）、静态公网 IP + DNS 标签、
Ubuntu 24.04 VM（cloud-init 装 Docker、系统托管身份），以及可选的 Azure Monitor CPU/内存告警。

```
infra/terraform/
├── versions.tf               provider 版本 + 远程状态（azurerm backend）
├── variables.tf              可调参数（区域、VM 规格、SSH 白名单……）
├── main.tf                   所有资源
├── cloud-init.yaml           VM 首次启动脚本
├── outputs.tf                FQDN、SSH 命令、托管身份 ID
└── terraform.tfvars.example  复制成 terraform.tfvars 填写
```

## 新建一套环境

需要 [Terraform ≥ 1.6](https://developer.hashicorp.com/terraform/install) 和 [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)。

```bash
az login
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # 填写 subscription_id、SSH 公钥、你的 IP
terraform init
terraform plan -out tfplan     # 先看会创建什么
terraform apply tfplan         # 会开始计费
terraform output fqdn          # 填到 deploy/.env.production 的 DOMAIN
```

之后的应用发布由 GitHub Actions 的 deploy 工作流完成，不需要再手动登录 VM。

## 现有 VM 怎么办（目前是手动在门户创建的）

有两条路，**推荐 A**：

**A. 蓝绿迁移。** 用 Terraform 新建一套（把 `dns_label` 改成新名字，例如 `companyagent-prod`），
让 CI 部署到新 VM 并验证，然后把域名切过去，最后删除旧资源组。
这样风险最小：旧环境在切换前一直可以用。

**B. 导入现有资源。** 门户创建的资源名一般和这里的命名（`companyagent-*`）不同，名字不同 Terraform 会判断为"需要重建"。
所以要让 Terraform 根据现有资源生成配置：

```bash
# 1. 找到现有资源的 ID
az resource list --resource-group <现有资源组> --query "[].{type:type, id:id}" -o table

# 2. 写 import 块（每个资源一个），例如：
#    import {
#      to = azurerm_linux_virtual_machine.main
#      id = "/subscriptions/.../resourceGroups/.../providers/Microsoft.Compute/virtualMachines/..."
#    }
# 3. 让 Terraform 生成匹配现有资源的 HCL，再和 main.tf 对比合并
terraform plan -generate-config-out=generated.tf
# 4. 直到 plan 显示 "No changes" 才算导入完成
```

## 远程状态

本地 state 文件里有资源 ID 等信息，而且多人或 CI 同时操作时没有锁。生产环境应该放到 Azure Storage：

```bash
az group create -n companyagent-tfstate -l belgiumcentral
az storage account create -n companyagenttfstate$RANDOM -g companyagent-tfstate --sku Standard_LRS --min-tls-version TLS1_2
az storage container create -n tfstate --account-name <上一步的账号名>
```

然后取消 `versions.tf` 里 `backend "azurerm"` 的注释，执行 `terraform init -migrate-state`。

## 设计取舍

| 决策 | 原因 |
|---|---|
| 单 VM + compose，而不是 AKS | 成本约为 AKS 的 1/5，当前流量足够；需要弹性时用 `deploy/k8s` 迁移 |
| 静态公网 IP + DNS 标签 | 重启 VM 地址不变，Caddy 的 Let's Encrypt 证书不受影响 |
| NSG 只开 80/443 | Redis、Chroma、Grafana 只在 Docker 内网或 127.0.0.1，通过 SSH 隧道访问 |
| `ssh_allowed_cidrs` 禁止 0.0.0.0/0 | 用 `validation` 块在 plan 阶段就拦下来 |
| `ignore_changes = [custom_data, source_image_reference]` | 这两个字段变化会导致 VM 被**销毁重建**，生产环境不能因此丢数据 |
| 系统托管身份 | 以后访问 Key Vault/ACR 不需要在 VM 上放密钥 |
