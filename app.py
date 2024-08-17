from flask import Flask, request, jsonify
import os
import google.generativeai as genai
from dotenv import load_dotenv
import re
import time
import threading
from threading import Lock
from deep_translator import GoogleTranslator
import pycountry
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)

# Set your Google AI API key
genai.configure(api_key=os.environ.get("GOOGLE_AI_API_KEY"))
cache = {}
cache_lock = Lock()
CACHE_TIMEOUT = timedelta(minutes=5)


def chunk_array_generator(arr):
    for i in range(0, len(arr), 4):
        yield arr[i : i + 4]


def get_language_code(language_name):
    """Get ISO 639-1 language code from language name."""
    try:
        language = pycountry.languages.get(name=language_name)
        return language.alpha_2 if language else None
    except KeyError:
        return None


def translate_array_of_strings(text_array, target_language):
    """Translate an array of strings into the target language."""
    translated_texts = []
    for text in text_array:
        try:
            translation = GoogleTranslator(
                source="auto", target=target_language
            ).translate(text)
            translated_texts.append(translation)
        except Exception as e:
            print(f"Translation failed for '{text}': {e}")
            translated_texts.append(text)  # Append original text if translation fails
    return translated_texts


def clean_text(text):
    """Remove unwanted characters from text."""
    text = re.sub(r"\*\*", "", text)  # Remove asterisks
    return text.strip()


def generate_question_and_answer(
    class_name, course_name, section, subsection, language, question_type, Difficulty
):
    """Generates questions and answers using the Gemini Pro model."""
    if question_type == "mcq":

        prompt = f"""Design a mcq type for {course_name} {class_name} studying {subsection}. The quiz should focus on {section}. Questions should be {Difficulty} level to understand and written in English.Convert into json format under heading question,option1,option2,option3,option4,answer. 
    . Give answer as correct answer not as option. Give 25 questions."
"""
    else:
        prompt = f"""Act as a rigorous examiner:
    Create 25 challenging {question_type} type questions and their corresponding answers in English on the topic of {course_name}, specifically focusing on the {section} section and {subsection} subsection. The difficulty level should be {Difficulty}. 
    Ensure that each question is clearly stated followed immediately by its answer in the format shown below:
    
    **Question:** [Question text here]
    **Answer:** [Answer text here]
    
    Example:
    **Question:** Find the rank of the matrix A = [1 2 3; 4 5 6; 7 8 9].
    **Answer:** 3
    
    **Question:** Solve the system of equations: x + 2y - z = 0, 2x + 3y + z = 4, x - y + 2z = 3.
    **Answer:** x = 1, y = 2, z = 1
    
    Please generate questions and answers following this format."""

    print(prompt)
    # Use the Gemini Pro model for question generation
    model = genai.GenerativeModel(model_name="gemini-pro")
    response = model.generate_content(prompt)

    # Extract the generated text
    generated_text = response.text
    # print(generated_text)

    if question_type == "mcq":
        # Revised regex patterns
        pattern = re.compile(
            r'\{\s*"question":\s*"([^"]*)",\s*'
            r'"option1":\s*"([^"]*)",\s*'
            r'"option2":\s*"([^"]*)",\s*'
            r'"option3":\s*"([^"]*)",\s*'
            r'"option4":\s*"([^"]*)",\s*'
            r'"answer":\s*"([^"]*)"\s*\}',
            re.DOTALL,
        )
        matches = pattern.findall(generated_text)
        print(matches)
        mcq_data = []

        for i in range(len(matches)):

            mcq_data.append(
                {
                    "description": matches[i][0],
                    "options": matches[i][1:5],
                    "answer": matches[i][5],
                }
            )
        print(mcq_data)
        language_code = get_language_code(language)
        if language_code:
            for item in mcq_data:
                item["description"] = translate_array_of_strings(
                    [item["description"]], language_code
                )[0]
                if "options" in item:
                    item["options"] = translate_array_of_strings(
                        item["options"], language_code
                    )
                if "answer" in item:
                    item["answer"] = translate_array_of_strings(
                        [item["answer"]], language_code
                    )[0]

        return mcq_data
    else:
        try:
            question_pattern = re.compile(
                r"\*\*Question:\*\*\s*(.*?)\s*(?=\*\*Answer:\*\*|$)",
                re.DOTALL | re.MULTILINE,
            )

            answer_pattern = re.compile(
                r"\*\*Answer:\*\*\s*(.*?)\s*(?=\*\*Question:\*\*|$)",
                re.DOTALL | re.MULTILINE,
            )
            questions = [
                clean_text(question)
                for question in question_pattern.findall(generated_text)
            ]
            answers = [
                clean_text(answer) for answer in answer_pattern.findall(generated_text)
            ]

            # Translate the questions and answers
        except ValueError as e:
            print(f"Error processing text: {e}")
            questions = [clean_text(generated_text)]
            answers = ["No answer provided."]

        return questions, answers


@app.route("/generate-question", methods=["POST"])
def generate_question_endpoint():
    """API endpoint to generate questions and answers."""
    data = request.json
    class_name = data.get("ClassName", "")
    course_name = data.get("CourseName", "")
    section = data.get("Section", "")
    subsection = data.get("Subsection", "")
    language = data.get("Language", "")
    question_type = data.get("QuestionType", "")
    Difficulty = data.get("Difficulty", "")

    if not all(
        [
            class_name,
            course_name,
            section,
            subsection,
            language,
            question_type,
            Difficulty,
        ]
    ):
        return jsonify({"error": "Missing data"}), 400

    cache_key = (
        class_name,
        course_name,
        section,
        subsection,
        language,
        question_type,
        Difficulty,
    )

    with cache_lock:
        # Clean expired cache entries
        now = datetime.now()
        keys_to_remove = [
            key
            for key, (_, timestamp) in cache.items()
            if now - timestamp > CACHE_TIMEOUT
        ]
        for key in keys_to_remove:
            del cache[key]
        print(f"Cleared {len(keys_to_remove)} entries from cache")

        # Get the previous questions
        cache_entry = cache.get(cache_key, (set(), datetime.now()))
        previous_questions, _ = cache_entry
    print(f"Previous questions count: {len(previous_questions)}")

    for attempt in range(5):  # Limit the number of retries
        if question_type == "mcq":
            mcq_data = generate_question_and_answer(
                class_name,
                course_name,
                section,
                subsection,
                language,
                question_type,
                Difficulty,
            )

            unique_questions = []
            unique_answers = []
            unique_option = []
            for item in mcq_data:
                q = item["description"]
                a = item["answer"]
                o = item["options"]
                if q not in previous_questions and len(unique_questions) < 10:
                    unique_questions.append(q)
                    unique_answers.append(a)
                    unique_option.append(o)
                    previous_questions.add(q)
            # language_code = get_language_code(language)
            # if language_code:
            #     for item in mcq_data:
            #         item["description"] = translate_array_of_strings(
            #             [item["description"]], language_code
            #         )[0]
            #         if "options" in item:
            #             item["options"] = translate_array_of_strings(
            #                 item["options"], language_code
            #             )
            #         if "answer" in item:
            #             item["answer"] = translate_array_of_strings(
            #                 [item["answer"]], language_code
            #             )[0]

            # else:
            #     print(
            #         f"Language '{language}' not found or does not have a valid alpha_2 code."
            #     )
        else:
            questions, answers = generate_question_and_answer(
                class_name,
                course_name,
                section,
                subsection,
                language,
                question_type,
                Difficulty,
            )

            unique_questions = []
            unique_answers = []
            for q, a in zip(questions, answers):
                if q not in previous_questions and len(unique_questions) < 10:
                    unique_questions.append(q)
                    unique_answers.append(a)
                    previous_questions.add(q)
            language_code = get_language_code(language)
            if language_code:
                unique_questions = translate_array_of_strings(
                    unique_questions, language_code
                )
                unique_answers = translate_array_of_strings(
                    unique_answers, language_code
                )
        print(f"Unique questions found in attempt {attempt + 1}: {unique_questions}")
        if unique_questions:
            with cache_lock:
                cache[cache_key] = (previous_questions, datetime.now())

            # Prepare response according to question type
            result = []
            if question_type.lower() == "mcq":
                for q, a, o in zip(unique_questions, unique_answers, unique_option):
                    result.append(
                        {
                            "description": q,
                            "options": o,
                            "correct_answer": a,
                        }
                    )
            elif question_type.lower() in ["short", "true/false"]:
                for q, a in zip(unique_questions, unique_answers):
                    result.append({"answer": a, "description": q})
            elif question_type.lower() == "essay":
                for q in unique_questions:
                    result.append({"description": q})

            return (
                jsonify(
                    {"result": result, "message": "all questions", "success": True}
                ),
                200,
            )

        # Log retry attempt
        print(f"No new unique questions found. Retrying generation...")
        time.sleep(1)  # Short delay to avoid rapid retries

    return (
        jsonify(
            {
                "success": False,
                "message": "No new unique questions found after multiple attempts",
            }
        ),
        500,
    )


if __name__ == "__main__":
    app.run(debug=True)
