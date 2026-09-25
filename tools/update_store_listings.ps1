# Update MS Store listings: translate all locales, then build the import CSV.
# Run from the dl-code root.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot\..

$py = (Get-Command python -ErrorAction Stop).Source

Write-Host "`n=== Step 1/3: Retranslate description + features (--force) ===" -ForegroundColor Cyan
& $py tools/translate_listing.py --force
if ($LASTEXITCODE -ne 0) { throw "translate_listing.py --force failed (exit $LASTEXITCODE)" }

Write-Host "`n=== Step 2/3: Fill missing keys (release_notes, captions, etc.) ===" -ForegroundColor Cyan
& $py tools/translate_listing.py --fill
if ($LASTEXITCODE -ne 0) { throw "translate_listing.py --fill failed (exit $LASTEXITCODE)" }

Write-Host "`n=== Step 3/3: Build Partner Center import CSV ===" -ForegroundColor Cyan
$export = "$HOME/Downloads/listingData-9PF25H395BB8-1152921505701887361.csv"
if (-not (Test-Path $export)) { throw "Export CSV not found at $export — re-export from Partner Center" }
& $py tools/build_listing_csv.py $export
if ($LASTEXITCODE -ne 0) { throw "build_listing_csv.py failed (exit $LASTEXITCODE)" }

Write-Host "`nDone. Import the -IMPORT.csv via 'Import .csv' in Partner Center." -ForegroundColor Green
