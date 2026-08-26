from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth
from django.http import HttpResponse
from django.urls import path
from django_ratelimit.decorators import ratelimit

from . import views

throttle_login = ratelimit(key='ip', rate=settings.RATE_LOGIN, method='POST', block=True)
admin.site.login = throttle_login(admin.site.login)

urlpatterns = [
    path('', views.shelf_list, name='shelves'),
    path('create/', views.create_shelf, name='create_shelf'),
    path('shelf/<int:pk>/', views.shelf_detail, name='shelf'),
    path('shelf/<int:pk>/add/', views.modify, {'action': 'add'}, name='add_book'),
    path('shelf/<int:pk>/remove/', views.modify, {'action': 'remove'}, name='remove_book'),
    path('shelf/<int:pk>/rename/', views.rename_shelf, name='rename_shelf'),
    path('new/<int:pk>/', views.new_shelf_detail, name='new_shelf'),
    path('new/<int:pk>/add/', views.modify, {'action': 'add', 'draft': True}, name='new_add_book'),
    path('new/<int:pk>/remove/', views.modify, {'action': 'remove', 'draft': True},
         name='new_remove_book'),
    path('new/<int:pk>/rename/', views.rename_shelf, {'draft': True}, name='new_rename'),
    path('review/', views.review_list, name='reviews'),
    path('review/<str:week>/', views.review_detail, name='review'),
    path('api/reports/', views.api_reports),
    path('api/reports/vetted/', views.api_vetted),
    path('api/reports/<str:week>/', views.api_report),
    path('api/reports/<str:week>/applied/', views.api_applied),
    path('login/', throttle_login(
        auth.LoginView.as_view(template_name='registration/login.html')), name='login'),
    path('logout/', auth.LogoutView.as_view(), name='logout'),
    path('robots.txt', lambda request: HttpResponse(
        'User-agent: *\nDisallow: /\n', content_type='text/plain')),
    path('%s/' % settings.ADMIN_URL, admin.site.urls),
]
