"""メールの送信。開発ではログに出すだけ、本番は SMTP で送る（設定の MAIL_BACKEND で切り替える）"""

import logging
import smtplib
from email.message import EmailMessage
from typing import NamedTuple

from flask import current_app, render_template, url_for

from marche.models import Order

logger = logging.getLogger(__name__)


class SentMail(NamedTuple):
    """テスト中に app.extensions["mail_outbox"] に溜めるメール"""

    to: str
    subject: str
    body: str


def send_mail(to: str, subject: str, body: str) -> None:
    """メールを1通送る。送れなくても例外を外に出さず、ログに残す（注文の確定などを止めない）"""
    config = current_app.config
    if current_app.testing:
        current_app.extensions["mail_outbox"].append(SentMail(to, subject, body))
        return
    if config["MAIL_BACKEND"] != "smtp":
        logger.info(
            "メール（送信はせずログに出す）\nTo: %s\nSubject: %s\n\n%s",
            to,
            subject,
            body,
        )
        return
    if not config["MAIL_SERVER"] or not config["MAIL_FROM"]:
        logger.error("MAIL_SERVER と MAIL_FROM を設定してください（件名 %s）", subject)
        return

    message = EmailMessage()
    message["From"] = config["MAIL_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(
            config["MAIL_SERVER"], config["MAIL_PORT"], timeout=10
        ) as smtp:
            if config["MAIL_USE_TLS"]:
                smtp.starttls()
            if config["MAIL_USERNAME"]:
                smtp.login(config["MAIL_USERNAME"], config["MAIL_PASSWORD"])
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        # 接続できない・時間切れ・認証の失敗など。パスワードなどの設定値はログに出さない
        logger.error(
            "メールを送れませんでした（宛先 %s、件名 %s）: %r", to, subject, error
        )
        return
    logger.info("メールを送りました（宛先 %s、件名 %s）", to, subject)


def send_order_paid(order: Order) -> None:
    """注文確認のメール。支払いが確定したとき（mark_as_paid が True のとき）に1回だけ送る"""
    subject = (
        f"【はちまん産直マルシェ】ご注文ありがとうございます（注文番号 {order.id}）"
    )
    body = render_template(
        "mail/order_paid.txt",
        order=order,
        # メールから開くので、ドメインまで入った URL にする（Webhook のリクエストの中で作る）
        order_url=url_for("orders.show", order_id=order.id, _external=True),
    )
    send_mail(order.user.email, subject, body)
