import pytest

from marche.extensions import db
from marche.services.inventory import MAX_QUANTITY


def cart_of(client):
    """セッションのカート（{"商品ID": 数量}。JSON なのでキーは文字列）"""
    with client.session_transaction() as session:
        return session.get("cart", {})


def set_cart(client, cart):
    with client.session_transaction() as session:
        session["cart"] = {str(product_id): qty for product_id, qty in cart.items()}


def add(client, product, quantity):
    return client.post(f"/cart/add/{product.id}", data={"quantity": quantity})


def update(client, product, quantity):
    return client.post(f"/cart/update/{product.id}", data={"quantity": quantity})


# カートに入れる（正常系）


def test_カートに入れるとセッションに数量が入る(client, producer, make_product):
    product = make_product(producer, is_published=True)
    response = add(client, product, 3)
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    assert cart_of(client) == {str(product.id): 3}


def test_同じ商品を2回入れると数量を足す(client, producer, make_product):
    product = make_product(producer, is_published=True)
    add(client, product, 2)
    add(client, product, 3)
    assert cart_of(client) == {str(product.id): 5}


def test_ログインしていなくてもカートを見られる(client, producer, make_product):
    product = make_product(producer, is_published=True)
    add(client, product, 1)
    response = client.get("/cart/")
    assert response.status_code == 200
    assert product.name in response.get_data(as_text=True)


# カートに入れる（不正な入力）


@pytest.mark.parametrize("quantity", ["0", "-1", "abc", "", "1.5"])
def test_数量が不正なら追加しない(client, producer, make_product, quantity):
    product = make_product(producer, stock=20, is_published=True)
    response = add(client, product, quantity)
    assert response.status_code == 302
    assert response.headers["Location"] == f"/products/{product.id}"
    assert cart_of(client) == {}


def test_数量がMAX_QUANTITYを超えるなら追加しない(client, producer, make_product):
    product = make_product(producer, stock=50, is_published=True)
    response = add(client, product, MAX_QUANTITY + 1)
    assert cart_of(client) == {}
    text = client.get(response.headers["Location"]).get_data(as_text=True)
    assert f"数量は1〜{MAX_QUANTITY}にしてください" in text


def test_いま買える数を超えるなら追加しない(client, producer, make_product):
    product = make_product(producer, stock=2, is_published=True)
    add(client, product, 3)
    assert cart_of(client) == {}


def test_カートの中と合わせて上限を超えるなら追加しない(client, producer, make_product):
    product = make_product(producer, stock=5, is_published=True)
    add(client, product, 4)
    response = add(client, product, 2)
    assert cart_of(client) == {str(product.id): 4}
    text = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "カートに4個入っています" in text


def test_売り切れの商品は追加しない(client, producer, make_product):
    product = make_product(producer, stock=0, is_published=True)
    response = add(client, product, 1)
    assert cart_of(client) == {}
    text = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "売り切れです" in text


def test_非公開の商品は404(client, producer, make_product):
    product = make_product(producer, is_published=False)
    assert add(client, product, 1).status_code == 404
    assert cart_of(client) == {}


def test_非公開の商品は出品者でもカートに入れられない(
    client, producer, make_product, login
):
    product = make_product(producer, is_published=False)
    login(producer)
    assert add(client, product, 1).status_code == 404


def test_存在しない商品は404(client):
    assert client.post("/cart/add/999", data={"quantity": 1}).status_code == 404


def test_GETではカートに入れられない(client, producer, make_product):
    product = make_product(producer, is_published=True)
    assert client.get(f"/cart/add/{product.id}").status_code == 405
    assert cart_of(client) == {}


# カートの画面


def test_カートに商品名と単価と数量と小計と合計が出る(client, producer, make_product):
    spinach = make_product(
        producer, name="ほうれん草 1束", price=180, is_published=True
    )
    miso = make_product(
        producer, name="手づくり味噌 1kg", price=1300, is_published=True
    )
    add(client, spinach, 3)
    add(client, miso, 2)
    text = client.get("/cart/").get_data(as_text=True)
    assert "ほうれん草 1束" in text
    assert "手づくり味噌 1kg" in text
    assert "180円" in text
    assert "540円" in text  # 180 × 3
    assert "1,300円" in text
    assert "2,600円" in text  # 1,300 × 2
    assert "3,140円" in text  # 合計
    assert 'value="3"' in text


def test_空のカート(client):
    text = client.get("/cart/").get_data(as_text=True)
    assert "カートは空です" in text


def test_非公開になった商品は表示せずカートから外す(client, producer, make_product):
    product = make_product(producer, name="非公開になるなす", is_published=True)
    # add() を使うと「〜をカートに入れました」のメッセージに商品名が出るので、セッションに直接入れる
    set_cart(client, {product.id: 1})
    product.is_published = False
    db.session.commit()
    text = client.get("/cart/").get_data(as_text=True)
    assert "非公開になるなす" not in text
    assert "販売を終えた商品をカートから外しました" in text
    assert cart_of(client) == {}


def test_削除された商品はカートから外す(client):
    set_cart(client, {999: 1})
    text = client.get("/cart/").get_data(as_text=True)
    assert "販売を終えた商品をカートから外しました" in text
    assert cart_of(client) == {}


def test_セッションの壊れた項目は無視する(client, producer, make_product):
    product = make_product(producer, is_published=True)
    with client.session_transaction() as session:
        session["cart"] = {"abc": 1, str(product.id): "x", "5": -1}
    text = client.get("/cart/").get_data(as_text=True)
    assert "カートは空です" in text


def test_ヘッダーにカートのリンクが出る(client, customer, login):
    assert 'href="/cart/"' in client.get("/").get_data(as_text=True)
    login(customer)
    assert 'href="/cart/"' in client.get("/").get_data(as_text=True)


# 数量を変える


def test_数量を変える(client, producer, make_product):
    product = make_product(producer, is_published=True)
    set_cart(client, {product.id: 2})
    response = update(client, product, 5)
    assert response.headers["Location"] == "/cart/"
    assert cart_of(client) == {str(product.id): 5}


@pytest.mark.parametrize("quantity", ["0", "-1"])
def test_数量を0以下にするとカートから外す(client, producer, make_product, quantity):
    product = make_product(producer, is_published=True)
    other = make_product(producer, name="残る商品", is_published=True)
    set_cart(client, {product.id: 2, other.id: 1})
    update(client, product, quantity)
    assert cart_of(client) == {str(other.id): 1}


def test_数量が上限を超えるなら変えない(client, producer, make_product):
    product = make_product(producer, stock=3, is_published=True)
    set_cart(client, {product.id: 2})
    response = update(client, product, 4)
    assert cart_of(client) == {str(product.id): 2}
    text = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "数量は1〜3にしてください" in text


@pytest.mark.parametrize("quantity", ["abc", ""])
def test_数量が整数でなければ変えない(client, producer, make_product, quantity):
    product = make_product(producer, is_published=True)
    set_cart(client, {product.id: 2})
    update(client, product, quantity)
    assert cart_of(client) == {str(product.id): 2}


def test_カートにない商品の数量は変えない(client, producer, make_product):
    product = make_product(producer, is_published=True)
    update(client, product, 3)
    assert cart_of(client) == {}


def test_GETでは数量を変えられない(client, producer, make_product):
    product = make_product(producer, is_published=True)
    set_cart(client, {product.id: 2})
    assert client.get(f"/cart/update/{product.id}").status_code == 405
    assert cart_of(client) == {str(product.id): 2}


# 商品詳細のカートに入れるフォーム


def test_商品詳細に数量の選択とカートに入れるボタンが出る(
    client, producer, make_product
):
    product = make_product(producer, stock=3, is_published=True)
    text = client.get(f"/products/{product.id}").get_data(as_text=True)
    assert "カートに入れる" in text
    assert f'action="/cart/add/{product.id}"' in text
    assert '<option value="3">' in text
    assert '<option value="4">' not in text


def test_数量の選択肢はMAX_QUANTITYまで(client, producer, make_product):
    product = make_product(producer, stock=50, is_published=True)
    text = client.get(f"/products/{product.id}").get_data(as_text=True)
    assert f'<option value="{MAX_QUANTITY}">' in text
    assert f'<option value="{MAX_QUANTITY + 1}">' not in text


def test_売り切れならカートに入れるボタンが出ない(client, producer, make_product):
    product = make_product(producer, stock=0, is_published=True)
    text = client.get(f"/products/{product.id}").get_data(as_text=True)
    assert "カートに入れる" not in text


def test_非公開の商品の詳細にはカートに入れるボタンが出ない(
    client, producer, make_product, login
):
    product = make_product(producer, is_published=False)
    login(producer)
    text = client.get(f"/products/{product.id}").get_data(as_text=True)
    assert "非公開の商品です" in text
    assert "カートに入れる" not in text
