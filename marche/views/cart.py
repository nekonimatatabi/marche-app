from typing import NamedTuple

from flask import Blueprint, abort, flash, redirect, render_template, session, url_for

from marche.extensions import db
from marche.forms import CartQuantityForm
from marche.models import Product
from marche.services.inventory import MAX_QUANTITY, available_stock

bp = Blueprint("cart", __name__, url_prefix="/cart")


class CartRow(NamedTuple):
    """カートの画面の1行。金額は DB の価格から計算する"""

    product: Product
    quantity: int
    subtotal: int
    limit: int  # 数量の上限（MAX_QUANTITY と、いま買える数の小さいほう）


def get_cart() -> dict[int, int]:
    """セッションのカートを {商品ID: 数量} の整数で返す。
    セッションは JSON で保存するので、商品IDは文字列（"3"）で入っている。
    整数にできない項目と、数量が 0 以下の項目は捨てる"""
    raw = session.get("cart")
    if not isinstance(raw, dict):
        return {}
    cart = {}
    for product_id, quantity in raw.items():
        try:
            product_id, quantity = int(product_id), int(quantity)
        except TypeError, ValueError:
            continue
        if quantity > 0:
            cart[product_id] = quantity
    return cart


def save_cart(cart: dict[int, int]) -> None:
    # 入れ子の辞書を書き換えるだけではセッションが保存されないので、毎回入れ直す
    session["cart"] = {
        str(product_id): quantity for product_id, quantity in cart.items()
    }


def clear_cart() -> None:
    session.pop("cart", None)


def _get_published_or_404(product_id: int) -> Product:
    product = db.get_or_404(Product, product_id)
    # 非公開の商品は、商品詳細と同じく 404 にする（「ある」ことも教えない）
    if not product.is_published:
        abort(404)
    return product


def _limit(product: Product) -> int:
    return min(MAX_QUANTITY, available_stock(product))


def _quantity_error(product: Product, quantity: int) -> str | None:
    """カートの数量が 1〜上限 に入っていなければ、画面に出すメッセージを返す"""
    limit = _limit(product)
    if limit < 1:
        return f"「{product.name}」は売り切れです"
    if not 1 <= quantity <= limit:
        return f"「{product.name}」の数量は1〜{limit}にしてください"
    return None


@bp.route("/")
def show():
    cart = get_cart()
    products = {}
    if cart:
        products = {
            product.id: product
            for product in db.session.scalars(
                db.select(Product)
                .where(Product.id.in_(list(cart)))
                .filter_by(is_published=True)
            )
        }
    if len(products) < len(cart):
        # 削除された・非公開になった商品は表示しない。
        # カートに残すと、見えない商品のせいで注文が失敗するので、カートからも外す
        cart = {pid: qty for pid, qty in cart.items() if pid in products}
        save_cart(cart)
        flash("販売を終えた商品をカートから外しました")

    rows = [
        CartRow(
            product=products[product_id],
            quantity=quantity,
            subtotal=products[product_id].price * quantity,
            limit=_limit(products[product_id]),
        )
        for product_id, quantity in cart.items()
    ]
    total = sum(row.subtotal for row in rows)
    return render_template("cart/show.html", rows=rows, total=total)


@bp.route("/add/<int:product_id>", methods=["POST"])
def add(product_id: int):
    product = _get_published_or_404(product_id)
    back = redirect(url_for("shop.show", product_id=product.id))
    form = CartQuantityForm()
    if not form.validate_on_submit():
        flash("数量を選んでください")
        return back

    cart = get_cart()
    added = form.quantity.data
    in_cart = cart.get(product.id, 0)
    if added < 1:
        error = "数量は1以上を選んでください"
    else:
        # すでにカートに入っている分と合わせて、上限を超えないようにする
        error = _quantity_error(product, in_cart + added)
        if error and in_cart:
            error += f"（カートに{in_cart}個入っています）"
    if error:
        flash(error)
        return back

    cart[product.id] = in_cart + added
    save_cart(cart)
    flash(f"「{product.name}」をカートに入れました")
    return redirect(url_for("cart.show"))


@bp.route("/update/<int:product_id>", methods=["POST"])
def update(product_id: int):
    back = redirect(url_for("cart.show"))
    form = CartQuantityForm()
    if not form.validate_on_submit():
        flash("数量は整数で入力してください")
        return back

    cart = get_cart()
    if product_id not in cart:
        flash("カートに入っていない商品です")
        return back
    quantity = form.quantity.data
    if quantity <= 0:
        del cart[product_id]
        save_cart(cart)
        flash("カートから外しました")
        return back

    product = _get_published_or_404(product_id)
    error = _quantity_error(product, quantity)
    if error:
        flash(error)
        return back
    cart[product.id] = quantity
    save_cart(cart)
    flash("数量を変えました")
    return back
