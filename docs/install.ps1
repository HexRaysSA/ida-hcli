#!/usr/bin/env pwsh
# TODO(everyone): Keep this script simple and easily auditable.

$ErrorActionPreference = 'Stop'

$HcliInstall = $env:HCLI_INSTALL
$BinName = "hcli"
$Download_Base_Url="https://hcli.docs.hex-rays.com"

$BinDir = if ($HcliInstall) {
  "${HcliInstall}\bin"
} else {
  "${Home}\.${BinName}\bin"
}

$HcliExe = "$BinDir\${BinName}.exe"


# Fetch version from API
try {
  $VersionResponse = Invoke-RestMethod -Uri "${Download_Base_Url}/release/version.json" -ErrorAction Stop
  $Version = $VersionResponse.version
} catch {
  Write-Error "Failed to fetch version from API: $_"
  exit 1
}

$DownloadUrl = "${Download_Base_Url}/release/${BinName}-windows-x86_64-${Version}.exe"

if (!(Test-Path $BinDir)) {
  New-Item $BinDir -ItemType Directory | Out-Null
}

curl.exe --ssl-revoke-best-effort -Lo $HcliExe $DownloadUrl

$User = [System.EnvironmentVariableTarget]::User
$Path = [System.Environment]::GetEnvironmentVariable('Path', $User)
if (!(";${Path};".ToLower() -like "*;${BinDir};*".ToLower())) {
  [System.Environment]::SetEnvironmentVariable('Path', "${Path};${BinDir}", $User)
  $Env:Path += ";${BinDir}"
}

Write-Output "${BinName} ${Version} was installed successfully to ${HcliExe}"
Write-Output "Run '${BinName} --help' to get started"