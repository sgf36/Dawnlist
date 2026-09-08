<#
.SYNOPSIS
    Show, replace or remove a Dawnlist credential in Windows Credential Manager.

.DESCRIPTION
    Dawnlist keeps three separate credentials, and they are easy to confuse
    because two of them are called "API key" in ordinary speech:

      dawnlist-anthropic / api-key   Your ANTHROPIC key (sk-ant-...). Pays for
                                     reading postings and drafting messages.
                                     Billed to you by Anthropic directly.

      dawnlist-feed / api-key        A TheirStack key. The DEVELOPER path only
                                     — a customer should never hold one, and
                                     setting it does nothing if a licence is
                                     present, because the licence wins.

      dawnlist-licence / key         Your Dawnlist licence (DAWN-...). This is
                                     what routes the feed through the metered,
                                     capped managed service.

    Putting the right key under the wrong name is the common mistake, and it
    fails confusingly: an Anthropic key stored as the feed key produces an
    authentication error from a service that was never going to accept it.

.NOTES
    THE KEY IS NEVER PASSED ON A COMMAND LINE. It is read with -AsSecureString
    so it is not echoed and does not enter PowerShell history, then handed to
    Python over STDIN — command-line arguments are visible to any other process
    on the machine, and stdin is not.

    This script only ever prints the LENGTH and the first few characters of a
    stored value, never the value itself.

.EXAMPLE
    .\tools\Set-DawnlistCredential.ps1
    Shows what is stored and prompts for what to change.

.EXAMPLE
    .\tools\Set-DawnlistCredential.ps1 -Name anthropic
    Goes straight to replacing the Anthropic key.

.EXAMPLE
    .\tools\Set-DawnlistCredential.ps1 -Name feed -Remove
    Deletes the developer feed key.
#>
[CmdletBinding()]
param(
    [ValidateSet('anthropic', 'feed', 'licence')]
    [string]$Name,

    [switch]$Remove,

    # Skip the live check after writing an Anthropic key. The check costs a
    # single tiny request; skip it only if you are offline.
    [switch]$NoVerify
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "Python not found at $python. Run this from the Dawnlist repository."
}

$creds = @{
    anthropic = @{ Service = 'dawnlist-anthropic'; Account = 'api-key'
                   Label = 'Anthropic API key'; Expect = 'sk-ant-' }
    feed      = @{ Service = 'dawnlist-feed';      Account = 'api-key'
                   Label = 'TheirStack feed key (developer only)'; Expect = '' }
    licence   = @{ Service = 'dawnlist-licence';   Account = 'key'
                   Label = 'Dawnlist licence key'; Expect = 'DAWN-' }
}

function Show-Stored {
    Write-Host ''
    Write-Host 'Currently stored' -ForegroundColor Cyan
    Write-Host '----------------'
    foreach ($key in 'anthropic', 'feed', 'licence') {
        $c = $creds[$key]
        $probe = @"
import keyring
v = keyring.get_password('$($c.Service)', '$($c.Account)')
print('not set' if v is None else 'SET   length %d   begins %s' % (len(v), v[:7]))
"@
        $state = $probe | & $python -
        '{0,-10} {1,-38} {2}' -f $key, $c.Label, $state | Write-Host
    }
    Write-Host ''
}

Show-Stored

if (-not $Name) {
    Write-Host 'Which credential do you want to change?' -ForegroundColor Cyan
    Write-Host '  1. anthropic  - your Anthropic key (sk-ant-...)'
    Write-Host '  2. feed       - TheirStack key (developer path only)'
    Write-Host '  3. licence    - Dawnlist licence key (DAWN-...)'
    Write-Host '  q. quit'
    switch ((Read-Host 'Choice').Trim().ToLower()) {
        '1'         { $Name = 'anthropic' }
        'anthropic' { $Name = 'anthropic' }
        '2'         { $Name = 'feed' }
        'feed'      { $Name = 'feed' }
        '3'         { $Name = 'licence' }
        'licence'   { $Name = 'licence' }
        default     { Write-Host 'Nothing changed.'; return }
    }
}

$cred = $creds[$Name]

if ($Remove) {
    $confirm = Read-Host "Delete the $($cred.Label)? Type DELETE to confirm"
    if ($confirm -cne 'DELETE') { Write-Host 'Nothing changed.'; return }
    $code = @"
import keyring
try:
    keyring.delete_password('$($cred.Service)', '$($cred.Account)')
    print('removed')
except Exception as e:
    print('nothing to remove (%s)' % type(e).__name__)
"@
    $code | & $python -
    Show-Stored
    return
}

Write-Host ''
Write-Host "Replacing: $($cred.Label)" -ForegroundColor Cyan
Write-Host "Stored as: $($cred.Service) / $($cred.Account)"
if ($cred.Expect) { Write-Host "Expected to begin: $($cred.Expect)" }
Write-Host 'The value will not be shown as you type, and is not kept in history.'
Write-Host ''

$secure = Read-Host 'Paste the key, then press Enter' -AsSecureString
if ($secure.Length -eq 0) { Write-Host 'Nothing entered. Nothing changed.'; return }

$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)

    if ($cred.Expect -and -not $plain.StartsWith($cred.Expect)) {
        Write-Host ''
        Write-Host "WARNING: that does not begin with '$($cred.Expect)'." -ForegroundColor Yellow
        Write-Host 'This is exactly the mix-up this script exists to catch:' -ForegroundColor Yellow
        Write-Host 'the right key filed under the wrong name fails later, with' -ForegroundColor Yellow
        Write-Host 'an error from a service that was never going to accept it.' -ForegroundColor Yellow
        if ((Read-Host 'Store it anyway? (y/N)').Trim().ToLower() -ne 'y') {
            Write-Host 'Nothing changed.'
            return
        }
    }

    # Over STDIN, never as an argument.
    $writer = @"
import sys, keyring
value = sys.stdin.read().strip()
keyring.set_password('$($cred.Service)', '$($cred.Account)', value)
print('stored %d characters' % len(value))
"@
    $plain | & $python -c $writer

    if ($Name -eq 'anthropic' -and -not $NoVerify) {
        Write-Host ''
        Write-Host 'Checking the key against Anthropic...' -ForegroundColor Cyan
        # Uses the app's OWN verifier, so this tests what Dawnlist will do
        # rather than something that merely resembles it.
        $check = @"
import sys
sys.path.insert(0, r'$repo')
from app.core import api_key
ok, message = api_key.verify(api_key.get())
print(('OK  ' if ok else 'FAILED  ') + str(message))
sys.exit(0 if ok else 1)
"@
        $check | & $python -
        if ($LASTEXITCODE -ne 0) {
            Write-Host ''
            Write-Host 'The key was stored but did not work. Re-run this script' -ForegroundColor Yellow
            Write-Host 'to try another, or check the key at console.anthropic.com.' -ForegroundColor Yellow
        }
    }
}
finally {
    # Clear the plaintext and the unmanaged copy rather than waiting for the
    # garbage collector, which may never run before the process is inspected.
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    if (Get-Variable -Name plain -Scope 0 -ErrorAction SilentlyContinue) {
        Remove-Variable -Name plain -Scope 0 -ErrorAction SilentlyContinue
    }
}

Show-Stored
Write-Host 'Done. Dawnlist reads the credential at run time, so there is' -ForegroundColor Green
Write-Host 'nothing to restart.' -ForegroundColor Green
