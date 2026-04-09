import numpy as np
import json
import os
import re
from sklearn.linear_model import LinearRegression
from sklearn.cluster import KMeans
from llama_index.llms.groq import Groq
import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer

nltk.download('vader_lexicon', quiet=True)

llm = Groq(model='llama-3.1-8b-instant', api_key=os.getenv('PLAYGROUND_API'), context_window=8192, temperature=0.2, max_tokens=1024)

MIN_TASKS_LINEAR = 5
MIN_TASKS_KMEANS = 10
MIN_REMARKS_AI = 3


def get_sentiment_score(text, rating):
    normalized = (rating - 3) / 2.0
    if text and text.strip():
        sia = SentimentIntensityAnalyzer()
        compound = sia.polarity_scores(text.strip())['compound']
        return (compound * 0.4) + (normalized * 0.6)
    return float(normalized)


def get_subject_time_ratios(completed_tasks):
    ratios = {}
    counts = {}
    for task in completed_tasks:
        sid = task.get('subject_id')
        actual = task.get('actual_minutes', 0)
        planned = task.get('duration_minutes', 0)
        if sid and planned > 0 and actual > 0:
            ratios[sid] = ratios.get(sid, 0.0) + (actual / planned)
            counts[sid] = counts.get(sid, 0) + 1
    for sid in ratios:
        ratios[sid] = ratios[sid] / counts[sid]
    return ratios, counts


def get_global_avg_ratio(subject_ratios):
    if not subject_ratios:
        return 1.0
    return sum(subject_ratios.values()) / len(subject_ratios)


def get_effective_ratio(subject_id, subject_ratios, subject_counts, global_avg, weight=3):
    if subject_id not in subject_ratios:
        return global_avg
    count = subject_counts[subject_id]
    ratio = subject_ratios[subject_id]
    return (ratio * count + global_avg * weight) / (count + weight)


def predict_topic_duration(subject_completed_tasks, estimated_minutes):
    pairs = [
        (t.get('duration_minutes', 0), t.get('actual_minutes', 0))
        for t in subject_completed_tasks
        if t.get('duration_minutes', 0) > 0 and t.get('actual_minutes', 0) > 0
    ]
    if len(pairs) < MIN_TASKS_LINEAR:
        return float(estimated_minutes)
    X = np.array([[p[0]] for p in pairs])
    y = np.array([p[1] for p in pairs])
    model = LinearRegression()
    model.fit(X, y)
    predicted = model.predict([[estimated_minutes]])[0]
    return max(estimated_minutes * 0.5, float(predicted))


def get_peak_window(completed_tasks):
    sessions = []
    for task in completed_tasks:
        start = task.get('start_time', '')
        actual = task.get('actual_minutes', 0)
        if start and actual > 0:
            try:
                h, m = map(int, start.split(':'))
                sessions.append([h * 60 + m, actual])
            except Exception:
                pass
    if len(sessions) < MIN_TASKS_KMEANS:
        return None
    X = np.array(sessions)
    kmeans = KMeans(n_clusters=2, random_state=0, n_init=10)
    kmeans.fit(X)
    centers = kmeans.cluster_centers_
    best = int(np.argmax([centers[i][1] for i in range(2)]))
    peak_min = int(centers[best][0])
    h = peak_min // 60
    m = peak_min % 60
    return f"{h:02d}:{m:02d}"


def get_mood_factor(recent_remarks):
    if not recent_remarks:
        return 0.0
    scores = [r.get('daily_score', 0.0) for r in recent_remarks]
    return sum(scores) / len(scores)


def ai_prioritize_topics(topics, completed_tasks, recent_remarks, subject_ratios, subject_counts, global_avg):
    mood = get_mood_factor(recent_remarks)
    subject_tasks = {}
    for ct in completed_tasks:
        sid = ct.get('subject_id', '')
        subject_tasks.setdefault(sid, []).append(ct)
    for t in topics:
        base = t.get('priority_score', 0)
        sid = t.get('subject_id', '')
        estimated_minutes = t.get('estimated_hours', 1) * 60
        predicted = predict_topic_duration(subject_tasks.get(sid, []), estimated_minutes)
        t['ai_estimated_minutes'] = round(predicted)
        if mood < -0.2:
            difficulty = t.get('difficulty', 3)
            strength = t.get('self_strength', 3)
            struggle_boost = (difficulty * 10) + ((5 - strength) * 8)
            t['ai_priority_score'] = base + struggle_boost * abs(mood)
        else:
            t['ai_priority_score'] = base
    topics.sort(key=lambda x: x['ai_priority_score'], reverse=True)
    return topics


def advanced_ai_prioritize_topics(topics, completed_tasks, recent_remarks, subject_ratios, subject_counts, global_avg, user_email, db):
    for t in topics:
        exam_score = t.get('exam_score', -1)
        session_scores = [ct.get('session_stars', 0) for ct in completed_tasks if ct.get('topic_id') == t['id'] and 'session_stars' in ct]
        avg_session_score = sum(session_scores) / len(session_scores) if session_scores else -1
        t['historical_exam_score'] = exam_score
        t['avg_session_score'] = avg_session_score
        t['base_priority'] = t.get('priority_score', 0)

    prompt_data = json.dumps([{
        'id': t['id'],
        'name': t['name'],
        'subject': t['subject_name'],
        'difficulty': t.get('difficulty', 3),
        'strength': t.get('self_strength', 3),
        'base_priority': t['base_priority'],
        'exam_score': t['historical_exam_score'],
        'avg_session_score': t['avg_session_score']
    } for t in topics])

    prompt = f"Analyze the following topics and their associated scores (exam_score 0-100, session_score 0-5). Re-prioritize them based on weaknesses revealed by low exam or session scores, combined with their base difficulty and strength. Return ONLY a JSON list of topic IDs in their new priority order from highest priority to lowest.\n\nTopics:\n{prompt_data}"
    
    try:
        response = llm.complete(prompt)
        prioritized_ids = json.loads(response.text.strip())
        id_to_topic = {t['id']: t for t in topics}
        sorted_topics = [id_to_topic[tid] for tid in prioritized_ids if tid in id_to_topic]
        
        missing = [t for t in topics if t['id'] not in prioritized_ids]
        sorted_topics.extend(missing)
        
        for idx, t in enumerate(sorted_topics):
            t['ai_priority_score'] = 1000 - idx 
            sid = t.get('subject_id', '')
            estimated_minutes = t.get('estimated_hours', 1) * 60
            subject_tasks = [ct for ct in completed_tasks if ct.get('subject_id') == sid]
            t['ai_estimated_minutes'] = round(predict_topic_duration(subject_tasks, estimated_minutes))
            
        return sorted_topics
    except Exception:
        return ai_prioritize_topics(topics, completed_tasks, recent_remarks, subject_ratios, subject_counts, global_avg)


def has_enough_data(completed_count, remarks_count):
    return completed_count >= MIN_TASKS_LINEAR and remarks_count >= MIN_REMARKS_AI