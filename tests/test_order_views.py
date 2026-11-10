from types import SimpleNamespace

from marche.extensions import db
from marche.models import Order, OrderStatus, Role, User
from marche.services.inventory import available_stock, create_order
from marche.services.payment import PaymentError


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


def make_order_with_session(user, product):
    """決済ページ（Checkout Session）まで作った、支払い待ちの注文"""
    order = create_order(user, {product.id: 1})
    order.stripe_checkout_session_id = f"cs_test_fake_{order.id}"
    db.session.commit()
    return order


# 注文を作る（正常系）


def test_注文するとStripeの決済ページへ303で移る(
    client, customer, producer, make_product, login, fake_payment
):
    product = make_product(producer, price=180, is_published=True)
    login(customer)
    set_cart(client, {product.id: 3})
    response = client.post("/orders/")
    order = db.session.scalar(db.select(Order))
    assert response.status_code == 303
    assert (
        response.headers["Location"]
        == f"https://checkout.stripe.test/c/pay/cs_test_fake_{order.id}"
    )
    assert fake_payment.created == [order.id]
    assert order.stripe_checkout_session_id == f"cs_test_fake_{order.id}"
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
    client.post("/orders/")  # 決済ページ（外のサイト）へ移るので、詳細は別に開く
    order = db.session.scalar(db.select(Order))
    text = client.get(f"/orders/{order.id}").get_data(as_text=True)
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


def test_決済ページを作れなければ注文を取り消してカートに戻る(
    client, customer, producer, make_product, login, fake_payment
):
    product = make_product(producer, stock=5, is_published=True)
    fake_payment.error = PaymentError("お支払いの準備に失敗しました")
    login(customer)
    set_cart(client, {product.id: 3})
    response = client.post("/orders/")
    order = db.session.scalar(db.select(Order))
    assert response.status_code == 302
    assert response.headers["Location"] == "/cart/"
    assert order.status == OrderStatus.CANCELLED
    assert order.stripe_checkout_session_id is None
    assert available_stock(product) == 5  # 押さえた在庫が戻る
    assert cart_of(client) == {str(product.id): 3}  # カートは残す
    text = client.get("/cart/").get_data(as_text=True)
    assert "お支払いの準備に失敗しました" in text


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


def test_支払い待ちなら注文詳細に取り消しのリンクが出る(
    client, customer, producer, make_product, login
):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    login(customer)
    text = client.get(f"/orders/{order.id}").get_data(as_text=True)
    assert f'href="/orders/{order.id}/cancel"' in text


def test_支払い済みなら注文詳細に取り消しのリンクが出ない(
    client, customer, producer, make_product, login
):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    order.status = OrderStatus.PAID
    db.session.commit()
    login(customer)
    text = client.get(f"/orders/{order.id}").get_data(as_text=True)
    assert f'href="/orders/{order.id}/cancel"' not in text


# 支払いの中断（/orders/<id>/cancel）


def test_中断ページに取り消しのボタンが出る(
    client, customer, producer, make_product, login
):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    login(customer)
    response = client.get(f"/orders/{order.id}/cancel")
    assert response.status_code == 200
    assert "この注文を取り消す" in response.get_data(as_text=True)


def test_中断ページを開くだけでは取り消さない(
    client, customer, producer, make_product, login, fake_payment
):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    login(customer)
    client.get(f"/orders/{order.id}/cancel")
    assert order.status == OrderStatus.PENDING
    assert fake_payment.expired == []


def test_中断ページで取り消すとSessionを期限切れにして取り消す(
    client, customer, producer, make_product, login, fake_payment
):
    product = make_product(producer, stock=5, is_published=True)
    order = make_order_with_session(customer, product)
    login(customer)
    response = client.post(f"/orders/{order.id}/cancel")
    assert response.headers["Location"] == f"/orders/{order.id}"
    assert fake_payment.expired == [f"cs_test_fake_{order.id}"]
    db.session.refresh(order)
    assert order.status == OrderStatus.CANCELLED
    assert available_stock(product) == 5  # 押さえていた在庫が戻る
    text = client.get(f"/orders/{order.id}").get_data(as_text=True)
    assert "注文を取り消しました" in text
    assert "キャンセル" in text


def test_Sessionを期限切れにできなければ取り消さない(
    client, customer, producer, make_product, login, fake_payment
):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    fake_payment.error = PaymentError("決済ページを閉じられなかったため")
    login(customer)
    response = client.post(f"/orders/{order.id}/cancel")
    assert response.headers["Location"] == f"/orders/{order.id}"
    db.session.refresh(order)
    assert order.status == OrderStatus.PENDING
    text = client.get(f"/orders/{order.id}").get_data(as_text=True)
    assert "決済ページを閉じられなかったため" in text


def test_支払い済みの注文は取り消せない(
    client, customer, producer, make_product, login, fake_payment
):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    order.status = OrderStatus.PAID
    db.session.commit()
    login(customer)
    assert "取り消せません" in client.get(f"/orders/{order.id}/cancel").get_data(
        as_text=True
    )
    client.post(f"/orders/{order.id}/cancel")
    db.session.refresh(order)
    assert order.status == OrderStatus.PAID
    assert fake_payment.expired == []  # 支払い済みの Session には触らない


def test_他人の注文の中断ページは403(
    client, customer, producer, make_product, login, fake_payment
):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    login(make_other_customer())
    assert client.get(f"/orders/{order.id}/cancel").status_code == 403
    assert client.post(f"/orders/{order.id}/cancel").status_code == 403
    db.session.refresh(order)
    assert order.status == OrderStatus.PENDING
    assert fake_payment.expired == []


def test_管理者は他人の注文を取り消せる(
    client, customer, admin, producer, make_product, login
):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    login(admin)
    client.post(f"/orders/{order.id}/cancel")
    db.session.refresh(order)
    assert order.status == OrderStatus.CANCELLED


def test_未ログインでは中断ページを見られない(client, customer, producer, make_product):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    response = client.post(f"/orders/{order.id}/cancel")
    assert response.headers["Location"].startswith("/login")
    db.session.refresh(order)
    assert order.status == OrderStatus.PENDING


# 支払いの完了（/orders/<id>/complete。確定の処理のテストは test_webhook.py）


def test_完了ページにお支払いを確認していますと出る(
    client, customer, producer, make_product, login, fake_payment
):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    session_id = f"cs_test_fake_{order.id}"
    fake_payment.sessions[session_id] = SimpleNamespace(
        id=session_id, payment_status="unpaid", payment_intent=None
    )
    login(customer)
    response = client.get(f"/orders/{order.id}/complete?session_id={session_id}")
    assert response.status_code == 200
    assert "お支払いを確認しています" in response.get_data(as_text=True)


def test_他人の注文の完了ページは403(client, customer, producer, make_product, login):
    product = make_product(producer, is_published=True)
    order = make_order_with_session(customer, product)
    login(make_other_customer())
    assert client.get(f"/orders/{order.id}/complete").status_code == 403
