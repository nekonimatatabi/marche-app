from flask import Blueprint, abort, render_template
from flask_login import current_user
from sqlalchemy.orm import joinedload

from marche.extensions import db
from marche.models import Product
from marche.services.inventory import available_stock

bp = Blueprint("shop", __name__)


def _can_see_unpublished(product: Product) -> bool:
    """非公開の商品を見られるのは、出品した生産者と管理者だけ"""
    if not current_user.is_authenticated:
        return False
    return current_user.id == product.producer_id or current_user.is_admin


@bp.route("/")
def index():
    products = db.session.scalars(
        db.select(Product)
        .options(joinedload(Product.producer))
        .filter_by(is_published=True)
        .order_by(Product.created_at.desc(), Product.id.desc())
    ).all()
    # テンプレートから services を呼ばないように、いま買える数はここで計算して渡す
    items = [(product, available_stock(product)) for product in products]
    return render_template("shop/index.html", items=items)


@bp.route("/products/<int:product_id>")
def show(product_id: int):
    product = db.get_or_404(Product, product_id)
    # 非公開の商品は、見られない人には 403 ではなく 404 にする（「ある」ことも教えない）
    if not product.is_published and not _can_see_unpublished(product):
        abort(404)
    return render_template(
        "shop/show.html", product=product, stock=available_stock(product)
    )
