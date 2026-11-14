"""services/mail.py のテスト。SMTP は偽物に差し替え、本物のメールは送らない"""

import logging
import smtplib

import pytest

from marche.services import mail
from marche.services.inventory import create_order
from marche.services.mail import SentMail


class SentList(list):
    """属性を足せるリスト（smtp_sent.fake_class で偽物のクラスに触れるようにする）"""


@pytest.fixture
def smtp_sent(app, monkeypatch):
    """MAIL_BACKEND=smtp にして、smtplib.SMTP を偽物に差し替える。作られた偽物のリストを返す"""
    app.config.update(
        TESTING=False,  # テスト中は outbox に溜めるだけなので、SMTP の経路を通すために切る
        MAIL_BACKEND="smtp",
        MAIL_SERVER="smtp.example.com",
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME="resend",
        MAIL_PASSWORD="secret-password",
        MAIL_FROM="はちまん産直マルシェ <no-reply@example.com>",
    )
    created = SentList()

    class FakeSMTP:
        error = None  # send_message で投げる例外

        def __init__(self, host, port, timeout):
            self.host, self.port, self.timeout = host, port, timeout
            self.calls = []
            self.message = None
            created.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            self.calls.append("starttls")

        def login(self, username, password):
            self.calls.append(("login", username, password))

        def send_message(self, message):
            if FakeSMTP.error is not None:
                raise FakeSMTP.error
            self.calls.append("send_message")
            self.message = message

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    created.fake_class = FakeSMTP
    return created


# 送り方


def test_テスト中はmail_outboxに溜める(app):
    mail.send_mail("a@example.com", "件名", "本文")
    assert app.extensions["mail_outbox"] == [SentMail("a@example.com", "件名", "本文")]


def test_logのときはログに出すだけ(app, caplog):
    app.config.update(TESTING=False, MAIL_BACKEND="log")
    with caplog.at_level(logging.INFO, logger="marche.services.mail"):
        mail.send_mail("a@example.com", "件名のテスト", "本文のテスト")
    assert "件名のテスト" in caplog.text
    assert "本文のテスト" in caplog.text
    assert app.extensions["mail_outbox"] == []


def test_smtpのときはSTARTTLSとログインをして送る(smtp_sent):
    mail.send_mail("a@example.com", "件名", "本文")
    [smtp] = smtp_sent
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.example.com", 587, 10)
    assert smtp.calls == [
        "starttls",
        ("login", "resend", "secret-password"),
        "send_message",
    ]
    assert smtp.message["To"] == "a@example.com"
    assert smtp.message["Subject"] == "件名"
    assert smtp.message["From"] == "はちまん産直マルシェ <no-reply@example.com>"
    assert smtp.message.get_content().strip() == "本文"


def test_送れなくても例外を出さずパスワードはログに出さない(smtp_sent, caplog):
    smtp_sent.fake_class.error = smtplib.SMTPAuthenticationError(535, b"rejected")
    with caplog.at_level(logging.INFO, logger="marche.services.mail"):
        mail.send_mail("a@example.com", "件名", "本文")  # 例外が出ない
    assert "メールを送れませんでした" in caplog.text
    assert "secret-password" not in caplog.text


def test_つながらなくても例外を出さない(app, smtp_sent, monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionRefusedError("つながりません")

    monkeypatch.setattr(mail.smtplib, "SMTP", refuse)
    mail.send_mail("a@example.com", "件名", "本文")  # 例外が出ない


def test_SMTPの設定が無ければ送らない(app, smtp_sent, caplog):
    app.config["MAIL_SERVER"] = None
    with caplog.at_level(logging.INFO, logger="marche.services.mail"):
        mail.send_mail("a@example.com", "件名", "本文")
    assert smtp_sent == []
    assert "MAIL_SERVER と MAIL_FROM を設定してください" in caplog.text


# 注文確認のメール


def test_注文確認のメールの宛先と件名と本文(app, customer, producer, make_product):
    spinach = make_product(
        producer, name="ほうれん草 1束", price=180, is_published=True
    )
    miso = make_product(
        producer, name="手づくり味噌 1kg", price=1300, is_published=True
    )
    order = create_order(customer, {spinach.id: 3, miso.id: 2})
    with app.test_request_context():
        mail.send_order_paid(order)
    [sent] = app.extensions["mail_outbox"]
    assert sent.to == customer.email
    assert sent.subject == (
        f"【はちまん産直マルシェ】ご注文ありがとうございます（注文番号 {order.id}）"
    )
    assert "購入者 様" in sent.body
    assert "・ほうれん草 1束\n  180円 × 3 = 540円" in sent.body
    assert "・手づくり味噌 1kg\n  1,300円 × 2 = 2,600円" in sent.body
    assert "合計: 3,140円" in sent.body
    assert f"http://localhost/orders/{order.id}" in sent.body
