"""
api/routes.py
-------------
FastAPI router implementing:
  POST   /api/upload          – upload a resource file to Cloudflare R2 + metadata
  GET    /api/notes           – list/filter notes
  DELETE /api/notes/{id}      – delete a note from DB and Cloudflare R2
"""

import os
import re
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from models.resource import Resource
from schemas.resource import MessageResponse, ResourceListResponse, ResourceOut

router = APIRouter(prefix="/api", tags=["resources"])

# ── Cloudflare R2 Client Setup ──────────────────────────────────────────────────

BUCKET_NAME = "dpharma-notes"

s3_client = boto3.client(
    "s3",
    endpoint_url=os.getenv("R2_ENDPOINT_URL"),
    aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
    config=Config(signature_version="s3v4")
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _secure_filename(filename: str) -> str:
    """
    Produce a filesystem-safe filename that:
    - Strips directory components and null bytes
    - Normalises unicode characters to ASCII equivalents
    - Replaces any remaining unsafe chars with underscores
    - Preserves the original extension
    """
    filename = unicodedata.normalize("NFKD", filename)
    filename = filename.encode("ascii", "ignore").decode("ascii")

    filename = filename.replace("\x00", "").replace("/", "_").replace("\\", "_")

    stem, _, suffix = filename.rpartition(".")
    suffix = suffix.lower()
    stem = stem or "file"

    # Replace anything that isn't alphanumeric, dash, underscore, or dot
    stem = re.sub(r"[^\w\-]", "_", stem)

    # Collapse repeated underscores
    stem = re.sub(r"_+", "_", stem).strip("_") or "file"

    return f"{stem}.{suffix}"


def _validate_extension(filename: str) -> str:
    """Return the lowercased extension or raise 400 if not whitelisted."""
    ext = Path(filename).suffix.lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"File type '{ext}' is not permitted. "
                f"Accepted formats: {', '.join(sorted(settings.ALLOWED_EXTENSIONS))}"
            ),
        )
    return ext


def _check_r2_file_exists(filename: str) -> bool:
    """Check if an object already exists in the Cloudflare R2 bucket."""
    try:
        s3_client.head_object(Bucket=BUCKET_NAME, Key=filename)
        return True
    except Exception:
        return False


def _get_unique_r2_filename(safe_name: str) -> str:
    """
    If a file with `safe_name` already exists in R2, append a counter suffix
    so we never silently overwrite existing resources.
    """
    if not _check_r2_file_exists(safe_name):
        return safe_name

    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    counter = 1
    while True:
        candidate = f"{stem}_{counter}{suffix}"
        if not _check_r2_file_exists(candidate):
            return candidate
        counter += 1


# ── POST /api/upload ───────────────────────────────────────────────────────────

@router.post(
    "/upload",
    response_model=ResourceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new academic resource",
)
async def upload_resource(
    file: UploadFile,
    title: str = Form(..., min_length=2, max_length=512),
    subject: str = Form(..., min_length=1, max_length=256),
    semester: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> ResourceOut:
    # 1. Validate semester value
    if semester not in settings.VALID_SEMESTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid semester '{semester}'. Must be one of: {settings.VALID_SEMESTERS}",
        )

    # 2. Validate file extension
    original_name = file.filename or "upload"
    _validate_extension(original_name)

    # 3. Sanitise filename and resolve a unique cloud target key name
    safe_name = _secure_filename(original_name)
    final_name = _get_unique_r2_filename(safe_name)

    # 4. Stream file content directly from memory straight to Cloudflare R2 bucket
    try:
        file_content = await file.read()
        file_buffer = BytesIO(file_content)
        
        s3_client.upload_fileobj(
            file_buffer,
            BUCKET_NAME,
            final_name,
            ExtraArgs={"ContentType": file.content_type}
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file to Cloudflare R2 storage: {exc}",
        )

    # 5. Build dynamic R2 URL layout link for document retrieval
    endpoint_clean = os.getenv("R2_ENDPOINT_URL", "").rstrip("/")
    cloud_file_url = f"{endpoint_clean}/{BUCKET_NAME}/{final_name}"

    # 6. Persist cloud metadata references to the database
    resource = Resource(
        title=title.strip(),
        subject=subject.strip(),
        semester=semester,
        file_name=final_name,
        file_path=cloud_file_url,
    )
    db.add(resource)
    await db.flush()
    await db.refresh(resource)

    return ResourceOut.model_validate(resource)


# ── GET /api/notes ─────────────────────────────────────────────────────────────

@router.get(
    "/notes",
    response_model=ResourceListResponse,
    summary="List and filter academic resources",
)
async def list_notes(
    semester: Optional[str] = None,
    subject: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> ResourceListResponse:
    stmt = select(Resource).order_by(Resource.upload_date.desc())

    if semester:
        if semester not in settings.VALID_SEMESTERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid semester filter '{semester}'. Must be one of: {settings.VALID_SEMESTERS}",
            )
        stmt = stmt.where(Resource.semester == semester)

    if subject:
        stmt = stmt.where(Resource.subject.ilike(f"%{subject.strip()}%"))

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return ResourceListResponse(
        total=len(rows),
        items=[ResourceOut.model_validate(r) for r in rows],
    )


# ── DELETE /api/notes/{id} ─────────────────────────────────────────────────────

@router.delete(
    "/notes/{note_id}",
    response_model=MessageResponse,
    summary="Delete a resource and its cloud file",
)
async def delete_note(
    note_id: int,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    # 1. Fetch the record
    result = await db.execute(select(Resource).where(Resource.id == note_id))
    resource = result.scalar_one_or_none()

    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource with id={note_id} does not exist.",
        )

    # 2. Remove the object from Cloudflare R2 bucket storage
    try:
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=resource.file_name)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not remove file asset from cloud bucket storage: {exc}",
        )

    # 3. Drop the database row reference
    await db.delete(resource)

    return MessageResponse(
        message="Resource deleted successfully.",
        detail=f"Removed '{resource.file_name}' (id={note_id}) from the cloud vault.",
    )

