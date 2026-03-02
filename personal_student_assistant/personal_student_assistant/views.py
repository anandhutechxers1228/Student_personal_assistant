# views.py

from django.shortcuts import render, redirect
from django.contrib.auth.hashers import make_password, check_password
from .db_connector import get_db
from bson import ObjectId
from datetime import datetime, timedelta, date


def get_current_user(request):
    return request.session.get('user_email')


def fix_id(doc):
    doc['id'] = str(doc['_id'])
    return doc


def calculate_streak(user_email, db):
    streak = 0
    for i in range(60):
        d = (date.today() - timedelta(days=i)).isoformat()
        completed = db['scheduled_tasks'].count_documents({
            'user_email': user_email,
            'completed': True,
            'date': d,
            'is_break': {'$ne': True}
        })
        if completed > 0:
            streak += 1
        elif i > 0:
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
            generate_week_schedule(user_email, db, user)
            return redirect('schedule')
        elif action == 'generate_today':
            user = db['users'].find_one({'email': user_email})
            generate_week_schedule(user_email, db, user, single_day=True)
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

    return render(request, 'schedule.html', {
        'user': user,
        'week_days': week_days,
        'today': today.isoformat(),
        'has_future_tasks': has_future_tasks,
    })


def generate_week_schedule(user_email, db, user, single_day=False):
    now = datetime.now()
    today = date.today()

    if single_day:
        num_days = 1
    else:
        days_until_sunday = 6 - today.weekday()
        num_days = days_until_sunday + 1

    study_start = user.get('study_start_time', '18:00')
    study_end = user.get('study_end_time', '22:00')
    session_dur = user.get('session_duration', 25)
    break_dur = user.get('break_duration', 5)

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
            ct.get('actual_minutes', ct.get('duration_minutes', 0))
            for ct in db['scheduled_tasks'].find({
                'topic_id': t['id'],
                'user_email': user_email,
                'completed': True,
            })
        )
        total_min = int(t.get('estimated_hours', 1) * 60)
        t['remaining_minutes'] = max(0, total_min - completed_min)

    topics = [t for t in topics if t['remaining_minutes'] > 0]
    topics.sort(key=lambda x: x['priority_score'], reverse=True)

    task_queue = []
    for topic in topics:
        remaining = topic['remaining_minutes']
        while remaining > 0:
            chunk = min(remaining, session_dur)
            task_queue.append({
                'topic_id': topic['id'],
                'topic_name': topic['name'],
                'subject_name': topic['subject_name'],
                'subject_id': topic['subject_id'],
                'duration': chunk,
                'priority_score': topic['priority_score'],
            })
            remaining -= chunk

    for day_offset in range(num_days):
        if not task_queue:
            break

        d = (today + timedelta(days=day_offset)).isoformat()

        if day_offset == 0:
            now_min = now.hour * 60 + now.minute
            if now_min >= end_min_global:
                continue
            last_task_end = start_min_global
            for existing in db['scheduled_tasks'].find({'user_email': user_email, 'date': d}):
                try:
                    eh2, em2 = map(int, existing.get('end_time', '00:00').split(':'))
                    last_task_end = max(last_task_end, eh2 * 60 + em2)
                except Exception:
                    pass
            cur_min = max(start_min_global, now_min, last_task_end)
            if cur_min >= end_min_global:
                continue
        else:
            cur_min = start_min_global

        end_min = end_min_global
        first_in_day = True

        while cur_min < end_min and task_queue:
            if not first_in_day:
                b_end = cur_min + break_dur
                if b_end >= end_min:
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

            available = end_min - cur_min
            if available < 5:
                break

            task = task_queue[0]
            duration = min(task['duration'], available)

            if duration < 5:
                break

            t_end = cur_min + duration
            db['scheduled_tasks'].insert_one({
                'user_email': user_email,
                'date': d,
                'topic_id': task['topic_id'],
                'topic_name': task['topic_name'],
                'subject_name': task['subject_name'],
                'subject_id': task['subject_id'],
                'duration_minutes': duration,
                'start_time': f"{cur_min // 60:02d}:{cur_min % 60:02d}",
                'end_time': f"{t_end // 60:02d}:{t_end % 60:02d}",
                'is_break': False,
                'completed': False,
                'priority_score': task['priority_score'],
            })

            if duration >= task['duration']:
                task_queue.pop(0)
            else:
                task['duration'] -= duration

            cur_min = t_end
            first_in_day = False


def complete_task(request):
    if request.method != 'POST':
        return redirect('home')
    user_email = get_current_user(request)
    if not user_email:
        return redirect('login')

    task_id = request.POST.get('task_id')
    actual_min = request.POST.get('actual_minutes')
    redirect_to = request.POST.get('redirect_to', 'home')

    db = get_db()
    task = db['scheduled_tasks'].find_one({'_id': ObjectId(task_id), 'user_email': user_email})

    if task and not task.get('completed'):
        actual = int(actual_min) if actual_min else task.get('duration_minutes', 0)
        db['scheduled_tasks'].update_one(
            {'_id': ObjectId(task_id)},
            {'$set': {
                'completed': True,
                'completed_at': datetime.utcnow().isoformat(),
                'actual_minutes': actual,
            }}
        )
        points = 20 if task.get('date') == date.today().isoformat() else 10
        db['users'].update_one({'email': user_email}, {'$inc': {'points': points}})
        award_badges(user_email, db)
    return redirect(redirect_to)


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