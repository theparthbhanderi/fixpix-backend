from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth.models import User
from .models import ImageProject
from .permissions import IsAdminUser
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
import os

class EmergencyCreateAdmin(APIView):
    """
    Temporary view to create superuser if shell is inaccessible.
    Protected by the DJANGO_SUPERUSER_PASSWORD itself.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        env_password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
        param_password = request.query_params.get('key')
        
        if not env_password:
            return Response({"error": "Env var DJANGO_SUPERUSER_PASSWORD not set on server."}, status=400)

        if param_password != env_password:
             return Response({"error": "Unauthorized. Key does not match env password."}, status=401)
        
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
        
        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
            user.set_password(env_password)
            user.is_staff = True
            user.is_superuser = True
            user.save()
            msg = f"Superuser '{username}' already existed. Password updated to match env var."
        else:
            User.objects.create_superuser(username, email, env_password)
            msg = f"Superuser '{username}' created successfully."
            
        return Response({"message": msg, "login_credentials": {"username": username, "password_length": len(env_password)}})

class AdminDashboardStatsView(APIView):
    """
    API endpoint to retrieve high-level statistics for the Admin Dashboard.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        # User Stats
        total_users = User.objects.count()
        new_users_24h = User.objects.filter(date_joined__gte=timezone.now() - timedelta(hours=24)).count()

        # Job Stats
        total_projects = ImageProject.objects.count()
        active_jobs = ImageProject.objects.filter(status__in=['pending', 'processing']).count()
        failed_jobs = ImageProject.objects.filter(status='failed').count()
        completed_jobs = ImageProject.objects.filter(status='completed').count()
        
        # Determine strict processing jobs vs. just database entries
        # Assuming 'processing' means it's actively being worked on or queued
        gpu_queue_depth = ImageProject.objects.filter(status='pending').count()

        # Storage Usage (Estimate based on count, as we don't scan disk)
        # Assuming avg 2MB per project (original + processed)
        est_storage_gb = round((total_projects * 2) / 1024, 2) 

        return Response({
            "users": {
                "total": total_users,
                "new_24h": new_users_24h
            },
            "jobs": {
                "total": total_projects,
                "active": active_jobs,
                "failed": failed_jobs,
                "completed": completed_jobs,
                "success_rate": round((completed_jobs / total_projects * 100) if total_projects > 0 else 0, 1)
            },
            "system": {
                "gpu_queue_depth": gpu_queue_depth,
                "est_storage_gb": est_storage_gb,
                "status": "healthy" if active_jobs < 50 else "high_load"
            }
        })

class UserManagementView(APIView):
    """
    API endpoint to list users and perform administrative actions.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        query = request.query_params.get('search', '')
        users = User.objects.all().order_by('-date_joined')
        
        if query:
            users = users.filter(
                Q(username__icontains=query) | 
                Q(email__icontains=query)
            )

        # Pagination logic manually or via standard pagination
        # For simplicity in this view, limiting to 50 for MVP
        users = users[:50]
        
        user_data = []
        for user in users:
            user_data.append({
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "is_active": user.is_active,
                "is_staff": user.is_staff,
                "date_joined": user.date_joined,
                "job_count": ImageProject.objects.filter(user=user).count()
            })
            
        return Response(user_data)

    def post(self, request, pk=None):
        if not pk:
            return Response({"error": "User ID required"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            user = User.objects.get(pk=pk)
            action = request.data.get('action')
            
            if action == 'ban':
                user.is_active = False
                user.save()
                return Response({"message": f"User {user.username} banned"})
            
            elif action == 'unban':
                user.is_active = True
                user.save()
                return Response({"message": f"User {user.username} unbanned"})
                
            elif action == 'promote':
                if not request.user.is_superuser:
                    return Response({"error": "Only superusers can promote staff"}, status=status.HTTP_403_FORBIDDEN)
                user.is_staff = True
                user.save()
                return Response({"message": f"User {user.username} promoted to staff"})
            
            return Response({"error": "Invalid action"}, status=status.HTTP_400_BAD_REQUEST)
            
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

class JobMonitorView(APIView):
    """
    API endpoint to monitor and manage AI jobs.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        status_filter = request.query_params.get('status', 'all')
        jobs = ImageProject.objects.all().order_by('-created_at')
        
        if status_filter != 'all':
            jobs = jobs.filter(status=status_filter)
            
        # Limit for performance
        jobs = jobs[:50]
        
        job_data = []
        for job in jobs:
            job_data.append({
                "id": job.id,
                "type": job.processing_type,
                "status": job.status,
                "user": job.user.username if job.user else "Anonymous",
                "created_at": job.created_at,
                "source": job.source
            })
            
        return Response(job_data)
