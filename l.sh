<#
.SYNOPSIS
    This script installs 'yourprogram' for Windows by downloading the latest
    release from GitLab.
.DESCRIPTION
    It handles detection of the latest version, download, extraction, and
    permanently adding the program to the user's PATH.
.EXAMPLE
    iex (irm 'https://gitlab.com/your-user/your-repo/-/raw/main/install.ps1')
#>

# Stop script on first error
$ErrorActionPreference = 'Stop'

# --- SCRIPT CONFIGURATION ---
# The name of your program's executable.
$programName = "yourprogram"
# The GitLab repository path in the format "username/repo".
$gitlabRepo = "your-user/your-repo"
# ---


function Get-Latest-Version {
    param (
        [string]$RepoPath
    )
    $apiUrl = "https://gitlab.com/api/v4/projects/$($RepoPath.Replace('/', '%2F'))/releases"
    try {
        Write-Verbose "Querying GitLab API: $apiUrl"
        $releases = Invoke-RestMethod -Uri $apiUrl
        if ($releases) {
            return $releases[0].tag_name
        } else {
            throw "No releases found."
        }
    }
    catch {
        throw "Failed to get latest version from GitLab: $_"
    }
}

# --- MAIN LOGIC ---
try {
    # 1. Configuration and Pre-checks
    $installDir = Join-Path $HOME ".$programName"
    
    Write-Host "Running pre-installation checks..."
    if (-not (Test-Path -Path $installDir)) {
        New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    }
    # Test write permissions
    $tempFile = Join-Path $installDir "tmp-write-test.tmp"
    Set-Content -Path $tempFile -Value "test"; Remove-Item -Path $tempFile

    # 2. Get latest version and construct URL
    $latestVersion = Get-Latest-Version -RepoPath $gitlabRepo
    # Example binary name: yourprogram-v1.0.0-windows-amd64.zip
    $zipFileName = "$($programName)-$($latestVersion)-windows-amd64.zip"
    $downloadUrl = "https://gitlab.com/$gitlabRepo/-/releases/$latestVersion/downloads/$zipFileName"
    $zipFilePath = Join-Path $installDir $zipFileName

    Write-Host "Installing $($programName) v$($latestVersion) to $($installDir)..."

    # 3. Download and Extract
    Write-Host "Downloading from $downloadUrl..."
    Invoke-RestMethod -Uri $downloadUrl -OutFile $zipFilePath
    
    Write-Host "Extracting archive..."
    Expand-Archive -Path $zipFilePath -DestinationPath $installDir -Force
    
    # 4. Add to user's PATH environment variable
    Write-Host "Adding to PATH..."
    $currentUserPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentUserPath -notlike "*$installDir*") {
        $newPath = $currentUserPath + ";" + $installDir
        [System.Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Host "PATH updated successfully."
    } else {
        Write-Host "PATH entry already exists."
    }

    # 5. Cleanup
    Write-Host "Cleaning up installation files..."
    Remove-Item $zipFilePath

    # 6. Final Message
    Write-Host ""
    Write-Host -ForegroundColor Green "$programName was installed successfully!"
    Write-Host -ForegroundColor Yellow "IMPORTANT: You must open a new terminal for the PATH changes to take effect."
    Write-Host "You can then run the program by typing: $programName"

}
catch {
    Write-Error "Installation failed: $_"
    exit 1
}