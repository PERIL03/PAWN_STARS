import os
import json
from base64 import b64encode
from flask import Flask, request, render_template, send_file, jsonify
from werkzeug.utils import secure_filename
from encode import encode
from decode import decode
app = Flask(__name__, template_folder="templates", static_folder="static")

# Set maximum file size to 16 MB
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "outputs"
ALLOWED_EXTENSIONS = {'txt', 'png', 'jpg', 'jpeg', 'pgn'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_safe_filename(filename):
    filename = os.path.basename(filename)
    return secure_filename(filename)

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

@app.route("/visualizer")
def visualizer():
    return render_template("visualizer.html")

@app.route('/preview', methods=["GET", "POST"])
def preview():
    if request.method == "GET":
        # Render the preview template when accessed via GET
        return render_template("preview.html")
    else:
        # Handle POST request for file preview
        try:
            if 'file' not in request.files:
                return jsonify({"error": "No file part"}), 400
            
            file = request.files['file']
            if file.filename == '':
                return jsonify({"error": "No selected file"}), 400
            
            file_type = request.form.get("file_type")
            if not file_type or file_type not in ['text', 'image']:
                return jsonify({"error": "Invalid file type"}), 400
            
            # Read file data
            file_data = file.read()
            if len(file_data) == 0:
                return jsonify({"error": "File is empty"}), 400
                
            # For preview, we'll return some basic info and a sample of the binary data
            file_info = {
                "filename": file.filename,
                "size": len(file_data),
                "file_type": file_type,
                "bit_count": len(file_data) * 8,
                # Send back a base64 sample for the UI to show
                "data_sample": b64encode(file_data[:1024] if len(file_data) > 1024 else file_data).decode('utf-8')
            }
            
            return jsonify({"success": True, "file_info": file_info})
            
        except Exception as e:
            app.logger.error(f"Preview encoding error: {str(e)}", exc_info=True)
            return jsonify({"error": f"Error processing file: {str(e)}"}), 500

@app.route("/encode", methods=["POST"])
def handle_encode():
    try:
        app.logger.debug("Starting encode request")
        
        if 'file' not in request.files:
            app.logger.error("No file part in request")
            return jsonify({"error": "No file part"}), 400
        
        file = request.files['file']
        app.logger.debug(f"Received file: {file.filename}")
        
        if file.filename == '':
            app.logger.error("No selected file")
            return jsonify({"error": "No selected file"}), 400
        
        file_type = request.form.get("file_type")
        app.logger.debug(f"File type: {file_type}")
        
        # Get self-destruct timer from form (in seconds)
        self_destruct_timer = request.form.get("self_destruct_timer")
        if self_destruct_timer:
            try:
                self_destruct_timer = int(self_destruct_timer)
                app.logger.debug(f"Self-destruct timer set to: {self_destruct_timer} seconds")
            except ValueError:
                app.logger.error(f"Invalid self-destruct timer value: {self_destruct_timer}")
                return jsonify({"error": "Invalid self-destruct timer value"}), 400
        else:
            self_destruct_timer = None
            app.logger.debug("No self-destruct timer provided")
        
        # Get custom PGN headers from form
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
        
        if not file_type or file_type not in ['text', 'image']:
            app.logger.error(f"Invalid file type: {file_type}")
            return jsonify({"error": "Invalid file type"}), 400
        
        if file and allowed_file(file.filename):
            filename = get_safe_filename(file.filename)
            app.logger.debug(f"Safe filename: {filename}")

            input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            app.logger.debug(f"Saving to: {input_path}")
            file.save(input_path)

            output_path = os.path.join(app.config['OUTPUT_FOLDER'], "encoded_output.pgn")
            app.logger.debug(f"Output path: {output_path}")

            try:
                # Pass the self-destruct timer and custom headers to the encode function
                encode(input_path, output_path, self_destruct_timer, custom_headers if custom_headers else None)
                app.logger.debug("Encoding completed")
            except Exception as e:
                app.logger.error(f"Encoding failed: {str(e)}", exc_info=True)
                return jsonify({"error": f"Encoding failed: {str(e)}"}), 500

            if not os.path.exists(output_path):
                app.logger.error("Output file was not created")
                return jsonify({"error": "Output file was not created"}), 500

            app.logger.debug("Sending encoded file")
            return send_file(output_path, as_attachment=True, download_name="encoded_output.pgn")
        
        app.logger.error("File type not allowed")
        return jsonify({"error": "File type not allowed"}), 400

    except Exception as e:
        app.logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@app.route("/decode", methods=["POST"])
def handle_decode():
    try:
        app.logger.debug("Starting decode request")
        
        if 'file' not in request.files:
            app.logger.error("No file part in request")
            return jsonify({"error": "No file part"}), 400
        
        file = request.files['file']
        app.logger.debug(f"Received file: {file.filename}")

        if file.filename == '':
            app.logger.error("No selected file")
            return jsonify({"error": "No selected file"}), 400

        file_type = request.form.get("file_type")
        app.logger.debug(f"File type: {file_type}")
        
        if not file_type or file_type not in ['text', 'image']:
            app.logger.error(f"Invalid file type: {file_type}")
            return jsonify({"error": "Invalid file type"}), 400

        if file and allowed_file(file.filename):
            filename = get_safe_filename(file.filename)
            app.logger.debug(f"Safe filename: {filename}")
            
            input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            app.logger.debug(f"Saving to: {input_path}")
            file.save(input_path)

            output_extension = "txt" if file_type == "text" else "png"
            output_path = os.path.join(app.config['OUTPUT_FOLDER'], f"decoded_output.{output_extension}")
            app.logger.debug(f"Output path: {output_path}")
            
            try:
                decode(input_path, output_path)
                app.logger.debug("Decoding completed")
            except Exception as e:
                app.logger.error(f"Decoding failed: {str(e)}", exc_info=True)
                return jsonify({"error": f"Decoding failed: {str(e)}"}), 500
            
            if not os.path.exists(output_path):
                app.logger.error("Output file was not created")
                return jsonify({"error": "Output file was not created"}), 500
                
            app.logger.debug("Sending decoded file")
            return send_file(output_path, as_attachment=True, 
                             download_name=f"decoded_output.{output_extension}")
        
        app.logger.error("File type not allowed")
        return jsonify({"error": "File type not allowed"}), 400

    except Exception as e:
        app.logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500
        
@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "File is too large. Maximum file size is 16 MB"}), 413

if __name__ == "__main__":
    app.run(debug=True)