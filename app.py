import os
import json
import gzip
import uuid
import shutil
import time
from base64 import b64encode
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib import error as urllib_error
from flask import Flask, request, render_template, send_file, jsonify, after_this_request
from werkzeug.utils import secure_filename
from encode import encode, RUST_ENGINE_AVAILABLE as ENCODE_RUST_AVAILABLE
from decode import decode
app = Flask(__name__, template_folder="templates", static_folder="static")

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "outputs"
ALLOWED_EXTENSIONS = {'txt', 'png', 'jpg', 'jpeg', 'pgn', 'gz'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
MAX_PGN_HEADER_LENGTH = 256
STALE_FILE_RETENTION_SECONDS = int(os.getenv("ROOKHIDE_FILE_RETENTION_SECONDS", "21600"))
STALE_CLEANUP_INTERVAL_SECONDS = int(os.getenv("ROOKHIDE_CLEANUP_INTERVAL_SECONDS", "300"))
_LAST_STALE_CLEANUP = 0.0


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_safe_filename(filename: str) -> str:
    filename = os.path.basename(filename)
    return secure_filename(filename)


def safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        app.logger.warning("Could not remove file %s: %s", path, str(exc))


def cleanup_stale_files(folder: str, older_than_seconds: int) -> None:
    now = time.time()
    removed_count = 0
    try:
        for entry in os.scandir(folder):
            if not entry.is_file():
                continue
            age_seconds = now - entry.stat().st_mtime
            if age_seconds > older_than_seconds:
                safe_remove(entry.path)
                removed_count += 1
    except FileNotFoundError:
        return
    except OSError as exc:
        app.logger.warning("Could not scan folder %s for stale files: %s", folder, str(exc))
        return

    if removed_count:
        app.logger.info("Removed %d stale files from %s", removed_count, folder)


def maybe_cleanup_stale_artifacts() -> None:
    global _LAST_STALE_CLEANUP

    now = time.time()
    if now - _LAST_STALE_CLEANUP < STALE_CLEANUP_INTERVAL_SECONDS:
        return

    cleanup_stale_files(app.config['UPLOAD_FOLDER'], STALE_FILE_RETENTION_SECONDS)
    cleanup_stale_files(app.config['OUTPUT_FOLDER'], STALE_FILE_RETENTION_SECONDS)
    _LAST_STALE_CLEANUP = now


def sanitize_processing_error(message: str, action: str) -> str:
    text = (message or "").strip()
    safe_prefixes = (
        "Password is required",
        "Invalid password",
        "This file has expired",
        "Input PGN file is",
        "No valid chess games found",
        "Metadata carrier",
        "Metadata authentication failed",
        "Metadata integrity check failed",
        "Unsupported metadata",
        "Unsupported payload",
        "Failed to decompress payload",
        "Invalid payload",
        "Unsupported payload envelope",
        "Corrupted encryption envelope",
    )
    if any(text.startswith(prefix) for prefix in safe_prefixes):
        return text

    if action == "encode":
        return "Unable to encode file with the provided options"
    return "Unable to decode file. Verify file integrity and password"


def build_unique_path(folder: str, base_filename: str, forced_ext: str | None = None) -> str:
    safe_name = get_safe_filename(base_filename) or "upload.bin"
    stem, ext = os.path.splitext(safe_name)
    if forced_ext is not None:
        ext = forced_ext
    unique_name = f"{stem}_{uuid.uuid4().hex}{ext}"
    return os.path.join(folder, unique_name)


def validate_upload(file, action: str, file_type: str) -> tuple[bool, str]:
    if file is None or file.filename == '':
        return False, "No selected file"

    if file_type not in ['text', 'image']:
        return False, "Invalid file type"

    safe_name = get_safe_filename(file.filename)
    if not safe_name:
        return False, "Invalid filename"

    if not allowed_file(safe_name):
        return False, "File type not allowed"

    ext = safe_name.rsplit('.', 1)[1].lower()
    if action == "encode":
        allowed_by_type = {
            "text": {"txt"},
            "image": {"png", "jpg", "jpeg"},
        }
        if ext not in allowed_by_type[file_type]:
            return False, f"Invalid extension .{ext} for {file_type} file"
    elif action == "decode":
        is_pgn = ext == "pgn"
        is_pgn_gz = ext == "gz" and safe_name.lower().endswith(".pgn.gz")
        if not (is_pgn or is_pgn_gz):
            return False, "Decode only accepts .pgn or .pgn.gz files"

    return True, safe_name


def validate_self_destruct_timer(value: str | None) -> tuple[bool, int | None, str | None]:
    if not value:
        return True, None, None
    try:
        timer = int(value)
    except ValueError:
        return False, None, "Invalid self-destruct timer value"

    if timer <= 0 or timer > 31_536_000:
        return False, None, "Self-destruct timer must be between 1 and 31536000 seconds"
    return True, timer, None


def validate_custom_headers(headers: dict[str, str]) -> tuple[bool, str | None]:
    for key, value in headers.items():
        if len(value) > MAX_PGN_HEADER_LENGTH:
            return False, f"Header {key} exceeds max length of {MAX_PGN_HEADER_LENGTH}"

        if key == "Date" and value:
            parts = value.split('.')
            if len(parts) != 3 or any(not p.isdigit() for p in parts):
                return False, "Date header must use YYYY.MM.DD format"

        if key in {"WhiteElo", "BlackElo"} and value:
            if not value.isdigit() or not (600 <= int(value) <= 3500):
                return False, f"{key} must be an integer between 600 and 3500"

        if key == "Result" and value:
            if value not in {"*", "1-0", "0-1", "1/2-1/2"}:
                return False, "Result header must be one of *, 1-0, 0-1, 1/2-1/2"

    return True, None


def validate_compression_level(value: str | None) -> tuple[bool, int | None, str | None]:
    if value is None or value == "":
        return True, None, None
    try:
        level = int(value)
    except ValueError:
        return False, None, "Compression level must be an integer between 0 and 9"
    if level < 0 or level > 9:
        return False, None, "Compression level must be between 0 and 9"
    return True, level, None


def validate_password(value: str | None) -> tuple[bool, str | None, str | None]:
    password = (value or "").strip()
    if not password:
        return True, None, None
    if len(password) < 8:
        return False, None, "Password must be at least 8 characters"
    if len(password) > 128:
        return False, None, "Password must be at most 128 characters"
    return True, password, None

@app.route("/")
def index():
    return render_template("home.html")

@app.route("/home")
def home():
    return render_template("home.html")

@app.route("/file_upload")
def file_upload():
    return render_template("file.html")

@app.route("/about")
def about():
    return render_template("About.html")

@app.route("/get_in_touch")
def get_in_touch():
    return render_template("touch.html")


@app.route("/contact", methods=["POST"])
def submit_contact():
    access_key = os.getenv("WEB3FORMS_ACCESS_KEY")
    if not access_key:
        return jsonify({"error": "Contact form is unavailable right now"}), 503

    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    message = (request.form.get("message") or "").strip()

    if not name or not email or not message:
        return jsonify({"error": "Name, email, and message are required"}), 400
    if len(name) > 120 or len(email) > 254 or len(message) > 5000:
        return jsonify({"error": "Contact form fields exceed allowed length"}), 400
    if "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "Invalid email address"}), 400

    payload = urllib_parse.urlencode({
        "access_key": access_key,
        "name": name,
        "email": email,
        "message": message,
    }).encode("utf-8")

    req = urllib_request.Request(
        "https://api.web3forms.com/submit",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("success"):
            return jsonify({"success": True, "message": "Message sent successfully"}), 200
        app.logger.error("Web3Forms rejected request: %s", data)
        return jsonify({"error": "Unable to send message right now"}), 502
    except (urllib_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        app.logger.error("Contact submission failed: %s", str(exc), exc_info=True)
        return jsonify({"error": "Unable to send message right now"}), 502

@app.route("/visualizer")
def visualizer():
    return render_template("visualizer.html")


@app.route("/health", methods=["GET"])
def health():
    free_mb = int(shutil.disk_usage(os.path.abspath(os.sep)).free / (1024 * 1024))
    status = "healthy"

    checks = {
        "status": status,
        "rust_engine_available": ENCODE_RUST_AVAILABLE,
        "disk_free_mb": free_mb,
    }

    if free_mb < 100:
        checks["status"] = "unhealthy"
        return jsonify(checks), 503

    return jsonify(checks), 200

@app.route('/preview', methods=["GET", "POST"])
def preview():
    if request.method == "GET":
        return render_template("preview.html")
    else:
        try:
            if 'file' not in request.files:
                return jsonify({"error": "No file part"}), 400
            
            file = request.files['file']
            if file.filename == '':
                return jsonify({"error": "No selected file"}), 400
            
            file_type = request.form.get("file_type")
            if not file_type or file_type not in ['text', 'image']:
                return jsonify({"error": "Invalid file type"}), 400
            
            file_data = file.read()
            if len(file_data) == 0:
                return jsonify({"error": "File is empty"}), 400
                
            file_info = {
                "filename": file.filename,
                "size": len(file_data),
                "file_type": file_type,
                "bit_count": len(file_data) * 8,
                "data_sample": b64encode(file_data[:1024] if len(file_data) > 1024 else file_data).decode('utf-8')
            }
            
            return jsonify({"success": True, "file_info": file_info})
            
        except Exception as e:
            app.logger.error(f"Preview encoding error: {str(e)}", exc_info=True)
            return jsonify({"error": "Error processing file"}), 500

@app.route("/encode", methods=["POST"])
def handle_encode():
    input_path = None
    output_path = None
    delivery_path = None
    try:
        maybe_cleanup_stale_artifacts()
        app.logger.debug("Starting encode request")
        
        if 'file' not in request.files:
            app.logger.error("No file part in request")
            return jsonify({"error": "No file part"}), 400
        
        file = request.files['file']
        app.logger.debug(f"Received file: {file.filename}")
        
        file_type = request.form.get("file_type")
        app.logger.debug(f"File type: {file_type}")

        is_valid, validation_result = validate_upload(file, "encode", file_type)
        if not is_valid:
            app.logger.error("Upload validation failed: %s", validation_result)
            return jsonify({"error": validation_result}), 400
        
        timer_ok, self_destruct_timer, timer_error = validate_self_destruct_timer(
            request.form.get("self_destruct_timer")
        )
        if not timer_ok:
            app.logger.error("Invalid self-destruct timer: %s", timer_error)
            return jsonify({"error": timer_error}), 400
        
        custom_headers = {}
        pgn_header_fields = [
            "Event", "Site", "Date", "Round", "White", "Black", 
            "WhiteElo", "BlackElo", "Result", "ECO"
        ]
        
        for field in pgn_header_fields:
            value = request.form.get(f"pgn_{field.lower()}")
            if value:
                custom_headers[field] = value
                app.logger.debug(f"Custom header {field}: {value}")

        headers_ok, headers_error = validate_custom_headers(custom_headers)
        if not headers_ok:
            app.logger.error("Invalid custom header payload: %s", headers_error)
            return jsonify({"error": headers_error}), 400

        compression_ok, compression_level, compression_error = validate_compression_level(
            request.form.get("compression_level")
        )
        if not compression_ok:
            app.logger.error("Invalid compression level: %s", compression_error)
            return jsonify({"error": compression_error}), 400

        password_ok, encryption_password, password_error = validate_password(
            request.form.get("encryption_password")
        )
        if not password_ok:
            app.logger.error("Invalid encryption password")
            return jsonify({"error": password_error}), 400

        engine_guided = (request.form.get("engine_guided") or "").strip().lower() in {"1", "true", "on", "yes"}
        opening_camouflage = (request.form.get("opening_camouflage") or "").strip().lower() in {"1", "true", "on", "yes"}
        metadata_payload = (request.form.get("metadata_payload") or "").strip() or None
        
        filename = validation_result
        app.logger.debug(f"Safe filename: {filename}")

        input_path = build_unique_path(app.config['UPLOAD_FOLDER'], filename)
        app.logger.debug(f"Saving to: {input_path}")
        file.save(input_path)

        if not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
            app.logger.error("Upload validation failed: File is empty")
            safe_remove(input_path)
            return jsonify({"error": "File is empty"}), 400

        output_path = build_unique_path(app.config['OUTPUT_FOLDER'], filename, forced_ext=".pgn")
        app.logger.debug(f"Output path: {output_path}")

        encode_meta = encode(
            input_path,
            output_path,
            self_destruct_timer,
            custom_headers if custom_headers else None,
            password=encryption_password,
            compression_level=compression_level,
            engine_guided=engine_guided,
            opening_camouflage=opening_camouflage,
            metadata_payload=metadata_payload,
            hide_technical_headers=True,
            metadata_carrier_style="whitespace",
        )
        app.logger.debug("Encoding completed")

        if not os.path.exists(output_path):
            app.logger.error("Output file was not created")
            return jsonify({"error": "Output file was not created"}), 500

        with open(output_path, "rb") as pgn_file:
            pgn_bytes = pgn_file.read()

        gzip_bytes = gzip.compress(pgn_bytes, compresslevel=9, mtime=0)
        requested_format = (request.form.get("download_format") or "pgn").strip().lower()
        requested_gzip = requested_format in {"gz", "gzip", "pgn.gz"}
        use_gzip_delivery = requested_gzip and (len(gzip_bytes) + 32 < len(pgn_bytes))
        send_path = output_path
        download_name = "encoded_output.pgn"
        delivery_bytes = len(pgn_bytes)

        if use_gzip_delivery:
            delivery_path = build_unique_path(app.config['OUTPUT_FOLDER'], "encoded_output.pgn", forced_ext=".pgn.gz")
            with open(delivery_path, "wb") as gz_file:
                gz_file.write(gzip_bytes)
            send_path = delivery_path
            download_name = "encoded_output.pgn.gz"
            delivery_bytes = len(gzip_bytes)

        safe_remove(input_path)

        @after_this_request
        def cleanup(response):
            safe_remove(output_path)
            if delivery_path and delivery_path != output_path:
                safe_remove(delivery_path)
            return response

        app.logger.debug("Sending encoded file")
        response = send_file(send_path, as_attachment=True, download_name=download_name)
        response.headers["X-Compression-Used"] = "true" if encode_meta.get("compression_used") else "false"
        response.headers["X-Source-Bytes"] = str(int(encode_meta.get("source_bytes", 0)))
        response.headers["X-Payload-Bytes"] = str(int(encode_meta.get("payload_bytes", 0)))
        response.headers["X-Payload-Ratio"] = f"{float(encode_meta.get('payload_ratio', 1.0)):.6f}"
        response.headers["X-Expansion-Bytes"] = str(int(encode_meta.get("expansion_bytes", 0)))
        response.headers["X-Expansion-Ratio"] = f"{float(encode_meta.get('expansion_ratio', 1.0)):.6f}"
        source_bytes = int(encode_meta.get("source_bytes", 0))
        response.headers["X-Delivery-Compressed"] = "true" if use_gzip_delivery else "false"
        response.headers["X-Delivery-Bytes"] = str(delivery_bytes)
        response.headers["X-Delivery-Ratio"] = f"{(delivery_bytes / source_bytes) if source_bytes else 1.0:.6f}"
        response.headers["X-Encryption-Used"] = "true" if encode_meta.get("encryption_used") else "false"
        response.headers["X-Compression-Level"] = str(int(encode_meta.get("compression_level", 9)))
        response.headers["X-Deterministic-Seed"] = "true" if encode_meta.get("deterministic_seed_mode") else "false"
        response.headers["X-Engine-Guided"] = "true" if encode_meta.get("engine_guided") else "false"
        response.headers["X-Opening-Camouflage"] = "true" if encode_meta.get("opening_camouflage") else "false"
        response.headers["X-Rust-Path"] = "true" if encode_meta.get("rust_path_used") else "false"
        return response

    except ValueError as exc:
        app.logger.error("Encoding failed: %s", str(exc), exc_info=True)
        safe_remove(input_path)
        safe_remove(output_path)
        safe_remove(delivery_path)
        return jsonify({"error": sanitize_processing_error(str(exc), "encode")}), 400
    except Exception:
        app.logger.error("Unexpected error during encode", exc_info=True)
        safe_remove(input_path)
        safe_remove(output_path)
        safe_remove(delivery_path)
        return jsonify({"error": "Internal server error"}), 500

@app.route("/decode", methods=["POST"])
def handle_decode():
    input_path = None
    output_path = None
    try:
        maybe_cleanup_stale_artifacts()
        app.logger.debug("Starting decode request")
        
        if 'file' not in request.files:
            app.logger.error("No file part in request")
            return jsonify({"error": "No file part"}), 400
        
        file = request.files['file']
        app.logger.debug(f"Received file: {file.filename}")

        file_type = request.form.get("file_type")
        app.logger.debug(f"File type: {file_type}")

        password_ok, encryption_password, password_error = validate_password(
            request.form.get("encryption_password")
        )
        if not password_ok:
            app.logger.error("Invalid decryption password")
            return jsonify({"error": password_error}), 400

        is_valid, validation_result = validate_upload(file, "decode", file_type)
        if not is_valid:
            app.logger.error("Upload validation failed: %s", validation_result)
            return jsonify({"error": validation_result}), 400

        filename = validation_result
        app.logger.debug(f"Safe filename: {filename}")

        input_path = build_unique_path(app.config['UPLOAD_FOLDER'], filename)
        app.logger.debug(f"Saving to: {input_path}")
        file.save(input_path)

        output_extension = "txt" if file_type == "text" else "bin"
        output_path = build_unique_path(app.config['OUTPUT_FOLDER'], f"decoded_output.{output_extension}")
        app.logger.debug(f"Output path: {output_path}")

        decode(input_path, output_path, password=encryption_password)
        app.logger.debug("Decoding completed")

        if not os.path.exists(output_path):
            app.logger.error("Output file was not created")
            return jsonify({"error": "Output file was not created"}), 500

        download_extension = output_extension
        if file_type == "image":
            with open(output_path, "rb") as decoded_file:
                magic = decoded_file.read(16)

            if magic.startswith(b"\x89PNG\r\n\x1a\n"):
                download_extension = "png"
            elif magic.startswith(b"\xff\xd8\xff"):
                download_extension = "jpg"
            elif magic.startswith(b"GIF87a") or magic.startswith(b"GIF89a"):
                download_extension = "gif"
            elif len(magic) >= 12 and magic[:4] == b"RIFF" and magic[8:12] == b"WEBP":
                download_extension = "webp"
            else:
                download_extension = "bin"

        safe_remove(input_path)

        @after_this_request
        def cleanup(response):
            safe_remove(output_path)
            return response

        app.logger.debug("Sending decoded file")
        return send_file(output_path, as_attachment=True,
                         download_name=f"decoded_output.{download_extension}")

    except ValueError as exc:
        app.logger.error("Decoding failed: %s", str(exc), exc_info=True)
        safe_remove(input_path)
        safe_remove(output_path)
        return jsonify({"error": sanitize_processing_error(str(exc), "decode")}), 400
    except Exception:
        app.logger.error("Unexpected error during decode", exc_info=True)
        safe_remove(input_path)
        safe_remove(output_path)
        return jsonify({"error": "Internal server error"}), 500
        
@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "File is too large. Maximum file size is 16 MB"}), 413

if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=debug_mode, host=host, port=port)