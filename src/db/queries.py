from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from .models import Transaction


def get_recent_transactions(session: Session, limit: int = 20) -> List[Transaction]:
    stmt = (
        select(Transaction)
        .order_by(Transaction.created_at.desc())
        .limit(limit)
    )
    return session.execute(stmt).scalars().all()


def _scalar(session: Session, stmt, default=0):
    result = session.execute(stmt).scalar_one_or_none()
    return result if result is not None else default


def get_summary(session: Session) -> Dict[str, float]:
    total_amount_cents = _scalar(
        session, select(func.coalesce(func.sum(Transaction.amount_cents), 0))
    )
    total_transactions = _scalar(
        session, select(func.count(Transaction.id))
    )
    distinct_customers = _scalar(
        session, select(func.count(func.distinct(Transaction.customer_name)))
    )
    success_count = _scalar(
        session,
        select(func.count(Transaction.id)).where(Transaction.status == "success"),
    )

    avg_ticket_cents = (
        int(total_amount_cents / total_transactions)
        if total_transactions
        else 0
    )
    success_rate = (
        success_count / total_transactions if total_transactions else 0.0
    )

    latest = session.execute(
        select(Transaction.created_at).order_by(Transaction.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    return {
        "total_volume_cents": int(total_amount_cents),
        "total_transactions": int(total_transactions),
        "unique_customers": int(distinct_customers),
        "average_ticket_cents": int(avg_ticket_cents),
        "success_rate": success_rate,
        "latest_transaction_at": latest,
    }


def get_daily_volume(
    session: Session, days: int = 7
) -> List[Dict[str, float]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days - 1)
    stmt = (
        select(
            func.date(Transaction.created_at).label("day"),
            func.coalesce(func.sum(Transaction.amount_cents), 0).label("amount_cents"),
            func.count(Transaction.id).label("transactions"),
        )
        .where(Transaction.created_at >= cutoff)
        .group_by("day")
        .order_by("day")
    )
    result: Result = session.execute(stmt)

    return [
        {
            "day": row.day,
            "amount_cents": int(row.amount_cents),
            "transactions": int(row.transactions),
        }
        for row in result
    ]


def get_status_breakdown(session: Session) -> Dict[str, int]:
    stmt = (
        select(Transaction.status, func.count(Transaction.id))
        .group_by(Transaction.status)
    )

    breakdown: Dict[str, int] = defaultdict(int)
    for status, count in session.execute(stmt):
        if status is None:
            breakdown["unknown"] += int(count)
        else:
            breakdown[str(status)] += int(count)
    return dict(breakdown)


def get_transaction_by_id(
    session: Session, transaction_id: str
) -> Optional[Transaction]:
    stmt = select(Transaction).where(Transaction.transaction_id == transaction_id)
    return session.execute(stmt).scalar_one_or_none()
