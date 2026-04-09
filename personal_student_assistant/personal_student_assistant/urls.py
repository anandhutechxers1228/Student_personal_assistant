from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views

urlpatterns = [
    path('', views.home_view, name='home'),
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('subjects/', views.subjects_view, name='subjects'),
    path('subjects/<str:subject_id>/topics/', views.topics_view, name='topics'),
    path('schedule/', views.schedule_view, name='schedule'),
    path('complete-task/', views.complete_task, name='complete_task'),
    path('profile/', views.profile_view, name='profile'),
    path('daily-remark/', views.daily_remark_view, name='daily_remark'),
    path('history/', views.history_view, name='history'),
    path('alarm-check/', views.alarm_check_view, name='alarm_check'),
    path('topic-notes/<str:topic_id>/upload/', views.upload_topic_notes_view, name='upload_topic_notes'),
    path('topic-notes/<str:topic_id>/refer/', views.refer_view, name='topic_refer'),
    path('topic-notes/<str:topic_id>/search/', views.notes_search_view, name='notes_search'),
    path('session-qa/generate/', views.generate_session_qa_view, name='generate_session_qa'),
    path('session-qa/evaluate/', views.evaluate_session_qa_view, name='evaluate_session_qa'),
    path('topic-exam/generate/', views.generate_topic_exam_view, name='generate_topic_exam'),
    path('topic-exam/evaluate/', views.evaluate_topic_exam_view, name='evaluate_topic_exam'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)