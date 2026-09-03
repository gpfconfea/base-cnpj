CREATE TABLE IF NOT EXISTS dominio (
    tipo      text NOT NULL,
    codigo    text NOT NULL,
    descricao text,
    PRIMARY KEY (tipo, codigo)
);

CREATE TABLE IF NOT EXISTS carga (
    id              bigserial PRIMARY KEY,
    status          text NOT NULL DEFAULT 'em_andamento',
    iniciada_em     timestamptz NOT NULL DEFAULT now(),
    concluida_em    timestamptz,
    arquivos        integer,
    total_registros bigint,
    erro            text
);

CREATE INDEX IF NOT EXISTS carga_concluida_idx ON carga (concluida_em DESC NULLS LAST);
