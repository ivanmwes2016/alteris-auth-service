## System Design

Frontend (Next.js / React)
↓
API Gateway
↓
AWS Lambda
↓
FastAPI (Mangum adapter)
↓
SQLAlchemy ORM
↓
Supabase Postgres
↓
Stripe Webhooks

## Structure

app/ -------->
↓
|---main.py

    core/
    |---config.py
    |---db.py
    |---security.py

    middleware/
    |---auth.py
    |---tenant.py

    models/
    |---tenant.py
    |---user.py
    |---member.py
    |---invitation.py

    schemas/
    |---auth.py
    |---tenant.py
    |---member.py
    |---billing.py

    api/
    |---v1/
    **init**.py
    |---routes/
    ----auth.py
    ----tenants.py
    ----members.py
    ----billing.py

    requirements.txt
    Dockerfile
    .env
