from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, now_jst
from marche.services.inventory import (
    PENDING_MINUTES,
    available_stock,
    reserved_quantity,
)


def make_order(user, lines, status=OrderStatus.PENDING, minutes_ago=0, **fields):
    """lines は [(商品, 数量), ...]。minutes_ago 分前に作った注文にする"""
    order = Order(
        user_id=user.id,
        status=status,
        total_amount=sum(product.price * quantity for product, quantity in lines),
        created_at=now_jst() - timedelta(minutes=minutes_ago),
        **fields,
    )
    for product, quantity in lines:
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


# 在庫の計算: 正常系


def test_支払い待ちの数量だけいま買える数が減る(customer, producer, make_product):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 3)])
    assert reserved_quantity(product) == 3
    assert available_stock(product) == 7


def test_複数の注文の数量を合計する(customer, producer, make_product):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 2)])
    make_order(customer, [(product, 4)])
    assert reserved_quantity(product) == 6
    assert available_stock(product) == 4


def test_注文がなければ押さえている数は0(producer, make_product):
    product = make_product(producer, stock=10)
    assert reserved_quantity(product) == 0
    assert available_stock(product) == 10


# 在庫の計算: 数えない注文


@pytest.mark.parametrize("status", [OrderStatus.PAID, OrderStatus.CANCELLED])
def test_支払い済みとキャンセルの注文は数えない(
    customer, producer, make_product, status
):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 3)], status=status)
    assert reserved_quantity(product) == 0
    assert available_stock(product) == 10


def test_押さえる時間を過ぎた支払い待ちは数えない(customer, producer, make_product):
    product = make_product(producer, stock=10)
    make_order(customer, [(product, 2)], minutes_ago=PENDING_MINUTES - 1)  # 数える
    make_order(customer, [(product, 5)], minutes_ago=PENDING_MINUTES + 1)  # 数えない
    assert reserved_quantity(product) == 2
    assert available_stock(product) == 8


def test_別の商品の明細は数えない(customer, producer, make_product):
    spinach = make_product(producer, stock=10)
    tomato = make_product(producer, name="トマト 3個", stock=10)
    make_order(customer, [(spinach, 1), (tomato, 4)])
    assert reserved_quantity(spinach) == 1
    assert reserved_quantity(tomato) == 4


# 在庫の計算: 不正な状態


def test_押さえている数が在庫を超えてもいま買える数は0(
    customer, producer, make_product
):
    # 支払い待ちのあいだに、生産者が在庫を減らした場合など
    product = make_product(producer, stock=2)
    make_order(customer, [(product, 5)])
    assert reserved_quantity(product) == 5
    assert available_stock(product) == 0


# 注文のモデル


def test_注文を削除すると明細も消える(customer, producer, make_product):
    product = make_product(producer)
    order = make_order(customer, [(product, 1)])
    db.session.delete(order)
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count()).select_from(OrderItem)) == 0


def test_状態のラベルは日本語(customer, producer, make_product):
    product = make_product(producer)
    order = make_order(customer, [(product, 1)])
    assert order.status == OrderStatus.PENDING  # 既定は支払い待ち
    assert order.status_label == "支払い待ち"
    order.status = OrderStatus.PAID
    assert order.status_label == "支払い済み"
    order.status = OrderStatus.CANCELLED
    assert order.status_label == "キャンセル"


def test_注文時点の商品名と単価を明細に残す(customer, producer, make_product):
    product = make_product(producer, name="ほうれん草 1束", price=200)
    order = make_order(customer, [(product, 2)])
    product.name = "ほうれん草 2束"
    product.price = 350
    db.session.commit()
    item = order.items[0]
    assert item.product_name == "ほうれん草 1束"
    assert item.unit_price == 200
    assert item.subtotal == 400


def test_session_idは重複できない(customer, producer, make_product):
    product = make_product(producer)
    make_order(customer, [(product, 1)], stripe_checkout_session_id="cs_test_1")
    with pytest.raises(IntegrityError):
        make_order(customer, [(product, 1)], stripe_checkout_session_id="cs_test_1")
    db.session.rollback()


def test_session_idがNULLの注文は複数作れる(customer, producer, make_product):
    product = make_product(producer)
    make_order(customer, [(product, 1)])
    make_order(customer, [(product, 1)])
    assert db.session.scalar(db.select(db.func.count()).select_from(Order)) == 2
