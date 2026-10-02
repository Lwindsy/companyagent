terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.14"
    }
  }

  # 远程状态：多人/CI 共用同一份 state，并通过 blob lease 加锁。
  # 首次使用前先建存储账号（见 README「远程状态」），再取消注释并 `terraform init -migrate-state`。
  # backend "azurerm" {
  #   resource_group_name  = "companyagent-tfstate"
  #   storage_account_name = "companyagenttfstate<random>"
  #   container_name       = "tfstate"
  #   key                  = "prod.terraform.tfstate"
  # }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}
