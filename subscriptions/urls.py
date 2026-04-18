from django.urls import path
from . import views

urlpatterns = [
    path('status/', views.get_subscription_status, name='subscription-status'),
    path('create-order/', views.create_order, name='create-subscription-order'),
    path('verify-payment/', views.verify_payment, name='verify-subscription-payment'),
]
