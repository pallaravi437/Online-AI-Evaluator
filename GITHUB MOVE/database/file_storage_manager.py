import os
import uuid
import re
import logging
from typing import Optional, Dict, Any, Tuple
from config import Config
from database.server_registry import db_server_manager

logger = logging.getLogger("bea.file_storage")


class FileStorageManager:
    """
    Unified Object Storage Manager across Firebase Storage, Supabase Storage,
    and Local Disk Storage. Enforces clean separation between binary assets
    and structured relational metadata.
    """
    _instance: Optional['FileStorageManager'] = None

    def __init__(self):
        self.base_dir = os.path.dirname(os.path.dirname(__file__))
        self.local_upload_dir = os.path.join(self.base_dir, "static", "uploads")
        os.makedirs(self.local_upload_dir, exist_ok=True)

    @classmethod
    def get_instance(cls) -> 'FileStorageManager':
        if cls._instance is None:
            cls._instance = FileStorageManager()
        return cls._instance

    def upload_file(
        self,
        server_id: str,
        file_bytes: bytes,
        filename: str,
        folder: str = "academic_materials",
        mime_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Uploads binary file to the specified storage server (Supabase, Firebase, or Local).
        Returns metadata including storage_server_id, storage_path, and file_url.
        """
        if not file_bytes:
            raise ValueError("file_bytes cannot be empty.")

        server = db_server_manager.get_server(server_id) or db_server_manager.get_server("local_default")
        provider = server.get("provider", "local") if server else "local"

        ext = filename.rsplit(".", 1)[1].lower() if "." in filename else "bin"
        safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', filename)
        unique_prefix = uuid.uuid4().hex[:10]
        stored_filename = f"{unique_prefix}_{safe_name}"
        storage_rel_path = f"{folder}/{stored_filename}".replace("//", "/")
        size_kb = round(len(file_bytes) / 1024, 2)

        # 1. Supabase Storage
        if provider == "supabase":
            client = db_server_manager.get_supabase_client(server_id, service_role=True)
            bucket_name = server.get("config", {}).get("storage_bucket") or Config.STORAGE_BUCKET or "bea-answer-scripts"
            if client:
                try:
                    res = client.storage.from_(bucket_name).upload(
                        path=storage_rel_path,
                        file=file_bytes,
                        file_options={"content-type": mime_type or "application/octet-stream", "upsert": "true"}
                    )
                    # Attempt to get signed or public URL
                    try:
                        url_res = client.storage.from_(bucket_name).create_signed_url(storage_rel_path, 3600 * 24)
                        file_url = url_res.get("signedURL") or url_res.get("signedUrl")
                    except Exception:
                        file_url = f"/storage/supabase/{bucket_name}/{storage_rel_path}"

                    return {
                        "storage_server_id": server_id,
                        "storage_path": storage_rel_path,
                        "file_url": file_url,
                        "file_name": filename,
                        "stored_filename": stored_filename,
                        "file_type": ext,
                        "file_size_kb": size_kb,
                        "provider": "supabase",
                        "bucket": bucket_name
                    }
                except Exception as e:
                    logger.warning(f"Supabase storage upload failed ({e}), falling back to local storage.")

        # 2. Firebase Storage
        elif provider == "firebase":
            app = db_server_manager.get_firebase_app(server_id)
            if app:
                try:
                    from firebase_admin import storage as fb_storage
                    bucket_name = server.get("config", {}).get("storage_bucket")
                    bucket = fb_storage.bucket(name=bucket_name, app=app)
                    blob = bucket.blob(storage_rel_path)
                    blob.upload_from_string(file_bytes, content_type=mime_type or "application/octet-stream")

                    # Generate signed URL
                    try:
                        import datetime
                        file_url = blob.generate_signed_url(
                            expiration=datetime.timedelta(hours=24),
                            method='GET'
                        )
                    except Exception:
                        file_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{storage_rel_path}?alt=media"

                    return {
                        "storage_server_id": server_id,
                        "storage_path": storage_rel_path,
                        "file_url": file_url,
                        "file_name": filename,
                        "stored_filename": stored_filename,
                        "file_type": ext,
                        "file_size_kb": size_kb,
                        "provider": "firebase",
                        "bucket": bucket.name
                    }
                except Exception as e:
                    logger.warning(f"Firebase storage upload failed ({e}), falling back to local storage.")

        # 3. Local Filesystem Storage (Standard or Fallback)
        dest_folder = os.path.join(self.local_upload_dir, folder)
        os.makedirs(dest_folder, exist_ok=True)
        local_disk_path = os.path.join(dest_folder, stored_filename)

        with open(local_disk_path, "wb") as f:
            f.write(file_bytes)

        local_web_url = f"/static/uploads/{folder}/{stored_filename}"
        return {
            "storage_server_id": "local_default",
            "storage_path": storage_rel_path,
            "local_disk_path": local_disk_path,
            "file_url": local_web_url,
            "file_name": filename,
            "stored_filename": stored_filename,
            "file_type": ext,
            "file_size_kb": size_kb,
            "provider": "local"
        }

    def download_file(self, server_id: str, storage_path: str) -> Tuple[bool, Optional[bytes], str]:
        """
        Retrieves binary file from its original storage server.
        Guarantees retrieval even if system default server has changed.
        """
        if not storage_path:
            return False, None, "Storage path is empty."

        server = db_server_manager.get_server(server_id)
        provider = server.get("provider", "local") if server else "local"

        # 1. Supabase Storage
        if provider == "supabase":
            client = db_server_manager.get_supabase_client(server_id, service_role=True)
            bucket_name = server.get("config", {}).get("storage_bucket") or Config.STORAGE_BUCKET or "bea-answer-scripts"
            if client:
                try:
                    data = client.storage.from_(bucket_name).download(storage_path)
                    if data:
                        return True, data, "Retrieved from Supabase storage."
                except Exception as e:
                    logger.error(f"Error downloading from Supabase storage: {e}")

        # 2. Firebase Storage
        elif provider == "firebase":
            app = db_server_manager.get_firebase_app(server_id)
            if app:
                try:
                    from firebase_admin import storage as fb_storage
                    bucket_name = server.get("config", {}).get("storage_bucket")
                    bucket = fb_storage.bucket(name=bucket_name, app=app)
                    blob = bucket.blob(storage_path)
                    data = blob.download_as_bytes()
                    if data:
                        return True, data, "Retrieved from Firebase storage."
                except Exception as e:
                    logger.error(f"Error downloading from Firebase storage: {e}")

        # 3. Local Filesystem Storage check
        local_disk_path = os.path.join(self.local_upload_dir, storage_path.replace("/", os.sep))
        if os.path.exists(local_disk_path):
            try:
                with open(local_disk_path, "rb") as f:
                    data = f.read()
                return True, data, "Retrieved from local storage."
            except Exception as e:
                return False, None, f"Local read error: {e}"

        # Try base directory
        alt_path = os.path.join(self.base_dir, storage_path.lstrip("/\\"))
        if os.path.exists(alt_path):
            try:
                with open(alt_path, "rb") as f:
                    data = f.read()
                return True, data, "Retrieved from local root storage."
            except Exception as e:
                return False, None, f"Local read error: {e}"

        return False, None, f"File not found at storage server '{server_id}' path '{storage_path}'."

    def delete_file(self, server_id: str, storage_path: str) -> Tuple[bool, str]:
        """Deletes binary file from the designated storage server."""
        if not storage_path:
            return False, "Storage path is empty."

        server = db_server_manager.get_server(server_id)
        provider = server.get("provider", "local") if server else "local"

        if provider == "supabase":
            client = db_server_manager.get_supabase_client(server_id, service_role=True)
            bucket_name = server.get("config", {}).get("storage_bucket") or Config.STORAGE_BUCKET or "bea-answer-scripts"
            if client:
                try:
                    client.storage.from_(bucket_name).remove([storage_path])
                except Exception as e:
                    logger.warning(f"Could not remove from Supabase: {e}")

        elif provider == "firebase":
            app = db_server_manager.get_firebase_app(server_id)
            if app:
                try:
                    from firebase_admin import storage as fb_storage
                    bucket_name = server.get("config", {}).get("storage_bucket")
                    bucket = fb_storage.bucket(name=bucket_name, app=app)
                    blob = bucket.blob(storage_path)
                    blob.delete()
                except Exception as e:
                    logger.warning(f"Could not remove from Firebase: {e}")

        # Remove local file if present
        local_disk_path = os.path.join(self.local_upload_dir, storage_path.replace("/", os.sep))
        if os.path.exists(local_disk_path):
            try:
                os.remove(local_disk_path)
            except Exception:
                pass

        return True, "File removed from storage."


# Global singleton
file_storage_manager = FileStorageManager.get_instance()

