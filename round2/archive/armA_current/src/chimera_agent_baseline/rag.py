"""Guidelines retrieval via ChromaDB + embeddinggemma-300m.

The embedding model runs as a long-lived service started by the main
process (inference.py / run.py).  MCP server subprocesses connect to
it via a Unix socket to encode queries without loading the model.

Architecture::

    Main process (inference.py / run.py)
        └── EmbeddingService (subprocess, loads model once)
                └── listens on /tmp/chimera_embed.sock

    MCP server (short-lived per tool call)
        └── GuidelinesSearch.query()
                └── socket connect → encode query → ChromaDB search
"""

import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

COLLECTION_NAME = "guidelines"
DEFAULT_TOP_K = 5
SOCKET_PATH = Path(os.environ.get("CHIMERA_EMBED_SOCKET", "/tmp/chimera_embed.sock"))

# ---------------------------------------------------------------------------
# Embedding service — started once by the main process
# ---------------------------------------------------------------------------

_EMBED_SERVER_CODE = """\
import json, socket, sys, logging
logging.disable(logging.CRITICAL)

from sentence_transformers import SentenceTransformer
model = SentenceTransformer(sys.argv[1], device="cpu")

sock_path = sys.argv[2]
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(sock_path)
sock.listen(8)
print("ready", flush=True)

while True:
    conn, _ = sock.accept()
    try:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        query = json.loads(data.decode())
        embedding = model.encode_query(query).tolist()
        conn.sendall(json.dumps(embedding).encode())
    except Exception:
        pass
    finally:
        conn.close()
"""


class EmbeddingService:
    """Long-lived subprocess serving query embeddings over a Unix socket.

    Start this once from the main process before any MCP tool calls::

        svc = EmbeddingService("resources/embedding_model")
        # ... run agent ...
        svc.stop()
    """

    def __init__(self, model_path: str):
        log.info("Starting embedding service (model: %s)", model_path)
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _EMBED_SERVER_CODE, model_path, str(SOCKET_PATH)],
            stdout=subprocess.PIPE,
            text=True,
        )
        # Wait for "ready" signal
        line = self._proc.stdout.readline().strip()
        if line != "ready":
            raise RuntimeError(f"Embedding service failed to start: {line}")
        log.info("Embedding service ready at %s", SOCKET_PATH)

    def stop(self):
        if self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()


#def start_embedding_service(resource_dir: str | Path) -> EmbeddingService | None:
    #"""Start the embedding service if the model is available."""
    #model_path = Path(resource_dir) / "embedding_model"
    #if not model_path.exists():
        #log.info("No embedding model at %s — skipping embedding service", model_path)
        #return None
    #return EmbeddingService(str(model_path))

# The embedding model is passed as its own path, not assumed to live under
# resource_dir. In GC, resource_dir contains guidelines_db, while the embedding
# model can be provided separately under /opt/ml/model/embedding_model.
def start_embedding_service(embedding_model_dir: str | Path) -> EmbeddingService | None:
    """Start the embedding service if the embedding model is available."""
    model_path = Path(embedding_model_dir)
    if not model_path.exists():
        log.info("No embedding model at %s — skipping embedding service", model_path)
        return None
    return EmbeddingService(str(model_path))

# ---------------------------------------------------------------------------
# Socket client — used by GuidelinesSearch inside MCP subprocesses
# ---------------------------------------------------------------------------


def _embed_query(text: str) -> list[float]:
    """Encode a query via the embedding service socket."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(SOCKET_PATH))
    sock.sendall(json.dumps(text).encode())
    sock.shutdown(socket.SHUT_WR)
    data = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    return json.loads(data.decode())


# ---------------------------------------------------------------------------
# Guidelines search — used by the MCP server
# ---------------------------------------------------------------------------


class GuidelinesSearch:
    """Search over a pre-built ChromaDB collection of guideline chunks.

    Does NOT load the embedding model. Connects to the embedding
    service socket for query encoding — fast startup.
    """

    def __init__(self, resource_dir: str | Path):
        db_path = Path(resource_dir) / "guidelines_db"

        if not db_path.exists():
            raise FileNotFoundError(f"Guidelines DB not found at {db_path}. Run: python scripts/process_guidelines.py")

        import chromadb

        self._client = chromadb.PersistentClient(path=str(db_path))
        self._collection = self._client.get_collection(COLLECTION_NAME)
        log.info("Loaded guidelines collection: %d chunks", self._collection.count())

    def query(self, query_text: str, top_k: int = DEFAULT_TOP_K) -> list[dict]:
        """Search guidelines for passages relevant to the query."""
        if not SOCKET_PATH.exists():
            return [{"text": "Embedding service not running.", "page": None, "section": None, "score": None}]

        query_embedding = _embed_query(query_text)

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )

        hits = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results["distances"] else None
            hits.append(
                {
                    "text": doc,
                    "page": meta.get("page"),
                    "section": meta.get("section"),
                    "score": round(1 - distance, 4) if distance is not None else None,
                }
            )

        return hits
