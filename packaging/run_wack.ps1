<#
.SYNOPSIS
    Run the Windows App Certification Kit against dist\Dawnlist.msix.

.DESCRIPTION
    WACK runs Microsoft's own certification tests locally — including the
    deployment and install validation the Store performs — so a failure appears
    here as a report line instead of as a rejected submission days later. On
    the sibling project it caught an install failure (cert 10.3.4) that would
    otherwise have reached Microsoft.

    THREE THINGS THAT MAKE THIS SCRIPT NECESSARY RATHER THAN A ONE-LINER, each
    learned the slow way on Easy-Post Desktop:

      * appcert.exe needs ADMINISTRATOR rights, and refuses with an elevation
        error that does not mention WACK.
      * It has to DEPLOY the package to test it, and Windows will not deploy a
        package whose signing certificate it does not trust. A Store MSIX is
        signed with a throwaway self-signed certificate — correctly, because
        the Store re-signs on publish — so the certificate has to be trusted
        first and removed afterwards.
      * appcert rejects a RELATIVE -reportoutputpath with "must be valid path
        to the report file", and is unreliable when the path contains spaces.
        The report is therefore generated in a space-free temp directory and
        the summary copied back.

    Run from an ELEVATED PowerShell:
        pwsh -File packaging\run_wack.ps1
        pwsh -File packaging\run_wack.ps1 -MsixPath C:\path\to\Some.msix

    Add -KeepTrust to leave the signer certificate trusted, e.g. to launch the
    installed package by hand afterwards. The default removes it: a new
    certificate is minted on every signing run, so trusted copies otherwise
    accumulate in LocalMachine\TrustedPeople indefinitely.
#>
[CmdletBinding()]
param(
    [string]$MsixPath,
    [switch]$KeepTrust
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $MsixPath) { $MsixPath = Join-Path $repo "dist\Dawnlist.msix" }
if (-not (Test-Path $MsixPath)) {
    throw "MSIX not found: $MsixPath. Run packaging\build_msix.py then packaging\sign_msix.ps1 first."
}
$MsixPath = (Resolve-Path -LiteralPath $MsixPath).Path

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "Run this from an ELEVATED PowerShell (Run as administrator) — appcert.exe needs admin and says so in a way that does not mention WACK."
}

$appcert = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\App Certification Kit\appcert.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $appcert) {
    throw "appcert.exe not found. Install the Windows App Certification Kit (part of the Windows SDK)."
}

$workDir = Join-Path $env:TEMP "dawnlist-wack"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
$report  = Join-Path $workDir "Dawnlist.WACK-report.xml"
$summary = Join-Path (Split-Path -Parent $MsixPath) "Dawnlist.WACK-summary.txt"

# 1) Trust the package's own signer so Windows will deploy it for testing.
Write-Host "Trusting the package signer certificate ..."
$cert = (Get-AuthenticodeSignature $MsixPath).SignerCertificate
if ($null -eq $cert) { throw "The MSIX is not signed; WACK cannot deploy it. Run packaging\sign_msix.ps1." }
Write-Host "  signer: $($cert.Subject)"
$cerPath = Join-Path $env:TEMP "dawnlist-wack-signer.cer"
[IO.File]::WriteAllBytes($cerPath, $cert.Export("Cert"))
$imported = Import-Certificate -FilePath $cerPath -CertStoreLocation Cert:\LocalMachine\TrustedPeople

try {
    # 2) Reset any prior state, then run the certification tests.
    #
    # THE REPORT IS DELETED FIRST, AND THIS IS NOT TIDINESS. appcert refuses
    # outright if the report path already exists — "Please specify a unique
    # report file name" — and it refuses in a way that is easy to miss, because
    # it writes that line and exits while the stale XML sits there looking like
    # a result. On 2026-09-08 this script then parsed the OLD report and
    # announced "OVERALL RESULT: PASS. No failed tests." for a run that never
    # happened. A verifier that reports the previous answer is worse than one
    # that reports nothing.
    Remove-Item $report -Force -ErrorAction SilentlyContinue
    $startedAt = Get-Date

    Write-Host "Running the Windows App Certification Kit — this takes several minutes ..."
    & $appcert.FullName reset | Out-Null
    & $appcert.FullName test -appxpackagepath "$MsixPath" -reportoutputpath "$report"
    if ($LASTEXITCODE -ne 0) {
        throw "appcert.exe exited with code $LASTEXITCODE. No report was produced, so there is no result — do not treat anything below as one."
    }

    # Two independent checks that THIS run produced the file, because the exit
    # code alone did not catch the failure above.
    if (-not (Test-Path $report)) {
        throw "appcert.exe reported success but wrote no report at $report."
    }
    if ((Get-Item $report).LastWriteTime -lt $startedAt) {
        throw "The report at $report predates this run. It is a stale file from an earlier attempt and must not be read as a result."
    }

    # 3) Summarise. OVERALL_RESULT is the verdict; the failed tests are pulled
    #    out so the answer is readable without opening the XML.
    "WACK report for: $MsixPath"   | Set-Content $summary
    "Generated:       $report`n"   | Add-Content $summary
    try {
        [xml]$xml = Get-Content $report
        "OVERALL RESULT: $($xml.REPORT.OVERALL_RESULT)`n" | Add-Content $summary
        $failed = $xml.SelectNodes("//*[@RESULT='FAIL']")
        if ($failed.Count -gt 0) {
            "Failed tests:" | Add-Content $summary
            foreach ($f in $failed) {
                ("  - {0}: {1}" -f $f.NAME, ($f.MESSAGE -join " ")) | Add-Content $summary
            }
        } else {
            "No failed tests." | Add-Content $summary
        }
    } catch {
        "Could not parse the report XML: $($_.Exception.Message)" | Add-Content $summary
    }

    Write-Host ""
    Get-Content $summary
    Write-Host ""
    Write-Host "Full report: $report"
    Write-Host "Summary:     $summary"
}
finally {
    if ($KeepTrust) {
        Write-Host "`nSigner certificate left trusted (-KeepTrust)."
        Write-Host "Remove it later with: Remove-Item Cert:\LocalMachine\TrustedPeople\$($imported.Thumbprint)"
    } else {
        # A new self-signed certificate is minted on every signing run, so
        # leaving each one trusted grows the store without bound.
        Remove-Item "Cert:\LocalMachine\TrustedPeople\$($imported.Thumbprint)" -Force -ErrorAction SilentlyContinue
        Write-Host "`nSigner certificate removed from LocalMachine\TrustedPeople."
    }
    Remove-Item $cerPath -Force -ErrorAction SilentlyContinue
}
