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

def extract_aadhaar_numbers(text):
    clean = re.sub(r'[^0-9]', '', text)
    found = []
    for i in range(len(clean) - 11):
        candidate = clean[i:i+12]
        if (len(candidate) == 12 and
            candidate[0] not in ('0','1') and
            candidate not in found):
            found.append(candidate)
    return found, clean

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

def is_match(entered, numbers_found, clean_ocr_text):
    # Check 1: exact match in extracted numbers
    if entered in numbers_found:
        return True

    # Check 2: direct substring in cleaned OCR digits
    if entered in clean_ocr_text:
        return True

    return False


@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Aadhaar OCR"})


@app.route('/verify', methods=['POST'])
def verify():
    try:
        aadhaar_number = request.args.get("aadhaar_number", "").replace(" ","").replace("-","")

        uploaded_file = (
            request.files.get("content") or
            request.files.get("file") or
            request.files.get("Upload_Aadhaar") or
            (list(request.files.values())[0] if request.files else None)
        )

        if not aadhaar_number:
            return jsonify({"success":False,"match":False,
                "message":"aadhaar_number missing"}), 400
        if len(aadhaar_number) != 12 or not aadhaar_number.isdigit():
            return jsonify({"success":False,"match":False,
                "message":"Must be 12 digits. got="+aadhaar_number}), 400
        if not uploaded_file:
            return jsonify({"success":False,"match":False,
                "message":"file missing"}), 400

        image_bytes = uploaded_file.read()
        if not image_bytes or len(image_bytes) < 100:
            return jsonify({"success":False,"match":False,
                "message":"File empty"}), 400

        try:
            compressed = compress_image(image_bytes)
            del image_bytes
        except Exception as e:
            return jsonify({"success":False,"match":False,
                "message":"Compression failed: "+str(e)}), 500

        ocr_data = run_ocr(compressed)
        del compressed

        if ocr_data.get("IsErroredOnProcessing"):
            return jsonify({"success":False,"match":False,
                "message":"OCR error: "+str(ocr_data.get("ErrorMessage",""))}), 200

        parsed_results = ocr_data.get("ParsedResults", [])
        if not parsed_results:
            return jsonify({"success":False,"match":False,
                "message":"No text detected"}), 200

        full_text = " ".join([r.get("ParsedText","") for r in parsed_results if isinstance(r, dict)])

        if not full_text.strip():
            return jsonify({"success":False,"match":False,
                "message":"No text detected"}), 200

        numbers_found, clean_ocr = extract_aadhaar_numbers(full_text)
        match = is_match(aadhaar_number, numbers_found, clean_ocr)

        return jsonify({
            "success":       True,
            "match":         match,
            "numbers_found": numbers_found,
            "entered":       aadhaar_number,
            "message":       "Match found" if match else "Number not found on card"
        })

    except Exception as e:
        return jsonify({"success":False,"match":False,
            "message":"Server error: "+str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
