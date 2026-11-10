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
./scripts/atualizar.sh
```

O script baixa, extrai, ingere e limpa automaticamente o ZIP e os `.ndjson` ao final de uma carga bem-sucedida.
O argumento `--limpar` continua aceito por compatibilidade, mas não é mais necessário.

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
`--apagar` (para remover os `.ndjson` processados no final). No fim, depois dos índices, refaz as
tabelas derivadas (`municipio`).

Base carregada antes de a tabela `municipio` existir só precisa do passo de derivação, sem
recarregar nada:

```bash
docker compose --profile manual run --rm ingestor -m app.derivar
```



O histórico de cargas fica na tabela `carga` e em `GET /carga`.

## Endpoints

| Rota | Para que serve |
| --- | --- |
| `GET /health` | disponibilidade e se já existe base carregada |
| `GET /carga` | as dez últimas cargas, com contagem e status |
| `GET /{cnpj}` | dados de uma empresa |
| `GET /cnae/{codigo}` | empresas com o CNAE informado |
| `POST /busca` | os mesmos filtros de `/cnae/{codigo}`, em JSON, mais uma lista de CNPJs a excluir |
| `GET /cnaes` | códigos CNAE com descrição, por início do código ou trecho do texto |
| `GET /municipios` | municípios de uma UF com empresas na base |
| `POST /cnpjs` | consulta em lote, até 1000 CNPJs |
| `GET /nome` | empresas ativas por nome, por trecho, início ou casamento exato |
| `POST /nomes` | casamento exato em lote, por razão social ou nome fantasia, até 1000 nomes, com os candidatos de cada um |

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
| `capital_min` / `capital_max` | faixa de capital social |
| `inicio_de` / `inicio_ate` | faixa da data de início de atividade (`AAAA-MM-DD`) |
| `cnaes_secundarios` | só quem tem ao menos um destes códigos como CNAE secundário, separados por vírgula |
| `com_total` | `true` também devolve `total`, quantas empresas o filtro alcança |
| `formato` | `resumo` (padrão) ou `completo` |
| `amostra` / `semente` | sorteio no conjunto todo, até 1000 |
| `limit` / `offset` | paginação, até 1000 por página |

O formato `resumo` traz o que basta para pareamento e para listar: CNPJ, razão social, CNAE com
descrição, situação, data de início, porte, matriz ou filial, UF, município e capital social (número).

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

### `POST /busca`

```bash
curl -X POST http://localhost:8006/busca   -H 'Content-Type: application/json'   -d '{"cnae": "7112000", "uf": "GO", "situacao": "Ativa", "capital_min": 100000,
       "inicio_de": "2015-01-01", "cnaes_secundarios": ["4120400"],
       "excluir_cnpjs": ["00081395000102"], "limit": 50, "offset": 0}'
```

Mesmos filtros de `GET /cnae/{codigo}` e mesma resposta, com `total` incluído por padrão
(`com_total: false` desliga). O CNAE vai em `cnae` (um) ou `cnaes` (lista, até 100): a empresa entra
se tiver qualquer um deles como principal (ou como secundário, com `inclui_secundario`). A diferença é `excluir_cnpjs`, até 20.000 CNPJs que ficam fora do
resultado. O caso de uso é a preparação de evento no FTNF: quem monta a lista de alvos refaz a busca
e não quer ver de novo quem já está nela, e a lista tem centenas ou milhares de CNPJs, mais do que
cabe numa query string. Não há sorteio nesta rota; para amostra, use `GET /cnae/{codigo}`.

### `GET /cnaes`

```bash
curl "http://localhost:8006/cnaes?q=engenharia"
curl "http://localhost:8006/cnaes?q=711"
curl "http://localhost:8006/cnaes?codigos=7112000,4120400"
```

Termo só com dígitos casa o início do código; com letras, casa trecho da descrição, sem acento e
sem diferença de caixa. `codigos` devolve só os informados, para recompor rótulos. Lido do cache de
`dominio`, sem ir ao banco. Pagina com `limit` (padrão 50, até 2000) e `offset`, e a resposta traz
`total` para o cliente saber se há mais.

### `GET /municipios`

```bash
curl "http://localhost:8006/municipios?uf=GO&q=goi"
```

Municípios da UF que têm empresa na base, do mais populoso ao menos, cada um com `codigo` (o da
Receita, o mesmo aceito em `codigo_municipio`), `nome` e `empresas`. Vem da tabela derivada
`municipio`; numa carga anterior à tabela a rota responde `503` até rodar `app.derivar`.

### `POST /cnpjs`

```bash
curl -X POST http://localhost:8006/cnpjs \
  -H 'Content-Type: application/json' \
  -d '{"cnpjs": ["99017782000139", "00000000000191"]}'
```

Devolve os encontrados e a lista de `nao_encontrados`. Existe porque enriquecer milhares de CNPJs
uma requisição por vez levaria minutos.

### `GET /nome`

```bash
curl "http://localhost:8006/nome?q=engenharia&uf=GO&modo=contem&limit=50"
curl "http://localhost:8006/nome?q=engenharia%20civil&modo=inicio&limit=20"
curl "http://localhost:8006/nome?q=M.A.%20Engenharia%20Ltda&modo=exato&excluir_pessoa_fisica=true"
```

| Parâmetro | Efeito |
| --- | --- |
| `q` | nome ou trecho, com ao menos 3 letras ou números; 5 em `contem` e `inicio` sem `uf` |
| `modo` | `contem` (padrão), `inicio` ou `exato` |
| `uf` | opcional; sem ela, a busca vale para o país todo, com os cuidados abaixo |
| `municipio` | código do município na Receita |
| `situacao` | só `Ativa` (padrão); outro valor responde `400` |
| `excluir_pessoa_fisica` | `true` tira empresário individual, produtor rural pessoa física, candidato a cargo político e MEI |
| `limit` / `offset` | paginação, até 500 por página; 50 em `contem` e `inicio` sem `uf` |

`contem` e `inicio` procuram o termo na razão social e no nome fantasia, já em maiúsculas e sem
acento, com a pontuação mantida. `exato` compara só a razão social pela chave de casamento: maiúsculas,
sem acento, ponto e apóstrofo removidos, os demais sinais viram espaço e os espaços repetidos se juntam.
Assim `M.A. Engenharia Ltda.` e `MA ENGENHARIA LTDA` casam; `M. A. ENGENHARIA` vira `M A ENGENHARIA` e
não casa com os dois.

A resposta traz `total`, `mais` (se há outra página) e uma lista enxuta: `cnpj`, `razao_social`, `nome_fantasia`,
`natureza_juridica`, `situacao_cadastral`, `matriz_filial`, `matriz` (booleano), `uf`, `municipio`,
`codigo_municipio` e `cnae_principal`. Sem QSA, telefone nem e-mail; o restante sai de `GET /{cnpj}`.

Os limites existem porque a busca só é rápida dentro dos índices, que cobrem apenas empresas ativas.

`contem` e `inicio` sem `uf` usam os mesmos índices de trigramas, que cobrem o país, mas um trecho
comum alcança muitas linhas. Por isso, só nesse caso: o termo precisa de 5 letras ou números, o
`limit` vai até 50, `total` volta `null` (contar tudo custaria mais que a busca) e `mais` diz se há
outra página. A consulta tem 8 segundos; se estourar, a resposta é `504`, e o caminho é um trecho
mais específico ou a `uf`. Com `uf`, e no modo `exato`, `total` continua contado.

### `POST /nomes`

```bash
curl -X POST http://localhost:8006/nomes \
  -H 'Content-Type: application/json' \
  -d '{"nomes": ["M.A. Engenharia Ltda", "Construtora Ávila S.A."], "uf": "GO"}'
```

| Campo | Efeito |
| --- | --- |
| `nomes` | até 1000 nomes |
| `uf` | opcional, restringe os candidatos à UF |
| `excluir_pessoa_fisica` | ligado por padrão, mesmo critério de `GET /nome` |
| `max_candidatos` | quantos candidatos devolver por nome, padrão 20, até 100 |

Cada nome casa pela chave com a razão social **ou** com o nome fantasia. Para cada nome, na ordem
enviada, devolve `nome`, a `chave` de casamento calculada pelo banco, `total` de candidatos ativos
distintos com essa chave e `candidatos` no mesmo formato enxuto de `GET /nome`, mais `casou_por`:
`razao_social`, `nome_fantasia` ou `ambos`. Quem casou pela razão social vem antes de quem casou só
pelo fantasia; dentro de cada grupo, matrizes primeiro. `total` maior que `max_candidatos` indica que
a lista foi cortada; `total` zero, que nenhuma empresa ativa casou.

```json
{
  "casa_por_fantasia": true,
  "nomes": [
    {
      "nome": "M.A. Engenharia Ltda",
      "chave": "MA ENGENHARIA LTDA",
      "total": 2,
      "candidatos": [
        {"cnpj": "12345678000190", "razao_social": "MA ENGENHARIA LTDA", "nome_fantasia": "", "...": "...",
         "matriz": true, "casou_por": "razao_social"},
        {"cnpj": "98765432000110", "razao_social": "SILVA E SOUZA PROJETOS LTDA",
         "nome_fantasia": "M.A. ENGENHARIA LTDA", "...": "...", "matriz": true, "casou_por": "nome_fantasia"}
      ]
    }
  ]
}
```

Razão social e fantasia são duas consultas, cada uma no seu índice de chave, unidas e sem CNPJ
repetido. Enquanto `empresa_fantasia_chave_idx` não existir (ou estiver sendo criado), o casamento
pelo fantasia exigiria varrer as ativas a cada nome; nesse caso a rota casa só pela razão social, como
antes, e responde `casa_por_fantasia: false`.

## Modelo de dados

Uma linha por CNPJ em `empresa`, com colunas tipadas. Índices, por ora, só onde as consultas
batem: chave primária em `cnpj`, índice composto em `(cnae_principal, uf, codigo_municipio)` e um
GIN em `cnaes_secundarios`.

Pela regra da coluna à esquerda, o composto atende `/cnae/{codigo}`, `?uf=GO` e
`?uf=GO&codigo_municipio=...` com um índice só. Vale saber que **filtrar por município sem informar a
UF** aproveita apenas o CNAE — passe os dois juntos. Índices separados de `uf` ou de `municipio` não
valeriam o espaço: são poucos valores distintos em dezenas de milhões de linhas, e o planejador
prefere varrer a tabela a saltar milhões de vezes no heap.

O GIN atende `?inclui_secundario=true` e `cnaes_secundarios=...`. Por isso a consulta usa
`cnaes_secundarios @> ARRAY[...]` e `cnaes_secundarios && ARRAY[...]`, nunca
`... = ANY(cnaes_secundarios)`: só os operadores de continência e sobreposição são indexáveis. Os
filtros de capital social e data de início não têm índice próprio: aplicam-se sobre as linhas que o
composto já selecionou por CNAE e UF, que são milhares, não milhões.

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

#### `municipio`

Derivada de `empresa` no fim de cada carga (`app.derivar`): uma linha por par UF e código de
município com ao menos uma empresa. Existe porque listar os municípios de uma UF direto em
`empresa` é um `GROUP BY` sobre milhões de linhas a cada abertura de tela.

| Coluna | Tipo | Observação |
| --- | --- | --- |
| `uf` | `char(2)` | |
| `codigo` | `text` | código do município na Receita |
| `nome` | `text` | |
| `empresas` | `bigint` | quantas empresas têm esse município |

Chave primária em `(uf, codigo)`.

### Índices

| Índice | Tabela | Definição | Tamanho |
| --- | --- | --- | ---: |
| `empresa_pkey` | `empresa` | `PRIMARY KEY (cnpj)` | ~2,2 GB |
| `empresa_cnae_uf_municipio_idx` | `empresa` | `(cnae_principal, uf, codigo_municipio)` | ~700 MB |
| `empresa_cnae_sec_idx` | `empresa` | `GIN (cnaes_secundarios)` | ~600 MB |
| `empresa_razao_trgm_idx` | `empresa` | `GIN (razao_social gin_trgm_ops)`, só ativas | ~2,8 GB (estimado) |
| `empresa_fantasia_trgm_idx` | `empresa` | `GIN (nome_fantasia gin_trgm_ops)`, só ativas | ~0,5 GB (estimado) |
| `empresa_razao_chave_idx` | `empresa` | `(nome_chave(razao_social))`, só ativas | ~1,5 GB (estimado) |
| `empresa_fantasia_chave_idx` | `empresa` | `(nome_chave(nome_fantasia))`, só ativas | ~0,6 GB (estimado) |
| `dominio_pkey` | `dominio` | `PRIMARY KEY (tipo, codigo)` | KB |
| `carga_pkey` | `carga` | `PRIMARY KEY (id)` | KB |
| `carga_concluida_idx` | `carga` | `(concluida_em DESC NULLS LAST)` | KB |
| `municipio_pkey` | `municipio` | `PRIMARY KEY (uf, codigo)` | KB |

Tamanhos para as ~72,7 milhões de linhas da carga de setembro de 2026. Os índices de `empresa` são
criados **depois** do `COPY`, nunca antes: manter btree durante a carga trocaria escrita sequencial
por escrita aleatória e multiplicaria o tempo de ingestão.

Os quatro últimos atendem `GET /nome` e `POST /nomes`. São parciais em `situacao_cadastral = 'Ativa'`,
cerca de 29 milhões das 75 milhões de linhas, e a consulta repete essa condição literalmente para o
planejador poder usá-los. Dependem da extensão `pg_trgm` e da função `nome_chave`, que a carga cria
antes deles. `nome_chave` tira acento e põe em maiúsculas por conta própria, então o casamento vale
igual numa tabela carregada antes da normalização da razão social na ingestão.

#### Criar os índices de nome numa base já carregada

Temporário, até a próxima carga, que já os cria: `scripts/temp_criar_indices_nome.py` instala
`pg_trgm`, cria `nome_chave` e os quatro índices com `CREATE INDEX CONCURRENTLY IF NOT EXISTS`, sem
bloquear a API. Pode rodar de novo: o que já existe é pulado. Antes de criar, mostra o tamanho
estimado e o espaço livre no servidor do banco, e para se o livre não chegar ao dobro do estimado. No
fim, mostra o tamanho real e o tempo de cada índice.

```bash
# fora do Docker, com as credenciais do .env
python scripts/temp_criar_indices_nome.py --host 192.168.1.18 --porta 5436

# dentro do Docker
docker compose --profile manual run --rm -v ./app:/app/app -v ./scripts:/app/scripts \
  ingestor scripts/temp_criar_indices_nome.py
```

`--estimar` só mostra a estimativa. O espaço livre é medido com `df` no servidor, via
`COPY ... FROM PROGRAM`, o que exige superusuário; sem isso, meça na VM e passe `--livre-gb`. Se uma
tentativa cair no meio, o índice fica inválido e o script para, pedindo `DROP INDEX CONCURRENTLY`
antes de tentar de novo.

Base que já tem os três primeiros e só precisa da chave do nome fantasia:
`scripts/temp_criar_indice_fantasia.py` cria apenas `empresa_fantasia_chave_idx`, com os mesmos
cuidados (concorrente, idempotente, estimativa, espaço livre, parada em índice inválido) e roda
`ANALYZE empresa` no fim. Os mesmos argumentos valem:

```bash
python scripts/temp_criar_indice_fantasia.py --host 192.168.1.18 --porta 5436
```

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
virar nulo, CNPJ curto e descrições que vão para `dominio`. Razão social e nome fantasia saem em
maiúsculas, sem acento e sem espaços sobrando, com a pontuação mantida.

## Usar a partir do FTNF

Na mesma VM, o endereço é `http://localhost:8006`. Para o FTNF falar com o container por nome,
acrescente a rede `base-cnpj` ao serviço que vai consultar, ou publique a API na `ftnf-network`.
