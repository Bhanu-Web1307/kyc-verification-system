from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.database import get_db
from app.models import KYCSubmission, User, UserRole, VerificationStatus
from app.schemas import KYCSubmissionOut, ReviewDecision
from app.utils.face_service import check_image_quality, compute_face_similarity, is_match
from app.utils.ocr_service import extract_fields
from app.utils.security import encrypt_and_store, sha256_hex

router = APIRouter(prefix="/kyc", tags=["kyc"])


@router.post("/submit", response_model=KYCSubmissionOut)
async def submit_kyc(
    id_document: UploadFile = File(...),
    selfie: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Applicant-facing endpoint: uploads an ID document + a live selfie.
    Runs the full automated pipeline: OCR extraction, duplicate detection,
    selfie quality check, and face similarity scoring, then stores an
    encrypted copy of both files.
    """
    id_bytes = await id_document.read()
    selfie_bytes = await selfie.read()

    # --- Duplicate detection: hash the ID document content ---
    doc_hash = sha256_hex(id_bytes)
    existing = db.query(KYCSubmission).filter(KYCSubmission.document_hash == doc_hash).first()
    is_duplicate = existing is not None

    # --- Selfie image-quality check ---
    quality_ok, quality_msg = check_image_quality(selfie_bytes)

    # --- OCR extraction on the ID document ---
    extracted = extract_fields(id_bytes)

    # --- Face similarity: ID photo vs. live selfie ---
    similarity_score = None
    if quality_ok:
        try:
            similarity_score = compute_face_similarity(id_bytes, selfie_bytes)
        except ValueError:
            quality_ok = False
            quality_msg = "Could not detect a face on the ID document."

    # --- Encrypt + persist both files ---
    id_doc_path = encrypt_and_store(id_bytes, prefix="iddoc")
    selfie_path = encrypt_and_store(selfie_bytes, prefix="selfie")

    # --- Determine initial workflow status ---
    if is_duplicate:
        status_ = VerificationStatus.FLAGGED_DUPLICATE
    elif not quality_ok or similarity_score is None or not is_match(similarity_score):
        status_ = VerificationStatus.FLAGGED_FACE_MISMATCH
    else:
        status_ = VerificationStatus.UNDER_REVIEW  # passed automated checks, awaits human verifier

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


@router.get("/submissions/me", response_model=list[KYCSubmissionOut])
def my_submissions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(KYCSubmission).filter(KYCSubmission.applicant_id == current_user.id).all()


@router.get(
    "/submissions",
    response_model=list[KYCSubmissionOut],
    dependencies=[Depends(require_roles(UserRole.VERIFIER, UserRole.ADMIN))],
)
def list_submissions(db: Session = Depends(get_db)):
    """Verifier/Admin-only: view the full queue of pending KYC submissions."""
    return db.query(KYCSubmission).order_by(KYCSubmission.created_at.desc()).all()


@router.post(
    "/submissions/{submission_id}/review",
    response_model=KYCSubmissionOut,
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
