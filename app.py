from flask import Flask, request, jsonify
import os
import google.generativeai as genai
from dotenv import load_dotenv
import re
from datetime import datetime, timedelta
from flask_cors import CORS
from deep_translator import GoogleTranslator
from cachetools import TTLCache
import pycountry
import asyncio
from deep_translator import GoogleTranslator
import langcodes

load_dotenv()

app = Flask(__name__)
CORS(app)

# Set your Google AI API key
genai.configure(api_key=os.environ.get("GOOGLE_AI_API_KEY"))

# Set up cache
cache = TTLCache(maxsize=100, ttl=300)  # LRU Cache with TTL
CACHE_TIMEOUT = timedelta(minutes=5)

# Translation cache
translation_cache = TTLCache(maxsize=1000, ttl=CACHE_TIMEOUT.total_seconds())
LANGUAGE_ALIASES = {
    "Punjabi": "pa",
    # Add more aliases if needed
}

# Compile regex patterns once
mcq_pattern = re.compile(
    r'\{\s*"question":\s*"([^"]*)",\s*'
    r'"option1":\s*"([^"]*)",\s*'
    r'"option2":\s*"([^"]*)",\s*'
    r'"option3":\s*"([^"]*)",\s*'
    r'"option4":\s*"([^"]*)",\s*'
    r'"answer":\s*"([^"]*)"\s*\}',
    re.DOTALL,
)

tf_pattern = re.compile(
    r'\{\s*"question":\s*"([^"]*)",\s*'
    r'"answer":\s*"([^"]*)"\s*\}',
    re.DOTALL,
)


LANGUAGE_ALIASES = {
    "punjabi": "pa",
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "chinese": "zh",
    "japanese": "ja",
    "korean": "ko",
    "russian": "ru",
    "hindi": "hi",
    "arabic": "ar",
    "sindhi": "sd",  # Added Sindhi
    # Add more languages as needed
}

def get_language_code(language_name):
    # Check if the language name exists in LANGUAGE_ALIASES
    if language_name in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[language_name]
    
    # Use langcodes to get the ISO code if it's not in aliases
    try:
        lang = langcodes.get(language_name)
        return lang.language
    except Exception:
        return None

def chunk_text(text, chunk_size=5000):
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

def translate_sentence(sentence, target_language):
    # Get the target language code
    target_lang_code = get_language_code(target_language.lower())
    
    if not target_lang_code:
        return f"Language '{target_language}' is not supported."

    try:
        # If sentence is too long, split it into smaller chunks
        if len(sentence) > 5000:
            chunks = chunk_text(sentence)
            translated_chunks = [
                GoogleTranslator(source='auto', target=target_lang_code).translate(chunk)
                for chunk in chunks
            ]
            return ''.join(translated_chunks)
        else:
            return GoogleTranslator(source='auto', target=target_lang_code).translate(sentence)
    except Exception as e:
        return f"An error occurred: {e}"
 

def generate_question_and_answer(class_name, course_name, section, subsection, language, question_type, Difficulty):
    """Generates questions and answers using the Gemini Pro model."""
    if question_type == "true false":
      prompt = f"""Design a (true false) type quiz for {course_name} {class_name} studying {subsection}. The quiz should focus on {section}. Questions should be {Difficulty} level to understand and written in {language}. Convert into json format under heading question,answer. Give answer as correct answer not as option. Give 25 questions."""
      pattern = tf_pattern
    else:
      prompt = f"""Design a mcq type quiz for {course_name} {class_name} studying {subsection}. The quiz should focus on {section}. Questions should be {Difficulty} level to understand and written in {language}. Convert into json format under heading question,option1,option2,option3,option4,answer. Give answer as correct answer not as option. Give 25 questions."""
      pattern = mcq_pattern
    model = genai.GenerativeModel(model_name="gemini-pro")
    response = model.generate_content(prompt)
    generated_text = response.text
    print(generated_text)
    matches = pattern.findall(generated_text)
    mcq_data = []

    for match in matches:
        if question_type == "true false":
            mcq_data.append({
                "description": match[0],
                "answer": match[1],
            })
        else:
            mcq_data.append({
                "description": match[0],
                "options": match[1:5],
                "answer": match[5],
            })

    return mcq_data

async def process_questions(data):
    """Process questions asynchronously."""
    global cache  # Declare as global to modify the global variable
    global translation_cache  # Declare as global to modify the global variable

    class_name = data.get("className", "")
    course_name = data.get("courseName", "")
    section = data.get("sectionName", "")
    subsection = data.get("subSectionName", "")
    language = data.get("languageName", "")
    language1=data.get("languageName1", "")
    question_type = data.get("type", "")
    Difficulty = data.get("difficultyName", "")
    


    if not all([class_name, course_name, section, subsection, language, question_type, Difficulty]):
        return {"error": "Missing data"}, 400

    cache_key = (class_name, course_name, section, subsection, language, question_type, Difficulty)

    # Clean expired cache entries
    now = datetime.now()
    cache = {key: value for key, value in cache.items() if now - value[1] <= CACHE_TIMEOUT}

    # Get the previous questions
    previous_questions, _ = cache.get(cache_key, (set(), datetime.now()))

    for attempt in range(20):  # Limit the number of retries
        mcq_data = generate_question_and_answer(class_name, course_name, section, subsection, language, question_type, Difficulty)
        unique_questions = set()
        unique_answers = []
        unique_option = []

        for item in mcq_data:
            q = item["description"]
            a = item["answer"]
            if question_type != "true false":
                o = item["options"]

            if q not in previous_questions and len(unique_questions) < 10:
                unique_questions.add(q)
                unique_answers.append(a)
                if question_type != "true false":
                    unique_option.append(o)
                previous_questions.add(q)

        if unique_questions:
            cache[cache_key] = (previous_questions, datetime.now())
            lang1 = []
            lang2=[]
            if question_type.lower() == "mcq":
                for q, a, o in zip(unique_questions, unique_answers, unique_option):
                    ques = translate_sentence(q, language1)
                    ans = translate_sentence(a, language1)
                    opt = [translate_sentence(option, language1) for option in o]
                    lang2.append({
                        "description": ques,
                        "options": opt,
                        "answer": ans,

                    })
                    lang1.append({
                        "description": q,
                        "options": o,
                        "answer": a,
                    })
            elif question_type.lower() in ["short", "true false"]:
                for q, a in zip(unique_questions, unique_answers):
                    ques = translate_sentence(q, language1)
                    ans = translate_sentence(a, language1)
                    lang2.append({"answer": ans, "description": ques})
                    lang1.append({"answer": a, "description": q})
            elif question_type.lower() == "essay":
                for q in unique_questions:
                    ques = translate_sentence(q, language1)
                    lang2.append({"description": ques})
                    lang1.append({"description": q})
            
            print()

            return {"result": lang1,"result1":lang2, "message": "all questions", "success": True}, 200


        await asyncio.sleep(1)  # Short delay to avoid rapid retries
    return {"success": False, "message": "No new unique questions found after multiple attempts"}, 500

@app.route("/generateQuestionsUsingAi", methods=["POST"])
def generate_question_endpoint():
    """API endpoint to generate questions and answers."""
    data = request.json
    response, status_code = asyncio.run(process_questions(data))
    return jsonify(response), status_code

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, threaded=True)
