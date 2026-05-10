# Alpha Code — atualiza o repo + dependencias num comando so (Windows).
# Espelho PowerShell de bin/alpha-update (bash).
#
# Uso:
#   .\bin\alpha-update.ps1            # pula pra master mais recente
#   .\bin\alpha-update.ps1 v1.2.0     # fixa numa tag especifica

[CmdletBinding()]
param(
    [string]$Target = "master"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir  = Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Path)
$ProjectDir = Split-Path -Parent $ScriptDir
$VenvPy     = Join-Path $ProjectDir ".venv\Scripts\python.exe"

Set-Location $ProjectDir

if (-not (Test-Path $VenvPy)) {
    Write-Host "Erro: venv nao encontrado em $VenvPy" -ForegroundColor Red
    Write-Host "Cria com: python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -e ."
    exit 1
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments)] [string[]]$Args)
    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Args -join ' ') falhou (exit $LASTEXITCODE)"
    }
}

Write-Host "-> Atualizando Alpha em $ProjectDir`n"

$OldHead = (& git rev-parse --short HEAD).Trim()

Invoke-Git fetch --tags origin

# Tag exata? branch master/main? ou commit/branch arbitrario?
& git show-ref --verify --quiet "refs/tags/$Target" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "-> Checkout na tag $Target"
    Invoke-Git checkout $Target
}
elseif ($Target -in @("master", "main")) {
    & git checkout $Target 2>$null   # pode ja estar nessa branch
    Invoke-Git pull --ff-only origin $Target
}
else {
    Write-Host "-> Checkout em $Target (branch ou commit)"
    Invoke-Git checkout $Target
}

$NewHead = (& git rev-parse --short HEAD).Trim()

if ($OldHead -eq $NewHead) {
    Write-Host "OK Ja estava na versao mais recente ($NewHead)" -ForegroundColor Green
    exit 0
}

Write-Host "`n-> Reinstalando dependencias"
& $VenvPy -m pip install -e . --upgrade --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install falhou (exit $LASTEXITCODE)" }

Write-Host "`n-> Diferencas entre .env.example e .env (variaveis novas):"
if (Test-Path .env) {
    $exampleVars = (Select-String -Path .env.example -Pattern '^[A-Z_]+=' -ErrorAction SilentlyContinue) `
        | ForEach-Object { ($_.Line -split '=')[0] } | Sort-Object -Unique
    $localVars = (Select-String -Path .env -Pattern '^[A-Z_]+=' -ErrorAction SilentlyContinue) `
        | ForEach-Object { ($_.Line -split '=')[0] } | Sort-Object -Unique
    $missing = $exampleVars | Where-Object { $_ -notin $localVars }
    if ($missing) {
        $missing | ForEach-Object { Write-Host "  + $_" }
    } else {
        Write-Host "  (nenhuma variavel nova)"
    }
} else {
    Write-Host "  (.env nao existe — copia .env.example)"
}

Write-Host "`nOK Alpha atualizado: $OldHead -> $NewHead" -ForegroundColor Green
$lastMsg = (& git log -1 --pretty=format:'%s' HEAD)
Write-Host "  $lastMsg"
