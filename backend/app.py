from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

import asyncio
from sqlalchemy.exc import IntegrityError

from backend.qdrant_app import qdrant_get_top, qdrant_upload_photo, client, qdrant_create_collection

from backend.database.db import session_maker, AsyncSession, get_session
from backend.database.models import Collection
from sqlalchemy import select
from backend.schemas import CollectionCreate
import os
from pathlib import Path
import logging

from backend.s3_storage import (
    upload_file_to_s3, generate_presigned_url, validate_image,
    UnsupportedImageError, InvalidImageError,
)

from uuid import uuid4, UUID


app = FastAPI()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

UPLOAD_DIR = Path("temp_photo")
UPLOAD_DIR.mkdir(exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_FILE_SIZE = 10 * 1024 * 1024


def save_upload(source, path):
    source.seek(0)
    total = 0
    with path.open("wb") as target:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail="Image exceeds 10 MB")
            target.write(chunk)


def store_photo(source, path, photo_id, filename, collection):
    # One worker owns the file until S3 and inference have finished, even if
    # the awaiting request is cancelled. Cleanup cannot race with inference.
    s3_key = f"photos/{photo_id}/original"
    try:
        save_upload(source, path)
        upload_file_to_s3(str(path), os.environ["S3_BUCKET"], s3_key)
        qdrant_upload_photo(
            str(path), collection,
            {"filename": filename, "s3_key": s3_key},
            point_id=photo_id,
        )
        return {"id": photo_id, "filename": filename}
    except UnsupportedImageError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        # Do not delete the S3 object: a timed-out upsert may have committed.
        logger.exception("Photo processing failed: photo_id=%s s3_key=%s", photo_id, s3_key)
        raise HTTPException(status_code=503, detail="Photo storage or indexing unavailable") from exc
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Unable to remove temporary photo %s", path)


@app.post("/api/photos", status_code=201)
async def upload_photo(photo: UploadFile = File(...), collection_name: UUID = Form(...), session: AsyncSession = Depends(get_session)):
    collection = await session.get(Collection, collection_name)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    photo_id = str(uuid4())
    path = UPLOAD_DIR / photo_id
    return await asyncio.to_thread(
        store_photo, photo.file, path, photo_id, photo.filename or "image", collection.name,
    )


@app.post("/api/search")
async def search_photos(photo: UploadFile = File(...), limit: int = Query(3, ge=1, le=3), collection_name: UUID = Form(...), session: AsyncSession = Depends(get_session)):
    photo.file.seek(0)

    collection = await session.get(Collection, collection_name)
    if not collection:
        raise HTTPException(
            status_code=404,
            detail=f"Collection with id '{collection_name}' not found."
        )
    
    def search():
        # Validate a separate buffer: Pillow may close its input file.
        from io import BytesIO
        photo.file.seek(0)
        data = photo.file.read(MAX_FILE_SIZE + 1)
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="Image exceeds 10 MB")
        try:
            validate_image(BytesIO(data))
        except UnsupportedImageError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except InvalidImageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return qdrant_get_top(BytesIO(data), collection.name, limit)

    matches = await asyncio.to_thread(search)
    for m in matches:
        m["image_url"] = f"/api/photos/{m['file_id']}?collection_name={collection.id}"

    return {"matches": matches}

@app.get("/api/photos/{photo_id}")
async def get_photo(photo_id: UUID, collection_name: UUID, session: AsyncSession = Depends(get_session)):
    collection = await session.get(Collection, collection_name)
    if not collection:
        raise HTTPException(
            status_code=404,
            detail=f"Collection with id '{collection_name}' not found."
        )

    points = await asyncio.to_thread(
        client.retrieve,
        collection_name=collection.name,
        ids=[str(photo_id)],
        with_payload=True
    )

    if not points:
        raise HTTPException(
            status_code=404,
            detail="Photo not found"
        )

    payload = points[0].payload or {}
    s3_key = payload.get("s3_key")
    if s3_key:
        presigned_url = await asyncio.to_thread(
            generate_presigned_url,
            bucket_name=os.getenv("S3_BUCKET"),
            object_name=s3_key,
            expiration=3600
        )
        return RedirectResponse(url=presigned_url, status_code=302, headers={"Cache-Control": "no-store"})
    else:
        path = payload.get("image_path")

    if not path or not os.path.exists(path):
        raise HTTPException(
            status_code=404,
            detail="Image file not found"
        )

    return FileResponse(path)

@app.get("/api/collections")
async def get_collections(session: AsyncSession = Depends(get_session)):
    statement = select(Collection).order_by(Collection.created_at,
                                            Collection.id)
    result = await session.scalars(statement)
    collections = result.all()
    return {"collections": [{"id": str(c.id), "name": c.name} for c in collections]}

@app.post("/api/collections", status_code=201)
async def create_collection(collection: CollectionCreate, session: AsyncSession = Depends(get_session)):
    # Check if collection already exists in the database
    statement = select(Collection).where(Collection.name == collection.name)
    result = await session.scalars(statement)
    existing_collection = result.first()

    if existing_collection:
        raise HTTPException(
            status_code=409,
            detail=f"Collection with name '{collection.name}' already exists."
        )

    # Create collection in Qdrant
    try:
        await asyncio.to_thread(qdrant_create_collection, collection.name)
    except Exception as e:
        logger.error(f"Failed to create collection in Qdrant: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to create collection in Qdrant."
        )

    # Add collection to the database
    new_collection = Collection(name=collection.name)
    session.add(new_collection)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Collection with name '{collection.name}' already exists."
        )
    await session.refresh(new_collection)

    return {"id": str(new_collection.id), "name": new_collection.name}