[CmdletBinding()]
param(
    [string]$KeyPath = "$env:USERPROFILE\Desktop\zhenlin-companyagent.pem",
    [string]$HostName = 'zhenlin-companyagent.belgiumcentral.cloudapp.azure.com',
    [string]$SshUser = 'azureuser'
)

$ErrorActionPreference = 'Stop'

$projectPath = $PSScriptRoot
$archivePath = Join-Path $env:TEMP 'companyagent-deploy.zip'

foreach ($requiredPath in @(
    (Join-Path $projectPath 'CompanyAgent'),
    (Join-Path $projectPath 'CompanyAgentFrontend'),
    $KeyPath
)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required path was not found: $requiredPath"
    }
}

if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) {
    throw 'tar.exe was not found. Run this script in Windows PowerShell on your PC.'
}

# Keep the deployment-only fixes in the local source so a later upload cannot
# restore the Docker Desktop-only hostname or re-send the legacy wiki files.
$dockerIgnorePath = Join-Path $projectPath 'CompanyAgent\.dockerignore'
$dockerIgnore = Get-Content -LiteralPath $dockerIgnorePath -Raw
if ($dockerIgnore -notmatch '(?m)^wiki/$') {
    Add-Content -LiteralPath $dockerIgnorePath -Value 'wiki/' -Encoding utf8
}

$nginxConfigPath = Join-Path $projectPath 'CompanyAgentFrontend\docker\nginx.conf'
$nginxConfig = Get-Content -LiteralPath $nginxConfigPath -Raw
if ($nginxConfig -match 'host\.docker\.internal') {
    $nginxConfig = $nginxConfig -replace 'host\.docker\.internal', 'companyagent'
    [System.IO.File]::WriteAllText(
        $nginxConfigPath,
        $nginxConfig,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host 'Updated the frontend Docker proxy address for Azure.'
}

# The wiki contains legacy non-UTF-8 file names after Linux extraction and is not
# required by the running application. Secrets stay only on the Azure VM.
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}

Write-Host 'Creating deployment archive...'
Push-Location $projectPath
try {
    & tar.exe -a -c -f $archivePath `
        '--exclude=CompanyAgent/.env' `
        '--exclude=CompanyAgent/.venv' `
        '--exclude=CompanyAgent/.git' `
        '--exclude=CompanyAgent/wiki' `
        '--exclude=CompanyAgentFrontend/node_modules' `
        '--exclude=CompanyAgentFrontend/dist' `
        '--exclude=CompanyAgentJava/target' `
        '--exclude=CompanyAgentJava/.git' `
        '--exclude=CompanyAgentJava/.mvn' `
        '--exclude=CompanyAgentFrontend/.git' `
        '--exclude=CompanyAgentDotnet/.env' `
        '--exclude=CompanyAgentDotnet/data' `
        '--exclude=CompanyAgentDotnet/src/CompanyAgent.Api/bin' `
        '--exclude=CompanyAgentDotnet/src/CompanyAgent.Api/obj' `
        '--exclude=CompanyAgentDotnet/tests/CompanyAgent.Api.Tests/bin' `
        '--exclude=CompanyAgentDotnet/tests/CompanyAgent.Api.Tests/obj' `
        '--exclude=.idea' `
        -C $projectPath .
    if ($LASTEXITCODE -ne 0) {
        throw "tar.exe failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host "Uploading to $SshUser@$HostName ..."
& scp.exe -i $KeyPath $archivePath "${SshUser}@${HostName}:~/"
if ($LASTEXITCODE -ne 0) {
    throw "scp.exe failed with exit code $LASTEXITCODE"
}

Write-Host ''
Write-Host 'Upload complete. In Azure SSH, run:' -ForegroundColor Green
Write-Host 'cd ~/companyagent'
Write-Host 'unzip -oq ~/companyagent-deploy.zip -d ~/companyagent'
Write-Host 'docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --build frontend companyagent companyagent-java companyagent-dotnet caddy'
