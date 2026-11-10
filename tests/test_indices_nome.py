from app.schema import INDICES, INDICES_BUSCA_NOME, PREPARO_BUSCA_NOME, comandos_concorrentes


def test_preparo_vem_antes_dos_indices_de_nome_na_carga():
    posicoes = {comando: n for n, comando in enumerate(INDICES)}
    extensao = posicoes["CREATE EXTENSION IF NOT EXISTS pg_trgm"]
    funcao = next(n for c, n in posicoes.items() if "FUNCTION nome_chave" in c)
    primeiro_indice = min(n for c, n in posicoes.items() if "trgm_ops" in c or "nome_chave(razao_social)" in c)
    assert extensao < funcao < primeiro_indice


def test_indices_de_nome_sao_parciais_nas_ativas():
    assert len(INDICES_BUSCA_NOME) == 4
    for _, definicao in INDICES_BUSCA_NOME:
        assert definicao.endswith("WHERE situacao_cadastral = 'Ativa'")


def test_trigramas_e_chave_na_razao_e_no_fantasia():
    definicoes = dict(INDICES_BUSCA_NOME)
    assert "gin (razao_social gin_trgm_ops)" in definicoes["empresa_razao_trgm_idx"]
    assert "gin (nome_fantasia gin_trgm_ops)" in definicoes["empresa_fantasia_trgm_idx"]
    assert definicoes["empresa_razao_chave_idx"].startswith("(nome_chave(razao_social))")
    assert definicoes["empresa_fantasia_chave_idx"] == "(nome_chave(nome_fantasia)) WHERE situacao_cadastral = 'Ativa'"


def test_carga_cria_a_chave_do_fantasia():
    assert (
        "CREATE INDEX empresa_fantasia_chave_idx ON empresa "
        "(nome_chave(nome_fantasia)) WHERE situacao_cadastral = 'Ativa'"
    ) in INDICES


def test_funcao_de_chave_e_imutavel():
    funcao = next(c for c in PREPARO_BUSCA_NOME if "FUNCTION nome_chave" in c)
    assert "CREATE OR REPLACE FUNCTION nome_chave(texto text)" in funcao
    assert "IMMUTABLE" in funcao


def test_versao_concorrente_e_idempotente():
    preparo, indices = comandos_concorrentes()
    assert preparo == PREPARO_BUSCA_NOME
    assert [nome for nome, _ in indices] == [nome for nome, _ in INDICES_BUSCA_NOME]
    for nome, comando in indices:
        assert comando.startswith(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {nome} ON empresa ")


def test_script_temporario_cria_so_a_chave_do_fantasia_de_forma_idempotente():
    import importlib.util
    import pathlib

    caminho = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "temp_criar_indice_fantasia.py"
    especificacao = importlib.util.spec_from_file_location("temp_criar_indice_fantasia", caminho)
    script = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(script)

    preparo, comando = script.comando_do_indice()

    assert preparo == PREPARO_BUSCA_NOME
    assert comando == (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS empresa_fantasia_chave_idx ON empresa "
        "(nome_chave(nome_fantasia)) WHERE situacao_cadastral = 'Ativa'"
    )
