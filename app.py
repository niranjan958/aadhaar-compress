import os
import re
import base64
import requests
from flask import Flask, request, jsonify
from PIL import Image
from io import BytesIO

app = Flask(__name__)

OCR_API_KEY = os.environ.get("OCR_KEY", "YOUR_OCR_SPACE_API_KEY")

# ── Verhoeff ──────────────────────────────────────────────────
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
    return response.json()

def get_data_from_request():
    """
    Zoho Deluge invokeurl sends data in different formats.
    This function handles all cases.
    """
    # Try JSON body first
    json_data = request.get_json(force=True, silent=True)
    if json_data:
        return json_data

    # Try form data
    if request.form:
        return request.form.to_dict()

    # Try raw body and parse manually
    raw = request.get_data(as_text=True)
    if raw:
        # Try JSON parse
        try:
            import json
            return json.loads(raw)
        except:
            pass

        # Try parsing as key=value pairs (Zoho Map toString format)
        # Zoho Map.toString() produces: {key1=value1, key2=value2}
        try:
            raw = raw.strip().strip('{}')
            result = {}
            # Split by comma but not commas inside base64
            # base64 doesn't contain = outside of padding so split on ", " is safe
            import re
            # Find image_base64 value
            img_match = re.search(r'image_base64=([^,}]+(?:,(?![a-z_])[^,}]+)*)', raw)
            num_match = re.search(r'aadhaar_number=(\d+)', raw)
            if img_match:
                result['image_base64'] = img_match.group(1).strip()
            if num_match:
                result['aadhaar_number'] = num_match.group(1).strip()
            if result:
                return result
        except:
            pass

    return {}


@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Aadhaar OCR + Compress"})


@app.route('/verify', methods=['POST'])
def verify():
    try:
        data = get_data_from_request()

        if not data:
            return jsonify({"success":False,"match":False,"message":"No data received"}), 400

        image_base64   = data.get("image_base64", "")
        aadhaar_number = str(data.get("aadhaar_number", "")).replace(" ","").replace("-","")

        if not image_base64:
            return jsonify({"success":False,"match":False,"message":"image_base64 is required"}), 400
        if not aadhaar_number:
            return jsonify({"success":False,"match":False,"message":"aadhaar_number is required"}), 400
        if len(aadhaar_number) != 12 or not aadhaar_number.isdigit():
            return jsonify({"success":False,"match":False,"message":"Must be exactly 12 digits"}), 400
        if not verhoeff_validate(aadhaar_number):
            return jsonify({"success":False,"match":False,"message":"Invalid Aadhaar checksum"}), 400

        if "," in image_base64:
            image_base64 = image_base64.split(",")[1]

        try:
            image_bytes = base64.b64decode(image_base64)
        except Exception as e:
            return jsonify({"success":False,"match":False,"message":"Invalid base64: "+str(e)}), 400

        try:
            compressed = compress_image(image_bytes)
            del image_bytes
        except Exception as e:
            return jsonify({"success":False,"match":False,"message":"Compression failed: "+str(e)}), 500

        try:
            ocr_data = run_ocr(compressed)
            del compressed
        except Exception as e:
            return jsonify({"success":False,"match":False,"message":"OCR call failed: "+str(e)}), 500

        if ocr_data.get("IsErroredOnProcessing"):
            return jsonify({"success":False,"match":False,"message":"OCR error. Upload a clearer image."}), 200

        parsed = ocr_data.get("ParsedResults", [])
        if not parsed:
            return jsonify({"success":False,"match":False,"message":"No text detected. Upload a clearer image."}), 200

        full_text = " ".join([r.get("ParsedText","") for r in parsed])
        if not full_text.strip():
            return jsonify({"success":False,"match":False,"message":"No text detected. Upload a clearer image."}), 200

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
