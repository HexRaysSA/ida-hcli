<#
.SYNOPSIS

Enhanced installer for hcli - IDA Pro command-line interface

.DESCRIPTION

This script detects your platform and downloads the appropriate binary from
GitHub releases, then installs it to a suitable location.

Installation directories (in order of preference):
    $env:XDG_BIN_HOME (if set and exists)
    $env:LOCALAPPDATA\Programs\hcli (Windows standard)
    $env:USERPROFILE\.local\bin (fallback)

It will then add that directory to your user PATH via the Windows registry.

.PARAMETER GitHubRepo
Override the GitHub repository for downloads (format: owner/repo).
Default: HexRaysSA/ida-hcli

.PARAMETER InstallDir
Force installation to a specific directory. Overrides all other directory detection.

.PARAMETER Version
Install a specific version instead of the latest production release.

.PARAMETER NoModifyPath
Don't add the installation directory to PATH

.PARAMETER Verbose
Enable verbose output for debugging

.PARAMETER Help
Print detailed help information

.EXAMPLE
PS> .\install.ps1
Basic installation with default settings

.EXAMPLE  
PS> .\install.ps1 -InstallDir "C:\tools\hcli"
Install to a specific directory

.EXAMPLE
PS> .\install.ps1 -Version "0.7.3"
Install a specific version

.EXAMPLE
PS> .\install.ps1 -NoModifyPath -Verbose
Install without modifying PATH, with verbose output

#>

[CmdletBinding()]
param (
    [Parameter(HelpMessage = "Override the GitHub repository for downloads (format: owner/repo)")]
    [string]$GitHubRepo,
    
    [Parameter(HelpMessage = "Force installation to a specific directory")]
    [ValidateScript({
        if ([string]::IsNullOrWhiteSpace($_)) { return $true }
        if (-not [System.IO.Path]::IsPathRooted($_)) { 
            throw "InstallDir must be an absolute path" 
        }
        return $true
    })]
    [string]$InstallDir,
    
    [Parameter(HelpMessage = "Install a specific version instead of the latest production release")]
    [string]$Version,
    
    [Parameter(HelpMessage = "Don't add the installation directory to PATH")]
    [switch]$NoModifyPath,
    
    [Parameter(HelpMessage = "Print detailed help information")]
    [switch]$Help
)

# Application configuration
$app_name = 'hcli'
$app_version = $null  # Will be fetched from GitHub releases

# GitHub repository configuration
$github_repo = 'HexRaysSA/ida-hcli'
$github_api_base = 'https://api.github.com'

function Initialize-Configuration {
    <#
    .SYNOPSIS
    Centralizes environment variable and parameter precedence logic
    #>
    [CmdletBinding()]
    param()
    
    Write-Verbose "Applying configuration precedence (parameter > environment variable > default)"
    
    # GitHub repository configuration
    if ($GitHubRepo) {
        # Validate the GitHubRepo format
        if ($GitHubRepo -notmatch '^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$') {
            throw "Invalid GitHub repository format. Expected: owner/repo"
        }
        $script:github_repo = $GitHubRepo
        Write-Verbose "Using GitHub repo from parameter: $GitHubRepo"
    } elseif ($env:HCLI_GITHUB_REPO) {
        # Validate the environment variable format
        if ($env:HCLI_GITHUB_REPO -notmatch '^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$') {
            throw "Invalid GitHub repository format in HCLI_GITHUB_REPO. Expected: owner/repo"
        }
        $script:github_repo = $env:HCLI_GITHUB_REPO
        Write-Verbose "Using GitHub repo from environment: $env:HCLI_GITHUB_REPO"
    }
    
    # Installation directory configuration  
    if ($env:HCLI_IDA_INSTALL_DIR) {
        $script:InstallDir = $env:HCLI_IDA_INSTALL_DIR
        Write-Verbose "Using install dir from environment: $env:HCLI_IDA_INSTALL_DIR"
    }
    
    # Version configuration
    if ($Version) {
        # Validate the Version format
        if ($Version -notmatch '^v?[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]*)?$') {
            throw "Invalid version format. Expected: x.y.z or vx.y.z (e.g., 0.7.3 or v0.7.3)"
        }
    }
    if ($env:HCLI_VERSION) {
        # Validate the environment variable format
        if ($env:HCLI_VERSION -notmatch '^v?[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]*)?$') {
            throw "Invalid version format in HCLI_VERSION. Expected: x.y.z or vx.y.z"
        }
        $script:Version = $env:HCLI_VERSION
        Write-Verbose "Using version from environment: $env:HCLI_VERSION"
    }
    
    # PATH modification configuration
    if ($env:HCLI_NO_MODIFY_PATH) {
        $script:NoModifyPath = [bool]$env:HCLI_NO_MODIFY_PATH
        Write-Verbose "PATH modification disabled via environment variable"
    }
}

# Apply configuration precedence
Initialize-Configuration

# GitHub authentication token
$auth_token = $env:HCLI_GITHUB_TOKEN

# Set error handling
$ErrorActionPreference = 'Stop'

# Make Write-Information statements visible
$InformationPreference = "Continue"

#region Helper Functions

function Initialize-Environment {
    <#
    .SYNOPSIS
    Validates the PowerShell environment and requirements
    #>
    [CmdletBinding()]
    param()
    
    Write-Verbose "Checking PowerShell environment..."
    
    # Check PowerShell version
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        throw @"
Error: PowerShell 5.0 or later is required to install $app_name.
Current version: $($PSVersionTable.PSVersion)

Please upgrade PowerShell:
https://docs.microsoft.com/en-us/powershell/scripting/install/installing-powershell-windows

"@
    }
    
    # Check execution policy
    $allowedExecutionPolicy = @('Unrestricted', 'RemoteSigned', 'Bypass')
    $currentPolicy = Get-ExecutionPolicy
    if ($currentPolicy.ToString() -notin $allowedExecutionPolicy) {
        throw @"
Error: PowerShell requires an execution policy in [$($allowedExecutionPolicy -join ", ")] to run $app_name.
Current policy: $currentPolicy

To set the execution policy to 'RemoteSigned', run:
    Set-ExecutionPolicy RemoteSigned -Scope CurrentUser

"@
    }

    # Check TLS 1.2 support
    if ([System.Enum]::GetNames([System.Net.SecurityProtocolType]) -notcontains 'Tls12') {
        throw @"
Error: Installing $app_name requires at least .NET Framework 4.5 for TLS 1.2 support.
Please download and install it first:
    https://www.microsoft.com/net/download

"@
    }

    Write-Verbose "Environment validation passed"
}

function Get-Architecture {
    <#
    .SYNOPSIS
    Validates system architecture for hcli installation
    .DESCRIPTION
    Since hcli only supports x86_64, this function validates the system is 64-bit
    and returns the appropriate architecture string.
    #>
    [CmdletBinding()]
    param()

    Write-Verbose "Validating system architecture for hcli compatibility..."

    # Simple 64-bit check - sufficient since we only support x86_64
    if (-not [System.Environment]::Is64BitOperatingSystem) {
        throw "Error: hcli requires a 64-bit system. 32-bit systems are not supported."
    }

    # Additional check for ARM64 systems (which report as 64-bit but aren't x86_64)
    if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64" -or $env:PROCESSOR_ARCHITEW6432 -eq "ARM64") {
        throw "Error: ARM64 systems are not currently supported. Only x86_64 (64-bit Intel/AMD) systems are supported."
    }

    Write-Verbose "System validated as x86_64 compatible"
    return "x86_64"
}

function Get-InstallDirectory {
    <#
    .SYNOPSIS
    Determines the installation directory using Windows-appropriate standards and fallbacks
    #>
    [CmdletBinding()]
    param(
        [string]$ForceDir
    )

    Write-Verbose "Determining installation directory..."

    # If forced directory is specified, use it
    if ($ForceDir) {
        Write-Verbose "Using forced installation directory: $ForceDir"
        return $ForceDir
    }

    # Try XDG_BIN_HOME if set (for cross-platform compatibility)
    if ($env:XDG_BIN_HOME -and (Test-Path $env:XDG_BIN_HOME -PathType Container -ErrorAction SilentlyContinue)) {
        Write-Verbose "Using XDG_BIN_HOME: $env:XDG_BIN_HOME"
        return $env:XDG_BIN_HOME
    }

    # Windows-standard user binaries location (highest priority for Windows)
    # Use .NET method as $env: might not be available in all contexts
    $localAppData = [System.Environment]::GetEnvironmentVariable('LOCALAPPDATA')
    if (-not $localAppData) {
        $localAppData = $env:LOCALAPPDATA
    }

    if ($localAppData) {
        $windowsLocalBin = Join-Path $localAppData "Programs\hcli"
        Write-Verbose "Using Windows standard location: $windowsLocalBin"
        return $windowsLocalBin
    }

    # Fallback to .local/bin for compatibility with Unix-like environments on Windows
    $localBin = Join-Path $env:USERPROFILE ".local\bin"
    Write-Verbose "Using .local/bin fallback: $localBin"
    return $localBin
}

function Invoke-GitHubApiRequest {
    <#
    .SYNOPSIS
    Makes authenticated requests to GitHub API with proper error handling
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Uri,
        [string]$ErrorContext = "GitHub API request",
        [switch]$ReturnFullResponse
    )

    Write-Verbose "Making request to: $Uri"

    try {
        $headers = @{}
        if ($auth_token) {
            $headers["Authorization"] = "Bearer $auth_token"
            Write-Verbose "Using GitHub authentication token"
        }

        if ($headers.Count -gt 0) {
            $response = Invoke-RestMethod -Uri $Uri -Headers $headers -ErrorAction Stop
        } else {
            $response = Invoke-RestMethod -Uri $Uri -ErrorAction Stop
        }

        if ($ReturnFullResponse) {
            return $response
        }

        if (-not $response.tag_name) {
            throw "Tag name not found in GitHub releases API response"
        }

        # Remove 'v' prefix if present
        $version = $response.tag_name -replace '^v', ''
        Write-Verbose "Retrieved version: $version"
        return $version

    } catch [System.Net.WebException] {
        throw "Failed to ${ErrorContext} from ${Uri}. Check your internet connection and try again. Error: $_"
    } catch {
        throw "Failed to parse response from ${ErrorContext}: $_"
    }
}

function Get-LatestRelease {
    <#
    .SYNOPSIS
    Fetches the latest production release information from GitHub API (excluding dev releases)
    #>
    [CmdletBinding()]
    param(
        [string]$Repository
    )

    $releases_url = "$github_api_base/repos/$Repository/releases"
    Write-Verbose "Fetching latest production release from: $releases_url"

    $response = Invoke-GitHubApiRequest -Uri $releases_url -ErrorContext "fetch latest release information" -ReturnFullResponse
    
    # Filter out dev releases and get the first (latest) production release
    $productionRelease = $response | Where-Object { $_.tag_name -notmatch "dev" } | Select-Object -First 1
    
    if (-not $productionRelease -or -not $productionRelease.tag_name) {
        throw "No production releases found (excluding dev versions)"
    }
    
    # Remove 'v' prefix if present
    $version = $productionRelease.tag_name -replace '^v', ''
    Write-Verbose "Retrieved latest production version: $version"
    return $version
}

function Get-SpecificRelease {
    <#
    .SYNOPSIS
    Fetches information about a specific release from GitHub API
    #>
    [CmdletBinding()]
    param(
        [string]$Repository,
        [string]$TargetVersion
    )
    
    # Try with 'v' prefix first
    $releases_url = "$github_api_base/repos/$Repository/releases/tags/v$TargetVersion"
    Write-Verbose "Checking if version $TargetVersion exists: $releases_url"
    
    try {
        return Invoke-GitHubApiRequest -Uri $releases_url -ErrorContext "fetch specific release information"
    } catch {
        # Try without 'v' prefix if first attempt failed
        $releases_url = "$github_api_base/repos/$Repository/releases/tags/$TargetVersion"
        Write-Verbose "Retry without 'v' prefix: $releases_url"
        
        try {
            return Invoke-GitHubApiRequest -Uri $releases_url -ErrorContext "fetch specific release information"
        } catch {
            throw "Version $TargetVersion not found in GitHub releases. Available versions can be seen at: https://github.com/$Repository/releases"
        }
    }
}

function Get-AssetDownloadInfo {
    <#
    .SYNOPSIS
    Gets asset ID and constructs GitHub API download URL for the binary
    #>
    [CmdletBinding()]
    param(
        [string]$Repository,
        [string]$Version,
        [string]$Architecture
    )
    
    # Determine platform name and filename
    $platform_name = switch ($Architecture) {
        "x86_64" { "windows" }
        default { throw "Unsupported architecture for download: $Architecture" }
    }
    
    $filename = "$app_name-$platform_name-$Architecture-$Version.exe"
    $releases_url = "$github_api_base/repos/$Repository/releases/tags/v$Version"
    
    Write-Verbose "Fetching asset information from GitHub API"
    Write-Verbose "URL: $releases_url"
    Write-Verbose "Looking for asset: $filename"
    
    try {
        $response = Invoke-GitHubApiRequest -Uri $releases_url -ErrorContext "fetch release asset information" -ReturnFullResponse
        
        # Find the asset with matching filename
        $asset = $response.assets | Where-Object { $_.name -eq $filename } | Select-Object -First 1
        
        if (-not $asset -or -not $asset.id) {
            throw "Asset '$filename' not found in release v$Version. Available assets can be seen at: https://github.com/$Repository/releases/tag/v$Version"
        }
        
        # Return GitHub API asset download URL
        $asset_url = "$github_api_base/repos/$Repository/releases/assets/$($asset.id)"
        Write-Verbose "Found asset ID: $($asset.id)"
        Write-Verbose "Asset download URL: $asset_url"
        
        return $asset_url
        
    } catch {
        throw "Failed to get asset information: $_"
    }
}

function Download-Binary {
    <#
    .SYNOPSIS
    Downloads the hcli binary for the detected platform using GitHub API
    #>
    [CmdletBinding()]
    param(
        [string]$Repository,
        [string]$Version, 
        [string]$Architecture,
        [string]$DestinationPath
    )
    
    # Get asset download URL from GitHub API
    $download_url = Get-AssetDownloadInfo -Repository $Repository -Version $Version -Architecture $Architecture
    
    Write-Information "Downloading $app_name $Version ($Architecture)"
    Write-Verbose "  from: $download_url"
    Write-Verbose "  to: $DestinationPath"
    
    # Check if destination is a symbolic link and remove it first
    if (Test-Path $DestinationPath) {
        $existingFile = Get-Item $DestinationPath -ErrorAction SilentlyContinue
        if ($existingFile -and ($existingFile.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            Write-Verbose "Destination is a symbolic link, removing it first"
            Remove-Item $DestinationPath -Force
        }
    }
    
    try {
        # Prepare headers for authentication and asset download
        $headers = @{}
        if ($auth_token) {
            $headers["Authorization"] = "Bearer $auth_token"
            Write-Verbose "Added authorization header"
        }
        
        # Check if this is a GitHub API asset download URL
        if ($download_url -match "/repos/.*/releases/assets/\d+$") {
            # GitHub API asset download requires Accept: application/octet-stream header
            $headers["Accept"] = "application/octet-stream"
            Write-Verbose "Added Accept: application/octet-stream header for GitHub API asset download"
        }
        
        # Set TLS 1.2 for security
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        
        # Download using Invoke-WebRequest
        Write-Information "Downloading... (this may take a moment)"
        
        if ($headers.Count -gt 0) {
            $null = Invoke-WebRequest -Uri $download_url -OutFile $DestinationPath -Headers $headers -UseBasicParsing
        } else {
            $null = Invoke-WebRequest -Uri $download_url -OutFile $DestinationPath -UseBasicParsing
        }
        
        # Verify the file was downloaded
        if (-not (Test-Path $DestinationPath)) {
            throw "Download appeared to succeed but file not found at destination"
        }
        
        $fileSize = (Get-Item $DestinationPath).Length
        if ($fileSize -eq 0) {
            throw "Downloaded file is empty"
        }
        
        Write-Verbose "Successfully downloaded binary ($([math]::Round($fileSize/1MB, 2)) MB)"
        
    } catch [System.Net.WebException] {
        throw "Failed to download binary from $download_url. This may be a network error or the release may not be available for your platform. Error: $_"
    } catch {
        throw "Download failed: $_"
    }
}

function Send-EnvironmentChangeNotification {
    <#
    .SYNOPSIS
    Broadcasts environment variable changes to Windows applications
    .DESCRIPTION
    Uses Windows API to notify applications that environment variables have changed.
    Separated from PATH logic for better separation of concerns.
    #>
    [CmdletBinding()]
    param()
    
    Write-Verbose "Broadcasting environment variable change notification"
    
    try {
        # Define Windows API if not already defined
        if (-not ([System.Management.Automation.PSTypeName]'Win32.NativeMethods').Type) {
            Add-Type -Namespace Win32 -Name NativeMethods -MemberDefinition @'
                [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
                public static extern IntPtr SendMessageTimeout(
                    IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
                    uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
        }
        
        $HWND_BROADCAST = [IntPtr]0xffff
        $WM_SETTINGCHANGE = 0x1a
        $result = [UIntPtr]::Zero
        
        $broadcastResult = [Win32.NativeMethods]::SendMessageTimeout(
            $HWND_BROADCAST, $WM_SETTINGCHANGE, [UIntPtr]::Zero, 
            "Environment", 2, 30000, [ref]$result
        )
        
        Write-Verbose "Environment change notification sent (result: $broadcastResult)"
    } catch [System.ComponentModel.Win32Exception] {
        Write-Verbose "Windows API call failed for environment notification: $_"
        # Don't throw here - PATH update succeeded even if notification failed
    } catch [System.TypeLoadException] {
        Write-Verbose "Could not load Windows API types for environment notification: $_"
        # Don't throw here - PATH update succeeded even if notification failed
    } catch {
        Write-Verbose "Failed to send environment change notification: $_"
        # Don't throw here - PATH update succeeded even if notification failed
    }
}

function Add-ToUserPath {
    <#
    .SYNOPSIS
    Adds a directory to the user's PATH using the Windows registry
    .DESCRIPTION
    This is a more robust approach than environment variable modification
    as it persists across sessions and properly notifies the system.
    .OUTPUTS
    Returns $true if PATH was modified, $false if directory was already present
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Directory
    )
    
    Write-Verbose "Adding $Directory to user PATH via registry"
    
    $registryPath = 'HKCU:\Environment'
    
    try {
        # Get current PATH value (unexpanded)
        $currentPath = Get-ItemProperty -Path $registryPath -Name 'Path' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path
        if (-not $currentPath) {
            $currentPath = ''
        }
        
        # Split into directories, removing empty entries
        $currentDirectories = $currentPath -split ';' | Where-Object { $_.Trim() -ne '' }
        
        # Check if directory is already in PATH (case-insensitive)
        $directoryExists = $currentDirectories | Where-Object { $_.ToLower() -eq $Directory.ToLower() }
        if ($directoryExists) {
            Write-Verbose "Directory already in PATH"
            return $false
        }
        
        Write-Verbose "Adding directory to PATH registry entry"
        
        # Add new directory to the beginning of PATH
        $newDirectories = @($Directory) + $currentDirectories
        $newPath = $newDirectories -join ';'
        
        # Update registry (REG_EXPAND_SZ type to support environment variables)
        Set-ItemProperty -Path $registryPath -Name 'Path' -Value $newPath -Type ExpandString
        
        # Broadcast system message to refresh environment variables
        Send-EnvironmentChangeNotification
        
        Write-Verbose "Successfully updated PATH registry and broadcast change notification"
        return $true
        
    } catch {
        throw "Failed to update PATH in registry: $_"
    }
}

function Add-CiPath {
    <#
    .SYNOPSIS
    Adds the install directory to CI environment PATH if detected
    #>
    [CmdletBinding()]
    param(
        [string]$Directory
    )
    
    # GitHub Actions integration
    if ($env:GITHUB_PATH) {
        Write-Verbose "Detected GitHub Actions environment, adding to GITHUB_PATH"
        Add-Content -Path $env:GITHUB_PATH -Value $Directory -Encoding UTF8
    }
    
    # Add other CI integrations as needed (Azure DevOps, etc.)
}

#endregion

#region Output Formatting Functions

function Write-Title {
    <#
    .SYNOPSIS
    Writes a title with optional description, matching Unix script formatting
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Title,
        [string]$Description = ""
    )
    
    Write-Information ""
    Write-Information $Title
    if ($Description) {
        Write-Information $Description
    }
}

function Test-ExistingInstallation {
    <#
    .SYNOPSIS
    Checks for existing hcli installation and returns path if found
    #>
    [CmdletBinding()]
    param()
    
    try {
        $existingPath = Get-Command "hcli" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
        if ($existingPath) {
            return $existingPath
        }
    } catch {
        Write-Verbose "No existing hcli installation found"
    }
    
    return $null
}

function Get-ExistingVersion {
    <#
    .SYNOPSIS
    Gets version of existing hcli installation
    #>
    [CmdletBinding()]
    param(
        [string]$ExistingPath
    )
    
    if ($ExistingPath) {
        try {
            $versionOutput = & $ExistingPath --version 2>$null
            if ($versionOutput) {
                return $versionOutput
            }
        } catch {
            Write-Verbose "Could not determine existing version"
        }
    }
    
    return "unknown"
}

function Confirm-Installation {
    <#
    .SYNOPSIS
    Prompts user for installation confirmation, matching Unix script behavior
    #>
    [CmdletBinding()]
    param(
        [string]$ExistingPath,
        [string]$ExistingVersion,
        [string]$TargetVersion,
        [string]$Platform
    )
    
    if ($ExistingPath) {
        Write-Information ""
        Write-Information "Found existing hcli installation:"
        Write-Information "  Location: $ExistingPath"
        Write-Information "  Version:  $ExistingVersion"
        Write-Information ""
        Write-Information "Installing hcli version $TargetVersion for $Platform"
        Write-Information "Will replace the existing installation at: $(Split-Path $ExistingPath -Parent)"
    } else {
        Write-Information "Installing hcli version $TargetVersion for $Platform"
    }
    
    # Interactive confirmation unless in CI or non-interactive mode
    if ([Environment]::UserInteractive -and -not $env:CI) {
        Write-Information ""
        if ($ExistingPath) {
            $prompt = "Do you want to replace the existing installation? (yes/no): "
        } else {
            $prompt = "Do you want to continue? (yes/no): "
        }
        
        $response = Read-Host $prompt
        $response = if ([string]::IsNullOrWhiteSpace($response)) { "yes" } else { $response }
        
        if ($response -notmatch '^(y|yes)$') {
            Write-Information ""
            Write-Information "Installation Aborted"
            exit 0
        }
    }
}

function Test-PathShadowing {
    <#
    .SYNOPSIS
    Checks if installing hcli.exe will shadow existing executables in PATH
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$InstallDir
    )
    
    Write-Verbose "Checking for potential PATH shadowing issues..."
    
    try {
        # Get current PATH (both user and system)
        $userPath = [System.Environment]::GetEnvironmentVariable('Path', 'User')
        $systemPath = [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
        $currentPath = "$userPath;$systemPath"
        
        # Split and clean PATH directories
        $pathDirs = $currentPath -split ';' | Where-Object { 
            $_.Trim() -ne '' -and $_.ToLower() -ne $InstallDir.ToLower()
        }
        
        # Look for existing hcli.exe in PATH
        $existingHcli = @()
        foreach ($dir in $pathDirs) {
            if (Test-Path $dir -PathType Container -ErrorAction SilentlyContinue) {
                $hcliPath = Join-Path $dir "hcli.exe"
                if (Test-Path $hcliPath -PathType Leaf -ErrorAction SilentlyContinue) {
                    $existingHcli += $hcliPath
                }
            }
        }
        
        if ($existingHcli.Count -gt 0) {
            Write-Warning @"
The following commands are shadowed by other commands in your PATH: hcli

Installing to: $InstallDir
Will shadow existing: $($existingHcli -join ', ')

The new installation will take precedence. Existing installations may become inaccessible.
"@
            
            # In interactive mode, prompt user
            if ([Environment]::UserInteractive -and -not $env:CI) {
                $response = Read-Host "Continue with installation? (y/N)"
                if ($response -notmatch '^[Yy]') {
                    throw "Installation cancelled by user due to PATH shadowing concerns"
                }
            } else {
                Write-Information "Continuing installation (non-interactive mode)"
            }
        } else {
            Write-Verbose "No PATH shadowing detected"
        }
        
    } catch [System.UnauthorizedAccessException] {
        Write-Warning "Cannot check for PATH shadowing due to insufficient permissions. Installation will continue."
        Write-Verbose "PATH shadowing check failed: $_"
    } catch [System.Security.SecurityException] {
        Write-Warning "Cannot check for PATH shadowing due to security restrictions. Installation will continue."
        Write-Verbose "PATH shadowing check failed: $_"
    } catch {
        Write-Verbose "Failed to check PATH shadowing: $_"
        # Don't fail installation on other shadowing check errors
    }
}

#endregion

#region Main Installation Function


function Install-Hcli {
    <#
    .SYNOPSIS
    Main installation function that coordinates the entire process
    #>
    [CmdletBinding()]
    param()
    
    try {
        # Show help if requested
        if ($Help) {
            Get-Help $PSCommandPath -Detailed
            return
        }
        
        # Initialize and validate environment
        Initialize-Environment
        
        # Detect architecture
        $architecture = Get-Architecture
        Write-Verbose "Detected platform: windows:$architecture"
        
        # Get release version (latest or specific)
        if ($Version) {
            Write-Title "Fetching Specific Release" " version $Version"
            $version = Get-SpecificRelease -Repository $github_repo -TargetVersion $Version
        } else {
            Write-Title "Fetching Latest Production Release"
            $version = Get-LatestRelease -Repository $github_repo
        }
        
        # Check for existing installation
        $existingPath = Test-ExistingInstallation
        $existingVersion = Get-ExistingVersion -ExistingPath $existingPath
        
        # Determine installation directory (use existing location if found and not forced)
        if ($existingPath -and -not $InstallDir) {
            $install_dir = Split-Path $existingPath -Parent
        } else {
            $install_dir = Get-InstallDirectory -ForceDir $InstallDir
        }
        
        # Show installation info and get confirmation
        $platform = "windows:$architecture"
        Confirm-Installation -ExistingPath $existingPath -ExistingVersion $existingVersion -TargetVersion $version -Platform $platform
        
        # Create installation directory if it doesn't exist
        if (-not (Test-Path $install_dir)) {
            Write-Information "creating installation directory: $install_dir"
            $null = New-Item -Path $install_dir -ItemType Directory -Force
        }
        
        # Download binary
        $binary_path = Join-Path $install_dir "$app_name.exe"
        Write-Title "Downloading Binary" " from GitHub API (asset download)"
        Download-Binary -Repository $github_repo -Version $version -Architecture $architecture -DestinationPath $binary_path
        
        # Install binary
        Write-Title "Installing Binary" " $install_dir\$app_name.exe"
        
        # Check for PATH shadowing before modifying PATH
        if (-not $NoModifyPath) {
            Test-PathShadowing -InstallDir $install_dir
        }
        
        # Add to PATH if requested
        $pathModified = $false
        if (-not $NoModifyPath) {
            Write-Verbose "Configuring PATH..."
            
            # Add to CI environment if detected
            Add-CiPath -Directory $install_dir
            
            # Add to user PATH
            $pathModified = Add-ToUserPath -Directory $install_dir
            
            if ($pathModified) {
                Write-Title "PATH Setup" "To add $install_dir to your PATH, restart your shell or run:"
                Write-Information "    `$env:Path = [System.Environment]::GetEnvironmentVariable('Path','User') + ';' + [System.Environment]::GetEnvironmentVariable('Path','Machine')"
            }
        }
        
        # Show installation summary
        Write-Title "Installation Complete" " Run $app_name --help for more information"
        if ($NoModifyPath) {
            Write-Title "Note" "Add $install_dir to your PATH to run $app_name from anywhere"
        }
        Write-Information ""
        
    } catch {
        Write-Error $_.Exception.Message
        exit 1
    }
}

#endregion

# Execute main installation
Install-Hcli