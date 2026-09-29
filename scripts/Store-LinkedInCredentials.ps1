<#
.SYNOPSIS
    Stores LinkedIn OAuth 2.0 credentials in Windows Credential Manager
    for the Dawnlist app (service: dawnlist-linkedin).

.DESCRIPTION
    Prompts securely for Client ID and Client Secret from the LinkedIn
    developer app (ID 266550517), then writes them via Python keyring so
    the format matches what credentials.read() expects at runtime.

.NOTES
    Run from the dawnlist repo root so the correct Python environment is used.
    The Client Secret is entered as a SecureString and never echoed.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Collect credentials ──────────────────────────────────────────────
$clientId = Read-Host 'LinkedIn App Client ID'
if (-not $clientId.Trim()) {
    Write-Error 'Client ID cannot be empty.'
    return
}

$clientSecretSecure = Read-Host 'LinkedIn App Client Secret' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($clientSecretSecure)
try {
    $clientSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if (-not $clientSecret.Trim()) {
    Write-Error 'Client Secret cannot be empty.'
    return
}

# ── Write via Python keyring (format-compatible with the app) ────────
$pyScript = @"
import sys, json, keyring

creds = json.loads(sys.stdin.read())
keyring.set_password('dawnlist-linkedin', 'client-id', creds['id'])
keyring.set_password('dawnlist-linkedin', 'client-secret', creds['secret'])

# Verify round-trip
v_id = keyring.get_password('dawnlist-linkedin', 'client-id')
v_sec = keyring.get_password('dawnlist-linkedin', 'client-secret')
if v_id != creds['id'] or v_sec != creds['secret']:
    print('FAIL: round-trip verification failed', file=sys.stderr)
    sys.exit(1)
print('OK')
"@

$payload = @{ id = $clientId; secret = $clientSecret } | ConvertTo-Json -Compress

try {
    $result = $payload | python -c $pyScript 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Python keyring write failed: $result"
        return
    }
    Write-Host ''
    Write-Host 'Stored in Windows Credential Manager:' -ForegroundColor Green
    Write-Host "  Service:  dawnlist-linkedin"
    Write-Host "  Accounts: client-id, client-secret"
    Write-Host ''
    Write-Host "The app reads these via credentials.read('dawnlist-linkedin', 'client-id')" -ForegroundColor DarkGray
} finally {
    # Clear sensitive variables from the session
    Remove-Variable -Name clientSecret -ErrorAction SilentlyContinue
    Remove-Variable -Name payload -ErrorAction SilentlyContinue
    [GC]::Collect()
}
