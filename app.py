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
    pet_id = db.Column(db.Integer, db.ForeignKey("pet.id"), nullable=True)
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

# ---------- Настройки админа ----------
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@petnfc.ru")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# ---------- Хелперы ----------
def current_owner():
    oid = session.get("owner_id")
    return db.session.get(Owner, oid) if oid else None

# ---------- Главная страница (Вход и Регистрация) ----------
@app.route("/", methods=["GET", "POST"])
def index():
    tag_code = request.args.get("tag")
    is_register = request.args.get("register") == "1"  # Флаг для переключения на вкладку регистрации
    
    if request.method == "POST":
        action = request.form.get("action")
        email = request.form.get("email", "").strip().lower()
        pwd = request.form.get("password", "")

        # --- Логика ВХОДА ---
        if action == "login":
            if email == ADMIN_EMAIL.lower() and pwd == ADMIN_PASSWORD:
                session["is_admin"] = True
                session.pop("owner_id", None)
                return redirect(url_for("admin"))
            
            owner = Owner.query.filter_by(email=email).first()
            if owner and check_password_hash(owner.password_hash, pwd):
                session["owner_id"] = owner.id
                session.pop("is_admin", None)
                if tag_code:
                    return redirect(url_for("owner_dashboard", activate_tag=tag_code))
                return redirect(url_for("owner_dashboard"))
                
            flash("Неверный email или пароль")
            return redirect(url_for("index", tag=tag_code, register=1))

        # --- Логика РЕГИСТРАЦИИ ---
        elif action == "register":
            name = request.form.get("name", "").strip()
            phone = request.form.get("phone", "").strip()
            
            if Owner.query.filter_by(email=email).first():
                flash("Пользователь с таким email уже существует. Попробуйте войти.")
                return redirect(url_for("index", tag=tag_code))
            
            new_owner = Owner(
                email=email, 
                password_hash=generate_password_hash(pwd),
                name=name,
                phone=phone
            )
            db.session.add(new_owner)
            db.session.commit()
            
            # Автоматически логиним
            session["owner_id"] = new_owner.id
            flash("Регистрация успешна! Привяжите метку к питомцу.")
            
            if tag_code:
                return redirect(url_for("owner_dashboard", activate_tag=tag_code))
            return redirect(url_for("owner_dashboard"))
        
    return render_template("index.html", tag_code=tag_code, is_register=is_register)

# ---------- Умный роутинг для NFC ----------
@app.route("/tag/<code>")
def tag_redirect(code):
    tag = NfcTag.query.filter_by(code=code).first()
    if not tag:
        return "Метка не найдена или не существует", 404
    
    if tag.pet_id:
        return redirect(url_for("pet_page", slug=tag.pet.slug))
    else:
        return redirect(url_for("index", tag=code))

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

# ---------- Кабинет Владельца ----------
@app.route("/owner")
def owner_dashboard():
    owner = current_owner()
    if not owner:
        return redirect(url_for("index"))
        
    activate_tag = request.args.get("activate_tag")
    tag_to_activate = None
    if activate_tag:
        tag_to_activate = NfcTag.query.filter_by(code=activate_tag, pet_id=None).first()
    
    return render_template("owner_dashboard.html", owner=owner, tag_to_activate=tag_to_activate)

@app.route("/owner/logout")
def owner_logout():
    session.pop("owner_id", None)
    return redirect(url_for("index"))

# ... (Остальные маршруты владельца и админа оставляем без изменений) ...
# (activate_tag, owner_edit, delete_find, admin, admin_new_owner, admin_new_tag)

@app.route("/owner/activate/<code>", methods=["POST"])
def activate_tag(code):
    owner = current_owner()
    if not owner:
        return redirect(url_for("index"))
        
    tag = NfcTag.query.filter_by(code=code, pet_id=None).first_or_404()
    
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

@app.route("/owner/pet/<int:pet_id>/edit", methods=["GET", "POST"])
def owner_edit(pet_id):
    owner = current_owner()
    if not owner:
        return redirect(url_for("index"))
        
    pet = Pet.query.filter_by(id=pet_id, owner_id=owner.id).first_or_404()
    if request.method == "POST":
        pet.name = request.form["name"].strip()
        pet.species = request.form.get("species", "").strip()
        pet.breed = request.form.get("breed", "").strip()
        pet.color = request.form.get("color", "").strip()
        pet.notes = request.form.get("notes", "").strip()
        pet.photo_url = request.form.get("photo_url", "").strip()
        db.session.commit()
        flash("Сохранено")
        return redirect(url_for("owner_dashboard"))
    return render_template("owner_edit.html", pet=pet)

@app.route("/owner/find/<int:find_id>/delete", methods=["POST"])
def delete_find(find_id):
    owner = current_owner()
    if not owner:
        return redirect(url_for("index"))
    f = db.session.get(Find, find_id)
    if not f or f.pet.owner_id != owner.id:
        abort(403)
    db.session.delete(f)
    db.session.commit()
    return redirect(url_for("owner_dashboard"))

@app.route("/admin", methods=["GET"])
def admin():
    if not session.get("is_admin"):
        return redirect(url_for("index"))
    owners = Owner.query.all()
    tags = NfcTag.query.order_by(NfcTag.id.desc()).all()
    return render_template("admin.html", owners=owners, tags=tags)

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))

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
    part1 = secrets.token_hex(2).upper()
    part2 = secrets.token_hex(2).upper()
    part3 = secrets.token_hex(2).upper()
    code = f"PN-{part1}-{part2}-{part3}"
    
    db.session.add(NfcTag(code=code))
    db.session.commit()
    flash(f"Создана новая метка: {code}")
    return redirect(url_for("admin"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
