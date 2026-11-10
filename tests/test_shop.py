from marche.extensions import db
from marche.models import Order, OrderItem, Product, User


def make_pending_order(user, product, quantity):
    """支払い待ちの注文を作る（在庫を押さえる）"""
    order = Order(user_id=user.id, total_amount=product.price * quantity)
    order.items.append(
        OrderItem(
            product_id=product.id,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
            subtotal=product.price * quantity,
        )
    )
    db.session.add(order)
    db.session.commit()
    return order


def count(model):
    return db.session.scalar(db.select(db.func.count()).select_from(model))


# 一覧


def test_一覧には公開中の商品だけが出る(client, producer, make_product):
    make_product(producer, name="公開中のトマト", is_published=True)
    make_product(producer, name="非公開のなす", is_published=False)
    text = client.get("/").get_data(as_text=True)
    assert "公開中のトマト" in text
    assert "非公開のなす" not in text


def test_一覧に価格と生産者名と在庫が出る(client, producer, make_product):
    make_product(producer, price=1200, stock=8, is_published=True)
    text = client.get("/").get_data(as_text=True)
    assert "1,200円" in text
    assert "高原ファーム" in text
    assert "在庫 8" in text


def test_一覧で在庫0は売り切れと出る(client, producer, make_product):
    make_product(producer, stock=0, is_published=True)
    text = client.get("/").get_data(as_text=True)
    assert "売り切れ" in text
    assert "在庫 0" not in text


def test_一覧の在庫は支払い待ちの分を引いた数(client, customer, producer, make_product):
    product = make_product(producer, stock=10, is_published=True)
    make_pending_order(customer, product, 3)
    text = client.get("/").get_data(as_text=True)
    assert "在庫 7" in text


def test_公開中の商品がなければそう出る(client):
    text = client.get("/").get_data(as_text=True)
    assert "公開中の商品はまだありません" in text


# 詳細: 正常系


def test_公開中の商品の詳細は誰でも見られる(client, producer, make_product):
    product = make_product(
        producer,
        price=1200,
        stock=5,
        is_published=True,
        description="1行目\n2行目",
    )
    response = client.get(f"/products/{product.id}")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "ほうれん草 1束" in text
    assert "1,200円" in text
    assert "高原ファーム" in text
    assert "在庫 5" in text
    assert "1行目\n2行目" in text  # 改行はそのまま出し、CSS で保つ


def test_詳細で在庫0は売り切れと出る(client, producer, make_product):
    product = make_product(producer, stock=0, is_published=True)
    text = client.get(f"/products/{product.id}").get_data(as_text=True)
    assert "売り切れ" in text


def test_非公開の商品は出品した生産者なら見られる(
    client, producer, make_product, login
):
    product = make_product(producer, is_published=False)
    login(producer)
    response = client.get(f"/products/{product.id}")
    assert response.status_code == 200
    assert "非公開の商品です" in response.get_data(as_text=True)


def test_非公開の商品は管理者なら見られる(client, admin, producer, make_product, login):
    product = make_product(producer, is_published=False)
    login(admin)
    assert client.get(f"/products/{product.id}").status_code == 200


# 詳細: 権限なし（403 ではなく 404）


def test_非公開の商品は未ログインだと404(client, producer, make_product):
    product = make_product(producer, is_published=False)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の商品は購入者だと404(client, customer, producer, make_product, login):
    product = make_product(producer, is_published=False)
    login(customer)
    assert client.get(f"/products/{product.id}").status_code == 404


def test_非公開の商品は別の生産者だと404(
    client, producer, other_producer, make_product, login
):
    product = make_product(producer, is_published=False)
    login(other_producer)
    assert client.get(f"/products/{product.id}").status_code == 404


# 詳細: 不正な入力


def test_存在しない商品は404(client):
    assert client.get("/products/9999").status_code == 404


def test_紹介文のタグはエスケープして出す(client, producer, make_product):
    product = make_product(
        producer, is_published=True, description="<script>alert(1)</script>"
    )
    text = client.get(f"/products/{product.id}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


# flask seed


def test_seedで5人と8商品が入る(app):
    result = app.test_cli_runner().invoke(args=["seed"])
    assert result.exit_code == 0, result.output
    assert count(User) == 5
    assert count(Product) == 8
    published = db.session.scalar(
        db.select(db.func.count()).select_from(Product).filter_by(is_published=True)
    )
    assert published == 7
    admin = db.session.scalar(db.select(User).filter_by(email="admin@example.com"))
    assert admin.is_admin
    assert admin.check_password("password")


def test_seedを2回実行しても件数は同じ(app):
    runner = app.test_cli_runner()
    runner.invoke(args=["seed"])
    result = runner.invoke(args=["seed"])
    assert result.exit_code == 0, result.output
    assert count(User) == 5
    assert count(Product) == 8


def test_seedは前からある注文とユーザーを消す(app, customer, producer, make_product):
    product = make_product(producer)
    make_pending_order(customer, product, 1)
    result = app.test_cli_runner().invoke(args=["seed"])
    assert result.exit_code == 0, result.output
    assert count(Order) == 0
    assert count(OrderItem) == 0
    emails = db.session.scalars(db.select(User.email)).all()
    assert "customer@example.com" not in emails
