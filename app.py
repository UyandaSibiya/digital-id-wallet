import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, date
import face_recognition
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PIL import Image, ImageDraw, ImageFont
import io
import qrcode
import base64
import uuid
import secrets

# ---------------------- App Setup ----------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_change_in_production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///digital_id_wallet.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'jpg', 'jpeg', 'png', 'docx', 'txt'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'home'

# ---------------------- Security Headers ----------------------
@app.after_request
def after_request(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

# ---------------------- Models ----------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(256))
    id_number = db.Column(db.String(20), unique=True, nullable=False)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    face_image = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(120), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    certified = db.Column(db.Boolean, default=False)
    cert_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    doc_type = db.Column(db.String(50), nullable=True)
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    file_size = db.Column(db.Integer, nullable=True)
    owner = db.relationship('User', backref='documents')

class ShareToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(120), unique=True, nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_type = db.Column(db.String(50), nullable=True)  # 'bank', 'employer', 'government'
    purpose = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    accessed_at = db.Column(db.DateTime, nullable=True)
    access_count = db.Column(db.Integer, default=0)
    document = db.relationship('Document', backref='share_tokens')

class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(100), nullable=False)  # 'login', 'upload', 'certify', 'share', 'download'
    details = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(45), nullable=True)

# ---------------------- Helper Functions ----------------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def log_activity(action, details=None):
    """Log user activity for security and analytics"""
    if current_user.is_authenticated:
        log = ActivityLog(
            user_id=current_user.id,
            action=action,
            details=details,
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()

def get_file_size(filepath):
    """Get file size in bytes"""
    try:
        return os.path.getsize(filepath)
    except:
        return 0

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def hash_password(password: str) -> str:
    return generate_password_hash(password)

def verify_password(hashed_password: str, input_password: str) -> bool:
    return check_password_hash(hashed_password, input_password)

# ---------------------- Main Routes ----------------------
@app.route('/')
def home():
    """Always show the login/register page"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    documents = Document.query.filter_by(user_id=current_user.id).order_by(Document.upload_date.desc()).all()
    
    # Update last login
    current_user.last_login = datetime.utcnow()
    db.session.commit()
    
    log_activity('dashboard_view')
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
            filename = secure_filename(f"{uuid.uuid4()}_{file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            # Get file size
            file_size = get_file_size(filepath)
            
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
                doc_type=doc_type,
                file_size=file_size
            )
            db.session.add(new_doc)
            db.session.commit()

            log_activity('document_upload', f"Uploaded {doc_type}: {file.filename}")
            flash(f'{doc_type} uploaded successfully')
            return redirect(url_for('dashboard'))

        flash('File type not allowed')
        return redirect(request.url)

    return render_template('upload.html', doc_type=doc_type)

# ---------------------- Authentication Routes ----------------------
@app.route('/register', methods=['POST'])
def register():
    email = request.form.get('email')
    username = request.form.get('username')
    id_number = request.form.get('id_number')
    phone_number = request.form.get('phone_number')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')
    face_file = request.files.get('face_image')

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

    # Save face image with unique filename
    face_filename = None
    if face_file:
        face_filename = secure_filename(f"face_{uuid.uuid4()}_{face_file.filename}")
        face_path = os.path.join(app.config['UPLOAD_FOLDER'], face_filename)
        face_file.save(face_path)

        # Validate that a face is present
        try:
            img = face_recognition.load_image_file(face_path)
            encodings = face_recognition.face_encodings(img)
            if not encodings:
                flash("No face detected. Please try again.")
                return redirect(url_for('home'))
        except Exception as e:
            flash("Error processing face image.")
            return redirect(url_for('home'))

    hashed_password = hash_password(password)
    new_user = User(
        email=email,
        username=username,
        id_number=id_number,
        phone_number=phone_number,
        password=hashed_password,
        face_image=face_filename
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
        flash('Please enter all required fields.')
        return redirect(url_for('home'))

    user = User.query.filter_by(email=email, id_number=id_number, phone_number=phone_number).first()
    if user and verify_password(user.password, password):
        login_user(user)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        log_activity('login', 'Password login successful')
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
    temp_filename = f"temp_face_{uuid.uuid4()}.png"
    temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
    uploaded_file.save(temp_path)

    try:
        uploaded_face = face_recognition.load_image_file(temp_path)
        uploaded_encodings = face_recognition.face_encodings(uploaded_face)
        
        if not uploaded_encodings:
            flash("No face detected. Try again.", "danger")
            return redirect(url_for('home'))
            
        uploaded_encoding = uploaded_encodings[0]
    except Exception as e:
        flash("Error processing face image.", "danger")
        return redirect(url_for('home'))
    finally:
        # Clean up temp file
        try:
            os.remove(temp_path)
        except:
            pass

    # Compare with stored encodings for each user
    for user in User.query.all():
        if not user.face_image:
            continue

        stored_path = os.path.join(app.config['UPLOAD_FOLDER'], user.face_image)
        try:
            known_face = face_recognition.load_image_file(stored_path)
            known_encodings = face_recognition.face_encodings(known_face)
            
            if not known_encodings:
                continue
                
            known_encoding = known_encodings[0]
            match = face_recognition.compare_faces([known_encoding], uploaded_encoding, tolerance=0.6)[0]
            
            if match:
                login_user(user)
                user.last_login = datetime.utcnow()
                db.session.commit()
                
                log_activity('login', 'Face recognition login successful')
                flash("Face login successful!", "success")
                return redirect(url_for('dashboard'))
        except Exception as e:
            continue

    flash("Face not recognized.", "danger")
    return redirect(url_for('home'))

# ---------------------- Document Management Routes ----------------------
@app.route('/download/<int:doc_id>')
@login_required
def download_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    if doc.user_id != current_user.id:
        flash("Unauthorized access!")
        return redirect(url_for('dashboard'))

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], doc.filename)
    
    log_activity('document_download', f"Downloaded {doc.doc_type}: {doc.filename}")

    # If not certified, just return the original file
    if not doc.certified:
        return send_file(file_path, as_attachment=True, download_name=doc.filename)

    filename_lower = doc.filename.lower()
    stamped_file = io.BytesIO()

    # === Handle PDF documents ===
    if filename_lower.endswith(".pdf"):
        try:
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
            can.drawString(200, 710, f"Expires: {doc.expiry_date}")
            can.drawString(200, 690, f"Digital ID Wallet - Govt Verified")
            can.save()

            packet.seek(0)
            stamp_pdf = PdfReader(packet)

            # Overlay stamp on each page
            for page in reader.pages:
                page.merge_page(stamp_pdf.pages[0])
                writer.add_page(page)

            writer.write(stamped_file)
        except Exception as e:
            flash("Error processing PDF file.")
            return send_file(file_path, as_attachment=True)

    # === Handle images (JPG/PNG) ===
    elif filename_lower.endswith((".jpg", ".jpeg", ".png")):
        try:
            img = Image.open(file_path).convert("RGBA")
            draw = ImageDraw.Draw(img)

            # Add green stamp text
            font = ImageFont.load_default()
            draw.text((20, 20), "✅ CERTIFIED COPY", fill=(0, 200, 0, 255), font=font)
            draw.text((20, 40), f"Certified on: {doc.cert_date}", fill=(0, 200, 0, 255), font=font)
            draw.text((20, 60), f"Expires: {doc.expiry_date}", fill=(0, 200, 0, 255), font=font)
            draw.text((20, 80), "Digital ID Wallet - Govt Verified", fill=(0, 200, 0, 255), font=font)

            img.save(stamped_file, format="PNG")
        except Exception as e:
            flash("Error processing image file.")
            return send_file(file_path, as_attachment=True)

    # === Other files → return original ===
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
        
        log_activity('document_certify', f"Certified {doc.doc_type}: {doc.filename}")
        flash("Document certified successfully ✅")
    
    return redirect(url_for('dashboard'))

# ---------------------- Sharing & QR Code Routes ----------------------
@app.route('/share/<int:doc_id>', methods=['POST'])
@login_required
def share_document(doc_id):
    doc = Document.query.get_or_404(doc_id)
    if doc.user_id != current_user.id:
        return jsonify({"error": "Unauthorized"}), 403

    # Get sharing parameters
    data = request.get_json() or {}
    recipient_type = data.get('recipient_type', 'general')
    purpose = data.get('purpose', 'verification')
    expires_in_hours = data.get('expires_in', 1)

    # Generate secure token
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=expires_in_hours)

    # Create share token record
    share_token = ShareToken(
        token=token,
        document_id=doc_id,
        user_id=current_user.id,
        recipient_type=recipient_type,
        purpose=purpose,
        expires_at=expires_at
    )
    db.session.add(share_token)
    db.session.commit()

    # Create verification URL
    share_url = f"{request.host_url}verify/{token}"

    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(share_url)
    qr.make(fit=True)

    qr_image = qr.make_image(fill_color="black", back_color="white")

    # Convert to base64
    buffer = io.BytesIO()
    qr_image.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode()

    log_activity('document_share', f"Shared {doc.doc_type} with {recipient_type}")

    return jsonify({
        "success": True,
        "qr_code": f"data:image/png;base64,{qr_base64}",
        "share_url": share_url,
        "token": token,
        "expires_in": f"{expires_in_hours} hour(s)",
        "expires_at": expires_at.isoformat()
    })

@app.route('/verify/<token>')
def verify_document(token):
    """Public verification endpoint - no login required"""
    share_token = ShareToken.query.filter_by(token=token).first()
    
    if not share_token:
        return render_template('verify_error.html', error="Invalid verification token")
    
    if datetime.utcnow() > share_token.expires_at:
        return render_template('verify_error.html', error="Verification link has expired")
    
    # Update access tracking
    share_token.access_count += 1
    if not share_token.accessed_at:
        share_token.accessed_at = datetime.utcnow()
    db.session.commit()
    
    document = share_token.document
    user = User.query.get(document.user_id)
    
    verification_data = {
        "document_type": document.doc_type,
        "owner_name": user.username,
        "certified": document.certified,
        "cert_date": document.cert_date,
        "expiry_date": document.expiry_date,
        "purpose": share_token.purpose,
        "verified_at": datetime.utcnow(),
        "status": "Valid" if document.certified else "Not Certified"
    }
    
    return render_template('verify_success.html', verification=verification_data)

# ---------------------- API Endpoints ----------------------
@app.route("/ask_ai", methods=["POST"])
@login_required
def ask_ai():
    """Enhanced AI assistant with contextual responses"""
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "No message provided"}), 400

    user_message = data["message"].lower()
    
    # Get user context
    total_docs = Document.query.filter_by(user_id=current_user.id).count()
    certified_docs = Document.query.filter_by(user_id=current_user.id, certified=True).count()
    
    # Smart responses based on keywords and context
    if any(word in user_message for word in ["hello", "hi", "hey", "start"]):
        reply = f"👋 Hi {current_user.username}! I can help you manage your {total_docs} documents. What would you like to do today?"
    
    elif "id" in user_message or "identity" in user_message:
        reply = "🆔 I can help you upload your ID document, certify it for government verification, or generate a QR code for sharing. What do you need?"
    
    elif "passport" in user_message:
        reply = "📘 For passport documents, upload it securely and I'll help you get it certified for international use. Need help with travel documents?"
    
    elif "driver" in user_message or "license" in user_message:
        reply = "🚗 Upload your driver's license and I can certify it for employment verification, car rentals, or insurance purposes."
    
    elif "bank" in user_message or "account" in user_message:
        reply = "🏦 For banking applications, you'll typically need: ID document, proof of address, and income statement. I can help certify all of these!"
    
    elif "job" in user_message or "work" in user_message or "employ" in user_message:
        reply = "💼 For job applications, ensure your ID, qualifications, CV, and references are uploaded and certified. Want me to create a job application package?"
    
    elif "share" in user_message or "qr" in user_message:
        reply = "📤 I can generate secure QR codes for document sharing. Just tell me which document and who needs to verify it (bank, employer, etc.)."
    
    elif "certify" in user_message or "certified" in user_message:
        if certified_docs == 0:
            reply = "📋 None of your documents are certified yet. Certification adds a government stamp and makes them officially valid. Shall I help you certify a document?"
        else:
            reply = f"✅ You have {certified_docs} certified documents. Certified documents are valid for 90 days and accepted by banks, employers, and government offices."
    
    elif "expire" in user_message or "expiry" in user_message:
        expiring_soon = Document.query.filter(
            Document.user_id == current_user.id,
            Document.expiry_date <= date.today() + timedelta(days=30),
            Document.expiry_date > date.today()
        ).count()
        if expiring_soon > 0:
            reply = f"⏰ You have {expiring_soon} document(s) expiring soon. Check your dashboard for details and re-certify them before they expire."
        else:
            reply = "✅ All your certified documents are still valid! I'll notify you when they're close to expiring."
    
    elif "help" in user_message:
        reply = """🤖 I can help you with:
        
📤 Upload documents (ID, passport, licenses, certificates)
✅ Certify documents for official use
📱 Generate QR codes for secure sharing
📊 Track document expiry dates
💼 Create application packages for jobs/banking
🔐 Manage your digital identity securely

What would you like to do?"""
    
    elif "stats" in user_message or "statistics" in user_message:
        time_saved = certified_docs * 4  # 4 hours saved per certified doc
        money_saved = certified_docs * 150  # R150 saved per doc
        reply = f"📊 Your Digital Wallet Impact:\n• {total_docs} documents stored\n• {certified_docs} certified documents\n• ~{time_saved} hours saved\n• ~R{money_saved} saved in fees\n• 99.8% faster than traditional methods!"
    
    elif any(word in user_message for word in ["thanks", "thank you", "awesome", "great"]):
        reply = "😊 You're welcome! I'm here whenever you need help with your documents. Is there anything else I can assist you with?"
    
    else:
        reply = f"🤖 I understand you're asking about: '{user_message}'. I can help with document uploads, certification, sharing, and verification. What specific task would you like help with?"
    
    log_activity('ai_chat', f"AI query: {user_message[:50]}...")
    return jsonify({"reply": reply})

@app.route('/api/stats')
@login_required
def get_user_stats():
    """Get user statistics for dashboard"""
    total_docs = Document.query.filter_by(user_id=current_user.id).count()
    certified_docs = Document.query.filter_by(user_id=current_user.id, certified=True).count()
    
    # Calculate impact metrics
    time_saved_hours = certified_docs * 4
    money_saved = certified_docs * 150
    
    # Get recent activity
    recent_activity = ActivityLog.query.filter_by(user_id=current_user.id)\
        .order_by(ActivityLog.timestamp.desc()).limit(5).all()
    
    activity_list = [{
        "action": log.action,
        "details": log.details,
        "timestamp": log.timestamp.isoformat()
    } for log in recent_activity]
    
    return jsonify({
        "total_documents": total_docs,
        "certified_documents": certified_docs,
        "time_saved_hours": time_saved_hours,
        "money_saved_rand": money_saved,
        "Progress_rate": "99.8%",
        "avg_processing_time": "30 seconds",
        "member_since": current_user.created_at.strftime("%B %Y"),
        "last_login": current_user.last_login.strftime("%Y-%m-%d %H:%M") if current_user.last_login else "First time",
        "recent_activity": activity_list
    })

@app.route('/api/notifications')
@login_required
def get_notifications():
    """Get user notifications"""
    today = date.today()
    
    # Documents expiring soon
    expiring_soon = Document.query.filter(
        Document.user_id == current_user.id,
        Document.expiry_date <= today + timedelta(days=30),
        Document.expiry_date > today
    ).all()
    
    notifications = []
    
    for doc in expiring_soon:
        days_left = (doc.expiry_date - today).days
        urgency = "high" if days_left <= 7 else "medium" if days_left <= 14 else "low"
        
        notifications.append({
            "id": f"expiry_{doc.id}",
            "type": "expiry_warning",
            "title": f"{doc.doc_type} Expiring Soon",
            "message": f"Your certified {doc.doc_type} expires in {days_left} days. Renew certification to keep it valid.",
            "doc_id": doc.id,
            "urgency": urgency,
            "action": "Renew Certification",
            "created_at": datetime.utcnow().isoformat()
        })
    
    # Welcome message for new users
    if current_user.created_at > datetime.utcnow() - timedelta(days=7):
        notifications.append({
            "id": "welcome",
            "type": "welcome",
            "title": "Welcome to Digital ID Wallet! 🎉",
            "message": "Start by uploading your ID document and getting it certified. Need help? Ask our AI assistant!",
            "urgency": "low",
            "action": "Upload Document",
            "created_at": current_user.created_at.isoformat()
        })
    
    return jsonify({
        "notifications": notifications,
        "count": len(notifications)
    })

@app.route('/api/templates')
@login_required
def get_document_templates():
    """Get document templates for common use cases"""
    user_docs = Document.query.filter_by(user_id=current_user.id).all()
    doc_types = [doc.doc_type for doc in user_docs]
    
    templates = {
        "job_application": {
            "name": "Job Application Package",
            "icon": "💼",
            "required_docs": ["ID Document", "CV/Resume", "Qualifications", "References"],
            "description": "Complete package for job applications in South Africa"
        },
        "bank_account": {
            "name": "Bank Account Opening",
            "icon": "🏦",
            "required_docs": ["ID Document", "Proof of Address", "Income Statement"],
            "description": "Documents required for opening a bank account"
        },
        "travel": {
            "name": "Travel Documents",
            "icon": "✈️",
            "required_docs": ["Passport", "Visa", "ID Document"],
            "description": "Documents needed for international travel"
        }
    }

    return jsonify({
        "templates": templates,
        "available_types": list(templates.keys()),
        "user_doc_types": doc_types
    })

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
