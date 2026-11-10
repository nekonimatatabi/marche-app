from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from marche.extensions import db

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    """日本時間の現在時刻。SQLite はタイムゾーンを保存しないので tzinfo を外して使う"""
    return datetime.now(JST).replace(tzinfo=None)


class Role(StrEnum):
    CUSTOMER = "customer"
    PRODUCER = "producer"
    ADMIN = "admin"


ROLE_LABELS = {Role.CUSTOMER: "購入者", Role.PRODUCER: "生産者", Role.ADMIN: "管理者"}


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"


ORDER_STATUS_LABELS = {
    OrderStatus.PENDING: "支払い待ち",
    OrderStatus.PAID: "支払い済み",
    OrderStatus.CANCELLED: "キャンセル",
}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.CUSTOMER)
    created_at: Mapped[datetime] = mapped_column(default=now_jst)

    products: Mapped[list[Product]] = relationship(back_populates="producer")
    orders: Mapped[list[Order]] = relationship(back_populates="user")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_producer(self) -> bool:
        return self.role == Role.PRODUCER

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def role_label(self) -> str:
        return ROLE_LABELS[Role(self.role)]


class Product(db.Model):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 出品した生産者。注文した人（orders.user_id）と区別するため producer_id と呼ぶ
    producer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[int]  # 円、税込み
    stock: Mapped[int] = mapped_column(default=0)  # いまある数
    is_published: Mapped[bool] = mapped_column(default=False)
    image_filename: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=now_jst)
    updated_at: Mapped[datetime] = mapped_column(default=now_jst, onupdate=now_jst)

    producer: Mapped[User] = relationship(back_populates="products")
    order_items: Mapped[list[OrderItem]] = relationship(back_populates="product")


class Order(db.Model):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 注文した人。出品した生産者（products.producer_id）と区別するため user_id と呼ぶ
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.PENDING)
    total_amount: Mapped[int]  # 円。サーバーが明細の小計を足して計算する
    # 注文を作った時点ではまだないので NULL を許す。NULL は unique でも複数入れられる
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(
        String(255), unique=True
    )
    # 支払いが確定したときに入れる（返金の拡張 E2 で使う）
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    paid_at: Mapped[datetime | None]
    # 支払い待ちの注文が在庫を押さえる時間（services/inventory.py）を数える起点
    created_at: Mapped[datetime] = mapped_column(default=now_jst)

    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    @property
    def status_label(self) -> str:
        return ORDER_STATUS_LABELS[OrderStatus(self.status)]


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    # 注文時点の商品名と単価。あとで商品が変わっても注文は変わらない
    product_name: Mapped[str] = mapped_column(String(50))
    unit_price: Mapped[int]  # 円
    quantity: Mapped[int]
    subtotal: Mapped[int]  # unit_price × quantity（円）

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(back_populates="order_items")
