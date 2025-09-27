import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import face_recognition
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PIL import Image, ImageDraw, ImageFont
import io

# ---------------------- App Setup ----------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///something.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'jpg', 'jpeg', 'png', 'docx', 'txt'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'home'  # Redirect to '/' if not logged in

# ---------------------- Models ----------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(256))
    id_number = db.Column(db.String(20), unique=True, nullable=False)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    face_image = db.Column(db.String(120), nullable=True)  # NEW FIELD


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(120), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    certified = db.Column(db.Boolean, default=False)
    cert_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    doc_type = db.Column(db.String(50), nullable=True)
    owner = db.relationship('User', backref='documents')

# ---------------------- Helpers ----------------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def hash_password(password: str) -> str:
    return generate_password_hash(password)

def verify_password(hashed_password: str, input_password: str) -> bool:
    return check_password_hash(hashed_password, input_password)

# ---------------------- Routes ----------------------
@app.route('/')
def home():
    """Always show the login/register page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    documents = Document.query.filter_by(user_id=current_user.id).all()
    return render_template('dashboard.html', documents=documents, email=current_user.email)

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
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

            certified = 'certified' in request.form
            cert_date_str = request.form.get('cert_date', None)
            cert_date, expiry_date = None, None
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


@app.route('/register', methods=['POST'])
def register():
    email = request.form.get('email')
    username = request.form.get('username')
    id_number = request.form.get('id_number')
    phone_number = request.form.get('phone_number')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')
    face_file = request.files.get('face_image')   # NEW 👈

    if not all([email, username, id_number, phone_number, password, confirm_password, face_file]):
        flash('Please fill out all fields and capture your face.')
        return redirect(url_for('home'))

    if password != confirm_password:
        flash('Passwords do not match')
        return redirect(url_for('home'))

    if User.query.filter_by(email=email).first():
        flash('Email already registered. Please log in.')
        return redirect(url_for('home'))

    if User.query.filter_by(id_number=id_number).first():
        flash('ID number already registered.')
        return redirect(url_for('home'))

    if User.query.filter_by(phone_number=phone_number).first():
        flash('Phone number already registered.')
        return redirect(url_for('home'))

    # Save face image
    face_filename = None
    if face_file:
        face_filename = secure_filename(face_file.filename)
        face_path = os.path.join(app.config['UPLOAD_FOLDER'], face_filename)
        face_file.save(face_path)

        # Validate that a face is present
        try:
            img = face_recognition.load_image_file(face_path)
            encodings = face_recognition.face_encodings(img)
            if not encodings:
                flash("No face detected. Please try again.")
                return redirect(url_for('home'))
        except:
            flash("Error processing face image.")
            return redirect(url_for('home'))

    hashed_password = hash_password(password)
    new_user = User(
        email=email,
        username=username,
        id_number=id_number,
        phone_number=phone_number,
        password=hashed_password,
        face_image=face_filename   # save the filename
    )
    db.session.add(new_user)
    db.session.commit()

    flash('Registration successful. You can now log in.')
    return redirect(url_for('home'))


@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email')
    id_number = request.form.get('id_number')
    phone_number = request.form.get('phone_number')
    password = request.form.get('password')

    if not all([email, id_number, phone_number, password]):
        flash('Please enter email, ID number, phone number, and password.')
        return redirect(url_for('home'))

    user = User.query.filter_by(email=email, id_number=id_number, phone_number=phone_number).first()
    if user and verify_password(user.password, password):
        login_user(user)
        flash('Login successful! Welcome to Digital Wallet.')
        return redirect(url_for('dashboard'))
    else:
        flash('Invalid login credentials.')
        return redirect(url_for('home'))
    

@app.route('/face_login', methods=['POST'])
def face_login():
    uploaded_file = request.files.get('face_image')
    if not uploaded_file:
        flash("No face image uploaded.", "danger")
        return redirect(url_for('home'))

    # Save temporarily
    temp_path = os.path.join(app.config['UPLOAD_FOLDER'], "temp_face.png")
    uploaded_file.save(temp_path)

    try:
        uploaded_face = face_recognition.load_image_file(temp_path)
        uploaded_encoding = face_recognition.face_encodings(uploaded_face)[0]
    except:
        flash("No face detected. Try again.", "danger")
        return redirect(url_for('home'))

    # Compare with stored encodings for each user
    for user in User.query.all():
        if not user.face_image:
            continue

        stored_path = os.path.join(app.config['UPLOAD_FOLDER'], user.face_image)
        try:
            known_face = face_recognition.load_image_file(stored_path)
            known_encoding = face_recognition.face_encodings(known_face)[0]

            match = face_recognition.compare_faces([known_encoding], uploaded_encoding)[0]
            if match:
                login_user(user)
                flash("Face login successful!", "success")
                return redirect(url_for('dashboard'))
        except:
            continue

    flash("Face not recognized.", "danger")
    return redirect(url_for('home'))


@app.route('/download/<int:doc_id>')
@login_required
def download_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    if doc.user_id != current_user.id:
        flash("Unauthorized access!")
        return redirect(url_for('dashboard'))

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], doc.filename)

    # If not certified, just return the original file
    if not doc.certified:
        return send_file(file_path, as_attachment=True)

    filename_lower = doc.filename.lower()
    stamped_file = io.BytesIO()

    # === Handle PDF documents ===
    if filename_lower.endswith(".pdf"):
        reader = PdfReader(file_path)
        writer = PdfWriter()

        # Create a stamp with ReportLab
        packet = io.BytesIO()
        can = canvas.Canvas(packet, pagesize=letter)
        can.setFont("Helvetica-Bold", 20)
        can.setFillColorRGB(0, 0.6, 0)  # green
        can.drawString(200, 750, "✅ CERTIFIED COPY")
        can.setFont("Helvetica", 12)
        can.drawString(200, 730, f"Certified on: {doc.cert_date}")
        can.save()

        packet.seek(0)
        stamp_pdf = PdfReader(packet)

        # Overlay stamp on each page
        for page in reader.pages:
            page.merge_page(stamp_pdf.pages[0])
            writer.add_page(page)

        writer.write(stamped_file)

    # === Handle images (JPG/PNG) ===
    elif filename_lower.endswith((".jpg", ".jpeg", ".png")):
        img = Image.open(file_path).convert("RGBA")
        draw = ImageDraw.Draw(img)

        # Add green stamp text
        font = ImageFont.load_default()
        draw.text((20, 20), "✅ CERTIFIED COPY", fill=(0, 200, 0, 255), font=font)
        draw.text((20, 40), f"Certified on: {doc.cert_date}", fill=(0, 200, 0, 255), font=font)

        img.save(stamped_file, format="PNG")

    # === Other files (docx/txt) → just return original for now ===
    else:
        return send_file(file_path, as_attachment=True)

    stamped_file.seek(0)
    return send_file(
        stamped_file,
        as_attachment=True,
        download_name=f"certified_{doc.filename}"
    )

@app.route('/certify/<int:doc_id>')
@login_required
def certify_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    if doc.user_id != current_user.id:
        flash("Unauthorized access!")
        return redirect(url_for('dashboard'))
    if not doc.certified:
        doc.certified = True
        doc.cert_date = datetime.now().date()
        doc.expiry_date = doc.cert_date + timedelta(days=90)
        db.session.commit()
        flash("Document certified successfully ✅")
    return redirect(url_for('dashboard'))


# ---------------- AI Chat Endpoint ----------------
@app.route("/ask_ai", methods=["POST"])
@login_required
def ask_ai():
    """
    Receives a JSON payload with { "message": "user message" }
    Returns a JSON with { "reply": "AI response" }
    """
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "No message provided"}), 400

    user_message = data["message"]

    # --- Simple AI logic / placeholder ---
    # Replace this with OpenAI API or other AI logic
    reply = f"🤖 You said: {user_message}"

    return jsonify({"reply": reply})


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
    app.run(debug=True, port=8000)
