import os
import re
import json
import base64
import requests
from flask import Flask, request, jsonify
from PIL import Image
from io import BytesIO

app = Flask(__name__)

OCR_API_KEY = os.environ.get("OCR_KEY", "K83152116788957")

D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
     [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
     [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
     [9,8,7,6,5,4,3,2,1,0]]
P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
     [8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
     [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]

def verhoeff_validate(number):
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = D[c][P[i % 8][int(digit)]]
    return c == 0

def extract_aadhaar_numbers(text):
    clean = re.sub(r'[^0-9]', '', text)
    found = []
    for i in range(len(clean) - 11):
        candidate = clean[i:i+12]
        if (len(candidate) == 12 and
            candidate[0] not in ('0','1') and
            verhoeff_validate(candidate) and
            candidate not in found):
            found.append(candidate)
    return found

def compress_image(image_bytes):
    image = Image.open(BytesIO(image_bytes))
    image.load()
    if image.mode != 'RGB':
        image = image.convert('RGB')
    if image.width > 1200:
        ratio = 1200 / image.width
        image = image.resize((1200, int(image.height * ratio)), Image.LANCZOS)
    if image.height > 1600:
        ratio = 1600 / image.height
        image = image.resize((int(image.width * ratio), 1600), Image.LANCZOS)
    quality = 85
    output  = BytesIO()
    while quality >= 40:
        output = BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        if output.tell() / 1024 <= 700:
            break
        quality -= 10
    output.seek(0)
    return output.read()

def run_ocr(compressed_bytes):
    img_b64 = base64.b64encode(compressed_bytes).decode("utf-8")
    params  = {
        "apikey":            OCR_API_KEY,
        "base64Image":       "data:image/jpeg;base64," + img_b64,
        "language":          "eng",
        "isOverlayRequired": "false",
        "detectOrientation": "true",
        "scale":             "true",
        "filetype":          "jpg",
        "OCREngine":         "1"
    }
    response = requests.post("https://api.ocr.space/parse/image", data=params, timeout=30)
    try:
        result = response.json()
        return result if isinstance(result, dict) else {"IsErroredOnProcessing": True}
    except:
        return {"IsErroredOnProcessing": True}

def get_image_bytes():
    """
    Deluge sends file as form field string — not as file upload.
    form_keys=['aadhaar_number', 'file'], file_keys=[]
    So read from request.form.get("file")
    """
    # ── Try form string field (what Deluge actually sends) ────
    for key in ["file", "Upload_Aadhaar", "upload_aadhaar", "image"]:
        val = request.form.get(key, "")
        if val:
            # Could be raw bytes string or base64
            try:
                # Try decoding as base64 first
                decoded = base64.b64decode(val)
                return decoded
            except:
                # Try as raw string bytes
                try:
                    return val.encode('latin-1')
                except:
                    pass

    # ── Try request.files just in case ───────────────────────
    for key in ["file", "Upload_Aadhaar", "upload_aadhaar", "image"]:
        f = request.files.get(key)
        if f:
            return f.read()

    if request.files:
        return list(request.files.values())[0].read()

    return None

def get_aadhaar_number():
    for key in ["aadhaar_number", "Aadhaar_Number", "aadhaar"]:
        val = request.form.get(key, "")
        if val:
            return val.replace(" ","").replace("-","")
    return ""


@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Aadhaar OCR"})


@app.route('/debug', methods=['POST'])
def debug():
    file_val = request.form.get("file","")
    return jsonify({
        "form_keys":        list(request.form.keys()),
        "file_keys":        list(request.files.keys()),
        "file_val_length":  len(file_val),
        "file_val_preview": file_val[:100] if file_val else "",
        "aadhaar_number":   request.form.get("aadhaar_number",""),
        "content_type":     request.content_type
    })


@app.route('/verify', methods=['POST'])
def verify():
    try:
        aadhaar_number = get_aadhaar_number()
        image_bytes    = get_image_bytes()

        if not aadhaar_number:
            return jsonify({"success":False,"match":False,"message":"aadhaar_number required"}), 400
        if len(aadhaar_number) != 12 or not aadhaar_number.isdigit():
            return jsonify({"success":False,"match":False,"message":"Must be 12 digits"}), 400
        if not verhoeff_validate(aadhaar_number):
            return jsonify({"success":False,"match":False,"message":"Invalid Aadhaar checksum"}), 400
        if not image_bytes:
            return jsonify({"success":False,"match":False,"message":"file required"}), 400

        try:
            compressed = compress_image(image_bytes)
            del image_bytes
        except Exception as e:
            return jsonify({"success":False,"match":False,"message":"Compression failed: "+str(e)}), 500

        ocr_data = run_ocr(compressed)
        del compressed

        if ocr_data.get("IsErroredOnProcessing"):
            return jsonify({"success":False,"match":False,"message":"OCR error: "+str(ocr_data.get("ErrorMessage",""))}), 200

        parsed_results = ocr_data.get("ParsedResults", [])
        if not parsed_results:
            return jsonify({"success":False,"match":False,"message":"No text detected"}), 200

        full_text     = " ".join([r.get("ParsedText","") for r in parsed_results if isinstance(r, dict)])
        numbers_found = extract_aadhaar_numbers(full_text)
        match         = aadhaar_number in numbers_found

        return jsonify({
            "success":       True,
            "match":         match,
            "numbers_found": numbers_found,
            "entered":       aadhaar_number,
            "message":       "Match found" if match else "Number not found on card"
        })

    except Exception as e:
        return jsonify({"success":False,"match":False,"message":"Server error: "+str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
