import argparse

import uvicorn
from fastapi import FastAPI

parser = argparse.ArgumentParser(description="Run a ChaosProxy dummy backend.")
parser.add_argument("--port", type=int, required=True, help="Port to listen on.")
parser.add_argument("--name", required=True, help="Name reported by this backend.")
args = parser.parse_args()

app = FastAPI()


@app.get("/")
async def root() -> dict[str, str | int]:
    return {"backend": args.name, "port": args.port}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port)