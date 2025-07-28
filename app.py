import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'jpg', 'jpeg', 'png', 'docx', 'txt'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ----------------------- Models -----------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    username = db.Column(db.String(150), unique=True, nullable=False)  # ✅ added
    password = db.Column(db.String(256))

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(120), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    certified = db.Column(db.Boolean, default=False)
    cert_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    doc_type = db.Column(db.String(50), nullable=True)
    owner = db.relationship('User', backref='documents')

# ---------------------- Helpers -----------------------

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def hash_password(password: str, method: str = "scrypt", salt_length: int = 16) -> str:
    return generate_password_hash(password, method=method, salt_length=salt_length)

def verify_password(hashed_password: str, input_password: str) -> bool:
    return check_password_hash(hashed_password, input_password)

# ---------------------- Routes ------------------------

@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    documents = current_user.documents
    return render_template('dashboard.html', email=current_user.email, documents=documents)

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    doc_type = request.args.get('doc_type', 'Document')

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part')
            return redirect(request.url)

        file = request.files['file']

        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            certified = 'certified' in request.form
            cert_date_str = request.form.get('cert_date', None)

            cert_date = None
            expiry_date = None
            if cert_date_str:
                cert_date = datetime.strptime(cert_date_str, "%Y-%m-%d").date()
                expiry_date = cert_date + timedelta(days=90)

            new_doc = Document(
                filename=filename,
                user_id=current_user.id,
                certified=certified,
                cert_date=cert_date,
                expiry_date=expiry_date,
                doc_type=doc_type
            )
            db.session.add(new_doc)
            db.session.commit()

            flash(f'{doc_type} uploaded successfully')
            return redirect(url_for('dashboard'))

        flash('File type not allowed')
        return redirect(request.url)

    return render_template('upload.html', doc_type=doc_type)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        username = request.form['username']

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('Email already registered. Please log in.')
            return redirect(url_for('login'))

        hashed_password = hash_password(password)
        new_user = User(email=email, username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()

        flash('Registration successful. You can now log in.')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        print("Login attempt:", email, password)  # Debug

        if not email or not password:
            flash('Please enter both email and password.')
            return render_template('login.html')

        user = User.query.filter_by(email=email).first()
        if user:
            print("User found:", user.email)  # Debug
        else:
            print("No user found with email:", email)

        if user and verify_password(user.password, password):
            login_user(user)
            print("Login successful")  # Debug
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.')
            print("Login failed")  # Debug
            return render_template('login.html')

    return render_template('login.html')



@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.")
    return redirect(url_for('home'))

@app.route('/<path:path>')
def catch_all(path):
    return f"Route '{path}' not found, check your URL or add route."

# -------------------- Main Entry ----------------------

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=8080)
