from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user

from marche.decorators import role_required
from marche.extensions import db
from marche.forms import ProductForm
from marche.models import Product, Role
from marche.uploads import delete_image, detect_media_type, save_image

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


def _read_image(form: ProductForm) -> tuple[bytes, str] | None:
    """選ばれた写真のデータと種類を返す。選ばれていなければ None。
    中身が JPEG / PNG / WebP でなければ、フォームのエラーにして None を返す"""
    if not form.image.data:
        return None
    data = form.image.data.read()
    media_type = detect_media_type(data)
    if media_type is None:
        form.image.errors.append("JPEG・PNG・WebP の画像を選んでください")
        return None
    return data, media_type


def _commit_with_image(new_filename: str | None, old_filename: str | None) -> None:
    """写真を保存したあとにコミットする。失敗したら新しい写真を消し、成功したら古い写真を消す"""
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        delete_image(new_filename)
        raise
    if new_filename and old_filename:
        delete_image(old_filename)


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
        image = _read_image(form)
        if not form.image.errors:
            product = Product(producer_id=current_user.id)
            _apply_form(product, form)
            if image:
                product.image_filename = save_image(*image)
            db.session.add(product)
            _commit_with_image(product.image_filename, None)
            flash("登録しました")
            return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template(
        "producer/form.html", form=form, title="商品の登録", product=None
    ), status


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@role_required(Role.PRODUCER)
def edit(product_id: int):
    product = get_own_product_or_403(product_id)
    form = ProductForm(obj=product)
    if form.validate_on_submit():
        image = _read_image(form)
        if not form.image.errors:
            old_filename = product.image_filename
            new_filename = None
            _apply_form(product, form)
            if image:
                new_filename = save_image(*image)
                product.image_filename = new_filename
            _commit_with_image(new_filename, old_filename)
            flash("保存しました")
            return redirect(url_for("producer.index"))
    status = 422 if form.is_submitted() else 200
    return render_template(
        "producer/form.html", form=form, title="商品の編集", product=product
    ), status


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
    filename = product.image_filename
    db.session.delete(product)
    db.session.commit()
    delete_image(filename)  # DB から消せてから写真を消す
    flash("削除しました")
    return redirect(url_for("producer.index"))
