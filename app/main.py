from fastapi import FastAPI
from sqlalchemy import text

from app.db.engine import get_session

app = FastAPI(title="Payment Reconciliation Service")


@app.get("/health")
def health():
    session = next(get_session())
    try:
        session.execute(text("SELECT 1"))
    finally:
        session.close()
    return {"status": "ok"}
