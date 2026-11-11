import pytest

from marche import create_app
from marche.extensions import db
from marche.models import Product, Role, User

# 本物の外部サービス（Stripe、拡張で足す Claude API）は、テストでは呼ばない。
# フェイク（偽物）のクラスを作り、create_app() の設定で差し替える。


class FakePayment:
    """services/payment.py の代わり。本物と同じ名前の関数を持ち、呼ばれた引数を記録する"""

    def __init__(self):
        self.created = []  # create_checkout_session に渡された注文の ID
        self.sessions = {}  # retrieve_session が返す {session_id: Session の代わり}
        self.expired = []  # expire_session に渡された session_id
        # 例外を入れておくと、どの関数もそれを投げる（失敗の場面を試す）
        self.error = None

    def _raise_error(self):
        if self.error is not None:
            raise self.error

    def create_checkout_session(self, order):
        self._raise_error()
        self.created.append(order.id)
        session_id = f"cs_test_fake_{order.id}"
        return session_id, f"https://checkout.stripe.test/c/pay/{session_id}"

    def retrieve_session(self, session_id):
        self._raise_error()
        return self.sessions[session_id]

    def expire_session(self, session_id):
        self._raise_error()
        self.expired.append(session_id)


@pytest.fixture
def fake_payment():
    return FakePayment()


@pytest.fixture
def app(tmp_path, fake_payment):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            # テストの写真が marche/static/uploads に溜まらないように、テストごとの一時フォルダーに保存する
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            # 環境変数に本物のキーがあっても使わない（間違って本物を呼んでも、通信せずに失敗する）
            "STRIPE_SECRET_KEY": None,
            "STRIPE_WEBHOOK_SECRET": "whsec_test",
            "PAYMENT_SERVICE": fake_payment,
        }
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(role, email, name):
    user = User(name=name, email=email, role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def customer(app):
    return _make_user(Role.CUSTOMER, "customer@example.com", "購入者")


@pytest.fixture
def producer(app):
    return _make_user(Role.PRODUCER, "producer@example.com", "高原ファーム")


@pytest.fixture
def other_producer(app):
    return _make_user(Role.PRODUCER, "other@example.com", "別の農園")


@pytest.fixture
def admin(app):
    return _make_user(Role.ADMIN, "admin@example.com", "管理者")


@pytest.fixture
def make_product(app):
    def do_make(producer, **fields):
        values = {"name": "ほうれん草 1束", "price": 200, "stock": 10}
        values.update(fields)
        product = Product(producer_id=producer.id, **values)
        db.session.add(product)
        db.session.commit()
        return product

    return do_make


@pytest.fixture
def login(client):
    def do_login(user):
        return client.post("/login", data={"email": user.email, "password": "password"})

    return do_login
