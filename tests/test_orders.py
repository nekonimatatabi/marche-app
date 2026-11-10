import pytest

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus
from marche.services.inventory import (
    MAX_QUANTITY,
    OrderError,
    available_stock,
    cancel,
    create_order,
)


def count(model):
    return db.session.scalar(db.select(db.func.count()).select_from(model))


def assert_no_order():
    """注文も明細も作られていない"""
    assert count(Order) == 0
    assert count(OrderItem) == 0


# 正常系


def test_合計はDBの価格と数量から計算する(customer, producer, make_product):
    spinach = make_product(
        producer, name="ほうれん草 1束", price=180, is_published=True
    )
    miso = make_product(
        producer, name="手づくり味噌 1kg", price=1300, is_published=True
    )
    order = create_order(customer, {spinach.id: 3, miso.id: 2})
    assert order.total_amount == 180 * 3 + 1300 * 2
    assert order.status == OrderStatus.PENDING
    assert order.user_id == customer.id
    assert count(Order) == 1


def test_明細に注文時点の商品名と単価と小計を残す(customer, producer, make_product):
    product = make_product(
        producer, name="ほうれん草 1束", price=180, is_published=True
    )
    order = create_order(customer, {product.id: 3})
    [item] = order.items
    assert item.product_id == product.id
    assert item.product_name == "ほうれん草 1束"
    assert item.unit_price == 180
    assert item.quantity == 3
    assert item.subtotal == 540


def test_商品IDが文字列のカートでも作れる(customer, producer, make_product):
    # セッションに入れたカートは JSON を通るので、商品IDは文字列になる
    product = make_product(producer, price=200, is_published=True)
    order = create_order(customer, {str(product.id): 2})
    assert order.total_amount == 400


def test_在庫ちょうどの数なら作れる(customer, producer, make_product):
    product = make_product(producer, stock=3, is_published=True)
    order = create_order(customer, {product.id: 3})
    assert order.items[0].quantity == 3


def test_数量はMAX_QUANTITYまで買える(customer, producer, make_product):
    product = make_product(producer, stock=20, is_published=True)
    order = create_order(customer, {product.id: MAX_QUANTITY})
    assert order.items[0].quantity == MAX_QUANTITY


# 在庫


def test_在庫を超えるとOrderErrorで注文は作られない(customer, producer, make_product):
    product = make_product(producer, name="ほうれん草 1束", stock=2, is_published=True)
    with pytest.raises(OrderError, match="「ほうれん草 1束」の在庫が足りません"):
        create_order(customer, {product.id: 3})
    assert_no_order()


def test_支払い待ちで押さえられた分も在庫から引く(customer, producer, make_product):
    product = make_product(producer, stock=5, is_published=True)
    create_order(customer, {product.id: 3})  # 支払い待ちで3個押さえる
    with pytest.raises(OrderError, match="いま買える数: 2"):
        create_order(customer, {product.id: 3})
    assert count(Order) == 1  # 1回目の注文だけ


# 不正な入力


def test_非公開の商品はOrderErrorで注文は作られない(customer, producer, make_product):
    product = make_product(producer, name="試作中の干し野菜", is_published=False)
    with pytest.raises(OrderError) as error:
        create_order(customer, {product.id: 1})
    assert "販売を終えた商品" in str(error.value)
    assert "試作中の干し野菜" not in str(error.value)  # 非公開の商品の名前は出さない
    assert_no_order()


def test_存在しない商品はOrderError(customer):
    with pytest.raises(OrderError, match="販売を終えた商品"):
        create_order(customer, {9999: 1})
    assert_no_order()


def test_空のカートはOrderError(customer):
    with pytest.raises(OrderError, match="カートが空です"):
        create_order(customer, {})
    assert_no_order()


@pytest.mark.parametrize("quantity", [0, -1, MAX_QUANTITY + 1])
def test_数量が範囲の外ならOrderError(customer, producer, make_product, quantity):
    product = make_product(producer, stock=20, is_published=True)
    with pytest.raises(OrderError, match="数量は1〜10にしてください"):
        create_order(customer, {product.id: quantity})
    assert_no_order()


@pytest.mark.parametrize("cart", [{"abc": 1}, {"1": "たくさん"}, {"1": None}])
def test_カートの中身が数字でなければOrderError(customer, producer, make_product, cart):
    make_product(producer, is_published=True)
    with pytest.raises(OrderError, match="カートの中身が正しくありません"):
        create_order(customer, cart)
    assert_no_order()


def test_1つでも駄目な商品があれば他の商品の注文も作られない(
    customer, producer, make_product
):
    ok = make_product(producer, name="ほうれん草 1束", stock=10, is_published=True)
    short = make_product(producer, name="りんごジュース 1L", stock=1, is_published=True)
    with pytest.raises(OrderError, match="りんごジュース 1L"):
        create_order(customer, {ok.id: 2, short.id: 5})
    assert_no_order()


# 注文の取り消し（cancel）


def test_支払い待ちの注文を取り消せる(customer, producer, make_product):
    product = make_product(producer, is_published=True)
    order = create_order(customer, {product.id: 1})
    assert cancel(order) is True
    assert order.status == OrderStatus.CANCELLED


def test_取り消すと在庫の押さえが戻る(customer, producer, make_product):
    product = make_product(producer, stock=5, is_published=True)
    order = create_order(customer, {product.id: 3})
    assert available_stock(product) == 2
    cancel(order)
    assert available_stock(product) == 5


@pytest.mark.parametrize("status", [OrderStatus.PAID, OrderStatus.CANCELLED])
def test_支払い待ちでなければ取り消さない(customer, producer, make_product, status):
    product = make_product(producer, is_published=True)
    order = create_order(customer, {product.id: 1})
    order.status = status
    db.session.commit()
    assert cancel(order) is False
    assert order.status == status
