import logging
import json
from pdf_processor import get_pages_content, search_in_pdf, bm25_search

LOG_FILE = "scholarai.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================================
# SCHOLAR AI PERSONA
# ==========================================
SCHOLAR_PERSONA = """
You are ScholarAI — a smart personal reading assistant for a Teacher.

Your role:
- Help the teacher understand books and documents better.
- Generate clear, detailed summaries of reading material.
- Create helpful quizzes to test understanding.
- Answer questions based on the provided content only.
- Be professional, helpful, and encouraging.

STRICT RULES:
- Use ONLY the provided content to answer.
- Do NOT make up information.
- If answer is not in content, say: "This information is not available in the provided content."
- Always be concise but complete.

*** LANGUAGE INSTRUCTION ***
{language_instruction}
"""

# ==========================================
# LANGUAGE DETECTOR
# ==========================================
URDU_WORDS = {
    'kya', 'hai', 'hain', 'mein', 'ka', 'ki', 'ke', 'se', 'ko',
    'aur', 'nahi', 'nahin', 'koi', 'yeh', 'ye', 'wo', 'woh',
    'ap', 'aap', 'kab', 'kahan', 'kyun', 'kaise', 'kitna',
    'kitni', 'batao', 'bata', 'chahiye', 'milta', 'milti',
    'hoga', 'hogi', 'tha', 'thi', 'par', 'pe', 'wala',
    'wali', 'liye', 'sath', 'lekin', 'magar', 'phir', 'bas',
    'sirf', 'bhi', 'hi', 'jo', 'jab', 'tak', 'ya', 'agar',
    'karen', 'hota', 'hoti', 'karta', 'karti', 'karna',
    'chahta', 'chahti', 'pata', 'maloom', 'theek', 'bilkul',
    'zaroor', 'abhi', 'baad', 'pehle', 'accha', 'acha', 'ji',
    'haan', 'konsa', 'konsi', 'kuch', 'sab', 'bohat', 'bahut',
}

ENGLISH_WORDS = {
    'what', 'where', 'when', 'how', 'which', 'who', 'why',
    'is', 'are', 'was', 'were', 'do', 'does', 'did',
    'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for',
    'and', 'or', 'but', 'with', 'from', 'by', 'about',
    'can', 'could', 'will', 'would', 'should', 'have', 'has',
    'your', 'my', 'this', 'that', 'please', 'tell', 'me',
    'summary', 'explain', 'describe', 'define', 'list',
    'generate', 'create', 'make', 'give', 'show',
}

def detect_language(text: str) -> str:
    words = text.lower().split()
    if not words:
        return "ENGLISH"
    urdu_count = sum(1 for w in words if w in URDU_WORDS)
    english_count = sum(1 for w in words if w in ENGLISH_WORDS)
    if english_count > 0 and urdu_count == 0:
        return "ENGLISH"
    if urdu_count / len(words) > 0.15:
        return "URDU"
    return "ENGLISH"

def build_language_instruction(lang: str) -> str:
    if lang == "URDU":
        return (
            "User ne Urdu ya Roman Urdu mein likha hai.\n"
            "Aap ka jawab 100% Urdu script mein hona chahiye.\n"
            "Roman Urdu (English letters mein Urdu) nahi likhna.\n"
            "Sirf technical terms jaise 'Chapter', 'Page' English mein rakh sakte hain."
        )
    else:
        return (
            "User has written in English.\n"
            "Reply 100% in English only.\n"
            "Do not use any Urdu or Roman Urdu phrases."
        )


# ==========================================
# SUMMARY GENERATOR
# ==========================================
def generate_summary(book_id, page_from, page_to, client, model_name):
    try:
        content = get_pages_content(book_id, page_from, page_to)
        if not content:
            return "❌ Could not extract content from the selected pages."

        prompt = f"""
You are ScholarAI. Generate a clear, detailed, and well-structured summary of the following content.

Content (Pages {page_from} to {page_to}):
{content}

Instructions:
- Start with a brief overview (2-3 sentences).
- List the key points covered.
- End with main takeaways.
- Be thorough but concise.
- Use bullet points where appropriate.
"""
        response = client.chat.completions.create(
            model=model_name,
            temperature=0.3,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        summary = response.choices[0].message.content
        logging.info(f"✅ Summary generated for book {book_id}, pages {page_from}-{page_to}")
        return summary

    except Exception as e:
        logging.error(f"❌ Summary generation error: {e}")
        return "❌ Summary generation failed. Please try again."


# ==========================================
# QUIZ GENERATOR
# ==========================================
def generate_quiz(book_id, page_from, page_to, num_questions, client, model_name):
    try:
        content = get_pages_content(book_id, page_from, page_to)
        if not content:
            return None

        prompt = f"""
You are ScholarAI. Generate {num_questions} multiple choice questions based on the following content.

Content (Pages {page_from} to {page_to}):
{content}

IMPORTANT: Return ONLY a valid JSON array. No extra text, no markdown, no explanation.

Format:
[
  {{
    "question": "Question text here?",
    "options": ["A) Option 1", "B) Option 2", "C) Option 3", "D) Option 4"],
    "correct": "A) Option 1",
    "explanation": "Brief explanation why this is correct."
  }}
]

Rules:
- Questions must be based ONLY on the provided content.
- Make questions clear and unambiguous.
- Vary difficulty levels.
- Return exactly {num_questions} questions.
"""
        response = client.chat.completions.create(
            model=model_name,
            temperature=0.4,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        quiz_text = response.choices[0].message.content.strip()

        # Clean JSON
        if "```json" in quiz_text:
            quiz_text = quiz_text.split("```json")[1].split("```")[0].strip()
        elif "```" in quiz_text:
            quiz_text = quiz_text.split("```")[1].split("```")[0].strip()

        quiz_data = json.loads(quiz_text)
        logging.info(f"✅ Quiz generated for book {book_id}, pages {page_from}-{page_to}")
        return json.dumps(quiz_data)

    except json.JSONDecodeError as e:
        logging.error(f"❌ Quiz JSON parse error: {e}")
        return None
    except Exception as e:
        logging.error(f"❌ Quiz generation error: {e}")
        return None


# ==========================================
# Q&A — BM25 RAG (Vectorless)
# ==========================================
def answer_question(book_id, page_from, page_to, question, client, model_name):
    """
    BM25 se relevant pages dhundo, phir AI se answer lo.
    Full page range nahi bhejte — sirf relevant pages.
    """
    try:
        # BM25 se relevant pages dhundo
        context = bm25_search(book_id, question, top_k=4)

        # BM25 fail ho toh page range fallback
        if not context:
            context = get_pages_content(book_id, page_from, page_to)
        if not context:
            return "❌ Could not extract content from the selected pages."

        lang = detect_language(question)
        lang_instruction = build_language_instruction(lang)
        system_prompt = SCHOLAR_PERSONA.replace("{language_instruction}", lang_instruction)

        response = client.chat.completions.create(
            model=model_name,
            temperature=0,
            max_tokens=1000,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Relevant Book Content:\n\n{context}\n\nQuestion: {question}"
                }
            ]
        )

        answer = response.choices[0].message.content
        logging.info(f"✅ BM25 Q&A answered for book {book_id}")
        return answer

    except Exception as e:
        logging.error(f"❌ Q&A error: {e}")
        return "❌ Could not answer the question. Please try again."


# ==========================================
# SEARCH & ANSWER
# ==========================================
def search_and_answer(book_id, query, client, model_name):
    """
    BM25 search + AI answer.
    """
    try:
        results = search_in_pdf(book_id, query)

        if not results:
            return [], "No results found for your search query."

        # Context banao top 5 results se
        context = "\n".join(
            f"[Page {r['page_number']}]\n{r['snippet']}"
            for r in results[:5]
        )

        lang = detect_language(query)
        lang_instruction = build_language_instruction(lang)
        system_prompt = SCHOLAR_PERSONA.replace("{language_instruction}", lang_instruction)

        response = client.chat.completions.create(
            model=model_name,
            temperature=0,
            max_tokens=800,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Search Results for '{query}':\n{context}\n\nPlease summarize what was found about '{query}'."
                }
            ]
        )

        answer = response.choices[0].message.content
        logging.info(f"✅ Search answered for query: {query}")
        return results, answer

    except Exception as e:
        logging.error(f"❌ Search & Answer error: {e}")
        return [], "❌ Search failed. Please try again."


# ==========================================
# READING PLANNER
# ==========================================
def generate_reading_plan(total_pages, target_days):
    try:
        if target_days <= 0 or total_pages <= 0:
            return None

        daily_pages = max(1, round(total_pages / target_days))
        plan = {
            "total_pages": total_pages,
            "target_days": target_days,
            "daily_pages": daily_pages,
            "schedule": []
        }

        current_page = 1
        for day in range(1, target_days + 1):
            end_page = min(current_page + daily_pages - 1, total_pages)
            plan["schedule"].append({
                "day": day,
                "from_page": current_page,
                "to_page": end_page,
                "pages_count": end_page - current_page + 1
            })
            current_page = end_page + 1
            if current_page > total_pages:
                break

        logging.info(f"✅ Reading plan: {total_pages} pages in {target_days} days")
        return plan

    except Exception as e:
        logging.error(f"❌ Reading plan error: {e}")
        return None


# ==========================================
# TEST
# ==========================================
if __name__ == "__main__":
    print("✅ ScholarAI Bot ready!")
    print("   - generate_summary()   → page range content")
    print("   - generate_quiz()      → page range content")
    print("   - answer_question()    → BM25 RAG")
    print("   - search_and_answer()  → BM25 search")
    print("   - generate_reading_plan()")
