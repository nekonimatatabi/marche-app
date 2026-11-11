"""アップロードされた商品写真の判定・保存・削除"""

import uuid
from pathlib import Path

from flask import current_app

# 判定した中身から拡張子を決める（元のファイル名の拡張子は使わない）
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def detect_media_type(data: bytes) -> str | None:
    """先頭のバイト（マジックナンバー）で JPEG / PNG / WebP を判定する。どれでもなければ None"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _upload_folder() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"])


def save_image(data: bytes, media_type: str) -> str:
    """UUID のファイル名で保存し、ファイル名を返す"""
    filename = uuid.uuid4().hex + EXTENSIONS[media_type]
    folder = _upload_folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(data)
    return filename


def delete_image(filename: str | None) -> None:
    """写真を消す。無くてもエラーにしない"""
    if not filename:
        return
    # ファイル名の部分だけを使い、UPLOAD_FOLDER の外（../ など）を消せないようにする
    (_upload_folder() / Path(filename).name).unlink(missing_ok=True)
