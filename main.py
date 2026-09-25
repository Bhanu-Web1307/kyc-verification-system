"""
KYC Verification System — single-file backend.

Everything (config, database, models, schemas, security, OCR, face matching,
auth/RBAC, and API routes) lives in this one file so the project can be
deployed without any package/subfolder structure. Run with:

    uvicorn main:app --host 0.0.0.0 --port 8000

See README.md for setup, environment variables, and Docker instructions.
"""

import enum
import hashlib
import io
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import cv2
import numpy as np
import pytesseract
from PIL import Image
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from pydantic_settings import BaseSettings
from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, String, Text, create_engine
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

try:
    import face_recognition
except ImportError:  # pragma: no cover - optional at import time in some envs
    face_recognition = None


# =============================================================================
# CONFIG
# =============================================================================

class Settings(BaseSettings):
    database_url: str = "postgresql://kyc_user:kyc_pass@db:5432/kyc_db"

    jwt_secret_key: str = "change-this-secret-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    document_encryption_key: str = "PLEASE_SET_A_32_BYTE_URLSAFE_BASE64_FERNET_KEY="

    storage_dir: str = "/data/kyc_documents"

    face_match_threshold: float = 0.6
    duplicate_hash_check: bool = True

    class Config:
        env_file = ".env"


settings = Settings()
os.makedirs(settings.storage_dir, exist_ok=True)


# =============================================================================
# DATABASE
# =============================================================================

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# MODELS
# =============================================================================

def gen_uuid():
    return str(uuid.uuid4())


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    VERIFIER = "verifier"
    APPLICANT = "applicant"


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

    id_document_path = Column(String, nullable=False)
    selfie_path = Column(String, nullable=False)
    document_hash = Column(String, index=True, nullable=False)

    extracted_data = Column(Text, nullable=True)
    face_similarity_score = Column(Float, nullable=True)
    is_duplicate = Column(Boolean, default=False)
    image_quality_ok = Column(Boolean, default=True)

    status = Column(Enum(VerificationStatus), default=VerificationStatus.PENDING)
    reviewed_by = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    review_notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    applicant = relationship("User", back_populates="submissions", foreign_keys=[applicant_id])


# =============================================================================
# SCHEMAS
# =============================================================================

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
    decision: VerificationStatus
    notes: Optional[str] = None


# =============================================================================
# SECURITY: encrypted document storage
# =============================================================================

_fernet = Fernet(settings.document_encryption_key.encode())


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encrypt_and_store(raw_bytes: bytes, prefix: str) -> str:
    token = _fernet.encrypt(raw_bytes)
    filename = f"{prefix}_{uuid.uuid4().hex}.enc"
    path = os.path.join(settings.storage_dir, filename)
    with open(path, "wb") as f:
        f.write(token)
    return path


def decrypt_file(path: str) -> bytes:
    with open(path, "rb") as f:
        token = f.read()
    return _fernet.decrypt(token)


# =============================================================================
# OCR SERVICE
# =============================================================================

def _preprocess(image_bytes: bytes) -> np.ndarray:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, h=15)
    thresh = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )
    return thresh


def extract_text(image_bytes: bytes) -> str:
    processed = _preprocess(image_bytes)
    return pytesseract.image_to_string(processed)


def extract_fields(image_bytes: bytes) -> str:
    raw_text = extract_text(image_bytes)

    fields = {
        "raw_text_preview": raw_text.strip()[:500],
        "name": None,
        "dob": None,
        "id_number": None,
    }

    dob_match = re.search(r"(\d{2}[/-]\d{2}[/-]\d{4})", raw_text)
    if dob_match:
        fields["dob"] = dob_match.group(1)

    id_match = re.search(r"\b([A-Z]{2,5}[0-9]{6,12})\b", raw_text)
    if id_match:
        fields["id_number"] = id_match.group(1)

    name_match = re.search(r"(?:name)[:\s]+([A-Za-z\s]{3,40})", raw_text, re.IGNORECASE)
    if name_match:
        fields["name"] = name_match.group(1).strip()

    return json.dumps(fields)


# =============================================================================
# FACE MATCHING SERVICE
# =============================================================================

def _bytes_to_rgb_array(image_bytes: bytes) -> np.ndarray:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return np.array(image)


def check_image_quality(image_bytes: bytes):
    arr = _bytes_to_rgb_array(image_bytes)
    face_locations = face_recognition.face_locations(arr)

    if len(face_locations) == 0:
        return False, "No face detected in selfie."
    if len(face_locations) > 1:
        return False, "Multiple faces detected in selfie."

    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
    if blur_score < 50:
        return False, "Selfie image is too blurry."

    return True, "OK"


def compute_face_similarity(id_doc_bytes: bytes, selfie_bytes: bytes) -> float:
    id_arr = _bytes_to_rgb_array(id_doc_bytes)
    selfie_arr = _bytes_to_rgb_array(selfie_bytes)

    id_encodings = face_recognition.face_encodings(id_arr)
    selfie_encodings = face_recognition.face_encodings(selfie_arr)

    if not id_encodings or not selfie_encodings:
        raise ValueError("Could not detect a face in the ID document or selfie.")

    distance = face_recognition.face_distance([id_encodings[0]], selfie_encodings[0])[0]
    similarity = max(0.0, 1.0 - float(distance))
    return round(similarity, 4)


def is_match(similarity_score: float) -> bool:
    return similarity_score >= (1 - settings.face_match_threshold)


# =============================================================================
# AUTH / RBAC
# =============================================================================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, role: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_roles(*allowed_roles: List[UserRole]):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role.value}' is not permitted to perform this action.",
            )
        return current_user

    return role_checker


# =============================================================================
# APP + ROUTES
# =============================================================================

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="KYC Verification System",
    description="Backend service for identity verification via document upload and selfie-based face matching.",
    version="1.0.0",
)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}


# ---------- Auth routes ----------

@app.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED, tags=["auth"])
def register(payload: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered.")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=Token, tags=["auth"])
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    token = create_access_token(subject=user.id, role=user.role.value)
    return Token(access_token=token)


# ---------- KYC routes ----------

@app.post("/kyc/submit", response_model=KYCSubmissionOut, tags=["kyc"])
async def submit_kyc(
    id_document: UploadFile = File(...),
    selfie: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Applicant-facing endpoint: uploads an ID document + a live selfie.
    Runs OCR extraction, duplicate detection, selfie quality check, and
    face similarity scoring, then stores an encrypted copy of both files.
    """
    id_bytes = await id_document.read()
    selfie_bytes = await selfie.read()

    doc_hash = sha256_hex(id_bytes)
    existing = db.query(KYCSubmission).filter(KYCSubmission.document_hash == doc_hash).first()
    is_duplicate = existing is not None

    quality_ok, quality_msg = check_image_quality(selfie_bytes)

    extracted = extract_fields(id_bytes)

    similarity_score = None
    if quality_ok:
        try:
            similarity_score = compute_face_similarity(id_bytes, selfie_bytes)
        except ValueError:
            quality_ok = False
            quality_msg = "Could not detect a face on the ID document."

    id_doc_path = encrypt_and_store(id_bytes, prefix="iddoc")
    selfie_path = encrypt_and_store(selfie_bytes, prefix="selfie")

    if is_duplicate:
        status_ = VerificationStatus.FLAGGED_DUPLICATE
    elif not quality_ok or similarity_score is None or not is_match(similarity_score):
        status_ = VerificationStatus.FLAGGED_FACE_MISMATCH
    else:
        status_ = VerificationStatus.UNDER_REVIEW

    submission = KYCSubmission(
        applicant_id=current_user.id,
        id_document_path=id_doc_path,
        selfie_path=selfie_path,
        document_hash=doc_hash,
        extracted_data=extracted,
        face_similarity_score=similarity_score,
        is_duplicate=is_duplicate,
        image_quality_ok=quality_ok,
        status=status_,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


@app.get("/kyc/submissions/me", response_model=list[KYCSubmissionOut], tags=["kyc"])
def my_submissions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(KYCSubmission).filter(KYCSubmission.applicant_id == current_user.id).all()


@app.get(
    "/kyc/submissions",
    response_model=list[KYCSubmissionOut],
    tags=["kyc"],
    dependencies=[Depends(require_roles(UserRole.VERIFIER, UserRole.ADMIN))],
)
def list_submissions(db: Session = Depends(get_db)):
    """Verifier/Admin-only: view the full queue of pending KYC submissions."""
    return db.query(KYCSubmission).order_by(KYCSubmission.created_at.desc()).all()


@app.post(
    "/kyc/submissions/{submission_id}/review",
    response_model=KYCSubmissionOut,
    tags=["kyc"],
    dependencies=[Depends(require_roles(UserRole.VERIFIER, UserRole.ADMIN))],
)
def review_submission(
    submission_id: str,
    decision: ReviewDecision,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Verifier/Admin-only: manually approve or reject a flagged/under-review submission."""
    submission = db.query(KYCSubmission).filter(KYCSubmission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found.")

    if decision.decision not in (VerificationStatus.APPROVED, VerificationStatus.REJECTED):
        raise HTTPException(status_code=400, detail="Decision must be APPROVED or REJECTED.")

    submission.status = decision.decision
    submission.reviewed_by = current_user.id
    submission.review_notes = decision.notes
    db.commit()
    db.refresh(submission)
    return submission
