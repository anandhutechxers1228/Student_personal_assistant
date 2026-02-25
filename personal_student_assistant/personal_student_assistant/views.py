from django.shortcuts import render, redirect
from django.contrib.auth.hashers import make_password, check_password
from .db_connector import get_db

def signup_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        phone = request.POST.get('phone')
        password = request.POST.get('password')

        db = get_db()
        users_collection = db['users']

        if users_collection.find_one({'email': email}):
            return render(request, 'signup.html', {'error': 'Email is already registered'})

        hashed_password = make_password(password)
        
        users_collection.insert_one({
            'name': name,
            'email': email,
            'phone': phone,
            'password': hashed_password
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
        else:
            return render(request, 'login.html', {'error': 'Invalid email or password'})
            
    return render(request, 'login.html')

def logout_view(request):
    request.session.flush()
    return redirect('login')

def home_view(request):
    user_email = request.session.get('user_email')
    
    if not user_email:
        return redirect('login')
        
    db = get_db()
    user = db['users'].find_one({'email': user_email})
    
    return render(request, 'home.html', {'user': user})


