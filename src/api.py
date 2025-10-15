from __future__ import annotations

import html
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from string import Template
from typing import Annotated, Dict, Optional

from fastapi import Depends, FastAPI, Form, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, constr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import load_settings
from db import get_engine, get_session_factory, init_db, session_scope
from db.models import Transaction, User
from db.queries import get_user_by_api_key


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
    email: Optional[str] = Field(
        default=None,
        description="Customer email address for receipt delivery.",
        max_length=255,
    )
    price: Decimal = Field(gt=Decimal("0"))
    currency: constr(min_length=3, max_length=3) = "USD"
    status: constr(min_length=1, max_length=32) | None = None
    country: Optional[str] = Field(default=None, max_length=64)
    city: Optional[str] = Field(default=None, max_length=128)
    metadata: Optional[Dict[str, str]] = Field(
        default=None,
        description="Arbitrary metadata to associate with the transaction.",
    )


class TransactionResponse(BaseModel):
    transaction_id: str
    created_at: str
    amount_cents: int
    currency: str
    status: str
    customer_name: str
    customer_email: Optional[str]
    country: Optional[str]
    city: Optional[str]
    metadata: Optional[Dict[str, str]]


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
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
    session: Annotated[Session, Depends(get_db)],
) -> User:
    token = credentials.credentials
    user = get_user_by_api_key(session, token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )
    return user


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
    user: Annotated[User, Depends(require_api_key)],
    session: Annotated[Session, Depends(get_db)],
):
    amount_cents = _amount_to_cents(payload.price)
    currency = (payload.currency or settings.currency).upper()
    status_value = (payload.status or "success").lower()

    transaction = Transaction(
        api_key=user.api_key,
        customer_name=payload.name,
        customer_email=payload.email,
        amount_cents=amount_cents,
        currency=currency,
        status=status_value,
        extra=payload.metadata,
        country=payload.country,
        city=payload.city,
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
        customer_name=transaction.customer_name,
        customer_email=transaction.customer_email,
        country=transaction.country,
        city=transaction.city,
        metadata=transaction.extra,
    )


TEST_FORM_TEMPLATE = Template(
    """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8" />
        <title>Payments API Tester</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #fafafa;
                margin: 0;
                padding: 32px;
            }
            form {
                max-width: 520px;
                margin: 0 auto;
                background: #ffffff;
                border-radius: 12px;
                padding: 24px;
                box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
            }
            label {
                display: block;
                margin-bottom: 6px;
                font-weight: 600;
            }
            input, textarea {
                width: 100%;
                padding: 10px 12px;
                border-radius: 8px;
                border: 1px solid #d1d5db;
                font-size: 14px;
                margin-bottom: 18px;
                font-family: inherit;
            }
            textarea {
                min-height: 80px;
                resize: vertical;
            }
            button {
                background: #2563eb;
                color: #ffffff;
                border: none;
                padding: 10px 18px;
                border-radius: 8px;
                cursor: pointer;
                font-size: 15px;
            }
        </style>
    </head>
    <body>
        <h1>Payments API Tester</h1>
        <p style="max-width:520px; margin:0 auto 24px;">Submit this form to send a test transaction to <code>/transactions</code>. Results appear below the form.</p>
        <form method="post" action="/test">
            <label for="api_key">API Key</label>
            <input type="text" id="api_key" name="api_key" value="${api_key}" required />

            <label for="name">Customer Name</label>
            <input type="text" id="name" name="name" value="Ada Lovelace" required />

            <label for="email">Email (optional)</label>
            <input type="email" id="email" name="email" value="ada@example.com" />

            <label for="price">Amount (decimal)</label>
            <input type="number" step="0.01" min="0" id="price" name="price" value="19.99" required />

            <label for="currency">Currency</label>
            <input type="text" id="currency" name="currency" value="USD" maxlength="3" />

            <label for="country">Country (optional)</label>
            <input type="text" id="country" name="country" value="UK" />

            <label for="city">City (optional)</label>
            <input type="text" id="city" name="city" value="London" />

            <label for="metadata">Metadata JSON (optional)</label>
            <textarea id="metadata" name="metadata">{
  "product": "Starter Plan",
  "reference": "INV-1001"
}</textarea>

            <button type="submit">Send request</button>
        </form>

        ${result_block}
    </body>
    </html>
    """
)


def _render_test_form(result_block: str = "", api_key_value: str = "") -> str:
    return TEST_FORM_TEMPLATE.substitute(
        api_key=api_key_value,
        result_block=result_block,
    )


@app.get("/test", response_class=HTMLResponse)
def test_console():
    return HTMLResponse(content=_render_test_form())


PAYMENT_FORM_TEMPLATE = Template(
    """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <title>Payment Checkout (Preview)</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f4f4f8;
            margin: 0;
            padding: 32px;
        }
        form {
            max-width: 600px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 12px;
            padding: 28px;
            box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
        }
        fieldset {
            border: none;
            margin-bottom: 18px;
            padding: 0;
        }
        legend {
            font-weight: 600;
            margin-bottom: 8px;
        }
        label {
            display: block;
            margin-bottom: 6px;
            font-weight: 600;
        }
        input, textarea {
            width: 100%;
            padding: 10px 12px;
            border-radius: 8px;
            border: 1px solid #d1d5db;
            font-size: 14px;
            margin-bottom: 16px;
            font-family: inherit;
        }
        textarea {
            min-height: 80px;
            resize: vertical;
        }
        .inline-radio {
            display: flex;
            gap: 16px;
            margin-bottom: 16px;
        }
        .inline-radio label {
            display: flex;
            align-items: center;
            gap: 8px;
            font-weight: 500;
        }
        button {
            background: #10b981;
            color: #ffffff;
            border: none;
            padding: 12px 20px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 15px;
        }
        button:hover {
            background: #0f9a6b;
        }
    </style>
</head>
<body>
    <h1 style="text-align:center; margin-bottom:24px;">Mock Checkout Form</h1>
    <p style="text-align:center; margin-bottom:24px; color:#4b5563;">This preview form is non-functional and meant for design/testing only.</p>
    <form method="post" action="/payment">
        <fieldset>
            <legend>Contact</legend>
            <label for="payer_name">Full Name</label>
            <input type="text" id="payer_name" name="payer_name" placeholder="Jane Doe" required />

            <label for="payer_email">Email</label>
            <input type="email" id="payer_email" name="payer_email" placeholder="jane@example.com" required />
        </fieldset>

        <fieldset>
            <legend>Billing Address</legend>
            <label for="address_line">Address</label>
            <textarea id="address_line" name="address_line" placeholder="221B Baker Street, London"></textarea>

            <label for="postal_code">Postal Code</label>
            <input type="text" id="postal_code" name="postal_code" placeholder="NW1 6XE" />
        </fieldset>

        <fieldset>
            <legend>Payment Method</legend>
            <div class="inline-radio">
                <label><input type="radio" name="method" value="card" checked /> Credit / Debit Card</label>
                <label><input type="radio" name="method" value="upi" /> UPI</label>
            </div>

            <div>
                <label for="card_number">Card Number</label>
                <input type="text" id="card_number" name="card_number" placeholder="4242 4242 4242 4242" />

                <label for="card_expiry">Expiry (MM/YY)</label>
                <input type="text" id="card_expiry" name="card_expiry" placeholder="12/27" />

                <label for="card_cvv">CVV</label>
                <input type="text" id="card_cvv" name="card_cvv" placeholder="123" />
            </div>

            <div>
                <label for="upi_id">UPI ID</label>
                <input type="text" id="upi_id" name="upi_id" placeholder="username@bank" />
            </div>
        </fieldset>

        <button type="submit">Pay Now</button>
    </form>

    ${result_block}
</body>
</html>
    """
)


def _render_payment_form(result_block: str = "") -> str:
    return PAYMENT_FORM_TEMPLATE.substitute(result_block=result_block)


@app.get("/payment", response_class=HTMLResponse)
def payment_preview():
    return HTMLResponse(content=_render_payment_form())


@app.post("/payment", response_class=HTMLResponse)
def payment_preview_submit(
    payer_name: str = Form(...),
    payer_email: str = Form(...),
    address_line: Optional[str] = Form(None),
    postal_code: Optional[str] = Form(None),
    method: str = Form("card"),
    card_number: Optional[str] = Form(None),
    card_expiry: Optional[str] = Form(None),
    card_cvv: Optional[str] = Form(None),
    upi_id: Optional[str] = Form(None),
):
    safe_name = html.escape(payer_name)
    safe_method = html.escape(method.upper())
    notice = f"""
    <section style=\"max-width:600px;margin:24px auto 0;background:#ecfdf5;border-radius:12px;padding:16px 20px;border:1px solid #bbf7d0;\">
        <h2 style=\"margin-top:0;color:#047857;\">Submission Received</h2>
        <p style=\"margin-bottom:8px;color:#065f46;\">Thanks, {safe_name}! This demo form does not process real payments.</p>
        <p style=\"margin:0;color:#047857;\">Selected method: <strong>{safe_method}</strong></p>
    </section>
    """
    return HTMLResponse(content=_render_payment_form(result_block=notice))


@app.post("/test", response_class=HTMLResponse)
def test_console_submit(
    api_key: str,
    name: str,
    price: Decimal,
    currency: str = "USD",
    email: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    metadata: Optional[str] = None,
):
    payload = {
        "name": name,
        "price": price,
        "currency": currency.upper(),
    }
    if email:
        payload["email"] = email
    if country:
        payload["country"] = country
    if city:
        payload["city"] = city
    if metadata:
        try:
            payload["metadata"] = json.loads(metadata)
        except json.JSONDecodeError as exc:
            payload["metadata"] = {"_error": f"Invalid metadata JSON: {exc}"}

    response_data: Dict[str, str] | Dict[str, object]
    status_label = """<p style="color:#b91c1c;">Error: Invalid API key</p>"""

    with session_scope(engine=engine) as session:
        user = get_user_by_api_key(session, api_key)
        if not user:
            response_data = {"error": "Invalid API key"}
        else:
            status_label = """<p style="color:#15803d;">Success</p>"""
            try:
                transaction = Transaction(
                    api_key=user.api_key,
                    customer_name=payload["name"],
                    customer_email=payload.get("email"),
                    amount_cents=_amount_to_cents(payload["price"]),
                    currency=payload["currency"],
                    status="success",
                    extra=payload.get("metadata"),
                    country=payload.get("country"),
                    city=payload.get("city"),
                )

                session.add(transaction)
                session.flush()

                response_data = {
                    "transaction_id": transaction.transaction_id,
                    "created_at": transaction.created_at.isoformat()
                    if transaction.created_at
                    else "",
                    "amount_cents": transaction.amount_cents,
                    "currency": transaction.currency,
                    "status": transaction.status,
                    "customer_name": transaction.customer_name,
                    "customer_email": transaction.customer_email,
                }
            except Exception as exc:
                status_label = (
                    f"<p style=\"color:#b91c1c;\">Error: {exc}</p>"
                )
                response_data = {"error": str(exc)}

    result_html = f"""
    <section style=\"max-width:520px;margin:24px auto 0;background:#fff;border-radius:12px;padding:16px 20px;box-shadow:0 4px 12px rgba(15, 23, 42, 0.06);\">
        <h2 style=\"margin-top:0;\">Response</h2>
        {status_label}
        <pre style=\"background:#0f172a;color:#f8fafc;padding:12px;border-radius:8px;overflow-x:auto;white-space:pre-wrap;\">
{json.dumps(response_data, indent=2)}
        </pre>
    </section>
    """

    page = _render_test_form(result_block=result_html, api_key_value=api_key)
    return HTMLResponse(content=page)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
