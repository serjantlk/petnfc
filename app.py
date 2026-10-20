import os, secrets
from datetime import datetime
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, abort, session)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.instance_path = "/tmp/instance"
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:////tmp/petnfc.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ---------- Модели ----------
class Owner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120))
    phone = db.Column(db.String(50))
    pets = db.relationship("Pet", backref="owner", cascade="all, delete-orphan")

class NfcTag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    pet_id = db.Column(db.Integer, db.ForeignKey("pet.id"), nullable=True) # Может быть пустым до активации
    pet = db.relationship("Pet", backref="tags")

class Pet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    species = db.Column(db.String(50))
    breed = db.Column(db.String(80))
    color = db.Column(db.String(50))
    notes = db.Column(db.Text)
    photo_url = db.Column(db.String(500))
    owner_id = db.Column(db.Integer, db.ForeignKey("owner.id"), nullable=False)
    finds = db.relationship("Find", backref="pet", cascade="all, delete-orphan")

class Find(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pet_id = db.Column(db.Integer, db.ForeignKey("pet.id"), nullable=False)
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    accuracy = db.Column(db.Float)
    user_agent = db.Column(db.String(300))
    ip = db.Column(db.String(60))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# ---------- Хелперы ----------
def current_owner():
    oid = session.get("owner_id")
    return db.session.get(Owner, oid) if oid else None

def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*a, **kw):
        if not current_owner():
            return redirect(url_for("owner_login", next=request.path))
        return f(*a, **kw)
    return wrapper

# ---------- Умный роутинг для NFC ----------
@app.route("/tag/<code>")
def tag_redirect(code):
    tag = NfcTag.query.filter_by(code=code).first()
    if not tag:
        return "Метка не найдена или не существует", 404
    
    if tag.pet_id:
        # Метка активирована -> на страницу питомца
        return redirect(url_for("pet_page", slug=tag.pet.slug))
    else:
        # Метка не активирована -> на страницу входа/активации
        return redirect(url_for("owner_login", tag=code))

@app.route("/p/<slug>")
def pet_page(slug):
    pet = Pet.query.filter_by(slug=slug).first_or_404()
    return render_template("pet.html", pet=pet)

@app.route("/api/find/<slug>", methods=["POST"])
def report_find(slug):
    pet = Pet.query.filter_by(slug=slug).first_or_404()
    data = request.get_json(silent=True) or {}
    f = Find(
        pet_id=pet.id,
        lat=data.get("lat"), lon=data.get("lon"),
        accuracy=data.get("accuracy"),
        user_agent=request.headers.get("User-Agent", "")[:300],
        ip=request.remote_addr,
    )
    db.session.add(f)
    db.session.commit()
    return jsonify({"ok": True})

# ---------- Владелец ----------
@app.route("/owner/login", methods=["GET", "POST"])
def owner_login():
    tag_code = request.args.get("tag") # Получаем код метки из URL, если есть
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        pwd = request.form["password"]
        owner = Owner.query.filter_by(email=email).first()
        if owner and check_password_hash(owner.password_hash, pwd):
            session["owner_id"] = owner.id
            # Если пришли по метке, перенаправляем на страницу активации
            if tag_code:
                return redirect(url_for("owner_dashboard", activate_tag=tag_code))
            return redirect(url_for("owner_dashboard"))
        flash("Неверный email или пароль")
    return render_template("owner_login.html", tag_code=tag_code)

@app.route("/owner/logout")
def owner_logout():
    session.pop("owner_id", None)
    return redirect(url_for("owner_login"))

@app.route("/owner")
@login_required
def owner_dashboard():
    owner = current_owner()
    activate_tag = request.args.get("activate_tag")
    tag_to_activate = None
    if activate_tag:
        tag_to_activate = NfcTag.query.filter_by(code=activate_tag, pet_id=None).first()
    
    return render_template("owner_dashboard.html", owner=owner, tag_to_activate=tag_to_activate)

@app.route("/owner/activate/<code>", methods=["POST"])
@login_required
def activate_tag(code):
    owner = current_owner()
    tag = NfcTag.query.filter_by(code=code, pet_id=None).first_or_404()
    
    # Если у владельца нет питомцев, создаем нового
    if not owner.pets:
        pet_name = request.form.get("pet_name", "Мой питомец")
        slug = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
        pet = Pet(slug=slug, name=pet_name, owner_id=owner.id)
        db.session.add(pet)
        db.session.commit()
    else:
        pet_id = request.form.get("pet_id")
        pet = Pet.query.filter_by(id=pet_id, owner_id=owner.id).first_or_404()
        
    tag.pet_id = pet.id
    db.session.commit()
    flash(f"Метка {code} успешно привязана!")
    return redirect(url_for("owner_dashboard"))

# ... (Остальные роуты owner_edit, owner_new_pet, admin остаются без изменений, 
# но метод owner_new_pet можно оставить для ручного создания, если нужно) ...

# ---------- Админ ----------
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
        else:
            flash("Неверный пароль администратора")
            return redirect(url_for("admin"))
    if not session.get("is_admin"):
        return render_template("admin.html", logged=False)
    owners = Owner.query.all()
    tags = NfcTag.query.all()
    return render_template("admin.html", logged=True, owners=owners, tags=tags)

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin"))

@app.route("/admin/owner/new", methods=["POST"])
def admin_new_owner():
    if not session.get("is_admin"):
        abort(403)
    email = request.form["email"].strip().lower()
    pwd = request.form["password"]
    if Owner.query.filter_by(email=email).first():
        flash("Такой email уже есть")
    else:
        db.session.add(Owner(email=email, password_hash=generate_password_hash(pwd),
                             name=request.form.get("name", ""),
                             phone=request.form.get("phone", "")))
        db.session.commit()
        flash("Владелец создан")
    return redirect(url_for("admin"))

@app.route("/admin/tag/new", methods=["POST"])
def admin_new_tag():
    if not session.get("is_admin"):
        abort(403)
    # Генерируем случайный код для новой метки (например, 0001, 0002)
    # Для простоты берем следующий ID
    last_tag = NfcTag.query.order_by(NfcTag.id.desc()).first()
    next_id = (last_tag.id + 1) if last_tag else 1
    code = f"{next_id:04d}"
    db.session.add(NfcTag(code=code))
    db.session.commit()
    flash(f"Создана новая метка: {code}")
    return redirect(url_for("admin"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
