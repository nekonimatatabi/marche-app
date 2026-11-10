"""Stripe Checkout（カード決済）。Stripe を呼ぶのはこのファイルだけ

ビューからは current_app.extensions["payment"] 経由で呼ぶ（テストでは設定でフェイクに差し替える）。
"""

import logging
from datetime import UTC, datetime, timedelta

import stripe
from flask import current_app, url_for

from marche.models import Order

logger = logging.getLogger(__name__)

# Checkout Session の有効期限（分）。Stripe の最短は30分。
# 支払い待ちの注文が在庫を押さえる時間（inventory.PENDING_MINUTES = 35）より短くする
SESSION_EXPIRES_MINUTES = 31


class PaymentError(Exception):
    """決済の処理に失敗したとき。メッセージはそのまま画面に出せる日本語にする"""


def _client() -> stripe.StripeClient:
    """キーを持たせた Stripe のクライアント。テストではこの関数を monkeypatch で差し替える"""
    secret_key = current_app.config.get("STRIPE_SECRET_KEY")
    if not secret_key:
        logger.error("STRIPE_SECRET_KEY が設定されていません")
        raise PaymentError(
            "いまはお支払いの手続きができません。管理者に連絡してください"
        )
    return stripe.StripeClient(secret_key)


def _log(error: stripe.StripeError, action: str) -> None:
    # メッセージにはキーの一部が入ることがあるので、種類とコードと request id だけを残す
    logger.warning(
        "Stripe の%sに失敗しました: %s（status=%s, code=%s, request_id=%s）",
        action,
        type(error).__name__,
        error.http_status,
        error.code,
        error.request_id,
    )


def create_checkout_session(order: Order) -> tuple[str, str]:
    """注文の明細から Checkout Session を作り、(session_id, 決済ページの URL) を返す"""
    # 金額は注文明細（DB）から取る。JPY は小数の無い通貨なので、円のまま渡す（100倍しない）
    line_items = [
        {
            "quantity": item.quantity,
            "price_data": {
                "currency": "jpy",
                "unit_amount": item.unit_price,
                "product_data": {"name": item.product_name},
            },
        }
        for item in order.items
    ]
    # {CHECKOUT_SESSION_ID} は Stripe が本物の ID に置き換える。
    # url_for の引数に入れると { } が %7B %7D に変わって置き換わらないので、文字列でつなげる
    success_url = (
        url_for("orders.complete", order_id=order.id, _external=True)
        + "?session_id={CHECKOUT_SESSION_ID}"
    )
    expires_at = datetime.now(UTC) + timedelta(minutes=SESSION_EXPIRES_MINUTES)
    params = {
        "mode": "payment",
        "line_items": line_items,
        "customer_email": order.user.email,
        # Webhook と完了ページで、どの注文の支払いかを確かめるのに使う（値は文字列）
        "metadata": {"order_id": str(order.id)},
        "success_url": success_url,
        "cancel_url": url_for("orders.cancel", order_id=order.id, _external=True),
        "expires_at": int(expires_at.timestamp()),
    }
    try:
        session = _client().v1.checkout.sessions.create(params)
    except stripe.StripeError as error:
        _log(error, "Checkout Session の作成")
        raise PaymentError(
            "お支払いの準備に失敗しました。時間をおいて、もう一度お試しください"
        ) from error
    return session.id, session.url


def retrieve_session(session_id: str) -> stripe.checkout.Session:
    """Checkout Session を取り出す（完了ページで、支払いが済んだかを確かめる）"""
    try:
        return _client().v1.checkout.sessions.retrieve(session_id)
    except stripe.StripeError as error:
        _log(error, "Checkout Session の取得")
        raise PaymentError(
            "お支払いの状態を確かめられませんでした。しばらくしてから再読み込みしてください"
        ) from error


def expire_session(session_id: str) -> None:
    """Checkout Session を期限切れにする（中断ページ。このあとは決済ページで支払えない）"""
    try:
        _client().v1.checkout.sessions.expire(session_id)
    except stripe.StripeError as error:
        _log(error, "Checkout Session の期限切れ")
        raise PaymentError(
            "決済ページを閉じられなかったため、注文は取り消していません。"
            "お支払いが済んでいないか、注文の状態を確かめてください"
        ) from error
