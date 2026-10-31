"""Persistent usage limits. Reservations are atomic and never refunded on provider failure."""
import hashlib
import os
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import text
from signalloop_api.database import SessionLocal


def reserve(key, amount, limit):
    period = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with SessionLocal() as db:
        # Conditional UPSERT is atomic even across concurrent API processes.
        row = db.execute(text('''INSERT INTO portfolio_usage (key, period, used)
            VALUES (:key, :period, :amount)
            ON CONFLICT (key, period) DO UPDATE SET used = portfolio_usage.used + :amount
            WHERE portfolio_usage.used + :amount <= :limit RETURNING used'''),
            {'key':key, 'period':period, 'amount':amount, 'limit':limit}).first() if amount <= limit else None
        if row is None:
            raise HTTPException(429, 'Today’s demo limit has been reached. Try again tomorrow; saved work is safe.')
        db.commit()


def reserve_ai(text_size, max_tokens):
    # UTF-8 bytes conservatively bound input tokens; this is an allowance, not a bill estimate.
    reserve('ai_allowance', text_size + max_tokens, int(os.getenv('PROOFSTACK_DAILY_AI_ALLOWANCE','400000')))
    reserve('ai_calls', 1, int(os.getenv('PROOFSTACK_DAILY_AI_CALLS','120')))


def intake_limit(request, kind):
    address = request.headers.get('x-proofstack-client') or (request.client.host if request.client else 'unknown')
    key = hashlib.sha256(address.encode()).hexdigest()[:32]
    reserve(kind+':'+key, 1, 6 if kind=='create' else 8)
    reserve(kind+':global', 1, 40 if kind=='create' else 50)
