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
    [ValidatePattern('^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$')]
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
    [ValidatePattern('^v?[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]*)?$')]
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
        $script:github_repo = $GitHubRepo
        Write-Verbose "Using GitHub repo from parameter: $GitHubRepo"
    } elseif ($env:HCLI_GITHUB_REPO) {
        $script:github_repo = $env:HCLI_GITHUB_REPO
        Write-Verbose "Using GitHub repo from environment: $env:HCLI_GITHUB_REPO"
    }
    
    # Installation directory configuration  
    if ($env:HCLI_INSTALL_DIR) {
        $script:InstallDir = $env:HCLI_INSTALL_DIR
        Write-Verbose "Using install dir from environment: $env:HCLI_INSTALL_DIR"
    }
    
    # Version configuration
    if ($env:HCLI_VERSION) {
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
    $windowsLocalBin = Join-Path $env:LOCALAPPDATA "Programs\hcli"
    if ($env:LOCALAPPDATA) {
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
        [string]$ErrorContext = "GitHub API request"
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
    
    try {
        $headers = @{}
        if ($auth_token) {
            $headers["Authorization"] = "Bearer $auth_token"
            Write-Verbose "Using GitHub authentication token"
        }
        
        if ($headers.Count -gt 0) {
            $response = Invoke-RestMethod -Uri $releases_url -Headers $headers -ErrorAction Stop
        } else {
            $response = Invoke-RestMethod -Uri $releases_url -ErrorAction Stop
        }
        
        # Filter out dev releases and get the first (latest) production release
        $productionRelease = $response | Where-Object { $_.tag_name -notmatch "dev" } | Select-Object -First 1
        
        if (-not $productionRelease -or -not $productionRelease.tag_name) {
            throw "No production releases found (excluding dev versions)"
        }
        
        # Remove 'v' prefix if present
        $version = $productionRelease.tag_name -replace '^v', ''
        Write-Verbose "Retrieved latest production version: $version"
        return $version
        
    } catch [System.Net.WebException] {
        throw "Failed to fetch latest release information from ${releases_url}. Check your internet connection and try again. Error: $_"
    } catch {
        throw "Failed to parse response from fetch latest release information: $_"
    }
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

function Download-Binary {
    <#
    .SYNOPSIS
    Downloads the hcli binary for the detected platform
    #>
    [CmdletBinding()]
    param(
        [string]$Repository,
        [string]$Version, 
        [string]$Architecture,
        [string]$DestinationPath
    )
    
    # Determine platform name for download URL
    $platform_name = switch ($Architecture) {
        "x86_64" { "windows" }
        default { throw "Unsupported architecture for download: $Architecture" }
    }
    
    # Construct GitHub releases download URL
    $filename = "$app_name-$platform_name-$Architecture-$Version.exe"
    $download_url = "https://github.com/$Repository/releases/download/v$Version/$filename"
    Write-Information "Downloading $app_name $Version ($Architecture)"
    Write-Verbose "  from: $download_url"
    Write-Verbose "  to: $DestinationPath"
    
    # Create webclient with proper configuration
    $webClient = New-Object System.Net.WebClient
    
    try {
        # Add authentication if available
        if ($auth_token) {
            $webClient.Headers["Authorization"] = "Bearer $auth_token"
            Write-Verbose "Added authorization header"
        }
        
        # Set TLS 1.2 for security
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        
        # Download with progress indication using Invoke-WebRequest for better UX
        try {
            $headers = @{}
            if ($auth_token) {
                $headers["Authorization"] = "Bearer $auth_token"
            }
            
            Write-Information "Downloading... (this may take a moment)"
            if ($headers.Count -gt 0) {
                Invoke-WebRequest -Uri $download_url -OutFile $DestinationPath -Headers $headers -UseBasicParsing
            } else {
                Invoke-WebRequest -Uri $download_url -OutFile $DestinationPath -UseBasicParsing
            }
        } catch {
            # Fallback to WebClient if Invoke-WebRequest fails
            Write-Verbose "Invoke-WebRequest failed, falling back to WebClient"
            $webClient.DownloadFile($download_url, $DestinationPath)
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
    } finally {
        # Ensure WebClient is always disposed
        if ($webClient) {
            $webClient.Dispose()
        }
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

#region Main Installation Function

function Show-InstallationSummary {
    <#
    .SYNOPSIS
    Displays installation completion message and next steps
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Version,
        [Parameter(Mandatory)]
        [string]$InstallDir,
        [Parameter(Mandatory)]
        [string]$BinaryPath,
        [bool]$PathModified = $false,
        [bool]$NoModifyPath = $false
    )
    
    if (-not $NoModifyPath) {
        if ($PathModified) {
            Write-Information ""
            Write-Information "Added $InstallDir to your user PATH."
            Write-Information "You may need to restart your terminal or run:"
            Write-Information "    `$env:Path = [System.Environment]::GetEnvironmentVariable('Path','User') + ';' + [System.Environment]::GetEnvironmentVariable('Path','Machine')"
        }
    } else {
        Write-Information ""
        Write-Information "Installation complete! To use $app_name, either:"
        Write-Information "  1. Add $InstallDir to your PATH, or"  
        Write-Information "  2. Run the full path: $BinaryPath"
    }
    
    Write-Information ""
    Write-Information "$app_name $Version installed successfully!"
    Write-Information "Run '$app_name --help' to get started."
}

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
        Write-Information "Detected architecture: $architecture"
        
        # Get release version (latest or specific)
        if ($Version) {
            Write-Information "Fetching specific version: $Version"
            $version = Get-SpecificRelease -Repository $github_repo -TargetVersion $Version
            Write-Information "Target version: $version"
        } else {
            Write-Information "Fetching latest production release..."
            $version = Get-LatestRelease -Repository $github_repo
            Write-Information "Latest production version: $version"
        }
        
        # Determine installation directory
        $install_dir = Get-InstallDirectory -ForceDir $InstallDir
        Write-Information "Installing to: $install_dir"
        
        # Create installation directory if it doesn't exist
        if (-not (Test-Path $install_dir)) {
            Write-Verbose "Creating installation directory"
            $null = New-Item -Path $install_dir -ItemType Directory -Force
        }
        
        # Download binary
        $binary_path = Join-Path $install_dir "$app_name.exe"
        Download-Binary -Repository $github_repo -Version $version -Architecture $architecture -DestinationPath $binary_path
        Write-Information "  $app_name.exe"
        
        # Add to PATH if requested
        $pathModified = $false
        if (-not $NoModifyPath) {
            Write-Verbose "Configuring PATH..."
            
            # Add to CI environment if detected
            Add-CiPath -Directory $install_dir
            
            # Add to user PATH
            $pathModified = Add-ToUserPath -Directory $install_dir
        }
        
        # Show installation summary
        Show-InstallationSummary -Version $version -InstallDir $install_dir -BinaryPath $binary_path -PathModified $pathModified -NoModifyPath $NoModifyPath
        
    } catch {
        Write-Error $_.Exception.Message
        exit 1
    }
}

#endregion

# Execute main installation
Install-Hcli