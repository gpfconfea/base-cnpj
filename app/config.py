import os

DB_DSN = "postgresql://{u}:{s}@{h}:{p}/{d}".format(
    u=os.getenv("POSTGRES_USER", "basecnpj"),
    s=os.getenv("POSTGRES_PASSWORD", ""),
    h=os.getenv("POSTGRES_HOST", "db"),
    p=os.getenv("POSTGRES_PORT", "5432"),
    d=os.getenv("POSTGRES_DB", "basecnpj"),
)

DATA_DIR = os.getenv("DATA_DIR", "/data")
FONTE_ZIP = os.getenv("FONTE_ZIP", "https://file.opencnpj.org/releases/receita/data.zip")

WORKERS = int(os.getenv("INGEST_WORKERS", "4"))
