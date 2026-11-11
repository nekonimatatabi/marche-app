from marche.extensions import db
from marche.models import Order, OrderStatus, Role, User
from marche.services.inventory import create_order


def count_orders():
    return db.session.scalar(db.select(db.func.count()).select_from(Order))


def cart_of(client):
    with client.session_transaction() as session:
        return session.get("cart", {})


def set_cart(client, cart):
    with client.session_transaction() as session:
        session["cart"] = {str(product_id): qty for product_id, qty in cart.items()}


def make_other_customer():
    user = User(
        name="別の購入者", email="other-customer@example.com", role=Role.CUSTOMER
    )
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


# 注文を作る（正常系）


def test_カートから注文を作り注文詳細へ移る(
    client, customer, producer, make_product, login
):
    product = make_product(producer, price=180, is_published=True)
    login(customer)
    set_cart(client, {product.id: 3})
    response = client.post("/orders/")
    order = db.session.scalar(db.select(Order))
    assert response.status_code == 302
    assert response.headers["Location"] == f"/orders/{order.id}"
    assert order.user_id == customer.id
    assert order.status == OrderStatus.PENDING
    assert order.total_amount == 540


def test_注文したらカートを空にする(client, customer, producer, make_product, login):
    product = make_product(producer, is_published=True)
    login(customer)
    set_cart(client, {product.id: 1})
    client.post("/orders/")
    assert cart_of(client) == {}


def test_注文詳細に状態と明細と合計が出る(
    client, customer, producer, make_product, login
):
    spinach = make_product(
        producer, name="ほうれん草 1束", price=180, is_published=True
    )
    miso = make_product(
        producer, name="手づくり味噌 1kg", price=1300, is_published=True
    )
    login(customer)
    set_cart(client, {spinach.id: 3, miso.id: 2})
    text = client.post("/orders/", follow_redirects=True).get_data(as_text=True)
    assert "注文を受け付けました" in text
    assert "支払い待ち" in text
    assert "ほうれん草 1束" in text
    assert "手づくり味噌 1kg" in text
    assert "540円" in text
    assert "2,600円" in text
    assert "3,140円" in text


def test_注文詳細は注文時点の商品名と単価を出す(
    client, customer, producer, make_product, login
):
    product = make_product(producer, name="旧い名前", price=180, is_published=True)
    order = create_order(customer, {product.id: 2})
    product.name = "新しい名前"
    product.price = 999
    db.session.commit()
    login(customer)
    text = client.get(f"/orders/{order.id}").get_data(as_text=True)
    assert "旧い名前" in text
    assert "180円" in text
    assert "新しい名前" not in text
    assert "999円" not in text


# 注文を作る（権限なし・不正な入力）


def test_未ログインでは注文できない(client, producer, make_product):
    product = make_product(producer, is_published=True)
    set_cart(client, {product.id: 1})
    response = client.post("/orders/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
    assert count_orders() == 0
    assert cart_of(client) == {str(product.id): 1}


def test_カートが空なら注文せずにカートへ戻る(client, customer, login):
    login(customer)
    response = client.post("/orders/")
    assert response.headers["Location"] == "/cart/"
    assert count_orders() == 0
    text = client.get("/cart/").get_data(as_text=True)
    assert "カートが空です" in text


def test_在庫が足りなければ注文せずカートを残す(
    client, customer, producer, make_product, login
):
    product = make_product(producer, name="りんごジュース", stock=3, is_published=True)
    login(customer)
    set_cart(client, {product.id: 5})  # カートに入れたあとで在庫が減った場合
    response = client.post("/orders/")
    assert response.headers["Location"] == "/cart/"
    assert count_orders() == 0
    assert cart_of(client) == {str(product.id): 5}
    text = client.get("/cart/").get_data(as_text=True)
    assert "「りんごジュース」の在庫が足りません" in text


def test_GETでは注文を作れない(client, customer, producer, make_product, login):
    product = make_product(producer, is_published=True)
    login(customer)
    set_cart(client, {product.id: 1})
    assert client.get("/orders/").status_code == 405
    assert count_orders() == 0


# カートの注文ボタン


def test_未ログインならログインして注文するリンクが出る(client, producer, make_product):
    product = make_product(producer, is_published=True)
    set_cart(client, {product.id: 1})
    text = client.get("/cart/").get_data(as_text=True)
    assert "ログインして注文する" in text
    assert 'href="/login?next=/cart/"' in text
    assert "注文して支払いへ進む" not in text


def test_ログインしていれば注文のボタンが出る(
    client, customer, producer, make_product, login
):
    product = make_product(producer, is_published=True)
    login(customer)
    set_cart(client, {product.id: 1})
    text = client.get("/cart/").get_data(as_text=True)
    assert "注文して支払いへ進む" in text
    assert 'action="/orders/"' in text
    assert "ログインして注文する" not in text


def test_ログインのあとカートに戻る(client, customer, producer, make_product):
    product = make_product(producer, is_published=True)
    set_cart(client, {product.id: 1})
    response = client.post(
        "/login?next=/cart/",
        data={"email": customer.email, "password": "password"},
    )
    assert response.headers["Location"] == "/cart/"
    assert cart_of(client) == {str(product.id): 1}


# 注文詳細の権限


def test_他人の注文は403(client, customer, producer, make_product, login):
    product = make_product(producer, is_published=True)
    order = create_order(customer, {product.id: 1})
    login(make_other_customer())
    assert client.get(f"/orders/{order.id}").status_code == 403


def test_生産者でも自分の注文でなければ403(
    client, customer, producer, make_product, login
):
    product = make_product(producer, is_published=True)
    order = create_order(customer, {product.id: 1})
    login(producer)  # 商品を出品した生産者でも、注文した本人ではない
    assert client.get(f"/orders/{order.id}").status_code == 403


def test_管理者は他人の注文を見られる(
    client, customer, admin, producer, make_product, login
):
    product = make_product(producer, is_published=True)
    order = create_order(customer, {product.id: 1})
    login(admin)
    assert client.get(f"/orders/{order.id}").status_code == 200


def test_存在しない注文は404(client, customer, login):
    login(customer)
    assert client.get("/orders/999").status_code == 404


def test_未ログインでは注文詳細を見られない(client, customer, producer, make_product):
    product = make_product(producer, is_published=True)
    order = create_order(customer, {product.id: 1})
    response = client.get(f"/orders/{order.id}")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
