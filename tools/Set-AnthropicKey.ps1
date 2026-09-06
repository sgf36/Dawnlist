<#
.SYNOPSIS
    Store the Dawnlist Anthropic API key, and verify it before storing it.

.DESCRIPTION
    Prompts for the key, checks it against the live API, and only then writes it
    to Windows Credential Manager where the app reads it.

    The key is NEVER taken as a parameter, and never appears on a command line.
    A parameter would land in PowerShell's history file, in the process
    arguments, and in any transcript logging — which is how a credential ends up
    somewhere it cannot be recalled from. It is read with -AsSecureString, held
    as a SecureString, and handed to the storage step over stdin.

    Storage goes through Python's `keyring` rather than `cmdkey`, deliberately:
    the app reads the key with `keyring.get_password`, and having the writer and
    the reader be the same library is the only way to be sure the target name
    and encoding match. A credential stored in a subtly different format is
    invisible to the app and looks exactly like "no key configured".

.PARAMETER Service
    Credential Manager service name. Defaults to the one the desktop app reads.

.PARAMETER Account
    Credential Manager account name. Defaults to 'api-key'.

.PARAMETER SkipVerify
    Store without calling the API first. Not recommended: an unverified key
    fails later, inside a run, where it looks like a feed problem.

.PARAMETER Force
    Overwrite an existing stored key without asking.

.EXAMPLE
    .\tools\Set-AnthropicKey.ps1

.EXAMPLE
    # The managed-tier proxy key, kept apart from the desktop app's own key.
    .\tools\Set-AnthropicKey.ps1 -Service dawnlist-anthropic-managed
#>
[CmdletBinding()]
param(
    [string]$Service = 'dawnlist-anthropic',
    [string]$Account = 'api-key',
    [switch]$SkipVerify,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    Write-Error "No Python found. Expected $repoRoot\.venv\Scripts\python.exe"
}

# Fail early and clearly rather than at the storage step.
& $python -c "import keyring" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "The 'keyring' package is not installed for $python. Run: & '$python' -m pip install keyring"
}

Write-Host ""
Write-Host "Dawnlist — store an Anthropic API key" -ForegroundColor Cyan
Write-Host "  service : $Service"
Write-Host "  account : $Account"
Write-Host ""

# --- existing key ---------------------------------------------------------
$existing = & $python -c "import keyring,sys; v=keyring.get_password(sys.argv[1],sys.argv[2]); print('yes' if v else 'no')" $Service $Account
if ($existing -eq 'yes' -and -not $Force) {
    $answer = Read-Host "A key is already stored here. Replace it? (y/N)"
    if ($answer -notmatch '^[Yy]') {
        Write-Host "Left unchanged." -ForegroundColor Yellow
        exit 0
    }
}

# --- prompt ---------------------------------------------------------------
Write-Host "Paste the key. It will not be echoed." -ForegroundColor Yellow
$secure = Read-Host "Anthropic API key" -AsSecureString
if ($secure.Length -eq 0) { Write-Error "No key entered." }

# Converted as late as possible and cleared straight after.
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

$plain = $plain.Trim()

# A shape check, not a substitute for the live check below. Catches the two
# common paste errors — a truncated copy, and copying the key's NAME from the
# console instead of its value.
if ($plain -notmatch '^sk-ant-') {
    Write-Host ""
    Write-Warning "That does not start with 'sk-ant-'. Anthropic keys do."
    Write-Warning "If you copied a label or a key name rather than the key itself, stop and re-copy."
    $answer = Read-Host "Continue anyway? (y/N)"
    if ($answer -notmatch '^[Yy]') { Write-Host "Nothing stored." -ForegroundColor Yellow; exit 1 }
}

# --- verify BEFORE storing ------------------------------------------------
# An unverified key fails later, inside a run, where it reads as a feed
# problem rather than a credential problem. models.list is a cheap GET and
# costs nothing.
if (-not $SkipVerify) {
    Write-Host ""
    Write-Host "Checking the key against the API..." -NoNewline

    $verifier = @'
import sys
key = sys.stdin.readline().strip()
try:
    import anthropic
except ImportError:
    print("SKIP no-sdk"); raise SystemExit(0)
try:
    client = anthropic.Anthropic(api_key=key)
    models = [m.id for m in client.models.list(limit=5)]
    print("OK " + ",".join(models[:3]))
except Exception as exc:
    print(f"FAIL {type(exc).__name__}: {str(exc)[:160]}")
'@

    $result = $plain | & $python -c $verifier
    Write-Host ""

    if ($result -like 'FAIL*') {
        Write-Host $result -ForegroundColor Red
        Write-Error "The key was rejected. Nothing has been stored."
    } elseif ($result -like 'SKIP*') {
        Write-Warning "The 'anthropic' package is not installed, so the key could not be checked."
        Write-Warning "Storing it unverified."
    } else {
        Write-Host "  verified — $($result.Substring(3))" -ForegroundColor Green
    }
}

# --- store ----------------------------------------------------------------
$writer = @'
import sys
key = sys.stdin.readline().strip()
import keyring
keyring.set_password(sys.argv[1], sys.argv[2], key)
back = keyring.get_password(sys.argv[1], sys.argv[2])
print("STORED" if back == key else "MISMATCH")
'@

$stored = $plain | & $python -c $writer $Service $Account
$plain = $null
[GC]::Collect()

if ($stored -ne 'STORED') {
    Write-Error "Wrote the credential but read back something different ($stored). Do not assume it is set."
}

Write-Host ""
Write-Host "Stored and read back successfully." -ForegroundColor Green
Write-Host ""
Write-Host "Confirm the app can see it:" -ForegroundColor Cyan
Write-Host "  & '$python' -m app.main --doctor"
Write-Host ""
Write-Host "For the managed-tier Worker, the key goes to Cloudflare instead:" -ForegroundColor Cyan
Write-Host "  npx wrangler secret put ANTHROPIC_API_KEY"
Write-Host ""
Write-Host "  Type that command LITERALLY. It takes the secret's NAME; the value" -ForegroundColor Yellow
Write-Host "  goes at the interactive prompt only. Putting the value in the command" -ForegroundColor Yellow
Write-Host "  creates a secret whose NAME is your key and leaves the real one unset —" -ForegroundColor Yellow
Write-Host "  that exact mistake cost four broken deploys on the Easy-Post webhook." -ForegroundColor Yellow
Write-Host ""
