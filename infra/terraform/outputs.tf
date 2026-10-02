output "public_ip" {
  value = azurerm_public_ip.main.ip_address
}

output "fqdn" {
  description = "Use this as DOMAIN in deploy/.env.production"
  value       = azurerm_public_ip.main.fqdn
}

output "ssh_command" {
  value = "ssh ${var.admin_username}@${azurerm_public_ip.main.fqdn}"
}

output "vm_principal_id" {
  description = "Managed identity of the VM, for granting Key Vault / ACR access later"
  value       = azurerm_linux_virtual_machine.main.identity[0].principal_id
}
