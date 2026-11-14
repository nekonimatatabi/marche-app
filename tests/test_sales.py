from marche.extensions import db
from marche.models import OrderStatus
from marche.services.inventory import create_order


def make_order(customer, cart, status=OrderStatus.PAID):
    """注文を作り、状態を変える（既定は支払い済み）"""
    order = create_order(customer, cart)
    order.status = status
    db.session.commit()
    return order


def sales_text(client):
    response = client.get("/producer/sales")
    assert response.status_code == 200
    return response.get_data(as_text=True)


# 正常系


def test_自分の商品の売上だけが出る(
    client, customer, producer, other_producer, make_product, login
):
    mine = make_product(producer, name="ほうれん草 1束", price=180, is_published=True)
    others = make_product(
        other_producer, name="別の農園のトマト", price=300, is_published=True
    )
    make_order(customer, {mine.id: 3, others.id: 2})
    login(producer)
    text = sales_text(client)
    assert "ほうれん草 1束" in text
    assert "540円" in text  # 180 × 3
    assert "別の農園のトマト" not in text
    assert "600円" not in text  # 他の生産者の売上（300 × 2）
    assert "1,140円" not in text  # 他の生産者の分を足した合計


def test_支払い待ちと取り消しの注文は数えない(
    client, customer, producer, make_product, login
):
    product = make_product(producer, price=180, stock=20, is_published=True)
    make_order(customer, {product.id: 2})  # 支払い済み 360円
    make_order(customer, {product.id: 3}, status=OrderStatus.PENDING)  # 540円
    make_order(customer, {product.id: 4}, status=OrderStatus.CANCELLED)  # 720円
    login(producer)
    text = sales_text(client)
    assert "360円" in text
    assert "<td>2</td>" in text  # 販売数
    for amount in ["540円", "720円", "900円", "1,080円", "1,620円"]:
        assert amount not in text


def test_同じ商品の明細は足して1行にする(
    client, customer, producer, make_product, login
):
    product = make_product(
        producer, name="ほうれん草 1束", price=180, is_published=True
    )
    make_order(customer, {product.id: 2})
    make_order(customer, {product.id: 3})
    login(producer)
    text = sales_text(client)
    assert text.count("ほうれん草 1束") == 1
    assert "<td>5</td>" in text  # 販売数 2 + 3
    assert "900円" in text  # 180 × 5


def test_売上の多い順に並び合計が出る(client, customer, producer, make_product, login):
    small = make_product(producer, name="少しの商品", price=100, is_published=True)
    large = make_product(producer, name="多くの商品", price=500, is_published=True)
    middle = make_product(producer, name="中くらいの商品", price=300, is_published=True)
    make_order(customer, {small.id: 1, large.id: 2, middle.id: 1})
    login(producer)
    text = sales_text(client)
    # 1,000円 → 300円 → 100円 の順
    assert (
        text.index("多くの商品")
        < text.index("中くらいの商品")
        < text.index("少しの商品")
    )
    assert "<strong>4</strong>" in text  # 販売数の合計
    assert "1,400円" in text  # 売上の合計


def test_注文のあとで価格を変えても注文した時点の単価で数える(
    client, customer, producer, make_product, login
):
    product = make_product(producer, price=180, is_published=True)
    make_order(customer, {product.id: 2})
    product.price = 999
    db.session.commit()
    login(producer)
    text = sales_text(client)
    assert "360円" in text
    assert "1,998円" not in text


def test_売上がなければメッセージを出す(client, producer, make_product, login):
    make_product(producer, is_published=True)
    login(producer)
    assert "まだ売上はありません" in sales_text(client)


def test_ヘッダーの売上のリンクは生産者にだけ出る(client, customer, producer, login):
    login(producer)
    assert 'href="/producer/sales"' in client.get("/").get_data(as_text=True)
    client.post("/logout")
    login(customer)
    assert 'href="/producer/sales"' not in client.get("/").get_data(as_text=True)


# 権限なし


def test_購入者は売上画面で403(client, customer, login):
    login(customer)
    assert client.get("/producer/sales").status_code == 403


def test_管理者も売上画面は403(client, admin, login):
    login(admin)
    assert client.get("/producer/sales").status_code == 403


def test_未ログインならログイン画面へ(client):
    response = client.get("/producer/sales")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
