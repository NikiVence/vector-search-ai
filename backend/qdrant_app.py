from qdrant_client import  QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from uuid import uuid4

from PIL import Image
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import os

load_dotenv()

client = QdrantClient(
    url="http://127.0.0.1:6333",
    trust_env=False,
)

model = SentenceTransformer(
    "sentence-transformers/clip-ViT-B-32",
    device="cpu",
    token=os.getenv("HG_TOKEN")
)


def qdrant_create_collection(name):
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=512, distance=Distance.COSINE)
    )

def qdrant_upload_photo(photo_path: str, collection_name: str, payload: dict, point_id: str | None = None):
    point_id = point_id or str(uuid4())
    with Image.open(photo_path) as image:
        vector = model.encode(image.convert("RGB"))

    payload = {**payload, "file_id": point_id}
    operation_info = client.upsert(
        collection_name=collection_name,
        wait=True,
        points=[PointStruct(id=point_id, vector=vector.tolist(), payload=payload)],
    )
    return point_id, operation_info


def qdrant_get_top(photo_path: str, collection_name: str, limit: int = 3):
    with Image.open(photo_path) as image:
        vector = model.encode(image.convert("RGB"))

    result = client.query_points(
        collection_name=collection_name,
        query=vector.tolist(),
        limit=limit,
        with_payload=True,
    )

    matches = []
    for point in result.points:
        payload = point.payload or {}
        matches.append({
            "file_id": str(point.id),
            "filename": payload.get("filename", "unknown"),
            "score": float(point.score),
        })
    return matches


