import logging
import os

from flask import Flask, render_template

from marche.extensions import csrf, db, login_manager, migrate


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
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

    from marche.views import auth, cart, orders, producer, shop, webhooks

    for module in (auth, shop, cart, orders, producer, webhooks):
        app.register_blueprint(module.bp)

    from marche import commands

    commands.init_app(app)

    @app.template_filter("yen")
    def yen(value: int) -> str:
        return f"{value:,}円"

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
