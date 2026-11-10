import logging
import os
from datetime import datetime

from flask import Flask, render_template

from marche.extensions import csrf, db, login_manager, migrate


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    # 書き方の違い（1 / true / yes）で STARTTLS が切れて平文で送らないよう、どれも有効とみなす
    mail_use_tls = os.environ.get("MAIL_USE_TLS", "1").lower() in ("1", "true", "yes")
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or "dev",
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", "sqlite:///marche.sqlite"
        ),
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        UPLOAD_FOLDER=os.path.join(os.path.dirname(__file__), "static", "uploads"),
        # 写真は 5MB まで（フォームで確かめる）。それより大きいリクエストは読む前に 413 で断る
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        STRIPE_SECRET_KEY=os.environ.get("STRIPE_SECRET_KEY"),
        STRIPE_WEBHOOK_SECRET=os.environ.get("STRIPE_WEBHOOK_SECRET"),
        # 決済のサービス。None なら本物（services/payment.py）。テストではフェイクを渡す
        PAYMENT_SERVICE=None,
        # メール。"log" はログに出すだけ（開発）、"smtp" は MAIL_SERVER で送る（本番）
        MAIL_BACKEND=os.environ.get("MAIL_BACKEND", "log"),
        MAIL_SERVER=os.environ.get("MAIL_SERVER"),
        MAIL_PORT=int(os.environ.get("MAIL_PORT") or 587),
        MAIL_USE_TLS=mail_use_tls,
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
        MAIL_FROM=os.environ.get("MAIL_FROM"),
    )
    if config is not None:
        app.config.update(config)
    if app.config["SECRET_KEY"] == "dev" and not (app.debug or app.testing):
        # 本番で SECRET_KEY を設定し忘れると、セッションを偽造される。起動を止めて気づかせる
        raise RuntimeError(
            "SECRET_KEY を .env（本番は EnvironmentFile）に設定してください"
        )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "ログインしてください"

    from marche.services import payment

    # ビューは current_app.extensions["payment"] 経由で呼ぶ（テストではフェイクに差し替わる）
    app.extensions["payment"] = app.config["PAYMENT_SERVICE"] or payment
    if app.testing:
        # テスト中のメールは送らずにここへ溜める（services/mail.py）。テストで件数を確かめる
        app.extensions["mail_outbox"] = []

    from marche.views import auth, cart, orders, producer, shop, webhooks

    for module in (auth, shop, cart, orders, producer, webhooks):
        app.register_blueprint(module.bp)

    from marche import commands

    commands.init_app(app)

    @app.template_filter("yen")
    def yen(value: int) -> str:
        return f"{value:,}円"

    @app.template_filter("datetime_ja")
    def datetime_ja(value: datetime) -> str:
        # 「11月14日(土) 10:00」の形。日時は日本時間で保存しているので、形を変えるだけ
        weekday = "月火水木金土日"[value.weekday()]
        return f"{value.month}月{value.day}日({weekday}) {value:%H:%M}"

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def too_large(error):
        return render_template("errors/413.html"), 413

    logging.basicConfig(level=logging.INFO)
    return app
