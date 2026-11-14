import pytest

from marche.extensions import db
from marche.models import Product
from marche.services.inventory import cancel, create_order


def product_data(**fields):
    data = {
        "name": "ほうれん草 1束",
        "price": "200",
        "stock": "10",
        "description": "朝どれです",
    }
    data.update(fields)
    return data


def count_products():
    return db.session.scalar(db.select(db.func.count()).select_from(Product))


# 正常系


def test_商品を登録できる(client, producer, login):
    login(producer)
    response = client.post("/producer/products/new", data=product_data())
    assert response.status_code == 302
    assert response.headers["Location"] == "/producer/products"
    product = db.session.scalar(db.select(Product))
    assert product.producer_id == producer.id
    assert product.name == "ほうれん草 1束"
    assert product.price == 200
    assert product.stock == 10
    assert product.is_published is False  # 印を付けなければ非公開


def test_公開するに印を付けて登録すると公開中になる(client, producer, login):
    login(producer)
    client.post("/producer/products/new", data=product_data(is_published="y"))
    assert db.session.scalar(db.select(Product)).is_published is True


def test_在庫0で登録できる(client, producer, login):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(stock="0"))
    assert response.status_code == 302
    assert db.session.scalar(db.select(Product)).stock == 0


def test_フォームでproducer_idを送っても無視される(
    client, producer, other_producer, login
):
    login(producer)
    client.post(
        "/producer/products/new", data=product_data(producer_id=other_producer.id)
    )
    assert db.session.scalar(db.select(Product)).producer_id == producer.id


def test_一覧には自分の商品だけが出る(
    client, producer, other_producer, login, make_product
):
    make_product(producer, name="自分のトマト")
    make_product(other_producer, name="よそのナス")
    login(producer)
    response = client.get("/producer/products")
    assert response.status_code == 200
    assert "自分のトマト" in response.text
    assert "よそのナス" not in response.text
    assert "200円" in response.text


def test_商品を編集できる(client, producer, login, make_product):
    product = make_product(producer)
    login(producer)
    assert client.get(f"/producer/products/{product.id}/edit").status_code == 200
    response = client.post(
        f"/producer/products/{product.id}/edit",
        data=product_data(name="小松菜 1束", price="150"),
    )
    assert response.status_code == 302
    db.session.refresh(product)
    assert product.name == "小松菜 1束"
    assert product.price == 150


def test_公開と非公開を切り替えられる(client, producer, login, make_product):
    product = make_product(producer)
    login(producer)
    client.post(f"/producer/products/{product.id}/toggle")
    db.session.refresh(product)
    assert product.is_published is True
    client.post(f"/producer/products/{product.id}/toggle")
    db.session.refresh(product)
    assert product.is_published is False


def test_商品を削除できる(client, producer, login, make_product):
    product = make_product(producer)
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.status_code == 302
    assert count_products() == 0


def test_注文がある商品は削除できない(client, customer, producer, login, make_product):
    product = make_product(producer, is_published=True)
    create_order(customer, {product.id: 1})
    login(producer)
    response = client.post(f"/producer/products/{product.id}/delete")
    assert response.headers["Location"] == "/producer/products"
    assert count_products() == 1
    text = client.get("/producer/products").get_data(as_text=True)
    assert "注文がある商品は削除できません。非公開にしてください" in text


def test_取り消し済みの注文しかない商品も削除できない(
    client, customer, producer, login, make_product
):
    product = make_product(producer, is_published=True)
    cancel(create_order(customer, {product.id: 1}))
    login(producer)
    client.post(f"/producer/products/{product.id}/delete")
    assert count_products() == 1


# 権限なし


@pytest.mark.parametrize(
    ("method", "action"),
    [("get", "edit"), ("post", "edit"), ("post", "toggle"), ("post", "delete")],
)
def test_他の生産者の商品は操作できない(
    client, producer, other_producer, login, make_product, method, action
):
    product = make_product(other_producer, name="よそのナス")
    login(producer)
    url = f"/producer/products/{product.id}/{action}"
    if method == "get":
        response = client.get(url)
    else:
        response = client.post(url, data=product_data(name="書き換え"))
    assert response.status_code == 403
    db.session.refresh(product)
    assert product.name == "よそのナス"
    assert product.is_published is False
    assert count_products() == 1


@pytest.mark.parametrize(
    "url", ["/producer/products", "/producer/products/new", "/producer/products/1/edit"]
)
def test_購入者は商品管理を開けない(client, customer, login, url):
    login(customer)
    assert client.get(url).status_code == 403


def test_購入者は商品を登録できない(client, customer, login):
    login(customer)
    response = client.post("/producer/products/new", data=product_data())
    assert response.status_code == 403
    assert count_products() == 0


def test_未ログインで登録画面を開くとログイン画面へ(client):
    response = client.get("/producer/products/new")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_無い商品は404(client, producer, login):
    login(producer)
    assert client.get("/producer/products/999/edit").status_code == 404
    assert client.post("/producer/products/999/toggle").status_code == 404


@pytest.mark.parametrize("action", ["toggle", "delete"])
def test_切り替えと削除はGETでは使えない(client, producer, login, make_product, action):
    product = make_product(producer)
    login(producer)
    response = client.get(f"/producer/products/{product.id}/{action}")
    assert response.status_code == 405
    assert count_products() == 1


# 不正な入力


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"price": "0"}, "50〜100,000円で入力してください"),
        ({"price": "49"}, "50〜100,000円で入力してください"),
        ({"price": "100001"}, "50〜100,000円で入力してください"),
        ({"price": "abc"}, "整数で入力してください"),
        ({"price": ""}, "価格を入力してください"),
        ({"stock": "-1"}, "0〜999で入力してください"),
        ({"stock": "1000"}, "0〜999で入力してください"),
        ({"stock": ""}, "在庫数を入力してください"),
        ({"name": ""}, "商品名を入力してください"),
        ({"name": "あ" * 51}, "50文字以内で入力してください"),
        ({"description": "あ" * 2001}, "2,000文字以内で入力してください"),
    ],
)
def test_不正な入力では登録できない(client, producer, login, fields, message):
    login(producer)
    response = client.post("/producer/products/new", data=product_data(**fields))
    assert response.status_code == 422
    assert message in response.text
    assert count_products() == 0


def test_境界の値では登録できる(client, producer, login):
    login(producer)
    data = product_data(
        name="あ" * 50, price="100000", stock="999", description="あ" * 2000
    )
    assert client.post("/producer/products/new", data=data).status_code == 302
    data = product_data(price="50")
    assert client.post("/producer/products/new", data=data).status_code == 302
    assert count_products() == 2


def test_不正な入力では編集できない(client, producer, login, make_product):
    product = make_product(producer)
    login(producer)
    response = client.post(
        f"/producer/products/{product.id}/edit", data=product_data(price="49")
    )
    assert response.status_code == 422
    db.session.refresh(product)
    assert product.price == 200
