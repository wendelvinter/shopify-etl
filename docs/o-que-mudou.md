# O que mudou no Shopify ETL

Este documento explica, em linguagem simples, as melhorias feitas no sistema após uma revisão de qualidade e segurança. Não é necessário saber programar para entender o essencial.

---

## Para que serve este sistema?

O Shopify ETL copia dados da sua loja Shopify (pedidos, envios, locais) para um banco SQL Server, para relatórios e operação logística. Você controla tudo por um **painel na web** (endereço tipo `http://localhost:8000`) ou por scripts agendados.

---

## Resumo em uma frase

Corrigimos **erros que podiam gravar dados incompletos no banco**, **falhas silenciosas** (parecia que deu certo, mas não deu) e **riscos no painel web**; também melhoramos a **tela de configuração** e a **sincronização** com a Shopify.

---

## 1. Dados no banco — mais confiáveis

### Antes
Se algo falhasse no meio de uma importação (por exemplo, ao salvar itens de um pedido), o sistema podia:
- deixar **pedidos pela metade** no banco;
- registrar no histórico que “deu erro”, mas **confirmar mesmo assim** o que já tinha sido salvo.

### Agora
- Em caso de erro, as alterações daquela execução são **desfeitas** (não ficam gravadas pela metade).
- O histórico de execuções é salvo em uma **conexão separada**, para o registro de “erro” não confirmar dados incompletos.
- Na importação de envios (fulfillments), o sistema **confirma o progresso pedido a pedido**, em vez de segurar tudo até o final — menos risco de travar ou perder muito trabalho de uma vez.

**Na prática:** menos “lixo” ou inconsistência no SQL Server depois de uma falha.

---

## 2. Shopify — menos “falso positivo”

### Antes
- Buscar **um pedido por ID** que não existia podia terminar como **sucesso com zero registros**, como se estivesse tudo certo.
- Chamadas à API podiam **travar** sem limite de tempo ou repetir de forma frágil.

### Agora
- Pedido inexistente ou erro da API **aparecem como erro** na execução.
- Há **tempo máximo de espera** e **novas tentativas** quando a Shopify limita o uso (rate limit).

**Na prática:** o painel e os logs refletem melhor o que realmente aconteceu.

---

## 3. Sincronização — dados mais alinhados com a loja

### Melhorias
- Preços e números vindos como texto da Shopify são **convertidos** antes de ir ao banco.
- Itens, fretes e descontos **removidos na loja** podem ser **apagados no banco** naquela importação (menos registros “fantasma”).
- Eventos de rastreamento **atualizam** se mudarem na Shopify (antes só inseriam novos).
- Contagem de “quantos novos / quantos atualizados” ficou **mais precisa** (não depende só de contar linhas na tabela, o que falhava com duas importações ao mesmo tempo).
- Na rotina só de envios, evitamos processar envios **duas vezes** (uma embutida no pedido e outra na API).

**Na prática:** o warehouse fica mais fiel ao que está na Shopify hoje.

---

## 4. Painel web — mais seguro (sem login por enquanto)

Foi pedido **não** colocar senha do painel agora (autenticação com usuários virá depois no roadmap). Mesmo assim, várias proteções foram aplicadas:

| O que foi corrigido | O que isso evita |
|---------------------|------------------|
| Só scripts oficiais podem ser executados (`etl_orders`, `etl_fulfillments`, `etl_locations`) | Alguém não consegue rodar arquivos arbitrários no servidor |
| Cancelar execução só mata processos **que o próprio painel iniciou** | Não dá mais para encerrar programas aleatórios do computador |
| Token e senhas **não aparecem** de novo na tela depois de salvos | Quem olhar o código da página não vê suas chaves |
| Teste de conexão Shopify só aceita URLs de loja **\*.myshopify.com** | O servidor não é usado para acessar endereços maliciosos |
| Testes de conexão passaram de link na URL para **envio seguro no formulário** | Tokens não ficam no histórico do navegador na barra de endereço |
| OAuth só aceita a loja que você já configurou | Troca acidental de loja no fluxo de autorização |
| Mensagens de erro no painel são tratadas contra **código malicioso na tela** | Erros gravados no banco não “quebram” ou manipulam o painel |
| Apenas **uma importação manual** por vez pelo painel | Duas importações juntas não brigam no banco |
| Agendamentos não se sobrepõem de forma estranha ao salvar várias vezes | Horários duplicados ou inconsistentes |

**Importante:** o painel continua **aberto** para quem acessar a porta (ex.: `8000`) na rede, até você implementar login com usuário e permissões. Em rede compartilhada, restrinja quem alcança essa porta (firewall ou use só na sua máquina).

---

## 5. Tela de configuração — layout e consistência

### Antes (após a primeira correção de segurança)
O aviso “Token configurado” aparecia **ao lado do campo** e **desalinhava** a linha da Shopify API.

### Agora
- O aviso fica **no rótulo do campo** (texto pequeno verde ao lado de “Access Token”, “Senha”, etc.).
- Mesma regra para:
  - **Access Token** → “Token configurado”
  - **Client Secret** (OAuth) → “Secret configurado”
  - **Senha do SQL Server** → “Senha configurada”
- Campos já preenchidos mostram **••••••••** no lugar do valor real (você só digita de novo se quiser **trocar**).

**Na prática:** layout alinhado e você sabe, olhando a tela, o que já está salvo no `.env` sem expor a senha.

---

## 6. Configuração e arquivos — pequenos ajustes

- Ao salvar configurações pela tela, o sistema **recarrega** as variáveis sem precisar reiniciar o servidor em muitos casos.
- Valores com caracteres especiais no `.env` são **escapados** ao salvar (menos risco de corromper o arquivo).
- Horários e intervalos do agendamento são **validados** no servidor (não só no navegador).
- Falhas ao ler `schedules.json` ou migrations passam a **aparecer no log** em vez de serem ignoradas em silêncio.

---

## O que você precisa fazer

1. **Reiniciar o painel** se ele já estava aberto antes das mudanças:
   ```bash
   python3 -m uvicorn ui.main:app --host 0.0.0.0 --port 8000
   ```
2. **Recarregar a página** no navegador (F5) na aba de Configurações.
3. **Nada novo no `.env`** obrigatório — removemos a chave temporária de senha do painel (`PANEL_API_KEY`); se existir no seu arquivo, pode apagar.

---

## O que ainda está no roadmap (não feito agora)

- **Login no painel** com usuário, senha e permissões (você pediu para deixar para depois).
- Alguns itens menores de “higiene” técnica (template antigo não usado, ícone do gráfico externo, etc.) — não afetam o dia a dia.

---

## Onde está o código alterado (referência rápida)

| Área | Arquivos principais |
|------|---------------------|
| Scripts de importação | `scripts/etl_orders.py`, `etl_fulfillments.py`, `etl_locations.py` |
| Conexão Shopify | `extractors/shopify_api_extractor.py` |
| Gravação no SQL Server | `loaders/sqlserver_loader.py` |
| Histórico de execuções | `utils/run_log.py` |
| Painel e configuração | `ui/main.py` |
| Variáveis de ambiente | `config/constants.py` |

---

## Dúvidas comuns

**“Perco meus dados antigos?”**  
Não. As mudanças não apagam tabelas; só mudam *como* novas importações são feitas e como o painel se comporta.

**“Preciso rodar SQL manual?”**  
As migrations automáticas na subida do sistema continuam iguais em espírito; se o banco já estava atualizado, nada extra.

**“A importação ficou mais lenta?”**  
Pode ficar um pouco mais lenta em cargas enormes porque a contagem e algumas limpezas são mais cuidadosas; em troca, os dados tendem a ficar mais corretos.

**“Como sei se deu certo?”**  
No painel, em **Execuções recentes**: status **success** ou **error** com mensagem clara; em erro de pedido por ID, não aparece mais sucesso com zero registros.

---

*Documento gerado após a auditoria e correções do projeto shopify-etl. Para detalhes técnicos linha a linha, consulte o histórico de commits ou a equipe de desenvolvimento.*
