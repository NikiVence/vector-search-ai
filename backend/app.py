from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import asyncio
from sqlalchemy.exc import IntegrityError

from backend.qdrant_app import qdrant_get_top, qdrant_upload_photo, client, qdrant_create_collection

from backend.database.db import session_maker, AsyncSession, get_session
from backend.database.models import Collection
from sqlalchemy import select
from backend.schemas import CollectionCreate
import os, shutil
from pathlib import Path
import logging

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

@app.post("/api/photos")
async def upload_photo(photo: UploadFile = File(...), collection_name: UUID = Form(...), session: AsyncSession = Depends(get_session)):
    photo_id = str(uuid4())

    extension = Path(photo.filename).suffix
    path = UPLOAD_DIR / f"{photo_id}{extension}"

    collection = await session.get(Collection, collection_name)
    if not collection:
        raise HTTPException(
            status_code=404,
            detail=f"Collection with id '{collection_name}' not found."
        )

    with path.open("wb") as file:
        shutil.copyfileobj(photo.file, file)

    point_id, _ = await asyncio.to_thread(
        qdrant_upload_photo,
        str(path),
        payload={
            "filename": photo.filename,
            "image_path": str(path)
        },
        collection_name=collection.name,
    )

    return {
        "id": point_id,
        "filename": photo.filename
    }

@app.post("/api/search")
async def search_photos(photo: UploadFile = File(...), limit: int = 3, collection_name: UUID = Form(...), session: AsyncSession = Depends(get_session)):
    photo.file.seek(0)

    collection = await session.get(Collection, collection_name)
    if not collection:
        raise HTTPException(
            status_code=404,
            detail=f"Collection with id '{collection_name}' not found."
        )
    
    matches = await asyncio.to_thread(
        qdrant_get_top,
        photo_path=photo.file,
        collection_name=collection.name,
        limit=limit,
    )
    for m in matches:
        m["image_url"] = f"/api/photos/{m['file_id']}"

    return {"matches": matches}

@app.get("/api/photos/{photo_id}")
async def get_photo(photo_id: str):

    points = client.retrieve(
        collection_name="test",
        ids=[photo_id],
        with_payload=True
    )

    if not points:
        raise HTTPException(
            status_code=404,
            detail="Photo not found"
        )

    path = points[0].payload.get("image_path")

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
        qdrant_create_collection(collection.name)
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