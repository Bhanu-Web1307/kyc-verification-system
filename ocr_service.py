"""
Selfie-to-ID face matching using the face_recognition library (dlib-based
128-d face embeddings). Also performs basic selfie image-quality checks
(face detected, single face, not too blurry) as part of the fraud pipeline.
"""
import io
from typing import Tuple

import cv2
import numpy as np
import face_recognition
from PIL import Image

from app.config import settings


def _bytes_to_rgb_array(image_bytes: bytes) -> np.ndarray:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return np.array(image)


def check_image_quality(image_bytes: bytes) -> Tuple[bool, str]:
    """Flags obviously unusable selfies: no face, multiple faces, or too blurry."""
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
    """
    Returns a 0-1 similarity score (1.0 = identical) comparing the face on the
    ID document photo against the live selfie.
    """
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
