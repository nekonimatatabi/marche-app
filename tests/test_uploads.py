import io
import re
from pathlib import Path

import pytest

from marche.extensions import db
from marche.models import Product
from marche.uploads import delete_image, detect_media_type, save_image

# 画像のライブラリーを足さずに済むよう、先頭のバイト（マジックナンバー）だけを本物に合わせる
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8
MB = 1024 * 1024


def upload_folder(app) -> Path:
    return Path(app.config["UPLOAD_FOLDER"])


def saved_files(app) -> list[Path]:
    folder = upload_folder(app)
    return list(folder.iterdir()) if folder.exists() else []


def product_data(image=None, filename="photo.png", **fields):
    data = {"name": "ほうれん草 1束", "price": "200", "stock": "10"}
    data.update(fields)
    if image is not None:
        data["image"] = (io.BytesIO(image), filename)
    return data


# 中身の判定


@pytest.mark.parametrize(
    ("data", "expected"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")],
)
def test_画像の種類を中身で判定できる(data, expected):
    assert detect_media_type(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"GIF89a" + b"\x00" * 16,  # GIF は受け付けない
        b"<html><script>alert(1)</script></html>",
        b"",
        b"RIFF"
        + b"\x00\x00\x00\x00"
        + b"WAVE"
        + b"\x00" * 8,  # RIFF でも WebP ではない
    ],
)
def test_画像でなければNoneになる(data):
    assert detect_media_type(data) is None


# 保存と削除


def test_UUIDのファイル名で保存される(app):
    filename = save_image(PNG, "image/png")
    assert re.fullmatch(r"[0-9a-f]{32}\.png", filename)
    assert (upload_folder(app) / filename).read_bytes() == PNG


def test_無いファイルを消してもエラーにならない(app):
    delete_image("nothing.png")
    delete_image(None)


def test_アップロード先の外のファイルは消せない(app, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("消えてはいけない")
    upload_folder(app).mkdir(parents=True, exist_ok=True)
    delete_image("../outside.txt")
    assert outside.exists()


# 正常系


def test_写真を付けて登録できる(app, client, producer, login):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(PNG))
    assert response.status_code == 302
    product = db.session.scalar(db.select(Product))
    assert re.fullmatch(r"[0-9a-f]{32}\.png", product.image_filename)
    assert (upload_folder(app) / product.image_filename).read_bytes() == PNG


def test_拡張子は元のファイル名ではなく中身で決まる(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(JPEG, "photo.png"))
    assert db.session.scalar(db.select(Product)).image_filename.endswith(".jpg")


def test_写真を差し替えると古い写真が消える(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(PNG))
    product = db.session.scalar(db.select(Product))
    old_filename = product.image_filename
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(WEBP, "new.webp"),
    )
    assert response.status_code == 302
    db.session.refresh(product)
    assert product.image_filename != old_filename
    assert product.image_filename.endswith(".webp")
    assert [p.name for p in saved_files(app)] == [product.image_filename]


def test_写真を選ばずに編集すると写真が残る(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(PNG))
    product = db.session.scalar(db.select(Product))
    filename = product.image_filename
    client.post(
        f"/producer/products/{product.id}/edit", data=product_data(name="小松菜")
    )
    db.session.refresh(product)
    assert product.name == "小松菜"
    assert product.image_filename == filename
    assert (upload_folder(app) / filename).exists()


def test_商品を削除すると写真も消える(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(PNG))
    product = db.session.scalar(db.select(Product))
    client.post(f"/producer/products/{product.id}/delete")
    assert saved_files(app) == []


def test_編集画面に今の写真が出る(client, producer, login, make_product):
    product = make_product(producer, image_filename="abc.png")
    login(producer)
    response = client.get(f"/producer/products/{product.id}/edit")
    assert "/static/uploads/abc.png" in response.text
    assert 'enctype="multipart/form-data"' in response.text


# 不正な入力


@pytest.mark.parametrize(
    ("image", "filename", "message"),
    [
        (
            b"GIF89a" + b"\x00" * 16,
            "photo.gif",
            "JPEG・PNG・WebP の画像を選んでください",
        ),
        (b"not an image", "photo.png", "JPEG・PNG・WebP の画像を選んでください"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * MB), "big.png", "写真は 5MB までに"),
    ],
)
def test_不正な写真では登録できない(
    app, client, producer, login, image, filename, message
):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(image, filename))
    assert response.status_code == 422
    assert message in response.text
    assert db.session.scalar(db.select(Product)) is None
    assert saved_files(app) == []


def test_不正な写真では編集で差し替えられない(app, client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(PNG))
    product = db.session.scalar(db.select(Product))
    filename = product.image_filename
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(b"not an image", "photo.png"),
    )
    assert response.status_code == 422
    db.session.refresh(product)
    assert product.image_filename == filename
    assert [p.name for p in saved_files(app)] == [filename]


def test_6MBを超えると413(app, client, producer, login):
    login(producer)
    response = client.post(
        "/producer/products/new", data=product_data(PNG + b"\x00" * (6 * MB))
    )
    assert response.status_code == 413
    assert "ファイルが大きすぎます" in response.text
    assert saved_files(app) == []


# 権限なし


def test_他の生産者の商品には写真を送れない(
    app, client, producer, other_producer, login, make_product
):
    product = make_product(other_producer)
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit", data=product_data(PNG)
    )
    assert response.status_code == 403
    db.session.refresh(product)
    assert product.image_filename is None
    assert saved_files(app) == []
