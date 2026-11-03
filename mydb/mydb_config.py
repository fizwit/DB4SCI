# mydb_config.py
#
# Read mydb_config.yml and Environment variables

import os
from pathlib import Path
from types import SimpleNamespace

import yaml
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env", override=False)
CONFIG_FILE = HERE / "mydb_config.yml"

ENV_VARS = {
    "AWS_BUCKET_NAME": None,
    "AWS_ACCESS_KEY_ID": None,
    "AWS_SECRET_ACCESS_KEY": None,
    "AWS_DEFAULT_REGION": "us-west-2",
    "FLASK_SECRET": None,
    "FLASK_DEBUG": False,
    "SQLALCHEMY_ADMIN_URI": None,
    "SQLALCHEMY_MIGRATE_URI": None,
    "DB4SCI_ENV": "dev",
    "PG_ADMIN": None,
    "PG_ADMIN_PASS": None,
    "MARIADB_ROOT_PASSWORD": None,
    "SWARM_DRIVER": None,
    "SWARM_TYPE": None,
    "SWARM_OPTS": None,
    "SWARM_DEVICE": None,
    "container_host": None,
    "container_domain": None,
    "ADServer": None,
    "ADDomain": None,
    "ADSearchBase": None,
}

def _to_namespace(obj):
    """Recursively convert dicts to SimpleNamespace so nested keys
    are also accessible as attributes (config.db.host)."""
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_namespace(i) for i in obj]
    return obj


def load_config(path=CONFIG_FILE):
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    # Merge environment variables into the same namespace.
    # Precedence: environment > yaml > default
    for name, default in ENV_VARS.items():
        value = os.environ.get(name)
        if value is not None:
            data[name] = value
        else:
            data.setdefault(name, default)

    return _to_namespace(data)


config = load_config()
def default_image(dbengine):
    """Image the create form pre-selects: the first entry in ["V"]."""
    return getattr(config.dbs, dbengine).V[0].image

def mapped_volume(dbengine, image):
    """Container path the data volume is mounted on for `image`.

    The path is per version because the upstream images move it: Postgres 18
    relocated PGDATA to /var/lib/postgresql/<major>/docker and moved the
    image's VOLUME up to /var/lib/postgresql, while 17 and older keep the
    cluster in /var/lib/postgresql/data.  Mounting the wrong path does not
    error -- the volume is left empty and the database initializes into the
    container's writable layer, where it is lost on reschedule.

    Falls back to the engine-level "mapped_volume" for an image that is not
    listed (a container migrated from v1 can name an image we no longer offer).
    """
    engine = getattr(config.dbs, dbengine)
    for version in engine.V:
        if version.image == image:
            if getattr(version, "volume", None):
                return version.volume
            break
    fallback = getattr(engine, "mapped_volume", None)
    if not fallback:
        raise AppError(
            f"No volume path for {dbengine} image {image}: add it to "
            f"dbs.{dbengine}.V in mydb_config.yaml"
        )
    return fallback

# ---------------------------------------------------------------------------
# Test section: run with `python mydb/mydb_config.py`
# ---------------------------------------------------------------------------
def _print_namespace(ns, indent=0):
    """Print every variable in config, recursing into nested namespaces."""
    secret_words = ("SECRET", "PASSWORD", "TOKEN", "KEY")
    pad = "  " * indent
    for name, value in sorted(vars(ns).items()):
        if isinstance(value, SimpleNamespace):
            print(f"{pad}{name}:")
            _print_namespace(value, indent + 1)
        else:
            if value is not None and any(w in name.upper() for w in secret_words):
                value = "********"
            print(f"{pad}{name} = {value!r}")


if __name__ == "__main__":
    print(f"yaml file : {CONFIG_FILE}")
    print(f".env file : {HERE.parent / '.env'}")
    print("-" * 40)
    print("default_image('Postgres') = ",  default_image('Postgres'))
    print("-" * 40)
    print("mapped_volume('Postgres', 'postgres:18.6') = ", mapped_volume('Postgres', 'postgres:18.6'))
    print("-" * 40)
    _print_namespace(config)

