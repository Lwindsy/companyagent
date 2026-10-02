variable "subscription_id" {
  description = "Azure subscription ID (az account show --query id -o tsv)"
  type        = string
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "belgiumcentral"
}

variable "prefix" {
  description = "Name prefix for every resource"
  type        = string
  default     = "companyagent"
}

variable "dns_label" {
  description = "Public IP DNS label → <label>.<region>.cloudapp.azure.com"
  type        = string
  default     = "zhenlin-companyagent"
}

variable "vm_size" {
  description = "VM size. Standard_B2s = 2 vCPU / 4 GiB, enough for the compose stack without the observability profile."
  type        = string
  default     = "Standard_B2s"
}

variable "os_disk_size_gb" {
  type    = number
  default = 64
}

variable "admin_username" {
  type    = string
  default = "azureuser"
}

variable "admin_ssh_public_key" {
  description = "SSH public key content (e.g. file(\"~/.ssh/id_ed25519.pub\"))"
  type        = string
}

variable "ssh_allowed_cidrs" {
  description = "CIDRs allowed to SSH. Your own IP (/32), plus GitHub Actions if the deploy job connects over SSH."
  type        = list(string)

  validation {
    condition     = !contains(var.ssh_allowed_cidrs, "0.0.0.0/0")
    error_message = "Do not open SSH to the whole internet."
  }
}

variable "tags" {
  type = map(string)
  default = {
    project    = "companyagent"
    managed_by = "terraform"
  }
}

variable "alert_email" {
  description = "Email for Azure Monitor VM alerts; empty disables them"
  type        = string
  default     = ""
}
