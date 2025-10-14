from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Annotated, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, constr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import load_settings
from db import get_engine, get_session_factory, init_db
from db.models import Transaction


settings = load_settings()

engine = get_engine(settings.database_url or None)
init_db(engine)
SessionFactory = get_session_factory(engine)

app = FastAPI(title="Payments Ingestion API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer = HTTPBearer(auto_error=True)


class TransactionCreate(BaseModel):
    name: constr(min_length=1, max_length=255)
    price: Decimal = Field(gt=Decimal("0"))
    currency: constr(min_length=3, max_length=3) = "USD"
    status: constr(min_length=1, max_length=32) | None = None
    metadata: Optional[Dict[str, str]] = None


class TransactionResponse(BaseModel):
    transaction_id: str
    created_at: str
    amount_cents: int
    currency: str
    status: str


def get_db() -> Session:
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]
) -> str:
    token = credentials.credentials
    if token != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )
    return token


def _amount_to_cents(amount: Decimal) -> int:
    try:
        return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, OverflowError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid price value",
        )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post(
    "/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_transaction(
    payload: TransactionCreate,
    _: Annotated[str, Depends(require_api_key)],
    session: Annotated[Session, Depends(get_db)],
):
    amount_cents = _amount_to_cents(payload.price)

    transaction = Transaction(
        api_key=settings.api_key,
        customer_name=payload.name,
        amount_cents=amount_cents,
        currency=payload.currency or settings.currency,
        status=payload.status or "success",
        metadata=payload.metadata,
    )

    session.add(transaction)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate transaction",
        ) from exc

    return TransactionResponse(
        transaction_id=transaction.transaction_id,
        created_at=transaction.created_at.isoformat()
        if transaction.created_at
        else "",
        amount_cents=transaction.amount_cents,
        currency=transaction.currency,
        status=transaction.status,
    )
