from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr

from app.models import UserRole, VerificationStatus


# ---------- Auth ----------
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    role: UserRole = UserRole.APPLICANT


class UserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str]
    role: UserRole
    is_active: bool

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- KYC ----------
class KYCSubmissionOut(BaseModel):
    id: str
    applicant_id: str
    status: VerificationStatus
    face_similarity_score: Optional[float]
    is_duplicate: bool
    image_quality_ok: bool
    extracted_data: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ReviewDecision(BaseModel):
    decision: VerificationStatus  # APPROVED or REJECTED
    notes: Optional[str] = None
