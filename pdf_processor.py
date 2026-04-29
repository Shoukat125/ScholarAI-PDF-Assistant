import os
import re
import logging
import fitz  # PyMuPDF
from rank_bm25 import BM25Okapi
from database import (
    add_book, get_book_by_id,
    save_pages, get_page_from_db,
    get_pages_range_from_db, get_all_pages_from_db,
    pages_exist_in_db
)

UPLOAD_FOLDER = "uploads"
LOG_FILE = "scholarai.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

WHITE_COLOR = 16777215  # #FFFFFF — colored header boxes ka white text


# ==========================================
# CORE: SMART PAGE EXTRACTOR
# Position-aware deduplication
# ==========================================
def extract_page_clean(page) -> str:
    """
    PyMuPDF 'dict' mode — position-aware smart extraction.

    Problems solved:
    1. WHITE text skip (color=16777215)
       → Colored header boxes (UNIT:7, EXTENDED READING) ka text
       → PDF mein 4-5 baar repeat hota hai — sab filter

    2. Position-based duplicate removal
       → Same (x, y, text) = duplicate span — skip
       → Different colors mein same text same jagah = decoration layer

    3. Same-line span merging (x-position se sort)
       → "B" + "OOK" same y pe = "BOOK" sahi likha

    4. Standalone page numbers remove (1, 2 ... 999)
    """
    data = page.get_text('dict')
    seen_positions = {}

    # Har unique span ko y_bucket -> [(x, text)] mein store karo
    spans_by_line = {}

    for block in data['blocks']:
        if block.get('type') != 0:  # sirf text blocks
            continue

        for line in block.get('lines', []):
            for span in line.get('spans', []):
                color = span.get('color', 0)
                text = span.get('text', '').strip()
                bbox = span.get('bbox', [0, 0, 0, 0])

                if not text:
                    continue

                # WHITE text skip — decorative header boxes
                if color == WHITE_COLOR:
                    continue

                # Position-based duplicate check
                y_bucket = round(bbox[1] / 8) * 8
                x_bucket = round(bbox[0] / 5) * 5
                pos_key = (y_bucket, x_bucket, text)

                if pos_key in seen_positions:
                    continue
                seen_positions[pos_key] = True

                if y_bucket not in spans_by_line:
                    spans_by_line[y_bucket] = []
                spans_by_line[y_bucket].append((bbox[0], text))

    # Y order mein sort — lines reconstruct karo
    lines = []
    prev_y = None
    for y in sorted(spans_by_line.keys()):
        spans = sorted(spans_by_line[y], key=lambda s: s[0])
        line_text = ' '.join(s[1] for s in spans)

        # Paragraph gap: y distance zyada ho toh blank line
        if prev_y is not None and (y - prev_y) > 20:
            lines.append('')

        lines.append(line_text)
        prev_y = y

    full_text = '\n'.join(lines)

    # Standalone page numbers remove
    full_text = re.sub(r'(?m)^\s*\d{1,3}\s*$', '', full_text)

    # Extra blank lines compress
    full_text = re.sub(r'\n{3,}', '\n\n', full_text).strip()

    return full_text


# ==========================================
# EXTRACT ALL PAGES
# ==========================================
def extract_pdf_pages(pdf_path: str):
    """
    Saari pages extract karo — PyMuPDF dict mode.
    Returns: (pages_list, total_pages)
    """
    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        pages = []

        for i, page in enumerate(doc):
            content = extract_page_clean(page)
            if content:
                pages.append({
                    "page_number": i + 1,
                    "content": content
                })

        doc.close()
        logging.info(f"✅ Extracted {len(pages)} pages from {pdf_path}")
        return pages, total_pages

    except Exception as e:
        logging.error(f"❌ PDF extraction error: {e}")
        return [], 0


# ==========================================
# SAVE PDF FILE
# ==========================================
def save_pdf(file, filename: str) -> str:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    logging.info(f"✅ PDF saved: {filepath}")
    return filepath


# ==========================================
# PROCESS & REGISTER PDF
# ==========================================
def process_and_register_pdf(file, filename: str, title: str, subject: str):
    try:
        filepath = save_pdf(file, filename)
        pages, total_pages = extract_pdf_pages(filepath)
        if total_pages == 0:
            logging.error(f"❌ No pages extracted from {filename}")
            return None
        book_id = add_book(
            title=title,
            filename=filename,
            subject=subject,
            total_pages=total_pages
        )
        save_pages(book_id, pages)
        logging.info(f"✅ Book: '{title}' | ID: {book_id} | Pages: {total_pages}")
        return book_id
    except Exception as e:
        logging.error(f"❌ PDF processing error: {e}")
        return None


# ==========================================
# GET SINGLE PAGE — Reader ke liye
# ==========================================
def get_page_content(book_id: int, page_number: int):
    try:
        # DB se lo (fast)
        if pages_exist_in_db(book_id):
            content = get_page_from_db(book_id, page_number)
            if content is not None:
                return content

        # Fallback: live extract
        book = get_book_by_id(book_id)
        if not book:
            return None
        filepath = os.path.join(UPLOAD_FOLDER, book[2])
        if not os.path.exists(filepath):
            return None

        doc = fitz.open(filepath)
        if page_number < 1 or page_number > len(doc):
            doc.close()
            return None
        content = extract_page_clean(doc[page_number - 1])
        doc.close()
        return content

    except Exception as e:
        logging.error(f"❌ Get page error: {e}")
        return None


# ==========================================
# GET PAGE RANGE — Summary/Quiz ke liye
# ==========================================
def get_pages_content(book_id: int, page_from: int, page_to: int):
    try:
        # DB se lo
        if pages_exist_in_db(book_id):
            rows = get_pages_range_from_db(book_id, page_from, page_to)
            if rows:
                return "\n\n".join(f"[Page {r[0]}]\n{r[1]}" for r in rows)

        # Fallback: file se
        book = get_book_by_id(book_id)
        if not book:
            return None
        filepath = os.path.join(UPLOAD_FOLDER, book[2])
        if not os.path.exists(filepath):
            return None

        doc = fitz.open(filepath)
        pf = max(1, page_from)
        pt = min(len(doc), page_to)
        combined = []
        for i in range(pf - 1, pt):
            content = extract_page_clean(doc[i])
            if content:
                combined.append(f"[Page {i+1}]\n{content}")
        doc.close()
        return "\n\n".join(combined)

    except Exception as e:
        logging.error(f"❌ Get pages error: {e}")
        return None


# ==========================================
# SEARCH IN PDF — BM25 based
# ==========================================
def search_in_pdf(book_id: int, query: str, top_k: int = 10):
    """
    BM25 se PDF mein search karo.
    Returns: list of {page_number, snippet} dicts
    """
    try:
        rows = get_all_pages_from_db(book_id)
        if not rows:
            # Fallback: file se search
            book = get_book_by_id(book_id)
            if not book:
                return []
            filepath = os.path.join(UPLOAD_FOLDER, book[2])
            if not os.path.exists(filepath):
                return []
            doc = fitz.open(filepath)
            rows = []
            for i, page in enumerate(doc):
                content = extract_page_clean(page)
                rows.append((i + 1, content))
            doc.close()

        page_numbers = [r[0] for r in rows]
        contents = [r[1] for r in rows]

        # BM25 scoring
        tokenized_corpus = [doc.lower().split() for doc in contents]
        tokenized_query = query.lower().split()
        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(tokenized_query)

        import numpy as np
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            if scores[idx] <= 0:
                continue
            content = contents[idx]
            # Snippet: query ke aas paas text
            query_lower = query.lower()
            content_lower = content.lower()
            match_idx = content_lower.find(query_lower.split()[0])
            if match_idx == -1:
                match_idx = 0
            start = max(0, match_idx - 100)
            end = min(len(content), match_idx + 250)
            snippet = content[start:end].strip().replace('\n', ' ')
            snippet = re.sub(r' +', ' ', snippet)

            results.append({
                "page_number": page_numbers[idx],
                "snippet": f"...{snippet}...",
                "score": round(float(scores[idx]), 3)
            })

        logging.info(f"🔍 BM25 search '{query}' found {len(results)} results in book {book_id}")
        return results

    except Exception as e:
        logging.error(f"❌ Search error: {e}")
        return []


# ==========================================
# BM25 SEARCH — RAG ke liye (internal)
# ==========================================
def bm25_search(book_id: int, query: str, top_k: int = 4):
    """
    Q&A ke liye relevant pages dhundo.
    Returns combined context string.
    """
    try:
        rows = get_all_pages_from_db(book_id)
        if not rows:
            return None

        page_numbers = [r[0] for r in rows]
        contents = [r[1] for r in rows]

        tokenized_corpus = [doc.lower().split() for doc in contents]
        tokenized_query = query.lower().split()
        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(tokenized_query)

        import numpy as np
        top_indices = np.argsort(scores)[::-1][:top_k]

        relevant = []
        for idx in top_indices:
            if scores[idx] > 0:
                relevant.append({
                    "page_number": page_numbers[idx],
                    "content": contents[idx],
                    "score": round(float(scores[idx]), 3)
                })

        if not relevant:
            for idx in top_indices[:2]:
                relevant.append({
                    "page_number": page_numbers[idx],
                    "content": contents[idx],
                    "score": 0.0
                })

        context = "\n\n".join(
            f"[Page {r['page_number']}]\n{r['content']}"
            for r in relevant
        )

        logging.info(f"✅ BM25 RAG book={book_id} pages={[r['page_number'] for r in relevant]}")
        return context

    except Exception as e:
        logging.error(f"❌ BM25 RAG error: {e}")
        return None


# ==========================================
# GET TOTAL PAGES
# ==========================================
def get_total_pages(book_id: int) -> int:
    try:
        book = get_book_by_id(book_id)
        return book[4] if book else 0
    except Exception as e:
        logging.error(f"❌ Get total pages error: {e}")
        return 0


# ==========================================
# DELETE PDF FILE
# ==========================================
def delete_pdf_file(filename: str) -> bool:
    try:
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            logging.info(f"✅ PDF deleted: {filepath}")
            return True
        return False
    except Exception as e:
        logging.error(f"❌ Delete PDF error: {e}")
        return False


# ==========================================
# VALIDATE PDF
# ==========================================
def validate_pdf(file) -> tuple:
    if not file.filename.lower().endswith(".pdf"):
        return False, "Sirf PDF files allowed hain!"
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 20 * 1024 * 1024:
        return False, "File size 20MB se zyada nahi honi chahiye!"
    return True, None


# ==========================================
# TEST
# ==========================================
if __name__ == "__main__":
    print("✅ ScholarAI PDF Processor ready!")
    print("   Fix 1: White text filter (color=16777215)")
    print("   Fix 2: Position-based duplicate span removal")
    print("   Fix 3: Same-line span merging (x-sorted)")
    print("   Fix 4: BM25 search (replaces pypdf search)")
