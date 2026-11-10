"""在庫の計算

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
