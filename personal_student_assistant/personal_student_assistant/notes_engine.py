import os

_embedder = None
_reranker = None
_chroma_client = None
_collection = None

CHROMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'chroma_db')

TESSERACT_PATHS = [
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    '/usr/bin/tesseract',
    '/usr/local/bin/tesseract',
    '/opt/homebrew/bin/tesseract',
]


def _configure_tesseract():
    try:
        import pytesseract
        custom = os.environ.get('TESSERACT_CMD', '')
        if custom and os.path.isfile(custom):
            pytesseract.pytesseract.tesseract_cmd = custom
            return True
        for path in TESSERACT_PATHS:
            if os.path.isfile(path):
                pytesseract.pytesseract.tesseract_cmd = path
                return True
        return False
    except Exception:
        return False


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer('all-MiniLM-L6-v2')
    return _embedder


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers.cross_encoder import CrossEncoder
        _reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-2-v2')
    return _reranker


def _get_collection():
    global _chroma_client, _collection
    if _chroma_client is None:
        import chromadb
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
        _collection = _chroma_client.get_or_create_collection('topic_notes')
    return _collection


def _extract_pdf(file_path):
    try:
        from pdfminer.high_level import extract_text
        return extract_text(file_path) or ''
    except Exception:
        return ''


def _extract_image(file_path):
    try:
        import pytesseract
        from PIL import Image
        _configure_tesseract()
        return pytesseract.image_to_string(Image.open(file_path)) or ''
    except Exception:
        return ''


def _split_into_sentences(text):
    import re
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]


def _chunk_text(text, size=60, overlap=10):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = ' '.join(words[i:i + size])
        if chunk.strip():
            chunks.append(chunk)
        i += size - overlap
    return chunks


def ingest_notes(topic_id, user_email, file_entries):
    combined_text = ''
    for file_path, filename in file_entries:
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext == 'pdf':
            text = _extract_pdf(file_path)
        elif ext in ('png', 'jpg', 'jpeg', 'bmp', 'tiff', 'webp'):
            text = _extract_image(file_path)
        else:
            continue
        if text.strip():
            combined_text += text.strip() + '\n\n'

    combined_text = combined_text.strip()
    if not combined_text:
        return 0, 'No text could be extracted'

    chunks = _chunk_text(combined_text)
    if not chunks:
        return 0, 'No content after chunking'

    collection = _get_collection()
    embedder = _get_embedder()
    user_topic = '{}_{}'.format(user_email, topic_id)

    try:
        existing = collection.get(where={'user_topic': user_topic})
        if existing['ids']:
            collection.delete(ids=existing['ids'])
    except Exception:
        pass

    embeddings = embedder.encode(chunks).tolist()
    ids = ['{}__{}'.format(user_topic, i) for i in range(len(chunks))]
    metadatas = [{'user_topic': user_topic, 'topic_id': topic_id, 'user_email': user_email} for _ in chunks]

    collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    return len(chunks), 'ok'


def has_notes(topic_id, user_email):
    try:
        collection = _get_collection()
        user_topic = '{}_{}'.format(user_email, topic_id)
        result = collection.get(where={'user_topic': user_topic}, limit=1)
        return len(result['ids']) > 0
    except Exception:
        return False


def search_notes(topic_id, user_email, query, top_k=8, rerank_top=4):
    try:
        collection = _get_collection()
        embedder = _get_embedder()
        user_topic = '{}_{}'.format(user_email, topic_id)

        query_emb = embedder.encode([query])[0].tolist()
        results = collection.query(
            query_embeddings=[query_emb],
            n_results=min(top_k, 10),
            where={'user_topic': user_topic}
        )

        docs = results['documents'][0] if results['documents'] else []
        if not docs:
            return []

        reranker = _get_reranker()
        pairs = [[query, doc] for doc in docs]
        raw_scores = reranker.predict(pairs)
        scores = raw_scores.tolist() if hasattr(raw_scores, 'tolist') else list(raw_scores)

        ranked = sorted(zip(scores, docs), reverse=True)
        return [{'text': doc, 'score': float(score)} for score, doc in ranked[:rerank_top]]
    except Exception:
        return []


def summarize_with_groq(query, chunks, chat_history):
    from llama_index.llms.groq import Groq
    context = '\n\n'.join([c.get('text', '') if isinstance(c, dict) else str(c) for c in chunks])
    history_text = ''
    for turn in chat_history[-2:]:
        history_text += 'User: {}\nAssistant: {}\n\n'.format(turn.get('user', ''), turn.get('assistant', ''))
    system_prompt = 'You are a helpful study assistant. Answer the student\'s question using only the provided context from their personal notes. Be concise, accurate, and clear. Do not mention that you are using context or notes.'
    full_prompt = '{}\n\n{}Context:\n{}\n\nQuestion: {}\nAnswer:'.format(system_prompt, history_text, context, query)
    llm = Groq(model='llama-3.1-8b-instant', api_key=os.getenv('PLAYGROUND_API'), context_window=8192, temperature=0.2, max_tokens=512)
    response = llm.complete(full_prompt)
    return response.text.strip()
