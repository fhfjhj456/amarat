import os
import tempfile
import logging
import requests
import json
import time # נשאר כדי לא לשבור את מבנה הקוד, אך אינו בשימוש פעיל
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

# --- משתנה פנימי לסימון תוכן לא חדשותי ---
# המחרוזת הזו תשמש לבדיקה אם ה-AI החליט שהתוכן אינו חדשותי.
NON_NEWS_MARKER = "[[NON_NEWS_CONTENT]]"

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

# --- פונקציה: סיכום טקסט באמצעות Gemini AI (הוסר מנגנון הניסיונות החוזרים להאצת התגובה) ---
def summarize_text_with_gemini(text_to_summarize: str) -> str:
    """
    מבצע קריאת API למודל Gemini לסיכום. אם לא חדשותי, מחזיר את ה-NON_NEWS_MARKER.
    """
    if not text_to_summarize or not GEMINI_API_KEY:
        logging.warning("Skipping Gemini summarization: Text or API Key is missing.")
        # מחזיר הודעת שגיאה קצרה אם אין מפתח API
        return "❌ שגיאת AI: לא ניתן לבצע סיכום." 

    API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"
    
    # הנחיית המערכת: כוללת הנחיה מפורשת לסימון תוכן לא חדשותי
    system_prompt = (
        "אתה עורך חדשות. סכם את הטקסט המועתק (תמלול אודיו) לדיווח חדשותי קצר, תמציתי ורשמי. "
        "השתמש בשפה עברית ברורה. הדיווח צריך להיות עד שתי פסקאות קצרות בלבד. "
        "*אל* תוסיף כותרות, הקדמות או משפטי סיום. הפלט שלך צריך להיות רק הטקסט המסוכם. "
        f"אם הטקסט לא מכיל דיווח חדשותי רלוונטי (כגון ברכות, שיחה אישית או תוכן שאינו חדשות), הפלט שלך צריך להיות *רק* המחרוזת: {NON_NEWS_MARKER}"
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

    # ביצוע הקריאה היחידה ל-API ללא ניסיונות חוזרים (להשגת מהירות)
    try:
        response = requests.post(
            f"{API_URL}?key={GEMINI_API_KEY}", 
            headers=headers, 
            data=json.dumps(payload),
            timeout=20 # ניתן להקטין את ה-timeout אם נדרש
        )
        response.raise_for_status() # יעלה חריגה במקרה של 4xx/5xx

        result = response.json()
        # ודא שהתוצאה היא מחרוזת נקייה
        generated_text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
        
        if generated_text:
            logging.info("Gemini summarization successful.")
            return generated_text.strip()
        
        # אם ה-API לא החזיר טקסט מסיבה כלשהי
        return text_to_summarize 

    except requests.exceptions.RequestException as e:
        # במקרה של כשל רשת או שגיאת API, השרת ימשיך מיד בלי לחכות
        logging.error(f"Gemini API request failed immediately: {e}")
        return f"❌ כשל בסיכום AI. הטקסט המקורי: \n{text_to_summarize}"
    except Exception as e:
        logging.error(f"Error processing Gemini response: {e}")
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
                
                # --- שלב 2: סיכום הטקסט ובדיקת הסינון ---
                summarized_text = summarize_text_with_gemini(recognized_text)
                
                # אם התוכן לא חדשותי (מחזיר את ה-MARKER), לא שולחים את ההודעה השנייה.
                if summarized_text.strip() != NON_NEWS_MARKER:
                    send_to_telegram(summarized_text)
                    logging.info("Sent Message 2: AI Summarized Text.")
                else:
                    logging.info("Skipped sending AI summary: Content was marked as non-news.")
                
                return jsonify({"recognized_text": recognized_text, "summarized_text": summarized_text})
            
            else:
                # אם לא זוהה דיבור, לא שולחים הודעה לטלגרם כלל.
                logging.info("Skipped sending any message: No speech recognized.")
                return jsonify({"recognized_text": ""})

    except Exception as e:
        logging.error(f"Error: {e}")
        # במקרה של כשל קריטי, עדיין רצוי ליידע את המשתמש באמצעות טלגרם (אם כי זה מחוץ ללוגיקת 'לא זוהה דיבור')
        error_message = f"❌ **שגיאה קריטית בעיבוד**\nאירעה שגיאה בשרת: `{str(e)}`"
        send_to_telegram(error_message)
        return jsonify({"error": str(e)}), 500

# ------------------ Run ------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
