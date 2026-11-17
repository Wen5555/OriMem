<#
OriMem one-line installer for Windows.

    irm https://raw.githubusercontent.com/Wen5555/OriMem/main/scripts/install.ps1 | iex

Environment overrides:
    ORIMEM_SOURCE      package source (default: orimem-mcp from PyPI, falling back to the GitHub main archive)
    ORIMEM_INIT_ARGS   arguments for `orimem init` (default: interactive); "skip" skips init
    ORIMEM_MIRROR      "cn" forces China mirrors, "none" disables auto-detection
    ORIMEM_NO_MODIFY_PATH=1  do not add uv's tool directory to the user PATH
#>
$ErrorActionPreference = 'Stop'
$Fallback = 'https://github.com/Wen5555/OriMem/archive/refs/heads/main.zip'
$Source = if ($env:ORIMEM_SOURCE) { $env:ORIMEM_SOURCE } else { 'orimem-mcp' }

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

function Test-Url($url) {
    try { Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 5 -UseBasicParsing | Out-Null; return $true } catch { return $false }
}

# 1. Mirrors for networks where PyPI / GitHub are slow or blocked.
$useCn = $env:ORIMEM_MIRROR -eq 'cn'
if (-not $env:ORIMEM_MIRROR) { $useCn = -not (Test-Url 'https://pypi.org/simple/pip/') }
if ($useCn) {
    Step 'Using China mirrors (Tsinghua PyPI, npmmirror Python builds, hf-mirror.com)'
    $env:UV_DEFAULT_INDEX = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    $env:UV_PYTHON_INSTALL_MIRROR = 'https://registry.npmmirror.com/-/binary/python-build-standalone'
    $env:HF_ENDPOINT = 'https://hf-mirror.com'
}

# 2. uv (manages Python and an isolated tool environment).
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Step 'Installing uv'
    $installed = $false
    if (-not $useCn) {
        try { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression; $installed = $true } catch { }
    }
    if (-not $installed) {
        $py = Get-Command python -ErrorAction SilentlyContinue
        if (-not $py) { throw 'Could not install uv. Install it from https://docs.astral.sh/uv/ and re-run.' }
        $indexArgs = if ($useCn) { @('-i', $env:UV_DEFAULT_INDEX) } else { @() }
        & python -m pip install --user --upgrade uv @indexArgs
    }
    $env:Path = "$env:USERPROFILE\.local\bin;$env:APPDATA\Python\Scripts;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        $uvExe = Get-ChildItem "$env:APPDATA\Python" -Recurse -Filter uv.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($uvExe) { $env:Path = "$($uvExe.DirectoryName);$env:Path" } else { throw 'uv was installed but is not on PATH; open a new terminal and re-run.' }
    }
}

# 3. OriMem itself.
Step "Installing OriMem from $Source"
uv tool install --force --python 3.12 $Source
if ($LASTEXITCODE -ne 0 -and -not $env:ORIMEM_SOURCE) {
    Step "PyPI install failed; installing from $Fallback"
    uv tool install --force --python 3.12 $Fallback
}
if ($LASTEXITCODE -ne 0) { throw 'uv tool install failed' }
if (-not $env:ORIMEM_NO_MODIFY_PATH) { uv tool update-shell | Out-Null }
$toolBin = (uv tool dir --bin).Trim()
$env:Path = "$toolBin;$env:Path"

# 4. First-time setup.
if ($env:ORIMEM_INIT_ARGS -eq 'skip') {
    Step 'Skipping init (run `orimem init` later)'
} else {
    Step 'Running orimem init'
    $initArgs = if ($env:ORIMEM_INIT_ARGS) { $env:ORIMEM_INIT_ARGS -split ' ' } else { @() }
    & orimem init @initArgs
}

Write-Host ''
Write-Host 'OriMem is installed. Restart Claude Code / Codex / DeepSeek Harness to load it.' -ForegroundColor Green
Write-Host '  orimem doctor        check the setup'
Write-Host '  uv tool upgrade orimem-mcp   update later'
