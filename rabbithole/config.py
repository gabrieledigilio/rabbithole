"""Settings. Defaults match docker-compose.yml; override in a file called .env next to README.md
or in the environment."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env = os.path.join(ROOT, ".env")
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8"):
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.strip().split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def env(name, default=""):
    return os.environ.get(name, default)


# --- MariaDB. Port 3309: clear of a local MariaDB (3306) and the two starter demos (3307, 3308).
DB = dict(host=env("DB_HOST", "127.0.0.1"), port=int(env("DB_PORT", "3309")), user=env("DB_USER", "rabbithole"),
          password=env("DB_PASSWORD", "rabbithole"), database=env("DB_NAME", "rabbithole"))

# --- The embedding model for questions: the same bge-m3 that made the article vectors, through
# Ollama (as in mariadb-vector-wikipedia-rag). Without it the app still runs: keyword search only.
OLLAMA_URL = env("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = env("EMBED_MODEL", "bge-m3")
EMBED_CONTEXT = 8192

# --- The model that writes answers: any OpenAI-compatible chat endpoint. The default is Ollama on
# this machine; LLM_MODEL empty = the first Gemma (or any chat model) Ollama has pulled.
LLM_BASE_URL = env("LLM_BASE_URL", OLLAMA_URL.rstrip("/") + "/v1")
LLM_API_KEY = env("LLM_API_KEY", "")
LLM_MODEL = env("LLM_MODEL", "")

# How many candidates one vector index search looks at. 80 with M=16 found 100% of the exact
# top 10 in the RAG demo's measurements (the default, 20, found 79%).
EF_SEARCH = int(env("EF_SEARCH", "80"))

DATA = env("DATA_DIR", os.path.join(ROOT, "data"))
PORT = int(env("PORT", "8000"))
WIKI = "https://fi.wikipedia.org"
