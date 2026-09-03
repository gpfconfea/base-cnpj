# Base CNPJ

Espelho local da base [OpenCNPJ](https://api.opencnpj.org) em PostgreSQL, com uma API FastAPI para
consulta por CNPJ e por CNAE. Serve qualquer sistema interno; nasceu para montar grupo de controle
de empresas do mesmo CNAE nos resultados de fiscalização do FTNF.

Varrer os `.ndjson` a cada consulta não serve: os arquivos são fatiados por prefixo de CNPJ, então
buscar por CNAE exigiria ler a base inteira toda vez. Em Postgres com índice, a mesma pergunta é
respondida em milissegundos.

## Portas

| Serviço | Porta host | Porta container |
| --- | ---: | ---: |
| API (FastAPI) | **8006** | 8000 |
| PostgreSQL | **5436** | 5432 |

Escolhidas por não colidirem com nada que já roda na VM. Mude em `.env` se precisar.

## Subir

```bash
cp .env.example .env      # troque POSTGRES_PASSWORD
docker compose up -d --build
curl http://localhost:8006/health
```

`/health` responde `sem_dados` até a primeira carga terminar. Documentação interativa em
`http://localhost:8006/docs`.

## Carregar e atualizar a base

```bash
./scripts/atualizar.sh --limpar
```

O script faz duas etapas, que também podem ser rodadas separadamente:

```bash
docker compose --profile manual run --rm ingestor -m app.baixar
docker compose --profile manual run --rm ingestor -m app.ingerir
```

`app.baixar` puxa o `data.zip` da fonte, com retomada por `Range` se a conexão cair, e extrai só os
`.ndjson`. Aceita `--pular-download`, `--pular-extracao` e `--remover-zip`.

`app.ingerir` lê os arquivos em paralelo e joga tudo no Postgres via `COPY`. Aceita `--workers`,
`--limite-arquivos` (útil para um teste com 2 ou 3 arquivos antes de encarar a base toda) e
`--apagar` (para remover os `.ndjson` processados no final).



O histórico de cargas fica na tabela `carga` e em `GET /carga`.

## Endpoints

| Rota | Para que serve |
| --- | --- |
| `GET /health` | disponibilidade e se já existe base carregada |
| `GET /carga` | as dez últimas cargas, com contagem e status |
| `GET /{cnpj}` | dados de uma empresa |
| `GET /cnae/{codigo}` | empresas com o CNAE informado |
| `POST /cnpjs` | consulta em lote, até 1000 CNPJs |

Sem token e sem limite de requisição, como combinado. A API é de leitura: nenhuma rota escreve no
banco.

### `GET /{cnpj}`

Devolve **o mesmo JSON que `api.opencnpj.org/{cnpj}`**, campo por campo, incluindo `QSA`, `cnaes`
com descrição e `capital_social` no formato `"0,00"`. Isso é proposital: quem já consome a API
pública troca a URL base e não mexe em mais nada.

```bash
curl http://localhost:8006/99017782000139
```

### `GET /cnae/{codigo}`

```bash
curl "http://localhost:8006/cnae/7112000?uf=GO&situacao=Ativa&limit=200"
```

| Parâmetro | Efeito |
| --- | --- |
| `uf` | filtra pela UF |
| `situacao` | `Ativa`, `Baixada`, `Suspensa`, `Inapta`, `Nula` |
| `codigo_municipio` | código do município na Receita |
| `porte` | `Demais`, `ME`, `EPP` |
| `matriz` | `true` só matriz, `false` só filial |
| `inclui_secundario` | também quem tem o CNAE como secundário |
| `formato` | `resumo` (padrão) ou `completo` |
| `limit` / `offset` | paginação, até 1000 por página |

O formato `resumo` traz o que basta para pareamento: CNPJ, razão social, CNAE com descrição,
situação, data de início, porte, matriz ou filial, UF e município.

### `POST /cnpjs`

```bash
curl -X POST http://localhost:8006/cnpjs \
  -H 'Content-Type: application/json' \
  -d '{"cnpjs": ["99017782000139", "00000000000191"]}'
```

Devolve os encontrados e a lista de `nao_encontrados`. Existe porque enriquecer milhares de CNPJs
uma requisição por vez levaria minutos.

## Modelo de dados

Uma linha por CNPJ em `empresa`, com colunas tipadas. Índices, por ora, só onde as consultas
batem: chave primária em `cnpj` e índice em `cnae_principal`.

Código e descrição de CNAE, qualificação do responsável, motivo de situação cadastral e país são
poucos valores repetidos em dezenas de milhões de linhas, então a descrição mora na tabela
`dominio` e a empresa guarda apenas o código. A API recompõe o par na resposta.

`telefones` e `QSA` ficam em `jsonb`, por serem listas de tamanho variável. O `QSA` é o campo mais
pesado da base e entra inteiro, sem recorte.

### Disco

A base tem dezenas de milhões de empresas e é um clone integral do OpenCNPJ, sem tirar nem pôr:
todo campo da fonte, quadro societário incluído, está no banco. Reserve espaço para a tabela no PostgreSQL. Você pode usar a flag `--limpar` no script de atualização para garantir que o zip e os arquivos temporários extraídos sejam removidos automaticamente após a importação.

### Ajustes do Postgres

O `compose.yaml` sobe o banco com `synchronous_commit=off`, `max_wal_size=8GB` e
`maintenance_work_mem=1GB`, o que muda muito o tempo de carga. É seguro aqui porque o conteúdo é
derivado: se o servidor cair no meio de uma ingestão, a saída é rodar a ingestão de novo, não
recuperar transação.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Cobrem a conversão de cada registro do `.ndjson` em linha do banco, que é onde a base costuma
morder: data vazia ou `0000-00-00`, capital social em formato brasileiro, string vazia que precisa
virar nulo, CNPJ curto e descrições que vão para `dominio`.

## Usar a partir do FTNF

Na mesma VM, o endereço é `http://localhost:8006`. Para o FTNF falar com o container por nome,
acrescente a rede `base-cnpj` ao serviço que vai consultar, ou publique a API na `ftnf-network`.
