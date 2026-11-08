from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from marche.decorators import role_required
from marche.extensions import db
from marche.forms import ProductForm
from marche.models import Product, Role

bp = Blueprint("producer", __name__, url_prefix="/producer")


def get_own_product_or_403(product_id: int) -> Product:
    """自分の商品を取り出す。無ければ 404、他の生産者の商品なら 403"""
    product = db.get_or_404(Product, product_id)
    if product.producer_id != current_user.id:
        abort(403)
    return product


def _apply_form(product: Product, form: ProductForm) -> None:
    # producer_id はフォームから受け取らない（ビューで current_user.id を入れる）
    product.name = form.name.data.strip()
    product.price = form.price.data
    product.stock = form.stock.data
    product.description = (form.description.data or "").strip()
    product.is_published = form.is_published.data


@bp.route("/products")
@role_required(Role.PRODUCER)
def index():
    products = db.session.scalars(
        db.select(Product)
        .filter_by(producer_id=current_user.id)
        .order_by(Product.id.desc())
    ).all()
    return render_template("producer/index.html", products=products)


@bp.route("/products/new", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def new():
    form = ProductForm()
    if form.validate_on_submit():
        product = Product(producer_id=current_user.id)
        _apply_form(product, form)
        db.session.add(product)
        db.session.commit()
        flash("登録しました")
        return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template("producer/form.html", form=form, title="商品の登録"), status


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def edit(product_id: int):
    product = get_own_product_or_403(product_id)
    form = ProductForm(obj=product)
    if form.validate_on_submit():
        _apply_form(product, form)
        db.session.commit()
        flash("保存しました")
        return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template("producer/form.html", form=form, title="商品の編集"), status


@bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@role_required(Role.PRODUCER)
def toggle(product_id: int):
    product = get_own_product_or_403(product_id)
    product.is_published = not product.is_published
    db.session.commit()
    flash("公開しました" if product.is_published else "非公開にしました")
    return redirect(url_for("producer.index"))


@bp.route("/products/<int:product_id>/delete", methods=["POST"])
@role_required(Role.PRODUCER)
def delete(product_id: int):
    product = get_own_product_or_403(product_id)
    # 注文がある商品は削除できない、の確認は注文の章で足す（order_items ができてから）
    db.session.delete(product)
    db.session.commit()
    flash("削除しました")
    return redirect(url_for("producer.index"))
