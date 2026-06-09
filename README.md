# Shopify ETL — Logistics San Diego

Integração entre Shopify e SQL Server para visibilidade logística em tempo real.

## Estrutura

```
shopify_etl/
├── config/           # Constantes e variáveis de ambiente
├── extractors/       # Conexão com a API do Shopify
├── loaders/          # Gravação no SQL Server (upsert)
├── scripts/          # Scripts ETL executáveis
├── sql/              # DDL das tabelas
├── ui/               # Interface de controle (FastAPI)
├── utils/            # Logger e log de execuções
└── logs/             # Logs diários gerados automaticamente
```

## Pré-requisitos

- Python 3.10+
- ODBC Driver 17 ou 18 for SQL Server instalado
- Acesso à API do Shopify (Access Token)
- SQL Server acessível na rede

### Linux: instalar ODBC Driver

```bash
# Ubuntu/Debian
curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list > /etc/apt/sources.list.d/mssql-release.list
apt-get update
ACCEPT_EULA=Y apt-get install -y msodbcsql18

# RHEL/CentOS
curl https://packages.microsoft.com/config/rhel/8/prod.repo > /etc/yum.repos.d/mssql-release.repo
ACCEPT_EULA=Y yum install -y msodbcsql18
```

## Instalação

```bash
# 1. Clonar o repositório
git clone https://github.com/sua-org/shopify_etl.git
cd shopify_etl

# 2. Instalar dependências (Linux — system Python)
pip install -r requirements.txt

# Windows (dev local): opcionalmente use venv
# python -m venv venv && venv\Scripts\activate && pip install -r requirements.txt

# 3. Configurar variáveis de ambiente
cp .env.example .env
# Editar .env com suas credenciais

# 4. Criar tabelas no SQL Server (rodar uma única vez)
# Executar sql/create_tables.sql no SQL Server
```

## Uso

### Interface de controle (recomendado)

```bash
cd ui && uvicorn main:app --host 0.0.0.0 --port 8500
# Ou use: bash fix_port.sh
```

Acessar: `http://localhost:8500`

### Linha de comando

```bash
# Carga histórica
python scripts/etl_orders.py --start-date 2024-01-01 --end-date 2024-12-31
python scripts/etl_fulfillments.py --start-date 2024-01-01 --end-date 2024-12-31
python scripts/etl_locations.py

# Incremental (padrão: ontem → hoje)
python scripts/etl_orders.py
python scripts/etl_fulfillments.py
```

## Agendamento (Linux)

### Opção 1: Via interface web (recomendado)

O scheduler integrado na UI (`/setup`) gerencia execuções automáticas com suporte a daily, weekdays, weekly e intervalos customizáveis.

### Opção 2: Cron

```bash
# Executar diariamente às 06:00
0 6 * * * cd /opt/shopify-etl && python scripts/etl_orders.py >> logs/cron.log 2>&1
0 7 * * * cd /opt/shopify-etl && python scripts/etl_fulfillments.py >> logs/cron.log 2>&1
```

### Opção 3: Systemd timer

```ini
# /etc/systemd/system/shopify-etl.service
[Unit]
Description=Shopify ETL Orders Daily

[Service]
Type=oneshot
WorkingDirectory=/opt/shopify-etl
ExecStart=/usr/bin/python3 scripts/etl_orders.py
```

### Ambientes Windows

Para dev local Windows, use o scheduler integrado na UI (APScheduler) que roda junto com o servidor FastAPI.

## Tabelas geradas no SQL Server

| Tabela | Descrição |
|---|---|
| `shopify_orders` | Pedidos completos |
| `shopify_fulfillments` | Fulfillments com tracking |
| `shopify_fulfillment_events` | Eventos de rastreamento em tempo real |
| `shopify_locations` | Warehouses e lojas |
| `etl_run_log` | Histórico de execuções do ETL |

## Expansão futura — Projeto Devoluções

Adicionar:
- `scripts/etl_refunds.py`
- Tabela `shopify_refunds` no `sql/create_tables.sql`
- Método `upsert_refunds()` em `loaders/sqlserver_loader.py`
