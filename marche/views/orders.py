from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from marche.extensions import db
from marche.models import Order, OrderStatus
from marche.services.inventory import OrderError, create_order, mark_as_paid
from marche.services.inventory import cancel as cancel_order
from marche.services.payment import PaymentError
from marche.views.cart import clear_cart, get_cart

bp = Blueprint("orders", __name__, url_prefix="/orders")


def get_own_order_or_403(order_id: int) -> Order:
    """注文を取り出す。無ければ 404、注文した本人と管理者以外は 403"""
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return order


@bp.route("/", methods=["GET"])
@login_required
def index():
    """注文履歴。自分の注文だけを新しい順に（管理者でも自分の注文だけ）"""
    orders = db.session.scalars(
        db.select(Order)
        .filter_by(user_id=current_user.id)
        .order_by(Order.created_at.desc(), Order.id.desc())
    ).all()
    return render_template("orders/index.html", orders=orders)


@bp.route("/", methods=["POST"])
@login_required
def create():
    # 金額と在庫は create_order が DB から計算して確かめる。フォームからは何も受け取らない
    try:
        order = create_order(current_user, get_cart())
    except OrderError as error:
        flash(str(error))
        return redirect(url_for("cart.show"))

    try:
        session_id, checkout_url = current_app.extensions[
            "payment"
        ].create_checkout_session(order)
    except PaymentError as error:
        # 決済ページを作れなかった注文は、在庫を押さえたままにしないよう取り消す。カートは残す
        cancel_order(order)
        flash(str(error))
        return redirect(url_for("cart.show"))

    order.stripe_checkout_session_id = session_id
    db.session.commit()
    clear_cart()
    # 303: POST のあとに、決済ページ（Stripe のサイト）を GET で開かせる
    return redirect(checkout_url, code=303)


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template(
        "orders/show.html",
        order=order,
        can_cancel=order.status == OrderStatus.PENDING,
    )


@bp.route("/<int:order_id>/complete")
@login_required
def complete(order_id: int):
    """Stripe で支払ったあとに戻る画面（?session_id=...）。
    Webhook より先に来ることがあるので、まだ支払い待ちなら Stripe に問い合わせて確定する"""
    order = get_own_order_or_403(order_id)
    session_id = request.args.get("session_id")
    if (
        order.status == OrderStatus.PENDING
        and session_id
        and session_id == order.stripe_checkout_session_id
    ):
        try:
            session = current_app.extensions["payment"].retrieve_session(session_id)
        except PaymentError as error:
            flash(str(error))
        else:
            if session.payment_status == "paid":
                # Webhook と同じ関数。どちらが先でも、確定は1回だけ
                mark_as_paid(order, session.payment_intent)
    return render_template(
        "orders/complete.html",
        order=order,
        is_paid=order.status == OrderStatus.PAID,
        is_pending=order.status == OrderStatus.PENDING,
    )


@bp.route("/<int:order_id>/cancel", methods=["GET", "POST"])
@login_required
def cancel(order_id: int):
    """Stripe の決済ページで「戻る」を押したときの画面。POST で注文を取り消す"""
    order = get_own_order_or_403(order_id)
    can_cancel = order.status == OrderStatus.PENDING
    if request.method == "GET":
        return render_template("orders/cancel.html", order=order, can_cancel=can_cancel)

    detail = redirect(url_for("orders.show", order_id=order.id))
    if not can_cancel:
        flash("この注文は取り消せません")
        return detail
    if order.stripe_checkout_session_id:
        # 先に決済ページを期限切れにする。開いたままだと、取り消したあとに支払われてしまう
        try:
            current_app.extensions["payment"].expire_session(
                order.stripe_checkout_session_id
            )
        except PaymentError as error:
            # 支払いが済んで Session が完了していることもあるので、取り消さない
            flash(str(error))
            return detail
    if cancel_order(order):
        flash("注文を取り消しました")
    else:
        # 確かめたあとに、Webhook で支払い済みになった場合
        flash("この注文は取り消せません")
    return detail
