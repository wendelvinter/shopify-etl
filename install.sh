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
    if ! dpkg -l | grep -q "msodbcsql18"; then
        echo "    Instalando ODBC Driver 18 for SQL Server..."
        curl -s https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
        curl -s "https://packages.microsoft.com/config/ubuntu/${OS_VERSION}/prod.list" \
            > /etc/apt/sources.list.d/mssql-release.list
        apt-get update -qq
        ACCEPT_EULA=Y apt-get install -y -qq msodbcsql18
    else
        echo "    ODBC Driver 18 já instalado."
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
        apt-get update -qq
        apt-get install -y -qq python3 python3-pip curl
        install_odbc_ubuntu
        ;;
    rhel|centos|fedora|rocky|almalinux)
        yum install -y -q python3 python3-pip curl
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
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt"

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

# ── 7. Summary ────────────────────────────────────────────────
echo ""
echo -e "${GREEN}=== Instalação concluída! ===${NC}"
echo ""
echo "Próximos passos:"
echo "  1. Edite o arquivo .env com suas credenciais"
echo "  2. Execute as migrações SQL (sql/create_tables.sql) no SQL Server"
echo "  3. Inicie o servidor:  bash fix_port.sh"
echo "  4. Acesse a UI:        http://localhost:8500"
echo ""
echo "Comandos rápidos:"
echo "  bash fix_port.sh              # Inicia o servidor na porta 8500"
echo "  bash fix_port.sh 8080         # Inicia em outra porta"
echo "  python scripts/etl_orders.py  # Roda ETL manualmente"
