def notes_search_view(request, topic_id):
    if request.method != 'POST':
        return JsonResponse({'results': []})
    user_email = get_current_user(request)
    if not user_email:
        return JsonResponse({'results': []})

    query = request.POST.get('query', '').strip()
    if not query:
        return JsonResponse({'results': []})

    chat_history_raw = request.POST.get('chat_history', '[]')
    try:
        chat_history = json.loads(chat_history_raw)
    except Exception:
        chat_history = []

    try:
        results = notes_engine.search_notes(topic_id, user_email, query)
    except Exception:
        results = []

    answer = ''
    if results:
        try:
            answer = notes_engine.summarize_with_groq(query, results, chat_history)
        except Exception:
            answer = ''

    return JsonResponse({'results': results, 'answer': answer})
