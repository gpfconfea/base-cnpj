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

`app.baixar` puxa o `data.zip` da fonte e extrai só os `.ndjson`. Aceita `--pular-download`,
`--pular-extracao` e `--remover-zip`.

O download sempre recomeça do zero: a origem anuncia `Accept-Ranges: bytes` mas responde `200` com o
arquivo inteiro a um `GET` com `Range`, e retomar em modo append corrompia o zip. O arquivo é gravado
em `data.zip.parcial` e só vira `data.zip` depois de o tamanho bater com o `Content-Length`; queda de
conexão refaz o download (`DOWNLOAD_TENTATIVAS`, padrão 5).

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
| `excluir_municipios` | códigos a deixar de fora, separados por vírgula |
| `porte` | `Demais`, `ME`, `EPP` |
| `matriz` | `true` só matriz, `false` só filial |
| `inclui_secundario` | também quem tem o CNAE como secundário |
| `formato` | `resumo` (padrão) ou `completo` |
| `amostra` / `semente` | sorteio no conjunto todo, até 1000 |
| `limit` / `offset` | paginação, até 1000 por página |

O formato `resumo` traz o que basta para pareamento: CNPJ, razão social, CNAE com descrição,
situação, data de início, porte, matriz ou filial, UF e município.

#### Sortear em vez de paginar

```bash
curl "http://localhost:8006/cnae/7112000?uf=GO&situacao=Ativa&excluir_municipios=9373,9227&amostra=40&semente=evento-12"
```

Paginação e sorteio respondem a perguntas diferentes. `limit`/`offset` percorre o
conjunto em ordem de CNPJ, que serve para varrer tudo. Já quem precisa de **uma amostra**
não pode usar as primeiras N linhas: CNPJ não é identificador neutro, ele cresce com a data
de registro, então a primeira página é a das empresas mais antigas do universo. Para montar
grupo de controle isso é viés puro, porque idade da empresa tem tudo a ver com o
comportamento que se quer comparar.

Com `amostra`, a ordenação passa a ser `md5(cnpj || semente)`: uma permutação arbitrária e
**reprodutível** do conjunto filtrado. A mesma semente devolve a mesma amostra enquanto a
carga for a mesma, o que permite reconferir depois de onde saiu um número; sementes
diferentes dão amostras independentes. O `offset` é ignorado nesse modo.

A resposta ganha três campos: `amostra`, `semente` e `total` (quantas empresas o filtro
alcança no banco todo). Os dois primeiros são ecoados de propósito, para o cliente
distinguir uma base que sorteou de uma versão antiga que ignorou os parâmetros e devolveu a
primeira página.

`excluir_municipios` existe pelo mesmo caso de uso: o controle tem que sair de fora dos
municípios onde a fiscalização passou, e filtrar depois de receber os dados desperdiçaria a
maior parte da amostra justamente onde a atividade se concentra. Empresa sem município
cadastrado também sai, já que não dá para garantir que ela não esteja num dos códigos
excluídos.

O custo é ordenar por hash o conjunto filtrado, sem índice que ajude. Para um CNAE dentro de
uma UF, que é a consulta prevista, são milhares ou dezenas de milhares de linhas e a conta
sai em milissegundos. Um CNAE muito comum pedido sem `uf` é outra história: aí a ordenação
passa por milhões de linhas.

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
batem: chave primária em `cnpj`, índice composto em `(cnae_principal, uf, codigo_municipio)` e um
GIN em `cnaes_secundarios`.

Pela regra da coluna à esquerda, o composto atende `/cnae/{codigo}`, `?uf=GO` e
`?uf=GO&codigo_municipio=...` com um índice só. Vale saber que **filtrar por município sem informar a
UF** aproveita apenas o CNAE — passe os dois juntos. Índices separados de `uf` ou de `municipio` não
valeriam o espaço: são poucos valores distintos em dezenas de milhões de linhas, e o planejador
prefere varrer a tabela a saltar milhões de vezes no heap.

O GIN atende `?inclui_secundario=true`. Por isso a consulta usa `cnaes_secundarios @> ARRAY[...]` e
não `... = ANY(cnaes_secundarios)`: só a forma com o operador de continência é indexável.

Código e descrição de CNAE, qualificação do responsável, motivo de situação cadastral e país são
poucos valores repetidos em dezenas de milhões de linhas, então a descrição mora na tabela
`dominio` e a empresa guarda apenas o código. A API recompõe o par na resposta.

`telefones` e `QSA` ficam em `jsonb`, por serem listas de tamanho variável. O `QSA` é o campo mais
pesado da base e entra inteiro, sem recorte.

### Tabelas

#### `empresa`

Uma linha por estabelecimento. Definida em [`app/schema.py`](app/schema.py); a tabela é recriada a
cada carga, então esta é sempre a estrutura vigente.

**Identificação**

| Coluna | Tipo | Observação |
| --- | --- | --- |
| `cnpj` | `char(14)` | chave primária, só dígitos, com zeros à esquerda |
| `razao_social` | `text` | |
| `nome_fantasia` | `text` | |
| `matriz_filial` | `text` | `Matriz` ou `Filial` |
| `porte_empresa` | `text` | `ME`, `EPP` ou `Demais` |
| `natureza_juridica` | `text` | |
| `capital_social` | `numeric(18,2)` | `"1.234.567,89"` da fonte convertido na ingestão |
| `ente_federativo` | `text` | só para órgãos públicos |

**Situação cadastral**

| Coluna | Tipo | Observação |
| --- | --- | --- |
| `situacao_cadastral` | `text` | `Ativa`, `Baixada`, `Suspensa`, `Inapta`, `Nula` |
| `data_situacao_cadastral` | `date` | |
| `motivo_situacao_codigo` | `text` | descrição em `dominio`, tipo `motivo` |
| `situacao_especial` | `text` | |
| `data_situacao_especial` | `date` | |
| `data_inicio_atividade` | `date` | |

**Atividade econômica**

| Coluna | Tipo | Observação |
| --- | --- | --- |
| `cnae_principal` | `text` | descrição em `dominio`, tipo `cnae` |
| `cnaes_secundarios` | `text[]` | indexado com GIN |

**Endereço**

| Coluna | Tipo | Observação |
| --- | --- | --- |
| `tipo_logradouro` | `text` | |
| `logradouro` | `text` | |
| `numero` | `text` | texto porque a fonte traz `S/N` |
| `complemento` | `text` | |
| `bairro` | `text` | |
| `cep` | `text` | 8 dígitos, sem máscara |
| `uf` | `char(2)` | |
| `municipio` | `text` | nome |
| `codigo_municipio` | `text` | código da Receita |
| `nome_cidade_exterior` | `text` | |
| `codigo_pais` | `text` | descrição em `dominio`, tipo `pais` |

**Contato**

| Coluna | Tipo | Observação |
| --- | --- | --- |
| `email` | `text` | |
| `telefones` | `jsonb` | lista de `{ddd, numero, is_fax}` |

**Simples e MEI**

| Coluna | Tipo |
| --- | --- |
| `opcao_simples` | `text` |
| `data_opcao_simples` | `date` |
| `data_exclusao_simples` | `date` |
| `opcao_mei` | `text` |
| `data_opcao_mei` | `date` |
| `data_exclusao_mei` | `date` |

**Quadro societário**

| Coluna | Tipo | Observação |
| --- | --- | --- |
| `qualificacao_responsavel_codigo` | `text` | descrição em `dominio`, tipo `qualificacao` |
| `qsa` | `jsonb` | lista de sócios, inteira, sem recorte |

Campo vazio na fonte vira `NULL`, e data ausente ou `0000-00-00` também. O byte nulo (`0x00`), que a
fonte traz dentro de alguns nomes, é removido na ingestão: o PostgreSQL o recusa em `text` e em
`jsonb`.

#### `dominio`

Descrições que se repetem em dezenas de milhões de linhas. Criada por
[`sql/001_bootstrap.sql`](sql/001_bootstrap.sql).

| Coluna | Tipo | Observação |
| --- | --- | --- |
| `tipo` | `text` | `cnae`, `motivo`, `qualificacao` ou `pais` |
| `codigo` | `text` | |
| `descricao` | `text` | |

Chave primária em `(tipo, codigo)`.

#### `carga`

Histórico de importações, exposto em `GET /carga`.

| Coluna | Tipo | Observação |
| --- | --- | --- |
| `id` | `bigserial` | chave primária |
| `status` | `text` | `em_andamento`, `concluida` ou `falhou` |
| `iniciada_em` | `timestamptz` | |
| `concluida_em` | `timestamptz` | |
| `arquivos` | `integer` | quantos `.ndjson` a carga tinha |
| `total_registros` | `bigint` | |
| `erro` | `text` | mensagem, quando `falhou` |

Índice `carga_concluida_idx` em `(concluida_em DESC NULLS LAST)`.

### Índices

| Índice | Tabela | Definição | Tamanho |
| --- | --- | --- | ---: |
| `empresa_pkey` | `empresa` | `PRIMARY KEY (cnpj)` | ~2,2 GB |
| `empresa_cnae_uf_municipio_idx` | `empresa` | `(cnae_principal, uf, codigo_municipio)` | ~700 MB |
| `empresa_cnae_sec_idx` | `empresa` | `GIN (cnaes_secundarios)` | ~600 MB |
| `dominio_pkey` | `dominio` | `PRIMARY KEY (tipo, codigo)` | KB |
| `carga_pkey` | `carga` | `PRIMARY KEY (id)` | KB |
| `carga_concluida_idx` | `carga` | `(concluida_em DESC NULLS LAST)` | KB |

Tamanhos para as ~72,7 milhões de linhas da carga de setembro de 2026. Os índices de `empresa` são
criados **depois** do `COPY`, nunca antes: manter btree durante a carga trocaria escrita sequencial
por escrita aleatória e multiplicaria o tempo de ingestão.

Para conferir o que existe de fato no banco:

```sql
SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass))
FROM pg_indexes WHERE tablename = 'empresa';
```

### Onde ficam o zip e os `.ndjson`

No volume nomeado `dados`, dentro da VM do Docker — não numa pasta do host. Bind mount de pasta
Windows no Docker Desktop passa por uma ponte de filesystem (9p/gRPC-FUSE) que derruba a escrita
sequencial em várias vezes, e aqui são ~14 GB de zip mais as dezenas de GB de `.ndjson` extraídos.

São arquivos descartáveis, só o ingestor os lê. Para liberar o espaço depois da carga:

```bash
docker volume rm base-cnpj_dados
```

Se por algum motivo você precisar dos arquivos visíveis no host, aponte `DATA_VOLUME` para um
caminho (`DATA_VOLUME=./data`), ciente da perda de desempenho.

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
