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
    path('shelf/<int:pk>/', views.shelf_detail, name='shelf'),
    path('shelf/<int:pk>/add/', views.modify, {'action': 'add'}, name='add_book'),
    path('shelf/<int:pk>/remove/', views.modify, {'action': 'remove'}, name='remove_book'),
    path('login/', throttle_login(
        auth.LoginView.as_view(template_name='registration/login.html')), name='login'),
    path('logout/', auth.LogoutView.as_view(), name='logout'),
    path('robots.txt', lambda request: HttpResponse(
        'User-agent: *\nDisallow: /\n', content_type='text/plain')),
    path('%s/' % settings.ADMIN_URL, admin.site.urls),
]
