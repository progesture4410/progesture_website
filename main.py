from flask import Flask, render_template, request, redirect, url_for, flash, session
import firebase_admin
from firebase_admin import credentials, firestore
import os
import qrcode
import smtplib
from email.message import EmailMessage
from datetime import datetime

from PIL import Image
from pdf2image import convert_from_path
import cv2
from pptx import Presentation
from flask import send_from_directory

# ================================
# APP SETUP
# ================================
app = Flask(__name__)
app.secret_key = "progesture_super_secret_key_123"

app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

THUMB_FOLDER = os.path.join("static", "thumbnails")
os.makedirs(THUMB_FOLDER, exist_ok=True)


# ================================
# FIREBASE SETUP
# ================================
cred = credentials.Certificate("firebase_key.json")

firebase_admin.initialize_app(cred, {
    "storageBucket": "progesture-database.appspot.com"
})

db = firestore.client()


# ================================
# GLOBAL STORAGE
# ================================
favorites = {}   # {username: set(files)}
trash = {}       # {username: set(files)}


# ================================
# FILE TYPE GROUPS
# ================================
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
PDF_TYPES = (".pdf",)
VIDEO_TYPES = (".mp4", ".mov", ".avi", ".mkv")
DOC_TYPES = (".doc", ".docx", ".txt")
PPT_TYPES = (".ppt", ".pptx")


# ================================
# HELPER FUNCTIONS
# ================================
def require_login():
    if "username" not in session:
        return False
    return True


def get_user_folder():
    user = session["username"]
    user_folder = os.path.join(app.config["UPLOAD_FOLDER"], user)
    os.makedirs(user_folder, exist_ok=True)
    return user, user_folder


def generate_thumbnail(filepath, filename):

    thumb_path = os.path.join(THUMB_FOLDER, filename + ".png")

    try:

        if os.path.exists(thumb_path):
            return

        name = filename.lower()

        if name.endswith(IMAGE_TYPES):

            img = Image.open(filepath)
            img.thumbnail((400,300))
            img.save(thumb_path)


        elif name.endswith(PDF_TYPES):

            import fitz  # PyMuPDF

            doc = fitz.open(filepath)

            page = doc.load_page(0)  # first page

            pix = page.get_pixmap()

            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            img.thumbnail((400, 300))

            img.save(thumb_path)



        elif name.endswith((".doc", ".docx")):

            try:

                import pythoncom

                import comtypes.client

                import tempfile

                import fitz  # PyMuPDF

                pythoncom.CoInitialize()

                word = comtypes.client.CreateObject("Word.Application")

                word.Visible = False

                doc = word.Documents.Open(os.path.abspath(filepath))

                temp_pdf = os.path.join(tempfile.gettempdir(), filename + ".pdf")

                # Save as PDF (17 = wdFormatPDF)

                doc.SaveAs(temp_pdf, FileFormat=17)

                doc.Close()

                word.Quit()

                pythoncom.CoUninitialize()

                if not os.path.exists(temp_pdf):
                    raise Exception("Word PDF export failed")

                # Convert PDF → image

                pdf = fitz.open(temp_pdf)

                page = pdf.load_page(0)

                pix = page.get_pixmap()

                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                img.thumbnail((400, 300))

                img.save(thumb_path)


            except Exception as e:

                print("WORD thumbnail error:", e)

                # fallback (only if everything fails)

                img = Image.new("RGB", (400, 300), (240, 240, 240))

                img.save(thumb_path)

            except Exception as e:

                print("WORD thumbnail error:", e)

                from PIL import ImageDraw

                img = Image.new("RGB", (400, 300), (255, 255, 255))

                draw = ImageDraw.Draw(img)

                # Blue header (Word style)

                draw.rectangle([0, 0, 400, 80], fill=(43, 87, 154))

                # Big "W"

                draw.text((20, 10), "W", fill="white")

                # Filename (shortened)

                short_name = filename[:20] + "..." if len(filename) > 20 else filename

                draw.text((20, 120), short_name, fill=(0, 0, 0))

                img.save(thumb_path)

        elif name.endswith(VIDEO_TYPES):

            cap = cv2.VideoCapture(filepath)
            success, frame = cap.read()

            if success:
                frame = cv2.resize(frame,(400,300))
                cv2.imwrite(thumb_path,frame)

            cap.release()


        elif name.endswith(PPT_TYPES):

            try:

                import pythoncom

                import comtypes.client

                pythoncom.CoInitialize()  # ✅ FIX: initialize COM

                powerpoint = comtypes.client.CreateObject("Powerpoint.Application")

                powerpoint.Visible = 1  # safer than 0

                presentation = powerpoint.Presentations.Open(

                    os.path.abspath(filepath),

                    WithWindow=False

                )

                slide = presentation.Slides(1)

                slide.Export(os.path.abspath(thumb_path), "PNG", 400, 300)

                presentation.Close()

                powerpoint.Quit()

                pythoncom.CoUninitialize()  # cleanup


            except Exception as e:

                print("PPT thumbnail error:", e)

                # fallback

                img = Image.new("RGB", (400, 300), (240, 240, 240))

                img.save(thumb_path)

    except Exception as e:
        print("Thumbnail error:", e)

def preview_thumb(filename):

    thumb = os.path.join("static", "thumbnails", filename + ".png")

    if os.path.exists(thumb):
        return url_for("static", filename="thumbnails/" + filename + ".png")

    name = filename.lower()

    if name.endswith(IMAGE_TYPES):
        return url_for("uploaded_file", filename=filename)

    if name.endswith(PDF_TYPES):
        return url_for("static", filename="icons/pdf.png")

    if name.endswith(PPT_TYPES):
        return url_for("static", filename="icons/ppt.png")

    if name.endswith(VIDEO_TYPES):
        return url_for("static", filename="icons/video.png")

    if name.endswith((".doc", ".docx")):
        return url_for("static", filename="icons/word.png")

    return url_for("static", filename="icons/file.png")


# ================================
# BASIC ROUTES
# ================================
@app.route("/")
def home():
    return render_template("home.html")


@app.route("/faqs2")
def faqs2():
    return render_template("faqs2.html")

@app.route("/user")
def user():

    if not require_login():
        return redirect(url_for("login"))

    return render_template("user.html")


@app.route("/presentation")
def presentation():

    if not require_login():
        return redirect(url_for("login"))

    return render_template("presentation.html")


# ================================
# FILE FILTER ROUTES
# ================================
def filter_files(file_types):

    if not require_login():
        return redirect(url_for("login"))

    user, user_folder = get_user_folder()
    user_trash = trash.get(user, set())

    files = []

    for file in os.listdir(user_folder):

        # 🚫 Ignore Word temporary files
        if file.startswith("~$"):
            continue

        if file in user_trash:
            continue

        if file.lower().endswith(file_types):
            files.append(file)

    # get user profile
    user_doc = db.collection("users").document(user).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    return render_template(
        "dashboard.html",
        files=files,
        current_view="allfiles",
        preview_thumb=preview_thumb,
        profile=user_data,

        # ✅ ADD THESE
        files_this_week=0,
        favorites_this_week=0,
        trash_this_week=0,
    )

@app.route("/documents")
def documents():
    return filter_files(PDF_TYPES + DOC_TYPES)


@app.route("/pdf")
def pdf():
    return filter_files(PDF_TYPES)


@app.route("/img")
def img():
    return filter_files(IMAGE_TYPES)


@app.route("/videos")
def videos():
    return filter_files(VIDEO_TYPES)

@app.route("/features")
def features():
    return render_template("features.html")

# ================================
# AUTH SYSTEM
# ================================
@app.route("/signup", methods=["GET", "POST"])
def signup():

    if request.method == "POST":

        first_name = request.form.get("first_name")
        surname = request.form.get("surname")
        username = request.form.get("username")
        email = request.form.get("email")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("Passwords do not match.", "signup")
            return redirect(url_for("signup"))

        # Check username
        if db.collection("users").document(username).get().exists:
            flash("Username already exists.", "signup")
            return redirect(url_for("signup"))

        # Check email
        existing_emails = db.collection("users").where("email", "==", email).stream()

        for _ in existing_emails:
            flash("Email already registered.", "signup")
            return redirect(url_for("signup"))

        user_data = {
            "first_name": first_name,
            "surname": surname,
            "username": username,
            "email": email,
            "password": password
        }

        db.collection("users").document(username).set(user_data)

        # Generate QR
        qr_folder = os.path.join("static", "qrcodes")
        os.makedirs(qr_folder, exist_ok=True)

        qr = qrcode.make(f"LOGIN:{username}:{password}")
        qr_path = os.path.join(qr_folder, f"{username}.png")
        qr.save(qr_path)

        # send QR to email
        send_qr_email(email, username, password)

        return redirect(url_for("home"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        user_doc = db.collection("users").document(username).get()

        if user_doc.exists:
            user = user_doc.to_dict()

            if user["password"] == password:
                session["username"] = username
                return redirect(url_for("dashboard"))

        flash("Invalid username or password")
        return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "POST":

        email = request.form.get("email")

        # find user by email
        users = db.collection("users").where("email", "==", email).stream()

        user_found = None

        for user in users:
            user_found = user.to_dict()
            username = user_found["username"]
            break

        if not user_found:
            return render_template("forgot_password.html", error="Email not found.")

        # generate temporary password
        import random
        import string

        temp_password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))

        # update password in database
        db.collection("users").document(username).update({
            "password": temp_password
        })

        # regenerate QR
        qr_folder = os.path.join("static", "qrcodes")
        os.makedirs(qr_folder, exist_ok=True)

        qr = qrcode.make(f"LOGIN:{username}:{temp_password}")
        qr_path = os.path.join(qr_folder, f"{username}.png")
        qr.save(qr_path)

        # send email
        send_qr_email(email, username, temp_password)

        return render_template("forgot_password.html", success="New password + QR sent to your email.")

    return render_template("forgot_password.html")

@app.route("/settings")
def settings():
    if not require_login():
        return redirect(url_for("login"))
    return render_template("settings.html")

@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("home"))

# ================================
# FILE UPLOAD
# ================================
@app.route("/upload", methods=["POST"])
def upload():

    if not require_login():
        return redirect(url_for("login"))

    user, user_folder = get_user_folder()

    if "file" not in request.files:
        return redirect(url_for("dashboard"))

    file = request.files["file"]

    if file.filename == "":
        return redirect(url_for("dashboard"))

    filepath = os.path.join(user_folder, file.filename)
    file.save(filepath)

    generate_thumbnail(filepath, file.filename)

    return redirect(url_for("dashboard"))

@app.route("/uploads/<filename>")
def uploaded_file(filename):

    if not require_login():
        return redirect(url_for("login"))

    user, user_folder = get_user_folder()
    return send_from_directory(user_folder, filename)

@app.route("/open_word/<filename>")
def open_word(filename):

    if not require_login():
        return redirect(url_for("login"))

    import subprocess
    import time
    import win32gui
    import win32con

    user, user_folder = get_user_folder()
    filepath = os.path.abspath(os.path.join(user_folder, filename))

    try:
        # Open file with Word
        subprocess.Popen(['start', '', filepath], shell=True)

        # Wait a bit for Word to launch
        time.sleep(1.5)

        # Bring Word window to front
        def enum_handler(hwnd, _):
            if "Word" in win32gui.GetWindowText(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                win32gui.SetForegroundWindow(hwnd)

        win32gui.EnumWindows(enum_handler, None)

    except Exception as e:
        print("Error opening Word:", e)

    return redirect(url_for("dashboard"))

@app.route("/open_ppt/<filename>")
def open_ppt(filename):

    if not require_login():
        return redirect(url_for("login"))

    import subprocess
    import time
    import win32gui
    import win32con

    user, user_folder = get_user_folder()
    filepath = os.path.abspath(os.path.join(user_folder, filename))

    try:
        # Open file with PowerPoint
        subprocess.Popen(['start', '', filepath], shell=True)

        # Wait for PowerPoint to launch
        time.sleep(1.5)

        # Bring PowerPoint window to front
        def enum_handler(hwnd, _):
            title = win32gui.GetWindowText(hwnd)

            if "PowerPoint" in title:
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                win32gui.SetForegroundWindow(hwnd)

        win32gui.EnumWindows(enum_handler, None)

    except Exception as e:
        print("Error opening PowerPoint:", e)

    return redirect(url_for("dashboard"))

@app.route("/open_pdf/<filename>")
def open_pdf(filename):

    if not require_login():
        return redirect(url_for("login"))

    import subprocess
    import time
    import win32gui
    import win32con

    user, user_folder = get_user_folder()
    filepath = os.path.abspath(os.path.join(user_folder, filename))

    try:
        # Open PDF using Microsoft Edge
        subprocess.Popen(['start', 'msedge', filepath], shell=True)

        # Wait for Edge to launch
        time.sleep(1.5)

        # Bring Edge window to front
        def enum_handler(hwnd, _):
            title = win32gui.GetWindowText(hwnd)

            if "Microsoft Edge" in title:
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                win32gui.SetForegroundWindow(hwnd)

        win32gui.EnumWindows(enum_handler, None)

    except Exception as e:
        print("Error opening PDF:", e)

    return redirect(url_for("dashboard"))

@app.route("/open_video/<filename>")
def open_video(filename):

    if not require_login():
        return redirect(url_for("login"))

    import subprocess
    import time
    import win32gui
    import win32con

    user, user_folder = get_user_folder()
    filepath = os.path.abspath(os.path.join(user_folder, filename))

    try:
        # Open video using VLC
        subprocess.Popen(['start', 'vlc', filepath], shell=True)

        # Wait for VLC to launch
        time.sleep(1.5)

        # Bring VLC window to front
        def enum_handler(hwnd, _):
            title = win32gui.GetWindowText(hwnd)

            if "VLC media player" in title:
                win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
                win32gui.SetForegroundWindow(hwnd)

        win32gui.EnumWindows(enum_handler, None)

    except Exception as e:
        print("Error opening video:", e)

    return redirect(url_for("dashboard"))

# ================================
# DASHBOARD
# ================================
@app.route("/dashboard")
def dashboard():

    if not require_login():
        return redirect(url_for("login"))

    user, user_folder = get_user_folder()

    deleted_file = request.args.get("deleted")
    favorited = request.args.get("favorited")
    undone = request.args.get("undone")
    current_view = request.args.get("view", "allfiles")
    last_favorite = session.get("last_favorite")

    user_trash = trash.get(user, set())

    files = []
    trashed_files = []
    total_size_bytes = 0
    files_this_week = 0
    favorites_this_week = 0
    trash_this_week = 0

    for file in sorted(os.listdir(user_folder), key=lambda x: os.path.getmtime(os.path.join(user_folder, x)),
                       reverse=True):

        # 🚫 Ignore Word temporary files
        if file.startswith("~$"):
            continue

        filepath = os.path.join(user_folder, file)

        generate_thumbnail(filepath, file)

        # ✅ ADD THIS LINE
        file_date = os.path.getmtime(filepath)

        from datetime import datetime, timedelta

        is_this_week = (
                datetime.now() - datetime.fromtimestamp(file_date)
                <= timedelta(days=7)
        )

        # count total files this week (exclude trash)
        if is_this_week and file not in user_trash:
            files_this_week += 1

        # count favorites this week
        if is_this_week and file in favorites.get(user, set()):
            favorites_this_week += 1

        # count trash this week
        if is_this_week and file in user_trash:
            trash_this_week += 1

        # format date
        from datetime import datetime
        formatted_date = time_ago(file_date)

        # file size in KB
        size_bytes = os.path.getsize(filepath)
        total_size_bytes += size_bytes

        if size_bytes < 1024 * 1024:
            file_size = f"{size_bytes / 1024:.1f} KB"
        else:
            file_size = f"{size_bytes / (1024 * 1024):.2f} MB"

        file_data = {
            "name": file,
            "date": file_date,
            "modified": formatted_date,
            "size": file_size,
            "size_bytes": size_bytes  # ✅ ADD THIS
        }

        if file in user_trash:
            trashed_files.append(file_data)
        else:
            files.append(file_data)

    # ================= STORAGE CALCULATION =================
    total_size_gb = total_size_bytes / (1024 * 1024 * 1024)

    max_storage_gb = 1

    storage_percent = (
        (total_size_gb / max_storage_gb) * 100
        if max_storage_gb > 0 else 0
    )

    # Recent files
    recent_files = sorted(
        files,
        key=lambda x: x["date"],
        reverse=True
    )

    # Favorites
    favorite_files = [
        f for f in favorites.get(user, [])
        if f not in user_trash and os.path.exists(os.path.join(user_folder, f))
    ]

    # get user profile data
    user_doc = db.collection("users").document(user).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    return render_template(
        "dashboard.html",
        files=files,
        trashed_files=trashed_files,
        favorites=favorites.get(user, set()),
        preview_thumb=preview_thumb,
        recent_files=recent_files,
        deleted_file=deleted_file,
        favorited=favorited,
        undone=undone,
        current_view=current_view,
        last_favorite=last_favorite,
        profile=user_data,
        total_size_gb=round(total_size_gb, 2),
        storage_percent=round(storage_percent, 1),

        # ✅ THESE MUST EXIST
        files_this_week=files_this_week,
        favorites_this_week=favorites_this_week,
        trash_this_week=trash_this_week,
    )

@app.route("/favorites")
def favorites_page():

    if not require_login():
        return redirect(url_for("login"))

    user, user_folder = get_user_folder()
    user_trash = trash.get(user, set())

    favorite_list = []

    for file in favorites.get(user, set()):

        filepath = os.path.join(user_folder, file)

        if os.path.exists(filepath) and file not in user_trash:

            generate_thumbnail(filepath, file)

            file_date = os.path.getmtime(filepath)

            from datetime import datetime
            formatted_date = time_ago(file_date)
            size_bytes = os.path.getsize(filepath)

            if size_bytes < 1024 * 1024:
                file_size = f"{size_bytes / 1024:.1f} KB"
            else:
                file_size = f"{size_bytes / (1024 * 1024):.2f} MB"

            favorite_list.append({
                "name": file,
                "date": file_date,
                "modified": formatted_date,
                "size": file_size,
                "size_bytes": size_bytes  # ✅ ADD THIS
            })

    user_doc = db.collection("users").document(user).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    # ================= STORAGE FIX =================
    total_size_bytes = 0

    for file in os.listdir(user_folder):
        filepath = os.path.join(user_folder, file)

        if os.path.exists(filepath):
            total_size_bytes += os.path.getsize(filepath)

    total_size_gb = total_size_bytes / (1024 * 1024 * 1024)

    max_storage_gb = 2

    storage_percent = (total_size_gb / max_storage_gb) * 100 if max_storage_gb > 0 else 0

    return render_template(
        "dashboard.html",
        files=favorite_list,
        trashed_files=[],
        favorites=favorites.get(user, set()),
        preview_thumb=preview_thumb,
        recent_files=favorite_list,
        current_view="favorites",
        profile=user_data,

        total_size_gb=round(total_size_gb, 2),
        storage_percent=round(storage_percent, 1),

        files_this_week=0,
        favorites_this_week=len(favorite_list),
        trash_this_week=0,
    )

@app.route("/trash")
def trash_page():

    if not require_login():
        return redirect(url_for("login"))

    user, user_folder = get_user_folder()
    user_trash = trash.get(user, set())

    trash_list = []

    for file in user_trash:

        filepath = os.path.join(user_folder, file)

        if os.path.exists(filepath):

            generate_thumbnail(filepath, file)

            file_date = os.path.getmtime(filepath)

            from datetime import datetime
            formatted_date = time_ago(file_date)
            size_bytes = os.path.getsize(filepath)

            if size_bytes < 1024 * 1024:
                file_size = f"{size_bytes / 1024:.1f} KB"
            else:
                file_size = f"{size_bytes / (1024 * 1024):.2f} MB"

            trash_list.append({
                "name": file,
                "date": file_date,
                "modified": formatted_date,
                "size": file_size,
                "size_bytes": size_bytes
            })

    user_doc = db.collection("users").document(user).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}

    # ================= STORAGE FIX =================
    total_size_bytes = 0

    for file in os.listdir(user_folder):
        filepath = os.path.join(user_folder, file)

        if os.path.exists(filepath):
            total_size_bytes += os.path.getsize(filepath)

    total_size_gb = total_size_bytes / (1024 * 1024 * 1024)

    max_storage_gb = 2

    storage_percent = (total_size_gb / max_storage_gb) * 100 if max_storage_gb > 0 else 0

    return render_template(
        "dashboard.html",
        files=trash_list,
        trashed_files=trash_list,
        favorites=favorites.get(user, set()),
        preview_thumb=preview_thumb,
        recent_files=trash_list,
        current_view="trash",
        profile=user_data,

        total_size_gb=round(total_size_gb, 2),
        storage_percent=round(storage_percent, 1),

        files_this_week=0,
        favorites_this_week=0,
        trash_this_week=len(trash_list),
    )

@app.route("/move_to_trash/<filename>")
def move_to_trash(filename):

    if not require_login():
        return redirect(url_for("login"))

    user = session["username"]

    if user not in trash:
        trash[user] = set()

    trash[user].add(filename)

    # also remove from favorites if needed
    if user in favorites and filename in favorites[user]:
        favorites[user].remove(filename)

    return redirect(url_for("dashboard"))

@app.route("/restore/<filename>")
def restore_file(filename):

    if not require_login():
        return redirect(url_for("login"))

    user = session["username"]

    if user in trash and filename in trash[user]:
        trash[user].remove(filename)

    return redirect(url_for("trash_page"))


@app.route("/delete_permanently/<filename>")
def delete_permanently(filename):

    if not require_login():
        return redirect(url_for("login"))

    user, user_folder = get_user_folder()

    filepath = os.path.join(user_folder, filename)

    # remove from trash
    if user in trash and filename in trash[user]:
        trash[user].remove(filename)

    # remove from favorites
    if user in favorites and filename in favorites[user]:
        favorites[user].remove(filename)

    # delete file from disk
    if os.path.exists(filepath):
        os.remove(filepath)

    return redirect(url_for("trash_page"))

@app.route("/favorite/<filename>")
def toggle_favorite(filename):

    if not require_login():
        return redirect(url_for("login"))

    user = session["username"]

    if user not in favorites:
        favorites[user] = set()

    if filename in favorites[user]:
        favorites[user].remove(filename)
    else:
        favorites[user].add(filename)

    return redirect(url_for("dashboard"))

@app.route("/profile", methods=["GET", "POST"])
def profile():

    if not require_login():
        return redirect(url_for("login"))

    username = session["username"]
    user_ref = db.collection("users").document(username)
    user_doc = user_ref.get()

    if not user_doc.exists:
        flash("User not found. Please login again.")
        session.clear()
        return redirect(url_for("login"))

    user_data = user_doc.to_dict()

    # ======================
    # HANDLE PROFILE UPDATE (FIXED)
    # ======================
    if request.method == "POST":

        new_username = request.form.get("username")
        new_email = request.form.get("email")
        new_password = request.form.get("password")

        updated = False
        sensitive_changed = False  # 🔥 controls email sending
        current_user = session["username"]

        # ======================
        # USERNAME CHANGE
        # ======================
        if new_username and new_username != current_user:

            if db.collection("users").document(new_username).get().exists:
                flash("Username already taken.")
                return redirect(url_for("profile"))

            # copy existing data
            updated_data = user_data.copy()
            updated_data["username"] = new_username

            # apply new values if provided
            if new_email:
                updated_data["email"] = new_email
            if new_password:
                updated_data["password"] = new_password

            # create new document
            db.collection("users").document(new_username).set(updated_data)

            # ======================
            # 🔥 MOVE USER FILES (FIX)
            # ======================
            old_folder = os.path.join(app.config["UPLOAD_FOLDER"], current_user)
            new_folder = os.path.join(app.config["UPLOAD_FOLDER"], new_username)

            if os.path.exists(old_folder):
                os.rename(old_folder, new_folder)

            # delete old document
            db.collection("users").document(current_user).delete()

            # update session + references
            session["username"] = new_username
            current_user = new_username
            user_ref = db.collection("users").document(new_username)

            updated = True
            sensitive_changed = True

        # ======================
        # EMAIL CHANGE
        # ======================
        if new_email and new_email != user_data["email"]:
            user_ref.update({
                "email": new_email
            })
            updated = True
            sensitive_changed = True

        # ======================
        # PASSWORD CHANGE
        # ======================
        if new_password and new_password != user_data["password"]:
            user_ref.update({
                "password": new_password
            })
            updated = True
            sensitive_changed = True

        # ======================
        # PROFILE PICTURE
        # ======================
        if "profile_pic" in request.files:

            file = request.files["profile_pic"]

            if file and file.filename != "":
                pic_folder = os.path.join("static", "profile_pics")
                os.makedirs(pic_folder, exist_ok=True)

                filepath = os.path.join(pic_folder, f"{current_user}.png")
                file.save(filepath)

                import time

                user_ref.update({
                    "profile_pic": f"profile_pics/{current_user}.png",
                    "updated_at": time.time()  # 🔥 forces image refresh
                })

                updated = True

        # ======================
        # FINALIZE UPDATE (QR + EMAIL)
        # ======================
        if updated:

            # 🔥 ONLY send email if sensitive info changed
            if sensitive_changed:
                updated_doc = user_ref.get().to_dict()

                qr_folder = os.path.join("static", "qrcodes")
                os.makedirs(qr_folder, exist_ok=True)

                qr = qrcode.make(f"LOGIN:{current_user}:{updated_doc['password']}")
                qr_path = os.path.join(qr_folder, f"{current_user}.png")
                qr.save(qr_path)

                send_qr_email(
                    updated_doc["email"],
                    current_user,
                    updated_doc["password"]
                )

            flash("Profile updated successfully.")
            return redirect(url_for("profile"))

    # ======================
    # DISPLAY DATA
    # ======================
    full_name = f"{user_data.get('first_name', '')} {user_data.get('surname', '')}"

    return render_template(
        "profile.html",
        profile=user_data,
        full_name=full_name
    )

def send_qr_email(receiver_email, username, password):

    qr_path = os.path.join("static", "qrcodes", f"{username}.png")

    sender_email = "progesture4410@gmail.com"
    sender_password = "iaihvxuaikizysei"

    msg = EmailMessage()
    msg["Subject"] = "Your ProGesture QR Login Code"
    msg["From"] = sender_email
    msg["To"] = receiver_email

    msg.set_content(f"""
    Hello {username},

    Your ProGesture account details:

    Username: {username}
    Password: {password}

    You may use the QR code attached OR manually enter your credentials.

    If this was a password reset, please change your password after logging in.

    Regards,
    ProGesture Team
    """)

    with open(qr_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="image",
            subtype="png",
            filename=f"{username}_qr.png"
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender_email, sender_password)
        smtp.send_message(msg)

def time_ago(timestamp):
    now = datetime.now()
    diff = now - datetime.fromtimestamp(timestamp)

    seconds = diff.total_seconds()

    if seconds < 60:
        return "Just now"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes} min ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    elif seconds < 604800:
        days = int(seconds / 86400)
        return f"{days} day{'s' if days > 1 else ''} ago"
    else:
        return datetime.fromtimestamp(timestamp).strftime("%d %b %Y")

# ================================
# RUN APP
# ================================
import os

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
