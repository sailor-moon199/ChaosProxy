import argparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request

parser = argparse.ArgumentParser(description="Run a ChaosProxy dummy backend.")
parser.add_argument("--port", type=int, required=True, help="Port to listen on.")
parser.add_argument("--name", required=True, help="Name reported by this backend.")
args = parser.parse_args()

app = FastAPI()
app.state.failing = False


@app.get("/")
async def root(request: Request) -> dict[str, str | int]:
    if request.app.state.failing:
        raise HTTPException(status_code=500, detail="Backend is in failing mode")
    return {"backend": args.name, "port": args.port}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/fail")
async def fail() -> dict[str, str]:
    app.state.failing = True
    return {"status": "failing"}


@app.post("/heal")
async def heal() -> dict[str, str]:
    app.state.failing = False
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port)