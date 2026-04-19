import os
import re
import json
import base64
import requests
from flask import Flask, request, jsonify
from PIL import Image
from io import BytesIO

app = Flask(__name__)

OCR_API_KEY = os.environ.get("OCR_KEY", "YOUR_OCR_SPACE_API_KEY")

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


@app.route('/', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Aadhaar OCR"})


@app.route('/verify', methods=['POST'])
def verify():
    try:
        # ✅ Read aadhaar_number from query param
        aadhaar_number = request.args.get("aadhaar_number", "").replace(" ","").replace("-","")

        # ✅ File arrives with key 'content' from Deluge files:
        uploaded_file = (
            request.files.get("content") or
            request.files.get("file") or
            request.files.get("Upload_Aadhaar") or
            (list(request.files.values())[0] if request.files else None)
        )

        if not aadhaar_number:
            return jsonify({"success":False,"match":False,
                "message":"aadhaar_number missing. args="+str(dict(request.args))}), 400
        if len(aadhaar_number) != 12 or not aadhaar_number.isdigit():
            return jsonify({"success":False,"match":False,"message":"Must be 12 digits"}), 400
        if not verhoeff_validate(aadhaar_number):
            return jsonify({"success":False,"match":False,"message":"Invalid Aadhaar checksum"}), 400
        if not uploaded_file:
            return jsonify({"success":False,"match":False,
                "message":"file missing. file_keys="+str(list(request.files.keys()))}), 400

        image_bytes = uploaded_file.read()

        if not image_bytes or len(image_bytes) < 100:
            return jsonify({"success":False,"match":False,
                "message":"File empty: "+str(len(image_bytes))+" bytes"}), 400

        try:
            compressed = compress_image(image_bytes)
            del image_bytes
        except Exception as e:
            return jsonify({"success":False,"match":False,"message":"Compression failed: "+str(e)}), 500

        ocr_data = run_ocr(compressed)
        del compressed

        if ocr_data.get("IsErroredOnProcessing"):
            return jsonify({"success":False,"match":False,
                "message":"OCR error: "+str(ocr_data.get("ErrorMessage",""))}), 200

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
