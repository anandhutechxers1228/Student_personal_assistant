from django.urls import path
from . import views

urlpatterns = [
    path('',views.home_view, name='home'),
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('subjects/', views.subjects_view, name='subjects'),
    path('subjects/<str:subject_id>/topics/', views.topics_view, name='topics'),
    path('schedule/', views.schedule_view, name='schedule'),
    path('complete-task/', views.complete_task, name='complete_task'),
    path('profile/', views.profile_view, name='profile'),
    path('daily-remark/', views.daily_remark_view, name='daily_remark'),
]
