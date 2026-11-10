"""Stripe の Webhook の受け口。支払いの確定は Stripe からのこの通知と、完了ページの2か所から来る"""

import logging

import stripe
from flask import Blueprint, current_app, request

from marche.extensions import csrf, db
from marche.models import Order, OrderStatus
from marche.services.inventory import mark_as_paid

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)


@bp.route("/stripe/webhook", methods=["POST"])
@csrf.exempt  # Stripe は CSRF トークンを送れない。代わりに署名で、Stripe から来たことを確かめる
def stripe_webhook():
    secret = current_app.config.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        # 500 にしておくと、設定を直したあとで Stripe の再送で確定できる
        logger.error("STRIPE_WEBHOOK_SECRET が設定されていません")
        return "", 500
    try:
        # 署名は受け取ったままの本文で計算するので、JSON に直す前の bytes を渡す
        event = stripe.Webhook.construct_event(
            request.get_data(), request.headers.get("Stripe-Signature"), secret
        )
    except ValueError, stripe.SignatureVerificationError:
        # 本文が JSON でない・署名が無い・署名が違う。何も変えない
        logger.warning("Webhook の署名を確かめられませんでした")
        return "", 400

    # 扱うのは支払いの完了だけ。他のイベントでエラーを返すと、Stripe が再送し続ける
    if event.type != "checkout.session.completed":
        return "", 200

    # stripe 16 の StripeObject は dict ではない（.get が使えない）ので、辞書にしてから読む
    session = event.data.object.to_dict()
    _confirm(session)
    # 確定しなかった場合も 200。エラーを返しても、同じ内容が再送されるだけで結果は変わらない
    return "", 200


def _confirm(session: dict) -> None:
    session_id = session.get("id")
    if session.get("payment_status") != "paid":
        # カード以外の後払い（銀行振込など）は、あとで別のイベントが来る。コアでは扱わない
        logger.info("Webhook: Session %s はまだ支払われていません", session_id)
        return
    try:
        order_id = int((session.get("metadata") or {}).get("order_id"))
    except TypeError, ValueError:
        logger.warning("Webhook: Session %s に注文 ID がありません", session_id)
        return
    order = db.session.get(Order, order_id)
    # 注文 ID だけでなく、その注文のために作った Session であることも確かめる
    if order is None or order.stripe_checkout_session_id != session_id:
        logger.warning(
            "Webhook: 注文 %s と Session %s が一致しません", order_id, session_id
        )
        return

    if mark_as_paid(order, session.get("payment_intent")):
        logger.info("Webhook: 注文 %s の支払いを確定しました", order.id)
    elif order.status == OrderStatus.CANCELLED:
        # 取り消したあとに支払われた。支払い済みにはしない。返金はダッシュボードで（拡張 E2）
        logger.warning(
            "Webhook: 取り消し済みの注文 %s に支払いがありました。返金してください",
            order.id,
        )
    else:
        logger.info("Webhook: 注文 %s は確定済みです", order.id)
