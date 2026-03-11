from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib.auth.hashers import make_password, check_password
from .db_connector import get_db
from .ai_scheduler import (
    get_sentiment_score, get_peak_window, get_subject_time_ratios, 
    get_global_avg_ratio, ai_prioritize_topics, has_enough_data, get_effective_ratio
)
from bson import ObjectId
from datetime import datetime, timedelta, date
import json


def get_current_user(request):
    return request.session.get('user_email')


def fix_id(doc):
    doc['id'] = str(doc['_id'])
    return doc


def calculate_streak(user_email, db):
    streak = 0
    started = False
    for i in range(90):
        d = (date.today() - timedelta(days=i)).isoformat()
        completed = db['scheduled_tasks'].count_documents({
            'user_email': user_email,
            'completed': True,
            'date': d,
            'is_break': {'$ne': True}
        })
        if completed > 0:
            streak += 1
            started = True
        elif started:
            break
    return streak


def get_priority_score(topic, subject):
    exam_date_str = subject.get('exam_date', '')
    try:
        exam = datetime.strptime(exam_date_str, '%Y-%m-%d').date()
        days_until = max(1, (exam - date.today()).days)
    except Exception:
        days_until = 30
    urgency = max(0, 100 - days_until * 2)
    difficulty_score = topic.get('difficulty', 3) * 10
    weakness_score = (5 - topic.get('self_strength', 3)) * 8
    weightage_score = subject.get('weightage', 5) * 5
    return urgency + difficulty_score + weakness_score + weightage_score


def award_badges(user_email, db):
    user = db['users'].find_one({'email': user_email})
    badges = list(user.get('badges', []))
    total_completed = db['scheduled_tasks'].count_documents({
        'user_email': user_email, 'completed': True, 'is_break': {'$ne': True}
    })
    points = user.get('points', 0)
    streak = calculate_streak(user_email, db)
    checks = [
        (total_completed >= 1, 'First Task 🎯'),
        (total_completed >= 10, 'Task Achiever 🏅'),
        (total_completed >= 50, 'Study Champion 🏆'),
        (points >= 100, '100 Points ⭐'),
        (points >= 500, '500 Points 💎'),
        (streak >= 3, '3-Day Streak 🔥'),
        (streak >= 7, '7-Day Streak 🌟'),
    ]
    for condition, badge in checks:
        if condition and badge not in badges:
            badges.append(badge)
    db['users'].update_one({'email': user_email}, {'$set': {'badges': badges}})


def _get_total_study_hours(user_email, db):
    total_min = 0
    for ct in db['scheduled_tasks'].find({
        'user_email': user_email, 'completed': True, 'is_break': {'$ne': True}
    }):
        total_min += ct.get('actual_minutes', ct.get('duration_minutes', 0))
    return round(total_min / 60, 1)


def signup_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        phone = request.POST.get('phone')
        password = request.POST.get('password')
        db = get_db()
        if db['users'].find_one({'email': email}):
            return render(request, 'signup.html', {'error': 'Email is already registered'})
        db['users'].insert_one({
            'name': name, 'email': email, 'phone': phone,
            'password': make_password(password),
            'study_start_time': '18:00', 'study_end_time': '22:00',
            'session_duration': 25, 'break_duration': 5,
            'points': 0, 'badges': [],
        })
        request.session['user_email'] = email
        request.session['user_name'] = name
        return redirect('home')
    return render(request, 'signup.html')


def login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        db = get_db()
        user = db['users'].find_one({'email': email})
        if user and check_password(password, user['password']):
            request.session['user_email'] = user['email']
            request.session['user_name'] = user['name']
            return redirect('home')
        return render(request, 'login.html', {'error': 'Invalid email or password'})
    return render(request, 'login.html')


def logout_view(request):
    request.session.flush()
    return redirect('login')


def home_view(request):
    user_email = get_current_user(request)
    if not user_email:
        return redirect('login')

    db = get_db()
    user = db['users'].find_one({'email': user_email})
    today_str = date.today().isoformat()

    today_tasks = list(db['scheduled_tasks'].find(
        {'user_email': user_email, 'date': today_str}
    ).sort('start_time', 1))
    for t in today_tasks:
        fix_id(t)
    _ht_ids = list(set(str(t.get('topic_id', '')) for t in today_tasks if not t.get('is_break') and t.get('topic_id')))
    _ht_secs = {}
    for _tid in _ht_ids:
        if _tid:
            _sl = list(db['sections'].find({'topic_id': _tid, 'user_email': user_email}).sort('order', 1))
            for _s in _sl:
                fix_id(_s)
            _ht_secs[_tid] = _sl
    for t in today_tasks:
        if not t.get('is_break') and t.get('topic_id'):
            _tid = str(t['topic_id'])
            _sl = _ht_secs.get(_tid, [])
            _incomplete_secs = [_s for _s in _sl if not _s.get('completed')]
            _pin = str(t.get('pinned_section_id', ''))
            if _pin == '__done__':
                t['current_section'] = _incomplete_secs[0] if _incomplete_secs else None
            elif _pin:
                _pinned_sec = next((_s for _s in _sl if _s['id'] == _pin), None)
                if _pinned_sec and not _pinned_sec.get('completed'):
                    t['current_section'] = _pinned_sec
                else:
                    t['current_section'] = _incomplete_secs[0] if _incomplete_secs else None
            else:
                t['current_section'] = _incomplete_secs[0] if _incomplete_secs else None
            t['all_sections_complete'] = bool(_sl) and not bool(_incomplete_secs)

    upcoming_exams = list(db['subjects'].find({
        'user_email': user_email,
        'exam_date': {'$gte': today_str}
    }).sort('exam_date', 1).limit(5))
    for s in upcoming_exams:
        fix_id(s)
        try:
            exam = datetime.strptime(s['exam_date'], '%Y-%m-%d').date()
            s['days_until_exam'] = (exam - date.today()).days
        except Exception:
            s['days_until_exam'] = None

    today_completed = db['scheduled_tasks'].count_documents({
        'user_email': user_email, 'completed': True,
        'date': today_str, 'is_break': {'$ne': True}
    })
    today_total = db['scheduled_tasks'].count_documents({
        'user_email': user_email, 'date': today_str, 'is_break': {'$ne': True}
    })
    streak = calculate_streak(user_email, db)
    subject_count = db['subjects'].count_documents({'user_email': user_email})
    pending_topics = db['topics'].count_documents({'user_email': user_email, 'status': 'pending'})

    today_remark = db['daily_remarks'].find_one({'user_email': user_email, 'date': today_str})
    show_remark_prompt = today_completed > 0 and today_remark is None

    completed_tasks_all = list(db['scheduled_tasks'].find({
        'user_email': user_email, 'completed': True, 'is_break': {'$ne': True}
    }))
    peak_window = get_peak_window(completed_tasks_all)

    return render(request, 'home.html', {
        'user': user,
        'today_tasks': today_tasks,
        'upcoming_exams': upcoming_exams,
        'today_completed': today_completed,
        'today_total': today_total,
        'streak': streak,
        'subject_count': subject_count,
        'pending_topics': pending_topics,
        'today': today_str,
        'show_remark_prompt': show_remark_prompt,
        'today_remark': today_remark,
        'peak_window': peak_window,
    })


def subjects_view(request):
    user_email = get_current_user(request)
    if not user_email:
        return redirect('login')

    db = get_db()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            db['subjects'].insert_one({
                'user_email': user_email,
                'name': request.POST.get('name'),
                'exam_date': request.POST.get('exam_date'),
                'weightage': int(request.POST.get('weightage', 5)),
                'color': request.POST.get('color', '#007BFF'),
                'created_at': datetime.utcnow().isoformat(),
            })
        elif action == 'edit':
            sid = request.POST.get('subject_id')
            db['subjects'].update_one(
                {'_id': ObjectId(sid), 'user_email': user_email},
                {'$set': {
                    'name': request.POST.get('name'),
                    'exam_date': request.POST.get('exam_date'),
                    'weightage': int(request.POST.get('weightage', 5)),
                    'color': request.POST.get('color', '#007BFF'),
                }}
            )
        elif action == 'delete':
            sid = request.POST.get('subject_id')
            db['subjects'].delete_one({'_id': ObjectId(sid), 'user_email': user_email})
            db['topics'].delete_many({'subject_id': sid, 'user_email': user_email})
        return redirect('subjects')

    subjects = list(db['subjects'].find({'user_email': user_email}).sort('exam_date', 1))
    for s in subjects:
        fix_id(s)
        topic_count = db['topics'].count_documents({'subject_id': s['id'], 'user_email': user_email})
        completed_count = db['topics'].count_documents({'subject_id': s['id'], 'user_email': user_email, 'status': 'completed'})
        s['topic_count'] = topic_count
        s['completed_count'] = completed_count
        s['pending_count'] = topic_count - completed_count
        try:
            exam = datetime.strptime(s['exam_date'], '%Y-%m-%d').date()
            s['days_until_exam'] = (exam - date.today()).days
        except Exception:
            s['days_until_exam'] = None

    return render(request, 'subjects.html', {
        'user': db['users'].find_one({'email': user_email}),
        'subjects': subjects,
    })


def topics_view(request, subject_id):
    user_email = get_current_user(request)
    if not user_email:
        return redirect('login')

    db = get_db()
    subject = db['subjects'].find_one({'_id': ObjectId(subject_id), 'user_email': user_email})
    if not subject:
        return redirect('subjects')
    fix_id(subject)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            db['topics'].insert_one({
                'user_email': user_email,
                'subject_id': subject_id,
                'subject_name': subject['name'],
                'name': request.POST.get('name'),
                'difficulty': int(request.POST.get('difficulty', 3)),
                'self_strength': int(request.POST.get('strength', 3)),
                'estimated_hours': float(request.POST.get('estimated_hours', 1)),
                'status': 'pending',
                'created_at': datetime.utcnow().isoformat(),
            })
        elif action == 'edit':
            tid = ObjectId(request.POST.get('topic_id'))
            db['topics'].update_one(
                {'_id': tid, 'user_email': user_email},
                {'$set': {
                    'name': request.POST.get('name'),
                    'difficulty': int(request.POST.get('difficulty', 3)),
                    'self_strength': int(request.POST.get('strength', 3)),
                    'estimated_hours': float(request.POST.get('estimated_hours', 1)),
                }}
            )
        elif action == 'delete':
            db['topics'].delete_one({
                '_id': ObjectId(request.POST.get('topic_id')),
                'user_email': user_email
            })
        elif action == 'toggle_status':
            tid = ObjectId(request.POST.get('topic_id'))
            topic = db['topics'].find_one({'_id': tid})
            new_s = 'completed' if topic['status'] == 'pending' else 'pending'
            db['topics'].update_one({'_id': tid}, {'$set': {'status': new_s}})
            if new_s == 'completed':
                db['users'].update_one({'email': user_email}, {'$inc': {'points': 15}})
                award_badges(user_email, db)
        elif action == 'add_section':
            topic_id_for_sec = request.POST.get('topic_id')
            max_sec = db['sections'].find_one({'topic_id': topic_id_for_sec, 'user_email': user_email}, sort=[('order', -1)])
            next_order = (max_sec.get('order', 0) + 1) if max_sec else 0
            db['sections'].insert_one({
                'user_email': user_email,
                'topic_id': topic_id_for_sec,
                'name': request.POST.get('section_name'),
                'order': next_order,
            })
        elif action == 'edit_section':
            db['sections'].update_one(
                {'_id': ObjectId(request.POST.get('section_id')), 'user_email': user_email},
                {'$set': {'name': request.POST.get('section_name')}}
            )
        elif action == 'delete_section':
            sec_del_id = request.POST.get('section_id')
            db['sections'].delete_one({'_id': ObjectId(sec_del_id), 'user_email': user_email})
        elif action == 'reorder_sections':
            ids = json.loads(request.POST.get('order', '[]'))
            for i, sid2 in enumerate(ids):
                db['sections'].update_one({'_id': ObjectId(sid2), 'user_email': user_email}, {'$set': {'order': i}})
            return redirect('topics', subject_id=subject_id)
        return redirect('topics', subject_id=subject_id)

    topics = list(db['topics'].find({'subject_id': subject_id, 'user_email': user_email}))
    for t in topics:
        fix_id(t)
        t['priority_score'] = get_priority_score(t, subject)
        t['diff_filled'] = t.get('difficulty', 3)
        t['diff_empty'] = 5 - t.get('difficulty', 3)
        t['strength_filled'] = t.get('self_strength', 3)
        t['strength_empty'] = 5 - t.get('self_strength', 3)

    topics.sort(key=lambda x: x['priority_score'], reverse=True)
    total_topics = len(topics)
    completed_topics = sum(1 for t in topics if t['status'] == 'completed')

    topic_ids = [t['id'] for t in topics]
    all_sections = list(db['sections'].find({'topic_id': {'$in': topic_ids}, 'user_email': user_email}).sort('order', 1))
    for s in all_sections:
        fix_id(s)
    sections_by_topic = {}
    for s in all_sections:
        sections_by_topic.setdefault(s['topic_id'], []).append(s)
    for t in topics:
        t['sections'] = sections_by_topic.get(t['id'], [])

    return render(request, 'topics.html', {
        'user': db['users'].find_one({'email': user_email}),
        'subject': subject,
        'topics': topics,
        'total_topics': total_topics,
        'completed_topics': completed_topics,
    })


def schedule_view(request):
    user_email = get_current_user(request)
    if not user_email:
        return redirect('login')

    db = get_db()
    user = db['users'].find_one({'email': user_email})

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_study_hours':
            db['users'].update_one({'email': user_email}, {'$set': {
                'study_start_time': request.POST.get('study_start', '18:00'),
                'study_end_time': request.POST.get('study_end', '22:00'),
                'session_duration': int(request.POST.get('session_duration', 25)),
                'break_duration': int(request.POST.get('break_duration', 5)),
            }})
            user = db['users'].find_one({'email': user_email})
        elif action == 'generate':
            user = db['users'].find_one({'email': user_email})
            generate_week_schedule(user_email, db, user, use_ai=False)
            return redirect('schedule')
        elif action == 'generate_today':
            user = db['users'].find_one({'email': user_email})
            generate_week_schedule(user_email, db, user, single_day=True, use_ai=False)
            return redirect('schedule')
        elif action == 'generate_ai':
            user = db['users'].find_one({'email': user_email})
            generate_week_schedule(user_email, db, user, use_ai=True)
            return redirect('schedule')
        elif action == 'generate_today_ai':
            user = db['users'].find_one({'email': user_email})
            generate_week_schedule(user_email, db, user, single_day=True, use_ai=True)
            return redirect('schedule')
        elif action == 'clear':
            db['scheduled_tasks'].delete_many({
                'user_email': user_email,
                'date': {'$gte': date.today().isoformat()},
                'completed': {'$ne': True},
            })
            return redirect('schedule')

    today = date.today()
    days_until_sunday = 6 - today.weekday()
    week_days = []
    for i in range(days_until_sunday + 1):
        d = (today + timedelta(days=i)).isoformat()
        tasks = list(db['scheduled_tasks'].find(
            {'user_email': user_email, 'date': d}
        ).sort('start_time', 1))
        for t in tasks:
            fix_id(t)
        week_days.append({
            'date': d,
            'date_short': d[5:],
            'label': 'Today' if i == 0 else (today + timedelta(days=i)).strftime('%a'),
            'is_today': i == 0,
            'tasks': tasks,
        })

    has_future_tasks = db['scheduled_tasks'].count_documents({
        'user_email': user_email,
        'date': {'$gte': today.isoformat()},
        'completed': {'$ne': True},
    }) > 0

    all_topic_ids = list(set(
        str(t.get('topic_id', ''))
        for day in week_days for t in day['tasks']
        if not t.get('is_break') and t.get('topic_id')
    ))
    sections_by_topic = {}
    for tid in all_topic_ids:
        if tid:
            secs = list(db['sections'].find({'topic_id': tid, 'user_email': user_email}).sort('order', 1))
            for s in secs:
                fix_id(s)
            sections_by_topic[tid] = secs
    for day in week_days:
        for t in day['tasks']:
            if not t.get('is_break') and t.get('topic_id'):
                tid = str(t['topic_id'])
                secs = sections_by_topic.get(tid, [])
                _incomplete_secs = [s for s in secs if not s.get('completed')]
                pinned_id = str(t.get('pinned_section_id', ''))
                if pinned_id == '__done__':
                    t['current_section'] = _incomplete_secs[0] if _incomplete_secs else None
                elif pinned_id:
                    _pinned_sec = next((s for s in secs if s['id'] == pinned_id), None)
                    if _pinned_sec and not _pinned_sec.get('completed'):
                        t['current_section'] = _pinned_sec
                    else:
                        t['current_section'] = _incomplete_secs[0] if _incomplete_secs else None
                else:
                    t['current_section'] = _incomplete_secs[0] if _incomplete_secs else None
                t['all_sections_complete'] = bool(secs) and not bool(_incomplete_secs)

    all_subjects = list(db['subjects'].find({'user_email': user_email}).sort('exam_date', 1))
    subject_remaining = []
    for s in all_subjects:
        sid = str(s['_id'])
        pending_topics = list(db['topics'].find({'subject_id': sid, 'user_email': user_email, 'status': 'pending'}))
        total_est_min = sum(int(t.get('estimated_hours', 0) * 60) for t in pending_topics)
        completed_min = 0
        for t in pending_topics:
            for ct in db['scheduled_tasks'].find({'topic_id': str(t['_id']), 'user_email': user_email, 'completed': True}):
                completed_min += ct.get('duration_minutes', 0)
        remaining_min = max(0, total_est_min - completed_min)
        subject_remaining.append({
            'name': s['name'],
            'remaining_hours': round(remaining_min / 60, 1),
            'exam_date': s.get('exam_date', ''),
        })

    return render(request, 'schedule.html', {
        'user': user,
        'week_days': week_days,
        'today': today.isoformat(),
        'has_future_tasks': has_future_tasks,
        'subject_remaining': subject_remaining,
    })


def generate_week_schedule(user_email, db, user, single_day=False, use_ai=False):
    now = datetime.now()
    today = date.today()

    if single_day:
        num_days = 1
    else:
        days_until_sunday = 6 - today.weekday()
        num_days = days_until_sunday + 1

    study_start = user.get('study_start_time', '18:00')
    study_end = user.get('study_end_time', '22:00')
    session_dur = int(user.get('session_duration', 25))
    break_dur = int(user.get('break_duration', 5))

    sh, sm = map(int, study_start.split(':'))
    eh, em = map(int, study_end.split(':'))
    start_min_global = sh * 60 + sm
    end_min_global = eh * 60 + em

    if end_min_global - start_min_global <= 0:
        return

    if single_day:
        db['scheduled_tasks'].delete_many({
            'user_email': user_email,
            'date': today.isoformat(),
            'completed': {'$ne': True},
        })
    else:
        db['scheduled_tasks'].delete_many({
            'user_email': user_email,
            'date': {'$gte': today.isoformat()},
            'completed': {'$ne': True},
        })

    subjects_map = {str(s['_id']): s for s in db['subjects'].find({'user_email': user_email})}
    topics = list(db['topics'].find({'user_email': user_email, 'status': 'pending'}))

    for t in topics:
        t['id'] = str(t['_id'])
        subj = subjects_map.get(t['subject_id'], {})
        t['priority_score'] = get_priority_score(t, subj)
        completed_min = sum(
            ct.get('duration_minutes', 0)
            for ct in db['scheduled_tasks'].find({
                'topic_id': t['id'],
                'user_email': user_email,
                'completed': True,
            })
        )
        total_min = int(t.get('estimated_hours', 1) * 60)
        t['remaining_minutes'] = max(0, total_min - completed_min)

    subject_ratios, subject_counts, global_avg = {}, {}, 1.0

    if use_ai:
        completed_tasks_all = list(db['scheduled_tasks'].find({
            'user_email': user_email, 'completed': True, 'is_break': {'$ne': True}
        }))
        recent_remarks = list(db['daily_remarks'].find({'user_email': user_email}).sort('date', -1).limit(7))
        subject_ratios, subject_counts = get_subject_time_ratios(completed_tasks_all)
        global_avg = get_global_avg_ratio(subject_ratios)
        for t in topics:
            t['already_done_minutes'] = sum(
                ct.get('duration_minutes', 0)
                for ct in completed_tasks_all if ct.get('topic_id') == t['id']
            )
        topics = ai_prioritize_topics(topics, completed_tasks_all, recent_remarks, subject_ratios, subject_counts, global_avg)
        for t in topics:
            already_done = t.get('already_done_minutes', 0)
            ai_total = t.get('ai_estimated_minutes', int(t.get('estimated_hours', 1) * 60))
            t['remaining_minutes'] = max(0, ai_total - already_done)
        topics = [t for t in topics if t['remaining_minutes'] > 0]
        score_key = 'ai_priority_score'
    else:
        topics = [t for t in topics if t['remaining_minutes'] > 0]
        topics.sort(key=lambda x: x['priority_score'], reverse=True)
        score_key = 'priority_score'

    if not topics:
        return

    # Group topics by subject, preserving priority order within each subject
    subject_order = []
    subject_topics = {}
    for t in topics:
        sid = t['subject_id']
        if sid not in subject_topics:
            subject_order.append(sid)
            subject_topics[sid] = []
        subject_topics[sid].append(t)

    # Compute subject-level priority score (max of its topics)
    subject_scores = {}
    for sid in subject_order:
        subject_scores[sid] = max(max(t.get(score_key, 1), 1) for t in subject_topics[sid])

    topic_remaining = {t['id']: t['remaining_minutes'] for t in topics}

    day_window = end_min_global - start_min_global
    total_remaining = sum(t['remaining_minutes'] for t in topics)
    if single_day or num_days == 1:
        daily_cap = day_window
    else:
        daily_cap = max(session_dur * 2, round(total_remaining / num_days))
    daily_cap = min(daily_cap, day_window)

    # Build day-to-subject assignment: each day gets ONE subject
    # Assign days proportionally to subject priority scores
    # Higher priority subject gets more days
    total_score = sum(subject_scores.values()) or 1
    day_assignments = []
    subject_day_counts = {sid: max(1, round(num_days * subject_scores[sid] / total_score)) for sid in subject_order}

    # Fill day_assignments list respecting counts, cycling through subjects
    sid_cycle = []
    for sid in subject_order:
        sid_cycle.extend([sid] * subject_day_counts[sid])
    # Trim or extend to exactly num_days
    while len(sid_cycle) < num_days:
        sid_cycle.append(subject_order[len(sid_cycle) % len(subject_order)])
    sid_cycle = sid_cycle[:num_days]
    day_assignments = sid_cycle

    for day_offset in range(num_days):
        if not any(v > 0 for v in topic_remaining.values()):
            break

        d = (today + timedelta(days=day_offset)).isoformat()

        if day_offset == 0:
            now_min = now.hour * 60 + now.minute
            cur_min = max(start_min_global, now_min)
            end_min_today = end_min_global
        else:
            cur_min = start_min_global
            end_min_today = end_min_global

        if cur_min >= end_min_today:
            continue

        available_window = min(daily_cap, end_min_today - cur_min)
        if available_window < session_dur:
            continue

        # Pick the assigned subject for this day; fallback to highest priority with remaining work
        assigned_sid = day_assignments[day_offset]
        if not any(topic_remaining[t['id']] > 0 for t in subject_topics.get(assigned_sid, [])):
            # This subject is done, find next subject with remaining work
            assigned_sid = next(
                (sid for sid in subject_order if any(topic_remaining[t['id']] > 0 for t in subject_topics[sid])),
                None
            )
        if assigned_sid is None:
            break

        day_topics = [t for t in subject_topics[assigned_sid] if topic_remaining[t['id']] > 0]
        if not day_topics:
            break

        # Schedule session_dur chunks for each topic in this subject, one at a time
        first_session = True
        for t in day_topics:
            if use_ai:
                ratio = get_effective_ratio(t['subject_id'], subject_ratios, subject_counts, global_avg)
                effective_session_dur = max(session_dur, min(round(session_dur * ratio), session_dur * 3))
            else:
                effective_session_dur = session_dur
            while topic_remaining[t['id']] > 0 and cur_min < end_min_today:
                if not first_session:
                    b_end = cur_min + break_dur
                    if b_end >= end_min_today:
                        break
                    db['scheduled_tasks'].insert_one({
                        'user_email': user_email,
                        'date': d,
                        'topic_name': 'Break',
                        'subject_name': '',
                        'duration_minutes': break_dur,
                        'start_time': f"{cur_min // 60:02d}:{cur_min % 60:02d}",
                        'end_time': f"{b_end // 60:02d}:{b_end % 60:02d}",
                        'is_break': True,
                        'completed': False,
                    })
                    cur_min = b_end

                chunk = min(effective_session_dur, topic_remaining[t['id']], end_min_today - cur_min)
                if chunk < 5:
                    break

                t_end = cur_min + chunk
                db['scheduled_tasks'].insert_one({
                    'user_email': user_email,
                    'date': d,
                    'topic_id': t['id'],
                    'topic_name': t['name'],
                    'subject_name': t['subject_name'],
                    'subject_id': t['subject_id'],
                    'duration_minutes': chunk,
                    'start_time': f"{cur_min // 60:02d}:{cur_min % 60:02d}",
                    'end_time': f"{t_end // 60:02d}:{t_end % 60:02d}",
                    'is_break': False,
                    'completed': False,
                    'priority_score': t['priority_score'],
                    'ai_generated': use_ai,
                })

                topic_remaining[t['id']] = max(0, topic_remaining[t['id']] - chunk)
                cur_min = t_end
                first_session = False

            if cur_min >= end_min_today:
                break



def complete_task(request):
    if request.method != 'POST':
        return redirect('home')
    user_email = get_current_user(request)
    if not user_email:
        return redirect('login')

    task_id = request.POST.get('task_id')
    redirect_to = request.POST.get('redirect_to', 'home')
    continue_sec = request.POST.get('continue_section', '')
    cur_sec_id = request.POST.get('current_section_id', '')

    db = get_db()
    task = db['scheduled_tasks'].find_one({'_id': ObjectId(task_id), 'user_email': user_email})
    _ajax_resp = {'ok': True}

    if task and not task.get('completed'):
        now_dt = datetime.now()
        try:
            task_date = task.get('date', date.today().isoformat())
            task_start = task.get('start_time', '00:00')
            start_dt = datetime.strptime(task_date + ' ' + task_start, '%Y-%m-%d %H:%M')
            actual = max(1, int((now_dt - start_dt).total_seconds() / 60))
        except Exception:
            actual = task.get('duration_minutes', 0)
        update_data = {
            'completed': True,
            'completed_at': now_dt.isoformat(),
            'actual_minutes': actual,
        }
        if cur_sec_id:
            update_data['completed_section_id'] = cur_sec_id
            try:
                sec_doc = db['sections'].find_one({'_id': ObjectId(cur_sec_id)})
                if sec_doc:
                    update_data['completed_section_name'] = sec_doc.get('name', '')
            except Exception:
                pass
        db['scheduled_tasks'].update_one(
            {'_id': ObjectId(task_id)},
            {'$set': update_data}
        )

        if cur_sec_id and task.get('topic_id'):
            next_tasks = list(db['scheduled_tasks'].find({
                'user_email': user_email,
                'topic_id': task['topic_id'],
                'completed': {'$ne': True},
                'date': {'$gte': date.today().isoformat()},
            }).sort([('date', 1), ('start_time', 1)]).limit(5))
            if continue_sec == 'yes':
                for nt in next_tasks:
                    db['scheduled_tasks'].update_one(
                        {'_id': nt['_id']},
                        {'$set': {'pinned_section_id': cur_sec_id}}
                    )
            elif continue_sec == 'no':
                try:
                    db['sections'].update_one({'_id': ObjectId(cur_sec_id), 'user_email': user_email}, {'$set': {'completed': True}})
                except Exception:
                    pass
                all_secs = list(db['sections'].find({'topic_id': task['topic_id'], 'user_email': user_email}).sort('order', 1))
                sec_ids = [str(s['_id']) for s in all_secs]
                try:
                    cur_idx = sec_ids.index(cur_sec_id)
                    next_sec_id = sec_ids[cur_idx + 1] if cur_idx + 1 < len(sec_ids) else None
                except (ValueError, IndexError):
                    next_sec_id = None
                _incomplete = [s for s in all_secs if str(s['_id']) != cur_sec_id and not s.get('completed')]
                if next_sec_id is None and _incomplete:
                    next_sec_id = str(_incomplete[0]['_id'])
                for nt in next_tasks:
                    if next_sec_id:
                        db['scheduled_tasks'].update_one({'_id': nt['_id']}, {'$set': {'pinned_section_id': next_sec_id}})
                    else:
                        db['scheduled_tasks'].update_one({'_id': nt['_id']}, {'$set': {'pinned_section_id': '__done__'}})
                if next_sec_id:
                    _ns_doc = db['sections'].find_one({'_id': ObjectId(next_sec_id)})
                    _ajax_resp['next_section_id'] = next_sec_id
                    _ajax_resp['next_section_name'] = _ns_doc.get('name', '') if _ns_doc else ''
                    _ajax_resp['all_sections_complete'] = False
                else:
                    _ajax_resp['next_section_id'] = ''
                    _ajax_resp['next_section_name'] = ''
                    _ajax_resp['all_sections_complete'] = True
                _ajax_resp['topic_id'] = str(task.get('topic_id', ''))
        points = 20 if task.get('date') == date.today().isoformat() else 10
        db['users'].update_one({'email': user_email}, {'$inc': {'points': points}})
        award_badges(user_email, db)
    if request.POST.get('is_ajax') == '1':
        return JsonResponse(_ajax_resp)
    return redirect(redirect_to)


def daily_remark_view(request):
    if request.method != 'POST':
        return redirect('home')
    user_email = get_current_user(request)
    if not user_email:
        return redirect('login')
    db = get_db()
    today_str = date.today().isoformat()
    rating = int(request.POST.get('rating', 3))
    remark_text = request.POST.get('remark_text', '').strip()
    daily_score = get_sentiment_score(remark_text if remark_text else None, rating)
    existing = db['daily_remarks'].find_one({'user_email': user_email, 'date': today_str})
    if existing:
        db['daily_remarks'].update_one(
            {'user_email': user_email, 'date': today_str},
            {'$set': {
                'rating': rating,
                'remark_text': remark_text,
                'daily_score': daily_score,
                'updated_at': datetime.utcnow().isoformat(),
            }}
        )
    else:
        db['daily_remarks'].insert_one({
            'user_email': user_email,
            'date': today_str,
            'rating': rating,
            'remark_text': remark_text,
            'daily_score': daily_score,
            'created_at': datetime.utcnow().isoformat(),
        })
    return redirect('home')


def profile_view(request):
    user_email = get_current_user(request)
    if not user_email:
        return redirect('login')

    db = get_db()
    user = db['users'].find_one({'email': user_email})

    def _ctx(extra=None):
        ctx = {
            'user': db['users'].find_one({'email': user_email}),
            'streak': calculate_streak(user_email, db),
            'total_study_hours': _get_total_study_hours(user_email, db),
            'subject_count': db['subjects'].count_documents({'user_email': user_email}),
            'topics_total': db['topics'].count_documents({'user_email': user_email}),
            'topics_completed': db['topics'].count_documents({'user_email': user_email, 'status': 'completed'}),
        }
        if extra:
            ctx.update(extra)
        return ctx

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_profile':
            db['users'].update_one({'email': user_email}, {'$set': {
                'name': request.POST.get('name', user['name']),
                'phone': request.POST.get('phone', user['phone']),
            }})
            request.session['user_name'] = request.POST.get('name', user['name'])
            return redirect('profile')
        elif action == 'change_password':
            old_pw = request.POST.get('old_password')
            new_pw = request.POST.get('new_password')
            if check_password(old_pw, user['password']):
                db['users'].update_one({'email': user_email}, {'$set': {'password': make_password(new_pw)}})
                return render(request, 'profile.html', _ctx({'success': 'Password changed successfully!'}))
            else:
                return render(request, 'profile.html', _ctx({'error': 'Incorrect current password.'}))

    return render(request, 'profile.html', _ctx())


def history_view(request):
    user_email = get_current_user(request)
    if not user_email:
        return redirect('login')

    db = get_db()
    user = db['users'].find_one({'email': user_email})

    completed_tasks = list(db['scheduled_tasks'].find({
        'user_email': user_email,
        'completed': True,
        'is_break': {'$ne': True},
    }).sort([('date', -1), ('start_time', 1)]))
    for t in completed_tasks:
        fix_id(t)

    days_map = {}
    for t in completed_tasks:
        d = t['date']
        days_map.setdefault(d, []).append(t)

    history_days = []
    for d in sorted(days_map.keys(), reverse=True):
        tasks = days_map[d]
        total_min = sum(t.get('actual_minutes', t.get('duration_minutes', 0)) for t in tasks)
        subjects = list(dict.fromkeys(t.get('subject_name', '') for t in tasks if t.get('subject_name')))
        history_days.append({
            'date': d,
            'tasks': tasks,
            'total_minutes': total_min,
            'total_hours': round(total_min / 60, 1),
            'subjects': subjects,
            'task_count': len(tasks),
        })

    total_sessions = len(completed_tasks)
    total_minutes = sum(t.get('actual_minutes', t.get('duration_minutes', 0)) for t in completed_tasks)

    subject_map = {}
    for t in completed_tasks:
        sn = t.get('subject_name', '') or 'Unknown'
        if sn not in subject_map:
            subject_map[sn] = {'sessions': 0, 'minutes': 0}
        subject_map[sn]['sessions'] += 1
        subject_map[sn]['minutes'] += t.get('actual_minutes', t.get('duration_minutes', 0))

    max_min = max((v['minutes'] for v in subject_map.values()), default=1)
    subject_stats = sorted(
        [{'name': k, 'sessions': v['sessions'], 'minutes': v['minutes'],
          'hours': round(v['minutes'] / 60, 1),
          'pct': round(v['minutes'] / max_min * 100)}
         for k, v in subject_map.items()],
        key=lambda x: x['minutes'], reverse=True
    )

    return render(request, 'history.html', {
        'user': user,
        'history_days': history_days,
        'total_sessions': total_sessions,
        'total_minutes': total_minutes,
        'total_hours': round(total_minutes / 60, 1),
        'subject_stats': subject_stats,
        'study_days': len(history_days),
        'chart_daily_labels': json.dumps([d['date'][5:] for d in list(reversed(history_days[:14]))]),
        'chart_daily_hours': json.dumps([d['total_hours'] for d in list(reversed(history_days[:14]))]),
        'chart_subject_labels': json.dumps([s['name'] for s in subject_stats]),
        'chart_subject_hours': json.dumps([s['hours'] for s in subject_stats]),
    })


def alarm_check_view(request):
    user_email = get_current_user(request)
    if not user_email:
        return JsonResponse({'sessions': []})
    db = get_db()
    today_str = date.today().isoformat()
    tasks = list(db['scheduled_tasks'].find({
        'user_email': user_email,
        'date': today_str,
        'completed': {'$ne': True},
        'is_break': {'$ne': True},
    }))
    sessions = []
    for t in tasks:
        fix_id(t)
        sessions.append({
            'id': t['id'],
            'topic_name': t.get('topic_name', ''),
            'subject_name': t.get('subject_name', ''),
            'start_time': t.get('start_time', ''),
            'end_time': t.get('end_time', ''),
        })
    return JsonResponse({'sessions': sessions})

