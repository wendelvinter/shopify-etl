#!/usr/bin/env bash
# fix_port.sh — Libera a porta e reinicia o servidor ETL
# Uso: bash fix_port.sh [porta]

set -euo pipefail

PORT="${1:-8500}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Shopify ETL — Fix Port $PORT ==="

# 1. Encontrar e matar processos na porta
echo ""
echo "[1] Verificando processos na porta $PORT..."
if command -v fuser &>/dev/null; then
    PIDS=$(fuser "$PORT/tcp" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        for pid in $PIDS; do
            echo "    Matando PID $pid"
            kill -9 "$pid" 2>/dev/null || true
        done
        sleep 2
    else
        echo "    Nenhum processo encontrado na porta $PORT"
    fi
elif command -v ss &>/dev/null; then
    PIDS=$(ss -tlnp "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K\d+' || true)
    if [ -n "$PIDS" ]; then
        for pid in $PIDS; do
            echo "    Matando PID $pid"
            kill -9 "$pid" 2>/dev/null || true
        done
        sleep 2
    else
        echo "    Nenhum processo encontrado na porta $PORT"
    fi
else
    echo "    [!] AVISO: nem fuser nem ss encontrados — pulando verificação de porta"
fi

# 2. Confirmar que a porta está livre
STILL=$(ss -tlnp "sport = :$PORT" 2>/dev/null | grep -c ":$PORT " || true)
if [ "${STILL:-0}" -gt 0 ]; then
    echo ""
    echo "[!] AVISO: Porta $PORT ainda ocupada. Tente reiniciar manualmente."
    exit 1
else
    echo "    Porta $PORT liberada."
fi

# 3. Verificar se o Python está disponível
PYTHON=""
for py in python3 python; do
    if command -v "$py" &>/dev/null; then
        PYTHON="$py"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "[!] ERRO: Python não encontrado no PATH"
    exit 1
fi

# 4. Verificar se as dependências estão instaladas
if ! "$PYTHON" -c "import uvicorn" 2>/dev/null; then
    echo ""
    echo "[!] ERRO: uvicorn não encontrado. Rode: pip install -r requirements.txt"
    exit 1
fi

# 5. Iniciar o servidor
echo ""
echo "[2] Iniciando servidor ETL na porta $PORT..."
echo "    Pressione Ctrl+C para parar."
echo ""
exec "$PYTHON" -m uvicorn ui.main:app --host 0.0.0.0 --port "$PORT"
