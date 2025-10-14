from __future__ import annotations

from datetime import datetime
from typing import Dict, List

import pandas as pd
import streamlit as st

from config import load_settings
from db import get_engine, init_db, session_scope
from db.queries import (
    get_daily_volume,
    get_recent_transactions,
    get_status_breakdown,
    get_summary,
)


settings = load_settings()


@st.cache_resource
def ensure_database_ready():
    engine = get_engine(settings.database_url or None)
    init_db(engine)
    return engine


def _format_currency(amount_cents: int) -> str:
    dollars = amount_cents / 100
    return f"{settings.currency} {dollars:,.2f}"


def _recent_transactions_rows(transactions) -> List[Dict[str, str]]:
    rows = []
    for tx in transactions:
        created = tx.created_at
        if isinstance(created, datetime):
            created_display = created.isoformat()
        else:
            created_display = str(created)

        rows.append(
            {
                "Transaction": tx.transaction_id,
                "Customer": tx.customer_name,
                "Amount": _format_currency(tx.amount_cents),
                "Currency": tx.currency,
                "Status": tx.status,
                "Created At": created_display,
            }
        )
    return rows


def _build_daily_volume_frame(daily: List[Dict[str, int]]) -> pd.DataFrame:
    if not daily:
        return pd.DataFrame(columns=["day", "amount_cents", "transactions"])

    frame = pd.DataFrame(daily)
    frame["day"] = pd.to_datetime(frame["day"])
    frame = frame.sort_values("day")
    frame["amount"] = frame["amount_cents"] / 100
    return frame


@st.cache_data(show_spinner=False)
def load_snapshot(refresh_token: int):
    ensure_database_ready()
    with session_scope() as session:
        summary = get_summary(session)
        recent = get_recent_transactions(session)
        daily = get_daily_volume(session, days=14)
        statuses = get_status_breakdown(session)

    recent_df = pd.DataFrame(_recent_transactions_rows(recent))
    daily_df = _build_daily_volume_frame(daily)
    status_df = (
        pd.DataFrame(list(statuses.items()), columns=["status", "count"])
        if statuses
        else pd.DataFrame(columns=["status", "count"])
    )

    return {
        "summary": summary,
        "recent": recent_df,
        "daily": daily_df,
        "status": status_df,
    }


def _render_kpi_cards(summary: Dict[str, int]):
    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Total Volume",
        _format_currency(summary.get("total_volume_cents", 0)),
    )
    col2.metric(
        "Transactions",
        f"{summary.get('total_transactions', 0):,}",
    )
    col3.metric(
        "Average Ticket",
        _format_currency(summary.get("average_ticket_cents", 0)),
    )
    col4.metric(
        "Success Rate",
        f"{summary.get('success_rate', 0.0) * 100:.1f}%",
    )

    latest = summary.get("latest_transaction_at")
    if latest:
        st.caption(f"Latest transaction at: {latest}")


def _render_recent_table(recent_df: pd.DataFrame):
    st.subheader("Latest Transactions")
    if recent_df.empty:
        st.info("No transactions yet. Send a request to populate the dashboard.")
        return
    st.dataframe(recent_df, use_container_width=True, hide_index=True)


def _render_charts(daily_df: pd.DataFrame, status_df: pd.DataFrame):
    chart_cols = st.columns(2)
    with chart_cols[0]:
        st.subheader("Daily Volume")
        if daily_df.empty:
            st.info("No transaction volume captured yet.")
        else:
            st.line_chart(
                daily_df.set_index("day")[["amount"]],
                use_container_width=True,
            )
    with chart_cols[1]:
        st.subheader("Status Breakdown")
        if status_df.empty:
            st.info("No statuses to display yet.")
        else:
            st.bar_chart(
                status_df.set_index("status"),
                use_container_width=True,
            )


def _curl_example(api_key: str) -> str:
    endpoint = "http://localhost:8000/transactions"
    payload = """{
  "name": "Ada Lovelace",
  "price": 19.99,
  "currency": "USD"
}"""
    curl_lines = [
        "curl -X POST \\",
        f"  '{endpoint}' \\",
        f"  -H 'Authorization: Bearer {api_key}' \\",
        "  -H 'Content-Type: application/json' \\",
        f"  -d '{payload}'",
    ]
    return "\n".join(curl_lines)


def main():
    st.set_page_config(page_title="Payments Dashboard", layout="wide")
    st.title("Payments Dashboard")

    refresh_token = st.session_state.get("manual_refresh_token", 0)
    if hasattr(st, "experimental_autorefresh"):
        refresh_token += st.experimental_autorefresh(
            interval=settings.refresh_interval_seconds * 1000,
            key="dashboard_autorefresh",
            limit=10_000,
        )

    with st.expander("API Access", expanded=True):
        st.write("Use the API key below to authenticate requests.")
        st.code(settings.api_key, language=None)
        st.caption("Key is served directly from environment configuration.")
        st.write("Sample `curl` to simulate a payment:")
        st.code(_curl_example(settings.api_key))

    if st.button("Refresh now"):
        st.session_state["manual_refresh_token"] = (
            st.session_state.get("manual_refresh_token", 0) + 1
        )
        st.experimental_rerun()

    snapshot = load_snapshot(refresh_token)

    _render_kpi_cards(snapshot["summary"])
    _render_charts(snapshot["daily"], snapshot["status"])
    _render_recent_table(snapshot["recent"])


if __name__ == "__main__":
    main()
