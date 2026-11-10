"""在庫の計算と注文の作成

products.stock は「いまある数」で、支払いが確定したときに減らす。
注文を作るときと、一覧・詳細の表示では、stock から支払い待ちの数を引いた「いま買える数」を使う。
"""

import logging
from datetime import timedelta

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, now_jst
from marche.services.mail import send_order_paid

logger = logging.getLogger(__name__)

# 支払い待ちの注文が在庫を押さえる時間（分）。
# Stripe の Checkout Session の有効期限（最短30分）より少し長くする。
# これを過ぎた支払い待ちは、もう支払えないので数えない
PENDING_MINUTES = 35

# 1商品あたり1回に買える数
MAX_QUANTITY = 10


class OrderError(Exception):
    """注文を作れないとき。メッセージはそのまま画面に出せる日本語にする"""


def reserved_quantity(product: Product) -> int:
    """支払い待ちで、作成から PENDING_MINUTES 分以内の注文が押さえている、その商品の数量の合計"""
    since = now_jst() - timedelta(minutes=PENDING_MINUTES)
    statement = (
        db.select(db.func.coalesce(db.func.sum(OrderItem.quantity), 0))
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            OrderItem.product_id == product.id,
            Order.status == OrderStatus.PENDING,
            Order.created_at >= since,
        )
    )
    return db.session.scalar(statement)


def available_stock(product: Product) -> int:
    """いま買える数。在庫から支払い待ちの分を引く。0 未満にはしない"""
    return max(product.stock - reserved_quantity(product), 0)


def _parse_cart(cart: dict) -> dict[int, int]:
    """{商品ID: 数量} の商品IDと数量を整数にする。
    セッションは JSON で保存するので、商品IDは文字列（"3"）で来る"""
    try:
        return {int(product_id): int(quantity) for product_id, quantity in cart.items()}
    except TypeError, ValueError:
        raise OrderError("カートの中身が正しくありません") from None


def create_order(user, cart: dict) -> Order:
    """カート（{商品ID: 数量}）から支払い待ちの注文を作る。

    先に全ての商品を確かめ、1つでも駄目なら OrderError を投げる（DB には何も追加しない）。
    金額は引数で受け取らず、DB の価格から計算する。
    """
    if not cart:
        raise OrderError("カートが空です")
    quantities = _parse_cart(cart)

    products = {
        product.id: product
        for product in db.session.scalars(
            db.select(Product).where(Product.id.in_(list(quantities)))
        )
    }
    for product_id, quantity in quantities.items():
        product = products.get(product_id)
        # 非公開の商品は、名前も出さない（詳細の画面で 404 にするのと同じ）
        if product is None or not product.is_published:
            raise OrderError(
                "販売を終えた商品がカートに入っています。カートを確かめてください"
            )
        if not 1 <= quantity <= MAX_QUANTITY:
            raise OrderError(
                f"「{product.name}」の数量は1〜{MAX_QUANTITY}にしてください"
            )
        stock = available_stock(product)
        if quantity > stock:
            raise OrderError(
                f"「{product.name}」の在庫が足りません（いま買える数: {stock}）"
            )

    # ここまで来たら全部の商品を買える。明細には注文時点の商品名と単価（DB の価格）を残す
    order = Order(user_id=user.id, status=OrderStatus.PENDING)
    for product_id, quantity in quantities.items():
        product = products[product_id]
        order.items.append(
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                unit_price=product.price,
                quantity=quantity,
                subtotal=product.price * quantity,
            )
        )
    order.total_amount = sum(item.subtotal for item in order.items)
    db.session.add(order)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return order


def cancel(order: Order) -> bool:
    """支払い待ちの注文を取り消す。取り消せたら True、支払い済み・取り消し済みなら False。

    「読んで、pending なら書き換える」と2段階にすると、その間に Webhook で支払い済みになった注文まで
    取り消してしまう。読む・確かめる・書くを1つの UPDATE 文（WHERE status = 'pending'）にする。
    在庫の押さえ（reserved_quantity）は支払い待ちだけを数えるので、取り消せば自動で戻る
    """
    result = db.session.execute(
        db.update(Order)
        .where(Order.id == order.id, Order.status == OrderStatus.PENDING)
        .values(status=OrderStatus.CANCELLED)
    )
    db.session.commit()
    db.session.refresh(order)
    return result.rowcount == 1


def mark_as_paid(order: Order, payment_intent_id: str | None) -> bool:
    """支払い待ちの注文を支払い済みにして、在庫を減らす。
    自分が確定させたら True、既に確定済み・取り消し済みなら False（何もしない）。

    Webhook と完了ページの両方から呼ぶ。同時に来ても、2回来ても、確定と在庫の減算は1回だけにする。
    そのため cancel と同じく、条件付きの UPDATE 1文（WHERE status = 'pending'）の行数で判断する
    """
    try:
        result = db.session.execute(
            db.update(Order)
            .where(Order.id == order.id, Order.status == OrderStatus.PENDING)
            .values(
                status=OrderStatus.PAID,
                paid_at=now_jst(),
                stripe_payment_intent_id=payment_intent_id,
            )
        )
        if result.rowcount != 1:
            db.session.rollback()
            return False

        for item in order.items:
            if item.product.stock < item.quantity:
                # 売れたのに在庫がない（支払い待ちの押さえの時間を過ぎてから支払われた、など）。
                # コアではログに残すだけで、対策は拡張 E1
                logger.warning(
                    "注文 %s: 商品 %s の在庫が足りないため 0 にしました（在庫 %s、数量 %s）",
                    order.id,
                    item.product_id,
                    item.product.stock,
                    item.quantity,
                )
            # 在庫は DB の値から減らす（読んだ値から計算して書くと、同時の更新で数がずれる）。
            # 足りなければ 0 で止める。CASE は SQLite でも MySQL でも動く
            db.session.execute(
                db.update(Product)
                .where(Product.id == item.product_id)
                .values(
                    stock=db.case(
                        (Product.stock >= item.quantity, Product.stock - item.quantity),
                        else_=0,
                    )
                )
            )
        # 注文の確定と在庫の減算は、1回の commit にまとめる。
        # 途中で失敗したら全部戻るので、Stripe が Webhook を再送したときにやり直せる
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    db.session.refresh(order)

    # 確定させたときだけ（1回だけ）注文確認のメールを送る。
    # メールを送れなくても、確定は取り消さない（ログに残す）。Webhook を 500 にもしない
    try:
        send_order_paid(order)
    except Exception:
        logger.exception("注文 %s の確認メールを送れませんでした", order.id)
    return True
