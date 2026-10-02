# CompanyAgent 生产环境：一台 Ubuntu VM 运行 docker compose（Caddy 负责 HTTPS）。
#
#   Internet ──80/443──► Public IP (DNS label) ──► NIC ──► VM (docker compose)
#            ──22──────► 仅 ssh_allowed_cidrs
#
# 为什么是 VM 而不是 AKS / Container Apps：五个容器 + 两个有状态存储，单机 compose
# 每月成本约为 AKS 的 1/5；需要弹性时再迁到 deploy/k8s 里的 manifests。

resource "azurerm_resource_group" "main" {
  name     = "${var.prefix}-rg"
  location = var.location
  tags     = var.tags
}

# ── 网络 ──────────────────────────────────────────────────────────────────────

resource "azurerm_virtual_network" "main" {
  name                = "${var.prefix}-vnet"
  address_space       = ["10.20.0.0/16"]
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_subnet" "app" {
  name                 = "app"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.20.1.0/24"]
}

resource "azurerm_network_security_group" "app" {
  name                = "${var.prefix}-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags

  # 只开放 HTTP/HTTPS。Redis、Chroma、Prometheus、Grafana 都只在 Docker 网络或 127.0.0.1 上。
  security_rule {
    name                       = "allow-web"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["80", "443"]
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }

  # HTTP/3（Caddy 默认启用）
  security_rule {
    name                       = "allow-quic"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Udp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "allow-ssh-admin"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefixes    = var.ssh_allowed_cidrs
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "app" {
  subnet_id                 = azurerm_subnet.app.id
  network_security_group_id = azurerm_network_security_group.app.id
}

resource "azurerm_public_ip" "main" {
  name                = "${var.prefix}-pip"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  allocation_method   = "Static" # 静态 IP，重启 VM 不变，DNS 和证书都不受影响
  sku                 = "Standard"
  domain_name_label   = var.dns_label
  tags                = var.tags
}

resource "azurerm_network_interface" "main" {
  name                = "${var.prefix}-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.app.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.main.id
  }
}

# ── 虚拟机 ────────────────────────────────────────────────────────────────────

resource "azurerm_linux_virtual_machine" "main" {
  name                  = "${var.prefix}-vm"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  size                  = var.vm_size
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.main.id]
  tags                  = var.tags

  disable_password_authentication = true
  admin_ssh_key {
    username   = var.admin_username
    public_key = var.admin_ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = var.os_disk_size_gb
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  # 首次启动时装好 Docker；之后的应用发布由 GitHub Actions 完成。
  custom_data = base64encode(templatefile("${path.module}/cloud-init.yaml", {
    admin_username = var.admin_username
  }))

  # 托管身份：以后可以直接用它访问 Key Vault / ACR，而不是在 VM 上放密钥。
  identity {
    type = "SystemAssigned"
  }

  boot_diagnostics {}

  lifecycle {
    # cloud-init 只在首次创建时生效；镜像版本更新也不应该触发重建生产 VM。
    ignore_changes = [custom_data, source_image_reference]
  }
}

# ── 运维 ──────────────────────────────────────────────────────────────────────

# 每晚自动关机是开发环境省钱的常用做法；生产环境默认关闭。
# resource "azurerm_dev_test_global_vm_shutdown_schedule" "main" { ... }

# 平台层监控：VM 不可用时发邮件（应用层告警在 Prometheus 里）。
resource "azurerm_monitor_action_group" "ops" {
  count               = var.alert_email == "" ? 0 : 1
  name                = "${var.prefix}-ops"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "cagentops" # Azure 限制 1-12 个字符
  tags                = var.tags

  email_receiver {
    name          = "owner"
    email_address = var.alert_email
  }
}

resource "azurerm_monitor_metric_alert" "vm_cpu" {
  count               = var.alert_email == "" ? 0 : 1
  name                = "${var.prefix}-vm-cpu-high"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_linux_virtual_machine.main.id]
  description         = "VM CPU above 85% for 15 minutes"
  frequency           = "PT5M"
  window_size         = "PT15M"
  severity            = 2
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.Compute/virtualMachines"
    metric_name      = "Percentage CPU"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 85
  }

  action {
    action_group_id = azurerm_monitor_action_group.ops[0].id
  }
}

resource "azurerm_monitor_metric_alert" "vm_memory" {
  count               = var.alert_email == "" ? 0 : 1
  name                = "${var.prefix}-vm-memory-low"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_linux_virtual_machine.main.id]
  description         = "Less than 300 MB of available memory for 10 minutes"
  frequency           = "PT5M"
  window_size         = "PT15M"
  severity            = 2
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.Compute/virtualMachines"
    metric_name      = "Available Memory Bytes"
    aggregation      = "Average"
    operator         = "LessThan"
    threshold        = 300000000
  }

  action {
    action_group_id = azurerm_monitor_action_group.ops[0].id
  }
}
