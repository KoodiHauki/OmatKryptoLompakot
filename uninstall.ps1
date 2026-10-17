# OmatKryptoLompakot - Ohjelman ja tietojen poistoskripti (PowerShell)
param (
    [switch]$Force,
    [switch]$DryRun,
    [switch]$IncludeVenv
)

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " [POISTO] OmatKryptoLompakot - Ohjelman ja tietojen poistaja (PowerShell)" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""

$DataDir = "$env:LOCALAPPDATA\OmatKryptoLompakot"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "[*] Etsitaan ohjelman tallentamia tietoja..." -ForegroundColor Yellow

$FoundAny = $false

# 1. Ulkoinen datahakemisto
if (Test-Path $DataDir) {
    $Files = Get-ChildItem -Path $DataDir -Recurse -File -ErrorAction SilentlyContinue
    $TotalBytes = ($Files | Measure-Object -Property Length -Sum).Sum
    if (-not $TotalBytes) { $TotalBytes = 0 }
    $Mb = [math]::Round($TotalBytes / 1MB, 2)
    Write-Host ("  [LOYTYI] Datahakemisto: " + $DataDir + " (" + $Mb + " MB, " + $Files.Count + " tiedostoa)") -ForegroundColor White
    $FoundAny = $true
} else {
    Write-Host ("  [OK] Datahakemistoa ei loydy (" + $DataDir + ").") -ForegroundColor Gray
}

# 2. Windows Credential Manager
$CmdkeyOut = cmdkey /list
$CredTargets = @()
if ($CmdkeyOut) {
    foreach ($line in ($CmdkeyOut -split "`r?`n")) {
        if ($line -match 'Target:\s*(?:LegacyGeneric:target=)?([^\s]*omatkryptolompakot[^\s]*)') {
            $t = $matches[1]
            if ($t -match 'target=(.*)') { $t = $matches[1] }
            if ($t -and -not ($CredTargets -contains $t)) {
                $CredTargets += $t
            }
        }
    }
}

if ($CredTargets.Count -gt 0) {
    Write-Host ("  [LOYTYI] " + $CredTargets.Count + " API-avaintallennetta Windows Credential Managerissa") -ForegroundColor White
    $FoundAny = $true
} else {
    Write-Host "  [OK] Ei tallennettuja API-avaimia Windows Credential Managerissa." -ForegroundColor Gray
}

# 3. Virtuaaliympäristö
$VenvDir = Join-Path $ScriptDir ".venv"
if ($IncludeVenv -and (Test-Path $VenvDir)) {
    Write-Host ("  [LOYTYI] Virtuaaliymparisto: " + $VenvDir) -ForegroundColor White
    $FoundAny = $true
}

if (-not $FoundAny) {
    Write-Host "`n[OK] Mitaan poistettavaa ei loytynyt. Jarjestelma on jo puhdas!" -ForegroundColor Green
    exit 0
}

if ($DryRun) {
    Write-Host "`n[INFO] -DryRun valittu: Mitaan ei poistettu." -ForegroundColor Cyan
    exit 0
}

Write-Host ""
if (-not $Force) {
    $Confirm = Read-Host "Haluatko varmasti poistaa ylla mainitut tiedot pysyvasti? (k/e)"
    if ($Confirm -notmatch '^(k|kylla|y|yes)$') {
        Write-Host "Peruutus: Mitaan ei poistettu." -ForegroundColor Yellow
        exit 0
    }
}

Write-Host "`n[+] Poistetaan tietoja..." -ForegroundColor Yellow

# Poista Credential Manager -avaimet
if ($CredTargets.Count -gt 0) {
    $DelCount = 0
    foreach ($cred in $CredTargets) {
        cmdkey /delete:$cred | Out-Null
        $DelCount++
    }
    Write-Host ("  [POISTETTU] " + $DelCount + " API-avaintallennetta Windows Credential Managerista.") -ForegroundColor Green
}

# Poista Datahakemisto
if (Test-Path $DataDir) {
    try {
        Remove-Item -Path $DataDir -Recurse -Force
        Write-Host ("  [POISTETTU] Datahakemisto ja tietokannat: " + $DataDir) -ForegroundColor Green
    } catch {
        Write-Host ("  [VIRHE] Datahakemiston poisto epaonnistui: " + $_) -ForegroundColor Red
    }
}

# Poista .venv jos pyydetty
if ($IncludeVenv -and (Test-Path $VenvDir)) {
    try {
        Remove-Item -Path $VenvDir -Recurse -Force
        Write-Host "  [POISTETTU] .venv -virtuaaliymparisto." -ForegroundColor Green
    } catch {
        Write-Host ("  [VIRHE] Virtuaaliympariston poisto epaonnistui: " + $_) -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Kaikki ulkoiset ja tallennetut tiedot on poistettu!" -ForegroundColor Green
Write-Host (" Projektikoodi sailyy tassa kansiossa: " + $ScriptDir) -ForegroundColor White
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""
