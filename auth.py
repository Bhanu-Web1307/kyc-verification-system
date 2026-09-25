from fastapi import FastAPI

from app.database import Base, engine
from app.routers import auth_router, kyc_router

# Creates tables on startup if they don't exist (use Alembic migrations in production)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="KYC Verification System",
    description="Backend service for identity verification via document upload and selfie-based face matching.",
    version="1.0.0",
)

app.include_router(auth_router.router)
app.include_router(kyc_router.router)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
