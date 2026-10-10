"""Isolated tests: no database, model downloads or cloud access required."""
import ast
import asyncio
import io
import logging
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from fastapi import File, Form, Depends, Query, UploadFile, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image
from backend import s3_storage as storage

ROOT = Path(__file__).resolve().parents[2]


def image_bytes(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(stream, format=fmt)
    return stream.getvalue()


def handlers():
    # Extract handlers without importing the app's eager ML/database clients.
    tree = ast.parse((ROOT / "backend/app.py").read_text(encoding="utf-8"))
    names = {"save_upload", "store_photo", "upload_photo", "search_photos", "get_photo"}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    scope = dict(asyncio=asyncio, os=os, UUID=UUID, uuid4=uuid4, File=File,
                 Form=Form, Depends=Depends, Query=Query, UploadFile=UploadFile,
                 AsyncSession=object, get_session=lambda: None, Collection=object,
                 HTTPException=HTTPException, FileResponse=FileResponse,
                 RedirectResponse=RedirectResponse, MAX_FILE_SIZE=10*1024*1024,
                 logger=logging.getLogger("storage-test"),
                 validate_image=storage.validate_image,
                 UnsupportedImageError=storage.UnsupportedImageError,
                 InvalidImageError=storage.InvalidImageError)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app-handlers", "exec"), scope)
    return scope


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "photo"
        self.scope = handlers()
        self.cloud = Mock()
        self.patcher = patch.object(storage, "get_s3_client", return_value=self.cloud)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.scope["upload_file_to_s3"] = storage.upload_file_to_s3
        self.scope["qdrant_upload_photo"] = Mock(return_value=("id", None))
        self.env = patch.dict(os.environ, {"S3_BUCKET": "test-bucket"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def store(self, data):
        return self.scope["store_photo"](io.BytesIO(data), self.path, "photo-id", "photo", "album")

    def test_supported_formats_and_matching_ids(self):
        for fmt, mime in [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]:
            with self.subTest(fmt=fmt):
                result = self.store(image_bytes(fmt))
                self.assertEqual(result["id"], "photo-id")
                self.assertFalse(self.path.exists())
                self.assertEqual(self.cloud.upload_file.call_args.kwargs["ExtraArgs"]["ContentType"], mime)
                args = self.scope["qdrant_upload_photo"].call_args
                self.assertEqual(args.kwargs["point_id"], "photo-id")
                self.assertEqual(args.args[2], {"filename": "photo", "s3_key": "photos/photo-id/original"})

    def test_invalid_and_unsupported_images(self):
        for data, status in [(b"broken", 422), (image_bytes("GIF"), 415)]:
            with self.subTest(status=status):
                with self.assertRaises(HTTPException) as error:
                    self.store(data)
                self.assertEqual(error.exception.status_code, status)
                self.assertFalse(self.path.exists())
        self.cloud.upload_file.assert_not_called()

    def test_size_limit_and_partial_file_cleanup(self):
        self.scope["MAX_FILE_SIZE"] = 3
        with self.assertRaises(HTTPException) as error:
            self.store(b"1234")
        self.assertEqual(error.exception.status_code, 413)
        self.assertFalse(self.path.exists())
        self.cloud.upload_file.assert_not_called()

    def test_s3_failure_cleans_temp_and_skips_qdrant(self):
        self.cloud.upload_file.side_effect = RuntimeError("offline")
        with self.assertLogs("storage-test", level="ERROR"):
            with self.assertRaises(HTTPException) as error:
                self.store(image_bytes())
        self.assertEqual(error.exception.status_code, 503)
        self.scope["qdrant_upload_photo"].assert_not_called()
        self.assertFalse(self.path.exists())

    def test_qdrant_failure_does_not_delete_s3_object(self):
        self.scope["qdrant_upload_photo"].side_effect = TimeoutError()
        with self.assertLogs("storage-test", level="ERROR"):
            with self.assertRaises(HTTPException):
                self.store(image_bytes())
        self.assertFalse(self.path.exists())
        self.cloud.delete_object.assert_not_called()

    def test_cleanup_error_does_not_hide_success(self):
        with patch.object(Path, "unlink", side_effect=PermissionError()), self.assertLogs("storage-test", level="ERROR"):
            self.assertEqual(self.store(image_bytes())["id"], "photo-id")

    def test_image_redirect_and_legacy_file(self):
        collection_id = uuid4()
        photo_id = uuid4()
        class Session:
            async def get(self, model, key):
                return SimpleNamespace(name="album")
        self.scope["client"] = Mock()
        self.scope["generate_presigned_url"] = storage.generate_presigned_url
        self.cloud.generate_presigned_url.return_value = "https://example.com/photo?signed=1"
        self.scope["client"].retrieve.return_value = [SimpleNamespace(payload={"s3_key": "photos/id/original"})]
        result = asyncio.run(self.scope["get_photo"](photo_id, collection_id, Session()))
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result.headers["location"], "https://example.com/photo?signed=1")
        self.assertEqual(self.scope["client"].retrieve.call_args.kwargs["collection_name"], "album")
        self.path.write_bytes(image_bytes())
        self.scope["client"].retrieve.return_value = [SimpleNamespace(payload={"image_path": str(self.path)})]
        result = asyncio.run(self.scope["get_photo"](photo_id, collection_id, Session()))
        self.assertIsInstance(result, FileResponse)

    def test_missing_collection_does_not_save_file(self):
        class Session:
            async def get(self, model, key):
                return None
        photo = UploadFile(filename="photo.png", file=io.BytesIO(image_bytes()))
        with self.assertRaises(HTTPException) as error:
            asyncio.run(self.scope["upload_photo"](photo, uuid4(), Session()))
        self.assertEqual(error.exception.status_code, 404)
        self.cloud.upload_file.assert_not_called()

    def test_search_validates_and_uses_selected_collection(self):
        collection_id = uuid4()
        class Session:
            async def get(self, model, key):
                return SimpleNamespace(name="album", id=collection_id)
        self.scope["qdrant_get_top"] = Mock(return_value=[{"file_id": "photo-id"}])
        photo = UploadFile(filename="photo.webp", file=io.BytesIO(image_bytes("WEBP")))
        result = asyncio.run(self.scope["search_photos"](photo, 3, collection_id, Session()))
        self.assertEqual(result["matches"][0]["image_url"], f"/api/photos/photo-id?collection_name={collection_id}")
        self.assertEqual(self.scope["qdrant_get_top"].call_args.args[1], "album")


if __name__ == "__main__":
    unittest.main()
