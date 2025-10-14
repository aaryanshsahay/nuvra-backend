from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Annotated, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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
    api_key: Annotated[str, Depends(require_api_key)],
    session: Annotated[Session, Depends(get_db)],
):
    amount_cents = _amount_to_cents(payload.price)
    currency = (payload.currency or settings.currency).upper()
    status_value = (payload.status or "success").lower()

    transaction = Transaction(
        api_key=api_key,
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


@app.get("/test", response_class=HTMLResponse)
def test_console():
    html = f"""
    <!DOCTYPE html>
    <html lang=\"en\">
    <head>
        <meta charset=\"utf-8\" />
        <title>Payments API Tester</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
                background: #f5f5f7;
                margin: 0;
                padding: 32px;
            }}
            h1 {{
                margin-bottom: 16px;
            }}
            form {{
                background: #ffffff;
                border-radius: 12px;
                padding: 24px;
                max-width: 640px;
                box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
            }}
            label {{
                display: block;
                margin-bottom: 8px;
                font-weight: 600;
            }}
            input, textarea {{
                width: 100%;
                padding: 10px 12px;
                border-radius: 8px;
                border: 1px solid #d1d5db;
                font-size: 14px;
                margin-bottom: 16px;
                font-family: inherit;
            }}
            textarea {{
                min-height: 120px;
                resize: vertical;
            }}
            button {{
                background: #2563eb;
                color: #ffffff;
                border: none;
                padding: 10px 18px;
                border-radius: 8px;
                cursor: pointer;
                font-size: 15px;
            }}
            button:disabled {{
                opacity: 0.6;
                cursor: not-allowed;
            }}
            pre {{
                background: #0f172a;
                color: #f8fafc;
                padding: 16px;
                border-radius: 8px;
                overflow-x: auto;
                max-width: 640px;
                white-space: pre-wrap;
                word-break: break-word;
            }}
            .status {{
                margin-top: 16px;
                font-weight: 600;
            }}
            .status.success {{ color: #15803d; }}
            .status.error {{ color: #b91c1c; }}
        </style>
    </head>
    <body>
        <h1>Payments API Tester</h1>
        <form id=\"test-form\">
            <label for=\"api_key\">API Key</label>
            <input type=\"text\" id=\"api_key\" name=\"api_key\" value=\"{settings.api_key}\" required />

            <label for=\"name\">Customer Name</label>
            <input type=\"text\" id=\"name\" name=\"name\" value=\"Ada Lovelace\" required />

            <label for=\"email\">Customer Email</label>
            <input type=\"email\" id=\"email\" name=\"email\" value=\"ada@example.com\" />

            <label for=\"price\">Amount (decimal)</label>
            <input type=\"number\" step=\"0.01\" min=\"0\" id=\"price\" name=\"price\" value=\"19.99\" required />

            <label for=\"currency\">Currency</label>
            <input type=\"text\" id=\"currency\" name=\"currency\" value=\"USD\" maxlength=\"3\" />

            <label for=\"country\">Country</label>
            <input type=\"text\" id=\"country\" name=\"country\" value=\"UK\" />

            <label for=\"city\">City</label>
            <input type=\"text\" id=\"city\" name=\"city\" value=\"London\" />

            <label for=\"metadata\">Metadata (JSON)</label>
            <textarea id=\"metadata\" name=\"metadata\">{{\n  \"product\": \"Starter Plan\",\n  \"reference\": \"INV-1001\"\n}}</textarea>

            <button type=\"submit\" id=\"submit-btn\">Send Request</button>
            <span class=\"status\" id=\"status\"></span>
        </form>

        <h2>Response</h2>
        <pre id=\"response\">Waiting for request...</pre>

        <script>
            const form = document.getElementById('test-form');
            const responseEl = document.getElementById('response');
            const statusEl = document.getElementById('status');
            const submitBtn = document.getElementById('submit-btn');

            form.addEventListener('submit', async (event) => {{
                event.preventDefault();
                statusEl.textContent = '';
                responseEl.textContent = 'Sending request...';
                responseEl.className = '';
                submitBtn.disabled = true;

                const formData = new FormData(form);
                const metadataRaw = formData.get('metadata')?.trim();
                let metadata = undefined;
                if (metadataRaw) {{
                    try {{
                        metadata = JSON.parse(metadataRaw);
                    }} catch (err) {{
                        submitBtn.disabled = false;
                        statusEl.textContent = 'Invalid metadata JSON: ' + err.message;
                        statusEl.className = 'status error';
                        responseEl.textContent = 'Fix metadata and try again.';
                        return;
                    }}
                }

                const priceValue = parseFloat(formData.get('price'));
                if (!Number.isFinite(priceValue) || priceValue <= 0) {{
                    submitBtn.disabled = false;
                    statusEl.textContent = 'Price must be a positive number.';
                    statusEl.className = 'status error';
                    responseEl.textContent = 'Fix price and try again.';
                    return;
                }}

                const payload = {{
                    name: formData.get('name'),
                    email: formData.get('email') || null,
                    price: priceValue,
                    currency: (formData.get('currency') || 'USD').toUpperCase(),
                    country: formData.get('country') || null,
                    city: formData.get('city') || null,
                }};

                if (metadata !== undefined) {{
                    payload.metadata = metadata;
                }}

                if (!payload.email) {{
                    delete payload.email;
                }}
                if (!payload.country) {{
                    delete payload.country;
                }}
                if (!payload.city) {{
                    delete payload.city;
                }}

                try {{
                    const res = await fetch('/transactions', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${{formData.get('api_key')}}`
                        }},
                        body: JSON.stringify(payload),
                    }});

                    const text = await res.text();
                    let parsed;
                    try {{
                        parsed = JSON.parse(text);
                    }} catch (err) {{
                        parsed = {{ raw: text }};
                    }}

                    responseEl.textContent = JSON.stringify(parsed, null, 2);
                    statusEl.textContent = res.ok ? 'Success' : `Error ${{res.status}}`;
                    statusEl.className = res.ok ? 'status success' : 'status error';
                }} catch (err) {{
                    statusEl.textContent = 'Request failed';
                    statusEl.className = 'status error';
                    responseEl.textContent = err.message;
                }} finally {{
                    submitBtn.disabled = false;
                }}
            }});
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
