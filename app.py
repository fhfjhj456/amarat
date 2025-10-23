import os
import tempfile
import logging
import requests
import json
import time # נשאר מהקוד המקורי
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
    # שליחה ללא parse_mode או עיצוב נוסף כדי לוודא טקסט נקי
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text})

# --- פונקציה: סיכום טקסט באמצעות Gemini AI (עם ניסיון חוזר יחיד) ---
def summarize_text_with_gemini(text_to_summarize: str) -> str:
    """
    מבצע קריאת API למודל Gemini לסיכום. מנסה פעמיים (ניסיון מקורי + ניסיון חוזר)
    כדי להתגבר על שגיאות זמניות כמו 503.
    """
    if not text_to_summarize or not GEMINI_API_KEY:
        logging.warning("Skipping Gemini summarization: Text or API Key is missing.")
        return "❌ שגיאת AI: לא ניתן לבצע סיכום." 

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"
    MAX_RETRIES = 2  # נסיון מקורי + נסיון חוזר אחד

    # הנחיית המערכת: מורה ל-AI לנסח מחדש כל טקסט לדיווח חדשותי (אין סינון)
    system_prompt = (
        "אתה עורך חדשות. נסח מחדש את הטקסט המועתק (תמלול אודיו) לדיווח חדשותי קצר, תמציתי ורשמי. "
        "השתמש בשפה עברית ברורה. הדיווח צריך להיות עד שתי פסקאות קצרות בלבד. "
        "*אל* תוסיף כותרות, הקדמות או משפטי סיום. הפלט שלך צריך להיות רק הטקסט המסוכם. "
        "בכל מקרה, עליך לנסח מחדש את ההודעה כדיווח חדשותי. אם התוכן אינו חדשותי, נסח משפט רשמי קצר המתאר את התוכן בקצרה."
    )
    
    payload = {
        "contents": [
            {"parts": [{"text": text_to_summarize}]}
        ],
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "generationConfig": {
            "temperature": 0.2, 
        }
    }
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            # המתנה קצרה מאוד (למעט הניסיון הראשון) כדי לתת לשרת להתאושש
            if attempt > 0:
                time.sleep(1) # המתנה של שנייה אחת לפני ניסיון חוזר

            response = requests.post(
                f"{API_URL}?key={GEMINI_API_KEY}", 
                headers=headers, 
                data=json.dumps(payload),
                timeout=20
            )
            response.raise_for_status() 
            
            # אם הצליח:
            result = response.json()
            generated_text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
            
            if generated_text:
                logging.info(f"Gemini summarization successful on attempt {attempt + 1}.")
                return generated_text.strip()
            
            # אם ה-AI לא החזיר טקסט מסיבה כלשהי
            logging.warning(f"Gemini returned empty text on attempt {attempt + 1}. Continuing...")
            return text_to_summarize

        except requests.exceptions.RequestException as e:
            # אם השגיאה היא 503/429 (שגיאה זמנית), ננסה שוב
            if response.status_code in [503, 429]:
                 logging.warning(f"Gemini API temporary failure (Code {response.status_code}) on attempt {attempt + 1}. Retrying...")
                 last_error = e
                 continue
            
            # עבור כל שגיאה אחרת (כמו 400 Bad Request), אין טעם לנסות שוב
            logging.error(f"Gemini API request failed permanently on attempt {attempt + 1}: {e}")
            last_error = e
            break # יציאה מהלולאה

    # אם כל הניסיונות נכשלו:
    logging.error(f"Gemini API failed after {MAX_RETRIES} attempts. Last error: {last_error}")
    return f"❌ כשל בסיכום AI. הטקסט המקורי: \n{text_to_summarize}"

# ------------------ API Endpoint for Keep-Alive ------------------

@app.route("/health", methods=["GET"])
def health_check():
    """
    נתיב לבדיקת תקינות (Health Check).
    """
    return jsonify({"status": "healthy", "message": "Server is awake and ready."}), 200

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
                
                # --- שלב 2: סיכום הטקסט (הפעם תמיד יבוצע ניסוח) ---
                summarized_text = summarize_text_with_gemini(recognized_text)
                
                # כעת, תמיד שולחים את ההודעה השנייה, כפי שנדרש.
                send_to_telegram(summarized_text)
                logging.info("Sent Message 2: AI Summarized Text (Unconditionally).")
                
                return jsonify({"recognized_text": recognized_text, "summarized_text": summarized_text})
            
            else:
                # אם לא זוהה דיבור, לא שולחים הודעה לטלגרם כלל.
                logging.info("Skipped sending any message: No speech recognized.")
                return jsonify({"recognized_text": ""})

    except Exception as e:
        logging.error(f"Error: {e}")
        # במקרה של כשל קריטי, עדיין רצוי ליידע את המשתמש באמצעות טלגרם 
        error_message = f"❌ **שגיאה קריטית בעיבוד**\nאירעה שגיאה בשרת: `{str(e)}`"
        send_to_telegram(error_message)
        return jsonify({"error": str(e)}), 500

# ------------------ Run ------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
