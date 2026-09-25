"""
Centralized application configuration, loaded from environment variables (.env).
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://kyc_user:kyc_pass@db:5432/kyc_db"

    # JWT auth
    jwt_secret_key: str = "change-this-secret-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Document encryption (Fernet key - generate with cryptography.fernet.Fernet.generate_key())
    document_encryption_key: str = "PLEASE_SET_A_32_BYTE_URLSAFE_BASE64_FERNET_KEY="

    # Storage
    storage_dir: str = "/data/kyc_documents"

    # Fraud / matching thresholds
    face_match_threshold: float = 0.6  # lower distance = stricter match (face_recognition uses distance)
    duplicate_hash_check: bool = True

    class Config:
        env_file = ".env"


settings = Settings()
