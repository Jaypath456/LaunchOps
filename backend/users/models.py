from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import RegexValidator

username_validator = RegexValidator(
    r'^[a-zA-Z0-9_@]+$',
    'Username can only contain letters, numbers, @, and _'
)

class User(AbstractUser):
    username = models.CharField(max_length=150, unique=True, validators=[username_validator])
    ROLE_CHOICES = (
        ('instructor', 'Instructor'),
        ('student', 'Student'),
        ('hr', 'HR'),
        ('it', 'IT'),
        ('manager', 'Manager'),
        ('employee', 'Employee'),
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='student'
    )

    bio = models.TextField(blank=True, default='')

    def __str__(self):
        return self.username
