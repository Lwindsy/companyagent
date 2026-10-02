<#
Incremental deployment: uploads only the files whose content differs from the copy on the Azure VM.

1. Lists the deployable local files (build output, VCS data, virtual environments and secrets are skipped).
2. Asks the VM for the SHA-256 of the same paths over SSH (read-only).
3. Packs only new or changed files, uploads that archive and extracts it into ~/companyagent.
4. Prints the docker compose command for the services whose sources changed.

Use -DryRun to see what would be uploaded without changing anything on the VM.
#>
[CmdletBinding()]
param(
    [string]$KeyPath = "$env:USERPROFILE\Desktop\zhenlin-companyagent.pem",
    [string]$HostName = 'zhenlin-companyagent.belgiumcentral.cloudapp.azure.com',
    [string]$SshUser = 'azureuser',
    [string]$RemoteDir = '~/companyagent',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
$target = "${SshUser}@${HostName}"
$sshOptions = @('-i', $KeyPath, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20')

# Native commands must exchange UTF-8, not the Windows console code page.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

if (-not (Test-Path -LiteralPath $KeyPath)) { throw "SSH key was not found: $KeyPath" }

# ---------------------------------------------------------------------------
# 1. Deployable local files
# ---------------------------------------------------------------------------
# Directory names skipped anywhere in the tree: generated, VCS or dependency folders.
$skipDirs = @('.git', '.idea', '.vscode', '.vs', 'node_modules', '.venv', 'venv', '__pycache__',
              '.pytest_cache', 'bin', 'obj', 'target', 'dist', 'TestResults')
# Paths (relative, forward slashes) skipped together with everything below them.
$skipPrefixes = @('CompanyAgent/wiki/', 'CompanyAgentDotnet/data/', 'CompanyAgentJava/.mvn/', 'CompanyAgentDotnet/tests/')
# Secrets and local-only files.
$skipNames = @('.env', '.env.production', '.DS_Store')
$skipExtensions = @('.pem', '.log', '.zip', '.tgz')

function Test-Deployable([string]$relative) {
    $parts = $relative.Split('/')
    foreach ($part in $parts[0..($parts.Length - 2)]) {
        if ($skipDirs -contains $part) { return $false }
    }
    foreach ($prefix in $skipPrefixes) {
        if ($relative.StartsWith($prefix, [StringComparison]::Ordinal)) { return $false }
    }
    $name = $parts[-1]
    if ($skipNames -contains $name) { return $false }
    if ($skipExtensions -contains [System.IO.Path]::GetExtension($name).ToLowerInvariant()) { return $false }
    return $true
}

Write-Host 'Scanning local files...'
$root = (Resolve-Path -LiteralPath $projectPath).Path.TrimEnd('\') + '\'
$localFiles = @{}
$skippedNonAscii = @()
foreach ($dir in @('CompanyAgent', 'CompanyAgentFrontend', 'CompanyAgentJava', 'CompanyAgentDotnet', 'deploy')) {
    $full = Join-Path $projectPath $dir
    if (-not (Test-Path -LiteralPath $full)) { continue }
    Get-ChildItem -LiteralPath $full -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length).Replace('\', '/')
        if (-not (Test-Deployable $relative)) { return }
        # Non-ASCII names were garbled by Windows tar before (see DEPLOYMENT notes); keep them out.
        if ($relative -match '[^\x20-\x7E]') { $skippedNonAscii += $relative; return }
        $localFiles[$relative] = $_.FullName
    }
}
Write-Host ("  {0} deployable files" -f $localFiles.Count)

# ---------------------------------------------------------------------------
# 2. Remote hashes of the same paths (read-only)
# ---------------------------------------------------------------------------
Write-Host "Reading file hashes from $HostName ..."
$pathList = ($localFiles.Keys | Sort-Object) -join "`n"
# tr strips any CR; xargs -d '\n' keeps paths with spaces intact; missing files are simply not listed.
$remoteCommand = "cd $RemoteDir && tr -d '\r' | xargs -d '\n' sha256sum 2>/dev/null; true"
$remoteOutput = $pathList | & ssh.exe @sshOptions $target $remoteCommand
if ($LASTEXITCODE -ne 0) { throw "ssh failed with exit code $LASTEXITCODE" }

$remoteHashes = @{}
foreach ($line in $remoteOutput) {
    if ($line -match '^([0-9a-f]{64}) [ *](.+)$') { $remoteHashes[$Matches[2]] = $Matches[1] }
}

# ---------------------------------------------------------------------------
# 3. Changed files
# ---------------------------------------------------------------------------
$changed = @()
foreach ($relative in ($localFiles.Keys | Sort-Object)) {
    $localHash = (Get-FileHash -LiteralPath $localFiles[$relative] -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($remoteHashes[$relative] -ne $localHash) {
        $changed += [pscustomobject]@{
            Path   = $relative
            Status = $(if ($remoteHashes.ContainsKey($relative)) { 'changed' } else { 'new' })
            SizeKB = [math]::Ceiling((Get-Item -LiteralPath $localFiles[$relative]).Length / 1KB)
        }
    }
}

if ($skippedNonAscii.Count -gt 0) {
    Write-Warning ("Skipped {0} file(s) with non-ASCII names; upload them manually if they changed:" -f $skippedNonAscii.Count)
    $skippedNonAscii | ForEach-Object { Write-Warning "  $_" }
}

if ($changed.Count -eq 0) {
    Write-Host 'The VM already has every deployable file. Nothing to upload.' -ForegroundColor Green
    return
}

Write-Host ''
Write-Host ("{0} file(s) differ ({1} KB before compression):" -f $changed.Count, ($changed | Measure-Object SizeKB -Sum).Sum)
$changed | ForEach-Object { Write-Host ("  [{0,-7}] {1}" -f $_.Status, $_.Path) }

# Map changed paths to the compose services that must be rebuilt or recreated.
$services = [System.Collections.Generic.List[string]]::new()
function Add-Service([string]$name) { if (-not $services.Contains($name)) { $services.Add($name) } }
foreach ($path in $changed.Path) {
    switch -Wildcard ($path) {
        'CompanyAgent/*'          { Add-Service 'companyagent' }
        'CompanyAgentJava/*'      { Add-Service 'companyagent-java' }
        'CompanyAgentDotnet/*'    { Add-Service 'companyagent-dotnet' }
        'CompanyAgentFrontend/*'  { Add-Service 'frontend' }
        # Caddy mounts the Caddyfile as a single file; the container must be recreated to see a new copy.
        'deploy/Caddyfile'    { Add-Service 'caddy' }
    }
}
$composeChanged = $changed.Path -contains 'deploy/docker-compose.prod.yml'

$compose = 'docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml'
$buildServices = @($services | Where-Object { $_ -ne 'caddy' })
$commands = @()
if ($buildServices.Count -gt 0) { $commands += "$compose up -d --build $($buildServices -join ' ')" }
if ($services.Contains('caddy')) { $commands += "$compose up -d --force-recreate caddy" }
if ($composeChanged -and $commands.Count -eq 0) { $commands += "$compose up -d" }

if ($DryRun) {
    Write-Host ''
    Write-Host 'Dry run: nothing was uploaded. After a real run, rebuild with:' -ForegroundColor Yellow
    $commands | ForEach-Object { Write-Host "  cd $RemoteDir && $_" }
    return
}

# ---------------------------------------------------------------------------
# 4. Pack, upload, extract
# ---------------------------------------------------------------------------
$listFile = Join-Path $env:TEMP 'companyagent-changed-files.txt'
$archivePath = Join-Path $env:TEMP 'companyagent-changed.tgz'
# LF-only list: a trailing CR would become part of each file name.
[System.IO.File]::WriteAllText($listFile, (($changed.Path -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
if (Test-Path -LiteralPath $archivePath) { Remove-Item -LiteralPath $archivePath -Force }

# Use the Windows tar explicitly: from Git Bash, PATH finds GNU tar, which treats "C:" as a remote host.
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
& $tar -czf $archivePath -C $projectPath -T $listFile
if ($LASTEXITCODE -ne 0) { throw "tar.exe failed with exit code $LASTEXITCODE" }
Write-Host ("Archive: {0:N0} KB" -f ((Get-Item -LiteralPath $archivePath).Length / 1KB))

Write-Host "Uploading to $target ..."
& scp.exe @sshOptions $archivePath "${target}:~/companyagent-changed.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp.exe failed with exit code $LASTEXITCODE" }

& ssh.exe @sshOptions $target "tar -xzf ~/companyagent-changed.tgz -C $RemoteDir && rm ~/companyagent-changed.tgz"
if ($LASTEXITCODE -ne 0) { throw "Remote extraction failed with exit code $LASTEXITCODE" }

Write-Host ''
Write-Host 'Changed files are now in place on the VM. In Azure SSH, run:' -ForegroundColor Green
Write-Host "cd $RemoteDir"
$commands | ForEach-Object { Write-Host $_ }
