#!/usr/bin/env bash
# install.sh — Instalação do Shopify ETL para Linux (produção)
# Uso: sudo bash install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}=== Shopify ETL — Instalação Linux ===${NC}"
echo ""

# ── 1. Detect OS ──────────────────────────────────────────────
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS="$ID"
    OS_VERSION="$VERSION_ID"
    echo "[1] Sistema detectado: $OS $OS_VERSION"
else
    echo "[!] Não foi possível detectar o SO. Continuando sem instalar pacotes..."
    OS="unknown"
fi

# ── 2. Install system dependencies ────────────────────────────
echo ""
echo "[2] Instalando dependências do sistema..."

install_odbc_ubuntu() {
    if dpkg -l 2>/dev/null | grep -q "msodbcsql18"; then
        echo "    ODBC Driver 18 já instalado."
        return 0
    fi

    echo "    Instalando ODBC Driver 18 for SQL Server..."
    local MS_KEYRING="/usr/share/keyrings/microsoft-prod.gpg"
    local MS_LIST="/etc/apt/sources.list.d/mssql-release.list"

    # Ubuntu 24.04+ usa método moderno de GPG (apt-key foi deprecado)
    curl -s https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor --batch --yes -o "$MS_KEYRING" 2>/dev/null

    if [ -f "$MS_KEYRING" ]; then
        # Método moderno com signed-by
        echo "deb [arch=amd64,arm64,armhf signed-by=$MS_KEYRING] https://packages.microsoft.com/ubuntu/${OS_VERSION}/prod noble main" \
            > "$MS_LIST"
    else
        # Fallback: método antigo (Ubuntu < 22.04)
        echo "    [!] GPG moderno falhou, tentando método antigo..."
        curl -s https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
        curl -s "https://packages.microsoft.com/config/ubuntu/${OS_VERSION}/prod.list" \
            > "$MS_LIST"
    fi

    if ! apt-get update -qq 2>/dev/null; then
        echo "    [!] AVISO: apt-get update falhou. Tentando continuar mesmo assim..."
    fi

    if ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18 2>/dev/null; then
        echo "    ODBC Driver 18 instalado com sucesso."
    else
        echo "    [!] AVISO: Falha ao instalar ODBC Driver 18."
        echo "    O ETL funcionará, mas a conexão com SQL Server pode falhar."
        echo "    Instale manualmente: https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server"
    fi
}

install_odbc_rhel() {
    if ! rpm -q msodbcsql18 &>/dev/null; then
        echo "    Instalando ODBC Driver 18 for SQL Server..."
        curl -s https://packages.microsoft.com/config/rhel/8/prod.repo \
            > /etc/yum.repos.d/mssql-release.repo
        ACCEPT_EULA=Y yum install -y -q msodbcsql18
    else
        echo "    ODBC Driver 18 já instalado."
    fi
}

case "$OS" in
    ubuntu|debian)
        apt-get update -qq 2>/dev/null || echo "    [!] apt-get update teve warnings (não crítico)"
        apt-get install -y -qq python3 python3-pip curl gpg 2>/dev/null || true
        install_odbc_ubuntu
        ;;
    rhel|centos|fedora|rocky|almalinux)
        yum install -y -q python3 python3-pip curl 2>/dev/null || true
        install_odbc_rhel
        ;;
    *)
        echo "    [!] SO não reconhecido. Instale manualmente:"
        echo "        - Python 3.10+"
        echo "        - ODBC Driver 17 ou 18 for SQL Server"
        echo "        - pip"
        ;;
esac

# ── 3. Install Python dependencies ────────────────────────────
echo ""
echo "[3] Instalando dependências Python..."

# Ubuntu 24.04+ tem PEP 668 (externally-managed-environment)
# Precisa de --break-system-packages e remover pacotes APT conflitantes
PEP668_BREAK=""
if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
    # Detecta se o ambiente é externally-managed (PEP 668)
    if python3 -m pip install --dry-run pip 2>&1 | grep -q "externally-managed"; then
        PEP668_BREAK="--break-system-packages"
        echo "    PEP 668 detectado — usando --break-system-packages"
        echo "    Removendo pacotes APT conflitantes (rich, requests)..."
        apt-get remove -y python3-rich python3-requests 2>/dev/null || true
    fi
fi

echo "    Instalando via python3 -m pip..."
python3 -m pip install --upgrade pip -q $PEP668_BREAK 2>/dev/null || true
python3 -m pip install $PEP668_BREAK -r "$SCRIPT_DIR/requirements.txt"

# ── 4. Configure .env ─────────────────────────────────────────
echo ""
echo "[4] Configurando .env..."
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    if [ -f "$SCRIPT_DIR/.env.example" ]; then
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        echo "    Arquivo .env criado a partir de .env.example"
        echo "    ${RED}>> Edite .env com suas credenciais antes de iniciar!${NC}"
    else
        echo "    [!] .env.example não encontrado — crie o .env manualmente"
    fi
else
    echo "    .env já existe — mantendo configuração atual."
fi

# ── 5. Create log directory ───────────────────────────────────
mkdir -p "$SCRIPT_DIR/logs"
echo "    Diretório logs/ criado."

# ── 6. Make scripts executable ───────────────────────────────
chmod +x "$SCRIPT_DIR/fix_port.sh" 2>/dev/null || true
chmod +x "$SCRIPT_DIR/install.sh" 2>/dev/null || true

# ── 7. pm2 (auto-start) ──────────────────────────────────────
echo ""
echo "[7] Configurando pm2 (gerenciador de processos)..."
if command -v pm2 &>/dev/null; then
    pm2 stop shopify-etl 2>/dev/null || true
    pm2 delete shopify-etl 2>/dev/null || true
    pm2 start "python3 -m uvicorn ui.main:app --host 0.0.0.0 --port 8500" \
        --name shopify-etl --cwd "$SCRIPT_DIR"
    pm2 save
    echo "    pm2 configurado. O ETL iniciará automaticamente no boot."
    echo "    Comandos: pm2 status | pm2 logs shopify-etl | pm2 restart shopify-etl"
elif command -v npm &>/dev/null; then
    echo "    Instalando pm2 via npm..."
    npm install -g pm2 2>/dev/null || true
    if command -v pm2 &>/dev/null; then
        pm2 start "python3 -m uvicorn ui.main:app --host 0.0.0.0 --port 8500" \
            --name shopify-etl --cwd "$SCRIPT_DIR"
        pm2 save
        pm2 startup 2>/dev/null || true
        echo "    pm2 configurado."
    else
        echo "    [!] Não foi possível instalar pm2. Use: bash fix_port.sh"
    fi
else
    echo "    [!] npm não encontrado. Instale o pm2 manualmente:"
    echo "        npm install -g pm2"
    echo "        pm2 start 'python3 -m uvicorn ui.main:app --host 0.0.0.0 --port 8500' --name shopify-etl --cwd $SCRIPT_DIR"
    echo "        pm2 save && pm2 startup"
fi

# ── 8. Summary ────────────────────────────────────────────────
echo ""
echo -e "${GREEN}=== Instalação concluída! ===${NC}"
echo ""
echo "Próximos passos:"
echo "  1. Edite o arquivo .env com suas credenciais"
echo "  2. Execute as migrações SQL (sql/create_tables.sql) no SQL Server"
echo "  3. Acesse a UI:        http://localhost:8500"
echo ""
echo "Comandos rápidos:"
echo "  pm2 status                     # Status do serviço"
echo "  pm2 logs shopify-etl           # Logs em tempo real"
echo "  pm2 restart shopify-etl        # Reiniciar"
echo "  bash fix_port.sh               # Iniciar manualmente na porta 8500"
echo "  python3 scripts/etl_orders.py  # Roda ETL manualmente"
