import os
from urllib.parse import quote


def _uri(valor):
    """Escapa usuario/senha/base: sem isso um "@" ou "#" na senha quebra a URI."""
    return quote(valor, safe="")


DB_DSN = "postgresql://{u}:{s}@{h}:{p}/{d}".format(
    u=_uri(os.getenv("POSTGRES_USER", "basecnpj")),
    s=_uri(os.getenv("POSTGRES_PASSWORD", "")),
    h=os.getenv("POSTGRES_HOST", "db"),
    p=os.getenv("POSTGRES_PORT", "5432"),
    d=_uri(os.getenv("POSTGRES_DB", "basecnpj")),
)

DATA_DIR = os.getenv("DATA_DIR", "/data")
FONTE_ZIP = os.getenv("FONTE_ZIP", "https://file.opencnpj.org/releases/receita/data.zip")

WORKERS = int(os.getenv("INGEST_WORKERS", "4"))
