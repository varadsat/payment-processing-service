from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.engine import get_session
from app.schemas.events import EventIn, IngestResult
from app.services.events import ingest_event

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=IngestResult)
def ingest_single(event: EventIn, session: Session = Depends(get_session)):
    with session.begin():
        result = ingest_event(session, event)
    status_code = 201 if result.status == "created" else 200
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status_code, content=result.model_dump())


@router.post("/bulk", response_model=list[IngestResult])
def ingest_bulk(events: list[EventIn], session: Session = Depends(get_session)):
    results = []
    for event in events:
        with session.begin():
            results.append(ingest_event(session, event))
    return results
