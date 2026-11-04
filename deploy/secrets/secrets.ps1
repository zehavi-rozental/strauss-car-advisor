# STRAUSS DRIVE secrets, encrypted with Windows DPAPI (CurrentUser): only this Windows user on this computer can
# read them. Values are never printed; Get-StraussSecret copies one to the clipboard.
#   . C:\Users\user\strauss-secrets\secrets.ps1
#   Get-StraussSecret                      # the list: name, service, role, date (no values)
#   Get-StraussSecret -Name neon-database-url   # copies that value to the clipboard
# Keep a second copy of every value in your password manager: DPAPI data is lost with this Windows profile.
$StraussSecretsFile = Join-Path $PSScriptRoot 'secrets.csv'

function Add-StraussSecret([string]$Name, [string]$Service, [string]$Role, [string]$ValueFile) {
    $value = (Get-Content -Raw -LiteralPath $ValueFile).Trim()
    $protected = ConvertTo-SecureString -String $value -AsPlainText -Force | ConvertFrom-SecureString
    $rows = @(); if (Test-Path $StraussSecretsFile) { $rows = @(Import-Csv $StraussSecretsFile | Where-Object { $_.Name -ne $Name }) }
    $rows += [pscustomobject]@{ Name = $Name; Service = $Service; Role = $Role; Created = (Get-Date -Format 's'); Protected = $protected }
    $rows | Export-Csv -NoTypeInformation -Encoding UTF8 $StraussSecretsFile
    $check = Get-StraussSecretValue $Name
    if ($check -ne $value) { throw "secret $Name did not read back" }
    "stored and verified: $Name ($Service, $Role)"
}

function Get-StraussSecretValue([string]$Name) {
    $row = Import-Csv $StraussSecretsFile | Where-Object { $_.Name -eq $Name } | Select-Object -First 1
    if (-not $row) { throw "no secret named $Name" }
    $secure = ConvertTo-SecureString $row.Protected
    [System.Net.NetworkCredential]::new('', $secure).Password
}

function Get-StraussSecret([string]$Name) {
    if (-not $Name) { return Import-Csv $StraussSecretsFile | Select-Object Name, Service, Role, Created }
    Get-StraussSecretValue $Name | Set-Clipboard
    "copied $Name to the clipboard"
}

function Remove-StraussSecret([string]$Name) {
    $rows = @(Import-Csv $StraussSecretsFile | Where-Object { $_.Name -ne $Name })
    $rows | Export-Csv -NoTypeInformation -Encoding UTF8 $StraussSecretsFile
    "removed $Name"
}

function Export-StraussSecretsJson {
    Import-Csv $StraussSecretsFile | ForEach-Object {
        [pscustomobject]@{ name = $_.Name; service = $_.Service; role = $_.Role; created = $_.Created; value = (Get-StraussSecretValue $_.Name) }
    } | ConvertTo-Json -Compress
}

# Typed in a hidden prompt (Read-Host -AsSecureString) and stored straight into DPAPI: the value never appears on
# screen, in a file, in the command history or in a log.
function Add-StraussSecretSecure([string]$Name, [string]$Service, [string]$Role) {
    $secure = Read-Host -AsSecureString "Paste the value for $Name (hidden), then Enter"
    if ($secure.Length -lt 1) { throw "empty value: nothing stored" }
    $protected = ConvertFrom-SecureString $secure
    $rows = @(); if (Test-Path $StraussSecretsFile) { $rows = @(Import-Csv $StraussSecretsFile | Where-Object { $_.Name -ne $Name }) }
    $rows += [pscustomobject]@{ Name = $Name; Service = $Service; Role = $Role; Created = (Get-Date -Format 's'); Protected = $protected }
    $rows | Export-Csv -NoTypeInformation -Encoding UTF8 $StraussSecretsFile
    $len = (Get-StraussSecretValue $Name).Length
    if ($len -ne $secure.Length) { throw "secret $Name did not read back" }
    "stored and verified: $Name ($len characters)"
}
