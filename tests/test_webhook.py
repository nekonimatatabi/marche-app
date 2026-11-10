"""支払いの確定（Webhook と完了ページ）のテスト。
Webhook はフェイクにせず、本物の署名の検証を通す（署名は Stripe と同じ HMAC-SHA256 で作る）"""

import json
from types import SimpleNamespace

import pytest
import stripe

from marche.extensions import db
from marche.models import OrderStatus
from marche.services.inventory import available_stock, cancel, create_order
from marche.services.payment import PaymentError

SECRET = "whsec_test"  # conftest の STRIPE_WEBHOOK_SECRET と同じ


def make_paying_order(customer, cart):
    """決済ページ（Checkout Session）まで進んだ、支払い待ちの注文"""
    order = create_order(customer, cart)
    order.stripe_checkout_session_id = f"cs_test_{order.id}"
    db.session.commit()
    return order


def completed_event(order, payment_status="paid", **session_fields):
    """checkout.session.completed のイベント（Stripe が送ってくる JSON と同じ形）"""
    session = {
        "id": order.stripe_checkout_session_id,
        "object": "checkout.session",
        "payment_status": payment_status,
        "payment_intent": f"pi_test_{order.id}",
        "metadata": {"order_id": str(order.id)},
    }
    session.update(session_fields)
    return {
        "id": "evt_test_1",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {"object": session},
    }


def post_webhook(client, event, secret=SECRET, signature=True):
    payload = event if isinstance(event, str) else json.dumps(event)
    headers = {}
    if signature:
        headers["Stripe-Signature"] = stripe.WebhookSignature.generate_signature_header(
            payload, secret
        )
    return client.post(
        "/stripe/webhook",
        data=payload,
        headers=headers,
        content_type="application/json",
    )


def refreshed(*objects):
    for obj in objects:
        db.session.refresh(obj)


@pytest.fixture
def product(producer, make_product):
    return make_product(producer, stock=10, is_published=True)


@pytest.fixture
def order(customer, product):
    return make_paying_order(customer, {product.id: 3})


# Webhook（正常系）


def test_正しい署名で支払い済みになり在庫が減る(client, order, product):
    response = post_webhook(client, completed_event(order))
    assert response.status_code == 200
    refreshed(order, product)
    assert order.status == OrderStatus.PAID
    assert order.paid_at is not None
    assert order.stripe_payment_intent_id == f"pi_test_{order.id}"
    assert product.stock == 7
    assert available_stock(product) == 7  # 支払い済みは押さえとして数えない


def test_複数の商品の在庫をそれぞれ減らす(client, customer, producer, make_product):
    spinach = make_product(producer, stock=10, is_published=True)
    miso = make_product(producer, stock=5, is_published=True)
    order = make_paying_order(customer, {spinach.id: 2, miso.id: 1})
    post_webhook(client, completed_event(order))
    refreshed(spinach, miso)
    assert spinach.stock == 8
    assert miso.stock == 4


def test_同じWebhookが2回来ても在庫は1回だけ減る(client, order, product):
    event = completed_event(order)
    assert post_webhook(client, event).status_code == 200
    assert post_webhook(client, event).status_code == 200
    refreshed(order, product)
    assert order.status == OrderStatus.PAID
    assert product.stock == 7


def test_在庫が足りなければ0で止めて支払い済みにする(client, order, product):
    product.stock = 2  # 注文のあとで生産者が在庫を減らした
    db.session.commit()
    post_webhook(client, completed_event(order))
    refreshed(order, product)
    assert order.status == OrderStatus.PAID
    assert product.stock == 0


# Webhook（署名）


def test_署名が違うと400で注文は変わらない(client, order, product):
    response = post_webhook(client, completed_event(order), secret="whsec_wrong")
    assert response.status_code == 400
    refreshed(order, product)
    assert order.status == OrderStatus.PENDING
    assert product.stock == 10


def test_署名が無いと400で注文は変わらない(client, order, product):
    response = post_webhook(client, completed_event(order), signature=False)
    assert response.status_code == 400
    refreshed(order, product)
    assert order.status == OrderStatus.PENDING
    assert product.stock == 10


def test_本文がJSONでなければ400(client, order):
    assert post_webhook(client, "not json").status_code == 400
    refreshed(order)
    assert order.status == OrderStatus.PENDING


def test_CSRFを有効にしてもWebhookは署名で確かめる(app, order):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    # 署名が無いと 400 になるが、CSRF ではなく署名の検証で断っている
    response = post_webhook(client, completed_event(order), signature=False)
    assert response.status_code == 400
    assert b"CSRF" not in response.data
    # CSRF トークンが無くても、署名が正しければ受け付ける
    assert post_webhook(client, completed_event(order)).status_code == 200
    refreshed(order)
    assert order.status == OrderStatus.PAID


def test_Webhookの署名シークレットが無ければ500(app, client, order):
    app.config["STRIPE_WEBHOOK_SECRET"] = None
    assert post_webhook(client, completed_event(order)).status_code == 500
    refreshed(order)
    assert order.status == OrderStatus.PENDING


def test_GETでは受け付けない(client):
    assert client.get("/stripe/webhook").status_code == 405


# Webhook（確定しない場合）


def test_未払いなら確定しない(client, order, product):
    response = post_webhook(client, completed_event(order, payment_status="unpaid"))
    assert response.status_code == 200
    refreshed(order, product)
    assert order.status == OrderStatus.PENDING
    assert product.stock == 10


def test_取り消し済みの注文は支払い済みにならない(client, order, product):
    cancel(order)
    response = post_webhook(client, completed_event(order))
    assert response.status_code == 200
    refreshed(order, product)
    assert order.status == OrderStatus.CANCELLED
    assert product.stock == 10


def test_Sessionが注文と一致しなければ確定しない(client, order):
    response = post_webhook(client, completed_event(order, id="cs_test_other"))
    assert response.status_code == 200
    refreshed(order)
    assert order.status == OrderStatus.PENDING


@pytest.mark.parametrize(
    "metadata",
    [{}, None, {"order_id": "abc"}, {"order_id": "999"}],
    ids=["空", "無し", "数字でない", "存在しない注文"],
)
def test_注文IDが正しくなければ何もせず200(client, order, metadata):
    response = post_webhook(client, completed_event(order, metadata=metadata))
    assert response.status_code == 200
    refreshed(order)
    assert order.status == OrderStatus.PENDING


def test_他のイベントは何もせず200(client, order):
    event = completed_event(order)
    event["type"] = "payment_intent.succeeded"
    assert post_webhook(client, event).status_code == 200
    refreshed(order)
    assert order.status == OrderStatus.PENDING


# 完了ページ（/orders/<id>/complete?session_id=...）


def stripe_session(order, payment_status):
    """fake_payment.retrieve_session が返す Session の代わり"""
    return SimpleNamespace(
        id=order.stripe_checkout_session_id,
        payment_status=payment_status,
        payment_intent=f"pi_test_{order.id}",
    )


def open_complete(client, order, session_id=None):
    session_id = session_id or order.stripe_checkout_session_id
    return client.get(f"/orders/{order.id}/complete?session_id={session_id}")


def test_完了ページはStripeが支払い済みを返せば確定する(
    client, customer, login, order, product, fake_payment
):
    fake_payment.sessions[order.stripe_checkout_session_id] = stripe_session(
        order, "paid"
    )
    login(customer)
    text = open_complete(client, order).get_data(as_text=True)
    assert "ご注文ありがとうございます" in text
    assert 'http-equiv="refresh"' not in text
    assert fake_payment.retrieved == [order.stripe_checkout_session_id]
    refreshed(order, product)
    assert order.status == OrderStatus.PAID
    assert order.stripe_payment_intent_id == f"pi_test_{order.id}"
    assert product.stock == 7


def test_完了ページは未払いなら確認中で5秒ごとに読み込み直す(
    client, customer, login, order, fake_payment
):
    fake_payment.sessions[order.stripe_checkout_session_id] = stripe_session(
        order, "unpaid"
    )
    login(customer)
    text = open_complete(client, order).get_data(as_text=True)
    assert "お支払いを確認しています" in text
    assert '<meta http-equiv="refresh" content="5">' in text
    refreshed(order)
    assert order.status == OrderStatus.PENDING


def test_完了ページのあとにWebhookが来ても在庫は1回だけ減る(
    client, customer, login, order, product, fake_payment
):
    fake_payment.sessions[order.stripe_checkout_session_id] = stripe_session(
        order, "paid"
    )
    login(customer)
    open_complete(client, order)
    post_webhook(client, completed_event(order))
    refreshed(product)
    assert product.stock == 7


def test_Webhookで確定したあとの完了ページはStripeに問い合わせない(
    client, customer, login, order, product, fake_payment
):
    post_webhook(client, completed_event(order))
    login(customer)
    text = open_complete(client, order).get_data(as_text=True)
    assert "ご注文ありがとうございます" in text
    assert fake_payment.retrieved == []
    refreshed(product)
    assert product.stock == 7


def test_session_idが注文と一致しなければ問い合わせない(
    client, customer, login, order, fake_payment
):
    login(customer)
    text = open_complete(client, order, session_id="cs_test_other").get_data(
        as_text=True
    )
    assert "お支払いを確認しています" in text
    assert fake_payment.retrieved == []
    refreshed(order)
    assert order.status == OrderStatus.PENDING


def test_session_idが無ければ問い合わせない(
    client, customer, login, order, fake_payment
):
    login(customer)
    response = client.get(f"/orders/{order.id}/complete")
    assert response.status_code == 200
    assert fake_payment.retrieved == []


def test_問い合わせに失敗したら確認中のままメッセージを出す(
    client, customer, login, order, fake_payment
):
    fake_payment.error = PaymentError("お支払いの状態を確かめられませんでした")
    login(customer)
    text = open_complete(client, order).get_data(as_text=True)
    assert "お支払いの状態を確かめられませんでした" in text
    assert "お支払いを確認しています" in text
    refreshed(order)
    assert order.status == OrderStatus.PENDING


def test_取り消し済みの注文の完了ページ(client, customer, login, order, fake_payment):
    cancel(order)
    login(customer)
    text = open_complete(client, order).get_data(as_text=True)
    assert "この注文は取り消されています" in text
    assert fake_payment.retrieved == []
