from django.contrib import admin

from .models import Message


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('course', 'sender', 'body', 'created_at')
    list_filter = ('course',)
