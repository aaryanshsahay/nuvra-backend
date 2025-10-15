from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

import base64
import pandas as pd
import streamlit as st

from config import load_settings
from auth import generate_api_key, hash_password, verify_password
from db import get_engine, init_db, session_scope
from db.models import Transaction
from db.queries import (
    get_daily_volume,
    get_status_breakdown,
    get_summary,
    get_transaction_by_id,
    get_user_by_username,
    create_user,
    get_transactions_filtered,
)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


settings = load_settings()
LOGO_PATH = Path(__file__).resolve().parent / "nuvra_logo.jpeg"

TABLE_PAGE_SIZE = 100
DEFAULT_TABLE_FILTERS = {
    "start_date": None,
    "end_date": None,
    "min_amount": None,
    "max_amount": None,
    "statuses": [],
}


@st.cache_resource
def ensure_database_ready():
    engine = get_engine(settings.database_url or None)
    init_db(engine)
    return engine


def _render_brand_header(subtitle: Optional[str] = None):
    cols = st.columns([0.16, 0.84])
    with cols[0]:
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), use_container_width=True)
    with cols[1]:
        st.title("Nuvra Payments Dashboard")
        if subtitle:
            st.caption(subtitle)


def _format_currency(amount_cents: int) -> str:
    dollars = amount_cents / 100
    return f"{settings.currency} {dollars:,.2f}"

def _build_daily_volume_frame(daily: List[Dict[str, int]]) -> pd.DataFrame:
    if not daily:
        return pd.DataFrame(columns=["day", "amount_cents", "transactions"])

    frame = pd.DataFrame(daily)
    frame["day"] = pd.to_datetime(frame["day"])
    frame = frame.sort_values("day")
    frame["amount"] = frame["amount_cents"] / 100
    return frame


def _invoice_payloads(transactions) -> List[Dict[str, str]]:
    payloads = []
    for tx in transactions:
        payloads.append(
            {
                "transaction_id": tx.transaction_id,
                "customer_name": tx.customer_name,
                "customer_email": tx.customer_email,
                "amount_cents": tx.amount_cents,
                "currency": tx.currency,
                "status": tx.status,
                "created_at": tx.created_at,
                "country": tx.country,
                "city": tx.city,
                "extra": tx.extra,
                "api_key": tx.api_key,
            }
        )
    return payloads


def _generate_invoice_pdf(data: Dict[str, str]) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    pdf.setTitle(f"Invoice-{data['transaction_id']}")

    margin = 72  # 1 inch
    cursor_y = height - margin

    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(margin, cursor_y, "Payment Invoice")
    cursor_y -= 40

    pdf.setFont("Helvetica", 12)
    pdf.drawString(margin, cursor_y, f"Transaction ID: {data['transaction_id']}")
    cursor_y -= 20
    pdf.drawString(margin, cursor_y, f"Customer: {data['customer_name']}")
    cursor_y -= 20
    email = data.get("customer_email") or "Not provided"
    pdf.drawString(margin, cursor_y, f"Email: {email}")
    cursor_y -= 20
    location_bits = [
        bit for bit in [data.get("city"), data.get("country")] if bit
    ]
    location = ", ".join(location_bits) if location_bits else "Not provided"
    pdf.drawString(margin, cursor_y, f"Location: {location}")
    cursor_y -= 20
    pdf.drawString(margin, cursor_y, f"Currency: {data['currency']}")
    cursor_y -= 20

    amount = _format_currency(data["amount_cents"])
    pdf.drawString(margin, cursor_y, f"Amount: {amount}")
    cursor_y -= 20

    status = data.get("status", "success").title()
    pdf.drawString(margin, cursor_y, f"Status: {status}")
    cursor_y -= 20

    created_at = data.get("created_at")
    created_display = (
        created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
    )
    pdf.drawString(margin, cursor_y, f"Created At: {created_display}")
    cursor_y -= 40

    pdf.setFont("Helvetica", 10)
    pdf.drawString(
        margin,
        cursor_y,
        "This is a sample invoice generated for demonstration purposes only.",
    )

    pdf.showPage()
    pdf.save()

    buffer.seek(0)
    return buffer.getvalue()


def _render_invoice_viewer(data: Dict[str, str], pdf_bytes: bytes):
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    st.markdown(f"**{data['customer_name']}** — {_format_currency(data['amount_cents'])}")
    st.caption(f"Transaction: {data['transaction_id']}")
    st.download_button(
        "Download PDF",
        data=pdf_bytes,
        file_name=f"invoice-{data['transaction_id']}.pdf",
        mime="application/pdf",
        key=f"download-{data['transaction_id']}",
    )
    iframe_html = f"""
        <iframe
            src="data:application/pdf;base64,{pdf_b64}"
            width="100%"
            height="500"
            style="border: none;"
        ></iframe>
    """
    st.markdown(iframe_html, unsafe_allow_html=True)


def _set_current_user(user) -> None:
    st.session_state["user"] = {
        "id": user.id,
        "username": user.username,
        "api_key": user.api_key,
    }
    st.session_state["view"] = "dashboard"
    st.session_state.setdefault("active_invoice", None)
    st.session_state.setdefault("active_transaction_id", None)


def _logout():
    for key in ["user", "view", "active_invoice", "active_transaction_id"]:
        st.session_state.pop(key, None)
    rerun = getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _convert_filters_for_query(filters: Dict[str, object]) -> Dict[str, object]:
    start_at = filters.get("start_date")
    end_at = filters.get("end_date")
    tz = timezone.utc
    start_dt = None
    end_dt = None
    if start_at:
        start_dt = datetime.combine(start_at, datetime.min.time()).replace(tzinfo=tz)
    if end_at:
        # Include the entire end day by adding almost one day then subtract microsecond
        end_dt = datetime.combine(end_at, datetime.max.time()).replace(tzinfo=tz)

    min_amount = filters.get("min_amount")
    max_amount = filters.get("max_amount")
    min_amount_cents = int(min_amount * 100) if min_amount is not None else None
    max_amount_cents = int(max_amount * 100) if max_amount is not None else None

    statuses = filters.get("statuses") or None
    return {
        "start_at": start_dt,
        "end_at": end_dt,
        "min_amount_cents": min_amount_cents,
        "max_amount_cents": max_amount_cents,
        "statuses": statuses,
    }


def _transactions_dataframe(transactions: List[Transaction]) -> pd.DataFrame:
    rows = []
    for tx in transactions:
        created = tx.created_at.isoformat() if isinstance(tx.created_at, datetime) else str(tx.created_at)
        rows.append(
            {
                "Transaction": tx.transaction_id,
                "Customer": tx.customer_name,
                "Email": tx.customer_email or "",
                "Country": tx.country or "",
                "City": tx.city or "",
                "Amount": _format_currency(tx.amount_cents),
                "Currency": tx.currency,
                "Status": tx.status,
                "Created At": created,
            }
        )
    return pd.DataFrame(rows)


def load_snapshot(api_key: str, table_filters: Dict[str, object], page: int, page_size: int):
    engine = ensure_database_ready()
    with session_scope(engine=engine) as session:
        summary = get_summary(session, api_key=api_key)
        daily = get_daily_volume(session, days=14, api_key=api_key)
        statuses = get_status_breakdown(session, api_key=api_key)

        filter_kwargs = _convert_filters_for_query(table_filters)
        records, total = get_transactions_filtered(
            session,
            api_key=api_key,
            limit=page_size,
            offset=page * page_size,
            **filter_kwargs,
        )

    table_df = _transactions_dataframe(records)
    daily_df = _build_daily_volume_frame(daily)
    status_df = (
        pd.DataFrame(list(statuses.items()), columns=["status", "count"])
        if statuses
        else pd.DataFrame(columns=["status", "count"])
    )

    return {
        "summary": summary,
        "daily": daily_df,
        "status": status_df,
        "invoices": _invoice_payloads(records),
        "table": {
            "data": table_df,
            "total": total,
            "raw": records,
        },
        "status_options": sorted(statuses.keys()),
    }


def _render_auth():
    _render_brand_header("Sign in to view your transactions and API key.")

    tabs = st.tabs(["Login", "Sign Up"])

    with tabs[0]:
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")

            if submitted:
                if not username or not password:
                    st.error("Enter both username and password.")
                else:
                    engine = ensure_database_ready()
                    with session_scope(engine=engine) as session:
                        user = get_user_by_username(session, username)
                    if not user or not verify_password(password, user.password_hash):
                        st.error("Invalid credentials.")
                    else:
                        _set_current_user(user)
                        toast = getattr(st, "toast", None)
                        if callable(toast):
                            toast("Logged in", icon="✅")
                        rerun = getattr(st, "rerun", None)
                        if callable(rerun):
                            rerun()

    with tabs[1]:
        with st.form("signup_form", clear_on_submit=False):
            username = st.text_input("Choose a username", key="signup_username")
            password = st.text_input("Password", type="password", key="signup_password")
            confirm = st.text_input(
                "Confirm password", type="password", key="signup_confirm"
            )
            submitted = st.form_submit_button("Create account")

            if submitted:
                if not username or not password:
                    st.error("Username and password are required.")
                elif password != confirm:
                    st.error("Passwords do not match.")
                elif len(password) < 6:
                    st.error("Use a password with at least 6 characters.")
                else:
                    engine = ensure_database_ready()
                    new_user = None
                    with session_scope(engine=engine) as session:
                        existing = get_user_by_username(session, username)
                        if existing:
                            st.error("Username already taken.")
                            session.rollback()
                        else:
                            password_hash = hash_password(password)
                            api_key = generate_api_key()
                            new_user = create_user(session, username, password_hash, api_key)
                    if new_user is not None:
                        _set_current_user(new_user)
                        st.success("Account created!")
                        rerun = getattr(st, "rerun", None)
                        if callable(rerun):
                            rerun()


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


def _render_transactions_section(user: Dict[str, str], snapshot: Dict[str, object]):
    st.subheader("Transactions")

    filters = st.session_state.get("table_filters")
    if filters is None:
        filters = DEFAULT_TABLE_FILTERS.copy()
        st.session_state["table_filters"] = filters
    page = st.session_state.get("table_page", 0)

    status_options = snapshot.get("status_options", [])

    with st.form("transactions_filters"):
        row1 = st.columns(2)
        with row1[0]:
            use_start = st.checkbox(
                "Filter from date",
                value=filters.get("start_date") is not None,
                key="filter_use_start",
            )
            start_value = filters.get("start_date") or date.today()
            start_date_input = st.date_input(
                "Start date",
                value=start_value,
                key="filter_start_date",
                disabled=not use_start,
            )

        with row1[1]:
            use_end = st.checkbox(
                "Filter to date",
                value=filters.get("end_date") is not None,
                key="filter_use_end",
            )
            end_value = filters.get("end_date") or date.today()
            end_date_input = st.date_input(
                "End date",
                value=end_value,
                key="filter_end_date",
                disabled=not use_end,
            )

        row2 = st.columns(3)
        with row2[0]:
            min_amount_str = st.text_input(
                "Min amount",
                value="" if filters.get("min_amount") is None else str(filters["min_amount"]),
                placeholder="e.g. 10.00",
                key="filter_min_amount",
            )
        with row2[1]:
            max_amount_str = st.text_input(
                "Max amount",
                value="" if filters.get("max_amount") is None else str(filters["max_amount"]),
                placeholder="e.g. 250.00",
                key="filter_max_amount",
            )
        with row2[2]:
            status_selection = st.multiselect(
                "Status",
                options=status_options,
                default=filters.get("statuses", []),
                key="filter_statuses",
            )

        action_cols = st.columns([0.18, 0.18, 0.64])
        apply_filters = action_cols[0].form_submit_button("Apply filters")
        reset_filters = action_cols[1].form_submit_button("Reset")

        def _parse_amount(value: str) -> Optional[float]:
            value = value.strip()
            if not value:
                return None
            try:
                amt = float(value)
                return max(0.0, amt)
            except ValueError:
                st.warning("Amount filters must be numeric (e.g. 19.99).")
                return None

        if apply_filters:
            new_filters = DEFAULT_TABLE_FILTERS.copy()
            if use_start:
                new_filters["start_date"] = start_date_input
            if use_end:
                new_filters["end_date"] = end_date_input

            min_amount = _parse_amount(min_amount_str)
            max_amount = _parse_amount(max_amount_str)
            if min_amount is not None and max_amount is not None and min_amount > max_amount:
                st.warning("Min amount cannot exceed max amount.")
            else:
                new_filters["min_amount"] = min_amount
                new_filters["max_amount"] = max_amount
                new_filters["statuses"] = status_selection
                st.session_state["table_filters"] = new_filters
                st.session_state["table_page"] = 0
                rerun = getattr(st, "rerun", None)
                if callable(rerun):
                    rerun()

        if reset_filters:
            st.session_state["table_filters"] = DEFAULT_TABLE_FILTERS.copy()
            st.session_state["table_page"] = 0
            rerun = getattr(st, "rerun", None)
            if callable(rerun):
                rerun()

    table_info = snapshot["table"]
    df = table_info["data"]
    total = table_info["total"]
    records = table_info["raw"]

    if df.empty:
        st.info("No transactions match the current filters.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

    start_idx = page * TABLE_PAGE_SIZE
    end_idx = min(total, start_idx + TABLE_PAGE_SIZE)
    if total:
        st.caption(f"Showing {start_idx + 1}-{end_idx} of {total} transactions")
    else:
        st.caption("No transactions to display")

    pager_cols = st.columns([0.2, 0.2, 0.6])
    prev_disabled = page == 0
    next_disabled = end_idx >= total

    if pager_cols[0].button("← Previous", disabled=prev_disabled):
        st.session_state["table_page"] = max(0, page - 1)
        rerun = getattr(st, "rerun", None)
        if callable(rerun):
            rerun()

    if pager_cols[1].button("Next →", disabled=next_disabled):
        st.session_state["table_page"] = page + 1
        rerun = getattr(st, "rerun", None)
        if callable(rerun):
            rerun()

    if records:
        st.write("---")
        st.caption("Quick actions")
        for tx in records:
            cols = st.columns([2.6, 2, 1.4, 1.1, 1.6, 1.3, 0.6])
            tx_id = tx.transaction_id
            short_id = tx_id if len(tx_id) <= 12 else f"{tx_id[:10]}…"

            with cols[0]:
                if st.button(
                    short_id,
                    key=f"tx-detail-{tx_id}",
                    help="View transaction details",
                ):
                    st.session_state["view"] = "transaction_detail"
                    st.session_state["active_transaction_id"] = tx_id
                    st.session_state["active_invoice"] = None
                    rerun = getattr(st, "rerun", None)
                    if callable(rerun):
                        rerun()
                st.caption(tx_id)

            cols[1].markdown(tx.customer_name)
            cols[2].markdown(_format_currency(tx.amount_cents))
            cols[3].markdown(tx.currency)
            created_display = (
                tx.created_at.isoformat()
                if isinstance(tx.created_at, datetime)
                else str(tx.created_at)
            )
            cols[4].markdown(created_display)
            cols[5].markdown(tx.status.title())

            with cols[6]:
                if st.button(
                    "🧾",
                    key=f"invoice-btn-{tx.transaction_id}",
                    help="Invoice",
                ):
                    st.session_state["active_invoice"] = {
                        "transaction_id": tx.transaction_id,
                        "customer_name": tx.customer_name,
                        "customer_email": tx.customer_email,
                        "amount_cents": tx.amount_cents,
                        "currency": tx.currency,
                        "status": tx.status,
                        "created_at": tx.created_at,
                        "country": tx.country,
                        "city": tx.city,
                        "extra": tx.extra,
                        "api_key": tx.api_key,
                    }


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
  "email": "ada@example.com",
  "price": 19.99,
  "currency": "USD",
  "country": "UK",
  "city": "London"
}"""
    curl_lines = [
        "curl -X POST \\",
        f"  '{endpoint}' \\",
        f"  -H 'Authorization: Bearer {api_key}' \\",
        "  -H 'Content-Type: application/json' \\",
        f"  -d '{payload}'",
    ]
    return "\n".join(curl_lines)


def _render_dashboard(user: Dict[str, str]):
    _render_brand_header()

    with st.sidebar:
        st.markdown(f"### 👤 {user['username']}")
        if st.button("Log out"):
            _logout()

    st.session_state["active_transaction_id"] = None

    active_invoice = st.session_state.get("active_invoice")

    if hasattr(st, "experimental_autorefresh") and not active_invoice:
        interval_ms = max(1, int(settings.refresh_interval_seconds * 1000))
        st.experimental_autorefresh(
            interval=interval_ms,
            key="dashboard_autorefresh",
            limit=10_000,
        )

    with st.expander("API Access", expanded=True):
        st.write("Use the API key below to authenticate requests.")
        st.code(user["api_key"], language=None)
        st.caption("Each account has a unique API key. Keep it secret!")
        st.write("Sample `curl` to simulate a payment:")
        st.code(_curl_example(user["api_key"]))

    filters = st.session_state.get("table_filters")
    if filters is None:
        filters = DEFAULT_TABLE_FILTERS.copy()
        st.session_state["table_filters"] = filters
    page = st.session_state.get("table_page", 0)

    manual_refresh = st.button("Refresh now")
    snapshot = load_snapshot(user["api_key"], filters, page, TABLE_PAGE_SIZE)
    total = snapshot["table"]["total"]
    max_page = max(0, (total - 1) // TABLE_PAGE_SIZE) if total else 0
    if page > max_page:
        st.session_state["table_page"] = max_page
        rerun = getattr(st, "rerun", None)
        if callable(rerun):
            rerun()
        return
    if manual_refresh:
        toast = getattr(st, "toast", None)
        if callable(toast):
            toast("Dashboard refreshed", icon="✅")

    _render_kpi_cards(snapshot["summary"])
    _render_charts(snapshot["daily"], snapshot["status"])
    _render_transactions_section(user, snapshot)

    _render_invoice_modal(user)


def _render_invoice_modal(user: Dict[str, str]):
    active = st.session_state.get("active_invoice")
    if not active or active.get("api_key") != user["api_key"]:
        st.session_state["active_invoice"] = None
        return

    pdf_bytes = _generate_invoice_pdf(active)
    if hasattr(st, "modal"):
        with st.modal(f"Invoice · {active['transaction_id']}", key="invoice-modal"):
            _render_invoice_viewer(active, pdf_bytes)
            if st.button("Close", key="close-invoice"):
                st.session_state["active_invoice"] = None
    else:
        st.warning("Upgrade Streamlit to view invoices in a modal dialog.")
        _render_invoice_viewer(active, pdf_bytes)
        if st.button("Close", key="close-invoice"):
            st.session_state["active_invoice"] = None


def _render_transaction_detail(transaction_id: str, user: Dict[str, str]):
    st.title("Transaction Details")

    st.session_state["active_invoice"] = None

    if st.button("← Back to dashboard", key="back-to-dashboard"):
        st.session_state["view"] = "dashboard"
        st.session_state["active_transaction_id"] = None
        st.session_state["active_invoice"] = None
        rerun = getattr(st, "rerun", None)
        if callable(rerun):
            rerun()

    engine = ensure_database_ready()
    with session_scope(engine=engine) as session:
        transaction = get_transaction_by_id(session, transaction_id)

    if not transaction or transaction.api_key != user["api_key"]:
        st.warning("Transaction not found. It may have been deleted.")
        return

    info_columns = st.columns(4)
    info_columns[0].markdown(
        f"**Customer**\n\n{transaction.customer_name}"
    )
    info_columns[1].markdown(
        f"**Email**\n\n{transaction.customer_email or '—'}"
    )
    info_columns[2].markdown(
        f"**Amount**\n\n{_format_currency(transaction.amount_cents)}"
    )
    info_columns[3].markdown(
        f"**Status**\n\n{transaction.status.title()}"
    )

    meta_cols = st.columns(2)
    meta_cols[0].markdown(f"**Transaction ID**\n\n`{transaction.transaction_id}`")
    meta_cols[0].markdown(f"**API Key**\n\n`{transaction.api_key}`")
    created_display = (
        transaction.created_at.isoformat()
        if isinstance(transaction.created_at, datetime)
        else str(transaction.created_at)
    )
    location = ", ".join(
        [piece for piece in [transaction.city, transaction.country] if piece]
    ) or "Not provided"
    meta_cols[1].markdown(f"**Created At**\n\n{created_display}")
    meta_cols[1].markdown(f"**Location**\n\n{location}")

    st.write("---")
    st.subheader("Invoice")
    invoice_payload = {
        "transaction_id": transaction.transaction_id,
        "customer_name": transaction.customer_name,
        "customer_email": transaction.customer_email,
        "amount_cents": transaction.amount_cents,
        "currency": transaction.currency,
        "status": transaction.status,
        "created_at": transaction.created_at,
        "country": transaction.country,
        "city": transaction.city,
        "extra": transaction.extra,
    }
    pdf_bytes = _generate_invoice_pdf(invoice_payload)
    _render_invoice_viewer(invoice_payload, pdf_bytes)

    st.write("---")
    st.subheader("Metadata")
    meta_data = {
        "Customer": transaction.customer_name,
        "Email": transaction.customer_email,
        "Country": transaction.country,
        "City": transaction.city,
        "Amount (cents)": transaction.amount_cents,
        "Currency": transaction.currency,
        "Status": transaction.status,
        "Transaction ID": transaction.transaction_id,
        "API Key": transaction.api_key,
        "Created At": created_display,
    }
    st.json(meta_data)

    if transaction.extra:
        st.subheader("Additional Metadata")
        st.json(transaction.extra)


def main():
    st.set_page_config(
        page_title="Payments Dashboard",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    ensure_database_ready()

    user = st.session_state.get("user")
    if not user:
        _render_auth()
        return

    st.session_state.setdefault("view", "dashboard")
    st.session_state.setdefault("active_invoice", None)
    st.session_state.setdefault("active_transaction_id", None)

    view = st.session_state.get("view", "dashboard")
    active_transaction_id = st.session_state.get("active_transaction_id")

    if view == "transaction_detail" and active_transaction_id:
        _render_transaction_detail(active_transaction_id, user)
    else:
        st.session_state["view"] = "dashboard"
        _render_dashboard(user)


if __name__ == "__main__":
    main()
