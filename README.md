# KYC Verification System

Backend service for identity verification through ID document upload and
selfie-based face matching. Built with **FastAPI**, **PostgreSQL**, and
**OpenCV/OCR**, deployed with **Docker**.

This is the **single-file version**: all code (config, database, models,
OCR, face matching, auth/RBAC, and API routes) lives in `main.py`, so the
whole project can be uploaded to GitHub as flat files with no folder
structure to preserve.

## Features

- **Document ingestion & OCR** — uploads an ID document image, preprocesses
  it (denoising, adaptive thresholding) and extracts fields (name, DOB, ID
  number) with Tesseract OCR.
- **Selfie-based face matching** — compares the face on the ID document
  against a live selfie using `face_recognition` (dlib 128-d embeddings) and
  returns a similarity score.
- **Fraud checks** — face similarity scoring, selfie image-quality
  validation (no face / multiple faces / too blurry), and duplicate
  submission detection via document content hashing.
- **Automated approval workflow** — submissions are auto-routed to
  `under_review`, `flagged_duplicate`, or `flagged_face_mismatch`; verifiers
  make the final `approved`/`rejected` call.
- **Role-based access control (RBAC)** — `applicant`, `verifier`, `admin`
  roles enforced via JWT.
- **Encrypted document storage** — every uploaded file is encrypted
  (Fernet / AES) before being written to disk.
- **Dockerized** — API + PostgreSQL run via `docker-compose`.

## Project structure

```
kyc-verification-system/
├── main.py             # entire backend: config, models, routes, services
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

| Method | Endpoint                       | Role             | Description                              |
|--------|----------------------------------|------------------|--------------------------------------------|
| POST   | `/auth/register`                | public           | Create a user account                      |
| POST   | `/auth/login`                   | public           | Get a JWT access token                     |
| POST   | `/kyc/submit`                   | applicant        | Upload ID document + selfie for verification |
| GET    | `/kyc/submissions/me`           | applicant        | View your own submission history           |
| GET    | `/kyc/submissions`               | verifier, admin  | View the full verification queue           |
| POST   | `/kyc/submissions/{id}/review`   | verifier, admin  | Approve or reject a submission              |

## Notes on production hardening

- Store `DOCUMENT_ENCRYPTION_KEY` and `JWT_SECRET_KEY` in a secrets manager,
  not `.env`, in production.
- Swap local disk storage for encrypted object storage (S3/GCS) at scale.
- Add rate limiting on `/kyc/submit`.
- As the codebase grows, consider splitting `main.py` back into modules
  (this single-file layout trades organization for upload simplicity).
