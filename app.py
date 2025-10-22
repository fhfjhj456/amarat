import os
import tempfile
import logging
import requests
import json
import time 
from flask import Flask, request, jsonify
from pydub import AudioSegment
import speech_recognition as sr

# ------------------ Logging ------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)

app = Flask(__name__)

# ------------------ Telegram & Gemini Config ------------------
# אסימוני טלגרם מתוך הקוד המקורי שלך:
TELEGRAM_BOT_TOKEN = "8183670381:AAEkIUh-P7pU6HbMmHY_eqjSU2_6Qfnqnic"
TELEGRAM_CHAT_ID = "7820835795"

# אסימון Gemini: מומלץ להשתמש במשתנה סביבה!
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") 

# ------------------ Helper Functions ------------------

def add_silence(input_path: str) -> AudioSegment:
    """מוסיף שניית שקט לפני ואחרי קטע האודיו כדי לשפר את הדיוק בזיהוי דיבור."""
    audio = AudioSegment.from_file(input_path, format="wav")
    silence = AudioSegment.silent(duration=1000)
    return silence + audio + silence

def recognize_speech(audio_segment: AudioSegment) -> str:
    """מבצע זיהוי דיבור באמצעות Google Speech Recognition."""
    recognizer = sr.Recognizer()
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as temp_wav:
            audio_segment.export(temp_wav.name, format="wav")
            with sr.AudioFile(temp_wav.name) as source:
                data = recognizer.record(source)
            # זיהוי בעברית
            text = recognizer.recognize_google(data, language="he-IL")
            logging.info(f"Recognized text: {text}")
            return text
    except sr.UnknownValueError:
        # חוזר עם מחרוזת ריקה אם לא זוהה דיבור
        return ""
    except Exception as e:
        logging.error(f"Speech recognition error: {e}")
        return ""

def send_to_telegram(text: str):
    """שולח הודעה טקסטואלית נקייה לצ'אט היעד."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # שליחה ללא parse_mode כדי לוודא טקסט נקי, כפי שביקשת
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text})

# --- פונקציה חדשה: סיכום טקסט באמצעות Gemini AI ---
def summarize_text_with_gemini(text_to_summarize: str) -> str:
    """
    מבצע קריאת API למודל Gemini כדי לסכם טקסט חדשותי בצורה קצרה ותמציתית.
    המודל מונחה להחזיר רק את הטקסט המסוכם, ללא תוספות או כותרות.
    """
    if not text_to_summarize or not GEMINI_API_KEY:
        logging.warning("Skipping Gemini summarization: Text or API Key is missing. Returning original text.")
        # מחזיר הודעה קצרה המציינת את הכשל אם אין מפתח
        return "❌ שגיאת AI: לא ניתן לבצע סיכום. נא לוודא כי GEMINI_API_KEY מוגדר." 

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"
    
    # הנחיית המערכת: מונחית להחזיר רק את הטקסט המסוכם
    system_prompt = "אתה עורך חדשות. סכם את הטקסט המועתק (תמלול אודיו) לדיווח חדשותי קצר, תמציתי ורשמי. השתמש בשפה עברית ברורה. הדיווח צריך להיות עד שתי פסקאות קצרות בלבד. *אל* תוסיף כותרות, הקדמות או משפטי סיום. הפלט שלך צריך להיות רק הטקסט המסוכם."
    
    payload = {
        "contents": [
            {"parts": [{"text": text_to_summarize}]}
        ],
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "generationConfig": {
            "temperature": 0.2, # טמפרטורה נמוכה לעמידה בסיכום עובדתי
        }
    }
    
    headers = {
        'Content-Type': 'application/json'
    }

    # יישום Exponential Backoff לטיפול בכשלים זמניים ב-API
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # הקריאה ל-API של ג'מיני
            response = requests.post(
                f"{API_URL}?key={GEMINI_API_KEY}", 
                headers=headers, 
                data=json.dumps(payload),
                timeout=20
            )
            response.raise_for_status()
            
            result = response.json()
            generated_text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text')
            
            if generated_text:
                logging.info("Gemini summarization successful.")
                return generated_text.strip() # מוודא שהפלט נקי מרווחים מיותרים
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Attempt {attempt + 1} failed to call Gemini API: {e}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logging.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logging.error("All retries failed for Gemini API.")
                # במקרה של כשל סופי, מחזירים הודעת שגיאה ואת הטקסט המקורי
                return f"❌ כשל בסיכום AI. הטקסט המקורי: \n\n{text_to_summarize}"
        except Exception as e:
            logging.error(f"Error processing Gemini response: {e}")
            break

    # Fallback אחרון במקרה שהקוד לא הגיע ל-return בתוך הלולאה
    return "❌ שגיאה כללית בסיכום AI. הטקסט המקורי:\n" + text_to_summarize

# ------------------ API Endpoint ------------------

@app.route("/upload_audio", methods=["GET"])
def upload_audio():
    file_url = request.args.get("file_url")

    # ✅ נכניס את הטוקן כאן למעלה, לפני השימוש בו
    system_token = "0733181406:80809090"

    # ✅ תמיכה בימות המשיח – שימוש בפרמטר stockname אם file_url חסר
    if not file_url:
        stockname = request.args.get("stockname")
        if stockname:
            file_url = f"https://www.call2all.co.il/ym/api/DownloadFile?token={system_token}&path=ivr2:/{stockname}"
        else:
            return jsonify({"error": "Missing 'file_url' or 'stockname' parameter"}), 400

    # ✅ אם file_url לא מכיל http, נניח שזה נתיב מקומי מימות ונבנה URL מלא
    if not file_url.startswith("http"):
        file_url = file_url.strip("/")
        file_url = f"https://www.call2all.co.il/ym/api/DownloadFile?token={system_token}&path=ivr2:/{file_url}"

    logging.info(f"Downloading audio from: {file_url}")

    try:
        response = requests.get(file_url, timeout=15)
        if response.status_code != 200:
            logging.error(f"Failed to download file, status: {response.status_code}")
            return jsonify({"error": "Failed to download audio file"}), 400

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as temp_input:
            temp_input.write(response.content)
            temp_input.flush()

            processed_audio = add_silence(temp_input.name)
            recognized_text = recognize_speech(processed_audio)

            if recognized_text:
                
                # --- שלב 1: שליחת ההודעה הראשונה (תמלול מקורי) ---
                send_to_telegram(recognized_text)
                logging.info("Sent Message 1: Original Transcription.")
                
                # --- שלב 2: סיכום הטקסט ושליחת ההודעה השנייה (סיכום AI) ---
                summarized_text = summarize_text_with_gemini(recognized_text)
                send_to_telegram(summarized_text)
                logging.info("Sent Message 2: AI Summarized Text.")
                # --- סוף השינוי ---
                
                return jsonify({"recognized_text": recognized_text, "summarized_text": summarized_text})
            else:
                # אם לא זוהה טקסט, נשלח הודעה בהתאם
                error_msg = f"❌ לא זוהה דיבור. (קובץ: {file_url})"
                send_to_telegram(error_msg)
                return jsonify({"recognized_text": ""})

    except Exception as e:
        logging.error(f"Error: {e}")
        # במקרה של כשל כללי, נשלח הודעת שגיאה לטלגרם
        error_message = f"❌ **שגיאה קריטית בעיבוד**\nאירעה שגיאה בשרת: `{str(e)}`"
        send_to_telegram(error_message)
        return jsonify({"error": str(e)}), 500

# ------------------ Run ------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
