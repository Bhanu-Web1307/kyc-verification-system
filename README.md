# KYC Verification System

Backend service for identity verification through ID document upload and
selfie-based face matching. Built with **FastAPI**, **PostgreSQL**, and
**OpenCV/OCR**, deployed with **Docker**.

## Features

- **Document ingestion & OCR** — uploads an ID document image, preprocesses it
  (denoising, adaptive thresholding) and extracts fields (name, DOB, ID number)
  with Tesseract OCR.
- **Selfie-based face matching** — compares the face on the ID document against
  a live selfie using `face_recognition` (dlib 128-d embeddings) and returns a
  similarity score.
- **Fraud checks** — face similarity scoring, selfie image-quality validation
  (no face / multiple faces / too blurry), and duplicate-submission detection
  via document content hashing.
- **Automated approval workflow** — submissions are auto-routed to
  `under_review`, `flagged_duplicate`, or `flagged_face_mismatch`; verifiers
  make the final `approved`/`rejected` call.
- **Role-based access control (RBAC)** — `applicant`, `verifier`, `admin`
  roles enforced via JWT, so only verifiers/admins can view the submission
  queue or issue decisions.
- **Encrypted document storage** — every uploaded file is encrypted (Fernet /
  AES) before being written to disk.
- **Dockerized** — API + PostgreSQL run via `docker-compose`.

## Project structure

```
kyc_verification_system/
├── app/
│   ├── main.py              # FastAPI app entrypoint
│   ├── config.py            # settings (env-driven)
│   ├── database.py          # SQLAlchemy engine/session
│   ├── models.py            # User, KYCSubmission ORM models
│   ├── schemas.py            # Pydantic request/response schemas
│   ├── auth.py               # JWT auth + RBAC dependencies
│   ├── routers/
│   │   ├── auth_router.py    # /auth/register, /auth/login
│   │   └── kyc_router.py     # /kyc/submit, /kyc/submissions, /kyc/.../review
│   └── utils/
│       ├── security.py       # Fernet encryption for stored documents
│       ├── ocr_service.py    # OpenCV preprocessing + Tesseract OCR
│       └── face_service.py   # face_recognition-based similarity & quality checks
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Running locally

```bash
cp .env.example .env
# generate a real key and paste it into .env as DOCUMENT_ENCRYPTION_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker-compose up --build
```

The API is then available at `http://localhost:8000`, with interactive docs
at `http://localhost:8000/docs`.

## API overview

| Method | Endpoint                              | Role               | Description                              |
|--------|----------------------------------------|--------------------|-------------------------------------------|
| POST   | `/auth/register`                       | public             | Create a user account                     |
| POST   | `/auth/login`                          | public             | Get a JWT access token                    |
| POST   | `/kyc/submit`                          | applicant          | Upload ID document + selfie for verification |
| GET    | `/kyc/submissions/me`                  | applicant          | View your own submission history          |
| GET    | `/kyc/submissions`                     | verifier, admin    | View the full verification queue          |
| POST   | `/kyc/submissions/{id}/review`         | verifier, admin    | Approve or reject a submission            |

## Notes on production hardening

- Swap the auto `create_all` table creation for **Alembic** migrations.
- Store `DOCUMENT_ENCRYPTION_KEY` and `JWT_SECRET_KEY` in a secrets manager,
  not `.env`, in production.
- Put object storage (S3/GCS with server-side encryption) behind
  `security.py` instead of local disk for horizontal scalability.
- Add rate limiting on `/kyc/submit` to slow down automated abuse.
