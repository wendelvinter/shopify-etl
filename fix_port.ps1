# fix_port.ps1 - Libera a porta 8000 e reinicia o servidor ETL
# Uso: powershell -ExecutionPolicy Bypass -File fix_port.ps1

param(
    [int]$Port = 8000
)

Set-Location $PSScriptRoot

Write-Host "=== Shopify ETL - Fix Port $Port ===" -ForegroundColor Cyan

# 1. Encontrar e matar processos na porta
Write-Host ""
Write-Host "[1] Procurando processos na porta $Port..."
$connections = netstat -ano | Select-String ":$Port\s"
if ($connections) {
    $pids = $connections | ForEach-Object {
        ($_ -split '\s+') | Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1
    } | Sort-Object -Unique

    foreach ($p in $pids) {
        if ($p -and $p -ne '0') {
            $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "   Matando PID $p ($($proc.Name))" -ForegroundColor Yellow
                Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Start-Sleep -Seconds 2
} else {
    Write-Host "   Nenhum processo encontrado na porta $Port" -ForegroundColor Green
}

# 2. Confirmar que a porta esta livre
$still = netstat -ano | Select-String ":$Port\s"
if ($still) {
    Write-Host ""
    Write-Host "[!] AVISO: Porta $Port ainda ocupada. Tente reiniciar o servidor manualmente." -ForegroundColor Red
    exit 1
} else {
    Write-Host "   Porta $Port liberada." -ForegroundColor Green
}

# 3. Verificar se o venv existe
if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host ""
    Write-Host "[!] ERRO: venv nao encontrado em .\venv\Scripts\python.exe" -ForegroundColor Red
    Write-Host "    Crie o venv com: python -m venv venv"
    Write-Host "    Depois: .\venv\Scripts\pip install -r requirements.txt"
    exit 1
}

# 4. Iniciar o servidor
Write-Host ""
Write-Host "[2] Iniciando servidor ETL na porta $Port..." -ForegroundColor Cyan
Write-Host "    Pressione Ctrl+C para parar."
Write-Host ""
& .\venv\Scripts\python.exe -m uvicorn ui.main:app --host 0.0.0.0 --port $Port
