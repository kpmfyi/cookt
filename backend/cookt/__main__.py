"""`python -m cookt` runs the server (see scripts/demo.sh)."""

import logging

import uvicorn

from .config import settings

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run("cookt.app:asgi", host=settings.host, port=settings.port, proxy_headers=True)
