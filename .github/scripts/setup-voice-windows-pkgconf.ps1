# Download and extract only the pinned native pkgconf MSI for local Windows GNU builds.
$ErrorActionPreference = "Stop"

$Root = Join-Path $env:RUNNER_TEMP "voice-windows-pkgconf"
if (Test-Path $Root) { throw "pkgconf repository destination must be fresh: $Root" }
$Image = Join-Path $Root "pkgconf-image"
$Evidence = Join-Path $Root "evidence"
New-Item -ItemType Directory -Path $Image, $Evidence | Out-Null

$Msi = Join-Path $Root "pkgconf-x64-3.0.6.msi"
$ExpectedSha256 = "5604cf25ef38bb6a09520cff25ae9f0ecd8c2443053b15e40df4ad1eae0e4405"
$Uri = "https://github.com/pkgconf/pkgconf/releases/download/pkgconf-3.0.6/pkgconf-x64-3.0.6.msi"
Invoke-WebRequest -Uri $Uri -OutFile $Msi
$ActualSha256 = (Get-FileHash -Path $Msi -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualSha256 -ne $ExpectedSha256) { throw "pkgconf MSI digest mismatch" }

$Extract = Start-Process msiexec.exe -Wait -PassThru -ArgumentList @(
    "/a", "`"$Msi`"", "/qn", "/norestart",
    "TARGETDIR=`"$Image`"", "/L*v", "`"$(Join-Path $Evidence 'pkgconf-extract.log')`""
)
if ($Extract.ExitCode -ne 0) { throw "pkgconf MSI extraction failed: $($Extract.ExitCode)" }

$Candidates = @(Get-ChildItem $Image -Filter pkgconf.exe -File -Recurse)
if ($Candidates.Count -ne 1) { throw "Expected exactly one native pkgconf executable" }
$Native = Join-Path $Image "native"
if (Test-Path $Native) { throw "Native pkgconf destination must be fresh" }
Move-Item -LiteralPath $Candidates[0].Directory.FullName -Destination $Native
$PkgConfig = Join-Path $Native "pkgconf.exe"
$Version = & $PkgConfig --version
if ($LASTEXITCODE -ne 0 -or $Version -ne "3.0.6") { throw "Unexpected native pkgconf version" }

& python (Join-Path $PSScriptRoot "voice_windows_pkgconf.py") --root $Root --target x86_64-pc-windows-gnullvm --version $Version
if ($LASTEXITCODE -ne 0) { throw "Cannot declare the pkgconf-only Bazel repository" }
$Repository = $Root.Replace("\", "/")
"VOICE_WINDOWS_PKGCONF_REPOSITORY=$Repository" | Out-File $env:GITHUB_ENV -Encoding utf8 -Append
