from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from marche.extensions import db
from marche.models import Order
from marche.services.inventory import OrderError, create_order
from marche.views.cart import clear_cart, get_cart

bp = Blueprint("orders", __name__, url_prefix="/orders")


def get_own_order_or_403(order_id: int) -> Order:
    """注文を取り出す。無ければ 404、注文した本人と管理者以外は 403"""
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return order


@bp.route("/", methods=["POST"])
@login_required
def create():
    # 金額と在庫は create_order が DB から計算して確かめる。フォームからは何も受け取らない
    try:
        order = create_order(current_user, get_cart())
    except OrderError as error:
        flash(str(error))
        return redirect(url_for("cart.show"))
    clear_cart()
    # 支払い（Stripe）へ進む処理は決済の章で足す。いまは注文詳細を出す
    flash("注文を受け付けました（支払い待ち）")
    return redirect(url_for("orders.show", order_id=order.id))


@bp.route("/<int:order_id>")
@login_required
def show(order_id: int):
    order = get_own_order_or_403(order_id)
    return render_template("orders/show.html", order=order)
