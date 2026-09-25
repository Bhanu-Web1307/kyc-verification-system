import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, Enum, Float, Boolean, ForeignKey, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    ADMIN = "admin"          # full access, manages verifiers
    VERIFIER = "verifier"    # reviews & approves/rejects KYC submissions
    APPLICANT = "applicant"  # uploads own documents/selfie only


class VerificationStatus(str, enum.Enum):
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    FLAGGED_DUPLICATE = "flagged_duplicate"
    FLAGGED_FACE_MISMATCH = "flagged_face_mismatch"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.APPLICANT, nullable=False)
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    submissions = relationship("KYCSubmission", back_populates="applicant")


class KYCSubmission(Base):
    __tablename__ = "kyc_submissions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    applicant_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)

    # Encrypted-at-rest file paths (see security.py for encryption helpers)
    id_document_path = Column(String, nullable=False)
    selfie_path = Column(String, nullable=False)

    # Document content hash, used for duplicate submission detection
    document_hash = Column(String, index=True, nullable=False)

    # OCR-extracted fields (stored as JSON text)
    extracted_data = Column(Text, nullable=True)

    # Fraud-check outputs
    face_similarity_score = Column(Float, nullable=True)
    is_duplicate = Column(Boolean, default=False)
    image_quality_ok = Column(Boolean, default=True)

    status = Column(Enum(VerificationStatus), default=VerificationStatus.PENDING)
    reviewed_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    review_notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    applicant = relationship("User", back_populates="submissions", foreign_keys=[applicant_id])
