"""在庫の計算と注文の作成

products.stock は「いまある数」で、支払いが確定したときに減らす。
注文を作るときと、一覧・詳細の表示では、stock から支払い待ちの数を引いた「いま買える数」を使う。
"""

from datetime import timedelta

from marche.extensions import db
from marche.models import Order, OrderItem, OrderStatus, Product, now_jst

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
