<#
.SYNOPSIS
    Signs dist\Dawnlist.msix for Microsoft Store UPLOAD. No admin needed.

.DESCRIPTION
    Partner Center will not accept an unsigned MSIX, and yet the signature is
    thrown away: the Store strips it and re-signs with a Microsoft certificate
    on publish. So a throwaway self-signed certificate is not a shortcut here,
    it is the correct instrument — buying a real one would pay for a signature
    that never reaches a customer.

    What the certificate MUST get right is its subject, which has to equal the
    package's Publisher exactly. Windows treats Publisher as part of the
    package identity, so a mismatch is not a trust warning, it is "the
    publisher does not match" and the upload is refused.

        CN=A7D4B6C0-27D4-4F66-82EB-82F5DD466788

    That is the account-wide publisher id and is shared with Easy-Post Desktop.
    It is NOT a per-product value; do not invent one for Dawnlist. Read it from
    packaging\msix\AppxManifest.xml, which this script does, rather than
    pasting it — the two drifting apart is exactly the failure this guards.

    The certificate is built in managed .NET through CertificateRequest, NOT
    New-SelfSignedCertificate or certreq.exe. Those two crash with 0xC0000005
    inside certenroll.dll on this machine; CertificateRequest never loads that
    DLL. Ported from Easy-Post Desktop, where that was diagnosed the hard way.

    Not to be confused with the DIRECT-DOWNLOAD signing, which is a real
    Microsoft-managed Public Trust certificate through Azure Artifact Signing
    and is what clears SmartScreen. That one matters to buyers; this one only
    has to satisfy an upload check.

.EXAMPLE
    pwsh -File packaging\sign_msix.ps1
#>

param(
    [string]$MsixPath = (Join-Path $PSScriptRoot "..\dist\Dawnlist.msix"),
    [string]$ManifestPath = (Join-Path $PSScriptRoot "msix\AppxManifest.xml")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $MsixPath)) {
    throw "$MsixPath not found. Run packaging\build_msix.py first."
}
if (-not (Test-Path $ManifestPath)) {
    throw "$ManifestPath not found; cannot determine the publisher."
}

# Read the subject from the manifest rather than hard-coding it. A signature
# whose subject does not match Identity/@Publisher is rejected at upload with a
# message about the publisher, which reads like a Partner Center account
# problem and is not one.
[xml]$manifest = Get-Content -LiteralPath $ManifestPath
$subject = $manifest.Package.Identity.Publisher
if ([string]::IsNullOrWhiteSpace($subject)) {
    throw "No Identity/@Publisher in $ManifestPath."
}
Write-Host "Publisher from the manifest: $subject"

$pfxPath = Join-Path $env:TEMP "dawnlist_upload_signing.pfx"
$plainPassword = [System.Guid]::NewGuid().ToString("N")

Write-Host "Creating self-signed certificate in managed .NET code..."
$rsa = [System.Security.Cryptography.RSA]::Create(2048)
try {
    $req = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
        $subject,
        $rsa,
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)

    $req.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $true))

    $req.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
            [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature, $false))

    # Code Signing EKU. Without it signtool accepts the certificate and Windows
    # later refuses the package, which is the slowest possible way to find out.
    $eku = [System.Security.Cryptography.OidCollection]::new()
    [void]$eku.Add([System.Security.Cryptography.Oid]::new("1.3.6.1.5.5.7.3.3"))
    $req.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($eku, $false))

    # Backdated a day so a clock skew between runner and validator cannot make
    # a freshly minted certificate look not-yet-valid.
    $notBefore = [System.DateTimeOffset]::UtcNow.AddDays(-1)
    $notAfter = [System.DateTimeOffset]::UtcNow.AddYears(1)
    $cert = $req.CreateSelfSigned($notBefore, $notAfter)
}
finally {
    $rsa.Dispose()
}

try {
    $pfxBytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx, $plainPassword)
    [System.IO.File]::WriteAllBytes($pfxPath, $pfxBytes)

    $signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) {
        throw "signtool.exe not found under C:\Program Files (x86)\Windows Kits\10\bin\*\x64. Install the Windows 10/11 SDK."
    }

    Write-Host "Signing $MsixPath..."
    # /f + /p name the exact certificate. Deliberately not /a, which enumerates
    # every registered provider and can raise an unrelated login prompt that
    # nothing on a CI runner can answer.
    & $signtool.FullName sign /fd SHA256 /f $pfxPath /p $plainPassword $MsixPath
    if ($LASTEXITCODE -ne 0) {
        throw "signtool exited with code $LASTEXITCODE"
    }
}
finally {
    if (Test-Path $pfxPath) { Remove-Item $pfxPath -Force }
}

# Prove it, rather than trusting the exit code. A signature with the wrong
# subject signs successfully and fails at upload.
$signed = (Get-AuthenticodeSignature -LiteralPath $MsixPath).SignerCertificate.Subject
if ($signed -ne $subject) {
    throw "Signed with '$signed' but the manifest declares '$subject'. Partner Center will refuse this."
}

Write-Host ""
Write-Host "Signed for upload as $signed"
