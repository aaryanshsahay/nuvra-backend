from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, Index, Integer, JSON, String, Text, func

from . import Base


def generate_transaction_id() -> str:
    return str(uuid4())


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(
        String(36), nullable=False, unique=True, default=generate_transaction_id
    )
    api_key = Column(String(128), nullable=False, index=True)
    customer_name = Column(String(255), nullable=False)
    customer_email = Column(String(255), nullable=True, index=True)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(String(32), nullable=False, default="success")
    extra = Column(JSON, nullable=True)
    country = Column(String(64), nullable=True)
    city = Column(String(128), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )


Index("ix_transactions_api_key_created_at", Transaction.api_key, Transaction.created_at)


class Merchant(Base):
    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    api_key_hash = Column(String(128), nullable=False, unique=True)
    api_key_prefix = Column(String(16), nullable=False)
    contact_email = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


Index("ix_merchants_api_key_prefix", Merchant.api_key_prefix)
