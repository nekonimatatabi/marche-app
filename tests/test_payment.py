"""services/payment.py のテスト。_client を差し替えて、Stripe に渡す値を確かめる（本物の Stripe は呼ばない）"""

import time
from types import SimpleNamespace

import pytest
import stripe

from marche import create_app
from marche.services import payment
from marche.services.inventory import create_order
from marche.services.payment import PaymentError


class FakeSessions:
    """client.v1.checkout.sessions の代わり。渡された値を記録する"""

    def __init__(self):
        self.created = []
        self.retrieved = []
        self.expired = []
        self.error = None

    def _raise_error(self):
        if self.error is not None:
            raise self.error

    def create(self, params):
        self._raise_error()
        self.created.append(params)
        return SimpleNamespace(
            id="cs_test_123", url="https://checkout.stripe.com/c/pay/cs_test_123"
        )

    def retrieve(self, session_id):
        self._raise_error()
        self.retrieved.append(session_id)
        return SimpleNamespace(id=session_id, payment_status="paid")

    def expire(self, session_id):
        self._raise_error()
        self.expired.append(session_id)


@pytest.fixture
def sessions(app, monkeypatch):
    fake = FakeSessions()
    client = SimpleNamespace(
        v1=SimpleNamespace(checkout=SimpleNamespace(sessions=fake))
    )
    monkeypatch.setattr(payment, "_client", lambda: client)
    # url_for(..., _external=True) を使うので、リクエストの中として動かす
    with app.test_request_context():
        yield fake


@pytest.fixture
def order(customer, producer, make_product):
    spinach = make_product(
        producer, name="ほうれん草 1束", price=180, is_published=True
    )
    miso = make_product(
        producer, name="手づくり味噌 1kg", price=1300, is_published=True
    )
    return create_order(customer, {spinach.id: 3, miso.id: 2})


# create_checkout_session


def test_session_idと決済ページのURLを返す(sessions, order):
    assert payment.create_checkout_session(order) == (
        "cs_test_123",
        "https://checkout.stripe.com/c/pay/cs_test_123",
    )


def test_明細ごとにjpyと単価と数量と商品名を渡す(sessions, order):
    payment.create_checkout_session(order)
    [params] = sessions.created
    assert params["mode"] == "payment"
    # JPY は小数の無い通貨なので、unit_amount は円のまま（100倍しない）
    assert params["line_items"] == [
        {
            "quantity": 3,
            "price_data": {
                "currency": "jpy",
                "unit_amount": 180,
                "product_data": {"name": "ほうれん草 1束"},
            },
        },
        {
            "quantity": 2,
            "price_data": {
                "currency": "jpy",
                "unit_amount": 1300,
                "product_data": {"name": "手づくり味噌 1kg"},
            },
        },
    ]


def test_金額は商品ではなく注文明細から取る(sessions, order):
    # 注文のあとで商品の価格が変わっても、注文した時点の単価で支払う
    for item in order.items:
        item.product.price = 99999
    payment.create_checkout_session(order)
    [params] = sessions.created
    amounts = [line["price_data"]["unit_amount"] for line in params["line_items"]]
    assert amounts == [180, 1300]


def test_metadataに注文IDとcustomer_emailを入れる(sessions, order, customer):
    payment.create_checkout_session(order)
    [params] = sessions.created
    assert params["metadata"] == {"order_id": str(order.id)}
    assert params["customer_email"] == customer.email


def test_戻り先のURLは完了ページと中断ページ(sessions, order):
    payment.create_checkout_session(order)
    [params] = sessions.created
    # {CHECKOUT_SESSION_ID} は Stripe が置き換えるので、%7B のように変わっていないこと
    assert params["success_url"] == (
        f"http://localhost/orders/{order.id}/complete"
        "?session_id={CHECKOUT_SESSION_ID}"
    )
    assert params["cancel_url"] == f"http://localhost/orders/{order.id}/cancel"


def test_有効期限は31分後(sessions, order):
    payment.create_checkout_session(order)
    [params] = sessions.created
    seconds = params["expires_at"] - time.time()
    assert 31 * 60 - 10 < seconds <= 31 * 60


# retrieve_session と expire_session


def test_retrieve_sessionはSessionを返す(sessions):
    session = payment.retrieve_session("cs_test_123")
    assert session.payment_status == "paid"
    assert sessions.retrieved == ["cs_test_123"]


def test_expire_sessionにsession_idを渡す(sessions):
    payment.expire_session("cs_test_123")
    assert sessions.expired == ["cs_test_123"]


# 失敗


@pytest.mark.parametrize(
    "call",
    [
        lambda order: payment.create_checkout_session(order),
        lambda order: payment.retrieve_session("cs_test_123"),
        lambda order: payment.expire_session("cs_test_123"),
    ],
    ids=["create", "retrieve", "expire"],
)
def test_StripeErrorはPaymentErrorにして投げ直す(sessions, order, call):
    sessions.error = stripe.APIConnectionError("Stripe につながりません")
    with pytest.raises(PaymentError) as error:
        call(order)
    assert isinstance(error.value.__cause__, stripe.StripeError)


def test_キーが無ければStripeを呼ばずにPaymentError(app, order):
    # conftest で STRIPE_SECRET_KEY は None にしてある。_client は差し替えない
    with app.test_request_context(), pytest.raises(PaymentError):
        payment.create_checkout_session(order)


# create_app での差し替え


def test_設定が無ければ本物のpaymentモジュールを使う():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    assert app.extensions["payment"] is payment


def test_設定でフェイクに差し替えられる(app, fake_payment):
    assert app.extensions["payment"] is fake_payment
