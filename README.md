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
- ODBC Driver 17 for SQL Server instalado
- Acesso à API do Shopify (Access Token)
- SQL Server acessível na rede

## Instalação

```bash
# 1. Clonar o repositório
git clone https://github.com/sua-org/shopify_etl.git
cd shopify_etl

# 2. Criar ambiente virtual
python -m venv venv
venv\Scripts\activate  # Windows

# 3. Instalar dependências
pip install -r requirements.txt

# 4. Configurar variáveis de ambiente
copy .env.example .env
# Editar .env com suas credenciais

# 5. Criar tabelas no SQL Server (rodar uma única vez)
# Abrir o SQL Server Management Studio e executar:
# sql/create_tables.sql
```

## Uso

### Interface de controle (recomendado)

```bash
cd ui
uvicorn main:app --host 0.0.0.0 --port 8000
```

Acessar: `http://localhost:8000`

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

## Agendamento (Windows Task Scheduler)

Criar duas tarefas agendadas para rodar diariamente às 06:00:

```
Programa: C:\shopify_etl\venv\Scripts\python.exe
Argumentos: C:\shopify_etl\scripts\etl_orders.py
Pasta: C:\shopify_etl
```

```
Programa: C:\shopify_etl\venv\Scripts\python.exe
Argumentos: C:\shopify_etl\scripts\etl_fulfillments.py
Pasta: C:\shopify_etl
```

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
