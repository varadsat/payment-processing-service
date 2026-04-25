from fastapi import FastAPI
from sqlalchemy import text

from app.api import events as events_router
from app.api import transactions as transactions_router
from app.db.engine import get_session

app = FastAPI(title="Payment Reconciliation Service")
app.include_router(events_router.router)
app.include_router(transactions_router.router)


@app.get("/health")
def health():
    session = next(get_session())
    try:
        session.execute(text("SELECT 1"))
    finally:
        session.close()
    return {"status": "ok"}
