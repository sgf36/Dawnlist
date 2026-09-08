<#
    Rotate PADDLE_WEBHOOK_SECRET on the Dawnlist feed Worker.

    Run it from anywhere. PowerShell 7:

        pwsh -File C:\Users\SpencerFields\dawnlist\server\dawnlist-feed-worker\rotate-webhook-secret.ps1

    WHY THIS IS A SCRIPT AND NOT A COMMAND YOU TYPE
    -----------------------------------------------
    Two things have gone wrong before, and both look like something else
    afterwards.

    1. `wrangler secret put <KEY>` takes the secret's NAME, never its value.
       On 2026-08-16 the literal PADDLE_WEBHOOK_SECRET in the command was
       replaced with the secret itself. That created a secret whose NAME was
       the signing key, left the real one untouched, and wrote the credential
       into the Wrangler logs, which record full command lines. This script
       passes the name literally so that substitution cannot happen.

    2. Wrangler needs its config. Running it from C:\Windows\System32 fails
       with "Required Worker name missing", which reads like an auth or
       account problem and is not. This script sets the working directory to
       the Worker first.

    THE VALUE IS NEVER AN ARGUMENT AND NEVER TOUCHES THIS FILE. Wrangler
    prompts for it; paste it there and nowhere else.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$workerDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ''
Write-Host 'Rotating PADDLE_WEBHOOK_SECRET on dawnlist-feed-worker' -ForegroundColor Cyan
Write-Host "Worker directory: $workerDir"
Write-Host ''
Write-Host 'Paste the NEW signing secret at the prompt. It is not echoed and it' -ForegroundColor Yellow
Write-Host 'must not be typed on any command line.' -ForegroundColor Yellow
Write-Host ''

Push-Location $workerDir
try {
    # The KEY is a literal. Do not parameterise it, and never substitute the value here.
    npx wrangler secret put PADDLE_WEBHOOK_SECRET
    if ($LASTEXITCODE -ne 0) {
        throw "wrangler secret put exited with code $LASTEXITCODE - the secret was NOT rotated."
    }

    Write-Host ''
    Write-Host 'Stored. Checking the secret list for anything that looks like a credential...' -ForegroundColor Cyan

    $listRaw = npx wrangler secret list 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "wrangler secret list failed; verify by hand." }
    Write-Host $listRaw

    # A Paddle signing secret is ~70 chars of [A-Za-z0-9_]. A NAME that shape is
    # the 2026-08-16 failure recurring, and it means the credential is now in the
    # Wrangler logs as well as stored under the wrong key.
    $suspect = [regex]::Matches($listRaw, '[A-Za-z0-9_]{40,}') |
               Where-Object { $_.Value -notmatch '^(PADDLE|THEIRSTACK|RESEND|ANTHROPIC|ADMIN)_' }
    if ($suspect.Count -gt 0) {
        Write-Host ''
        Write-Host 'STOP. A secret NAME looks like a credential value:' -ForegroundColor Red
        $suspect | ForEach-Object { Write-Host ('  ' + $_.Value.Substring(0, [Math]::Min(12, $_.Value.Length)) + '...') -ForegroundColor Red }
        Write-Host 'Delete it, then clean the Wrangler logs - they record full command lines.' -ForegroundColor Red
        exit 1
    }

    Write-Host ''
    Write-Host 'No suspicious secret names.' -ForegroundColor Green
    Write-Host ''
    Write-Host 'NOT DONE YET. Storing a value proves only that A value was stored,' -ForegroundColor Yellow
    Write-Host 'not that it was the right one. Verify by replaying an event in the' -ForegroundColor Yellow
    Write-Host 'Paddle dashboard and confirming a 200. A 401 means the wrong secret.' -ForegroundColor Yellow
    Write-Host ''
}
finally {
    Pop-Location
}
