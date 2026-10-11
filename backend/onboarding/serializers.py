from rest_framework import serializers
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from courses.models import Course
from .models import Onboarding, Step, Event
from .services import step_state, valid_start_date
from .blueprint import FORMS, SIMULATIONS, DESCRIPTIONS

User = get_user_model()


def validate_start_date(value):
    today = timezone.localdate()
    if not today - timedelta(days=366) <= value <= today + timedelta(days=1095):
        raise serializers.ValidationError('Choose a start date within the past year or next three years.')
    return value


class RescheduleSerializer(serializers.Serializer):
    start_date = serializers.DateField(validators=[validate_start_date], required=False)
    training_course_id = serializers.PrimaryKeyRelatedField(queryset=Course.objects.filter(is_published=True), required=False)

    def validate(self, data):
        if not data:
            raise serializers.ValidationError('Supply a start date or published learning track.')
        return data


class CreateOnboardingSerializer(serializers.Serializer):
    employee_id = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role='employee'), source='employee')
    manager_id = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role='manager'), source='manager')
    training_course_id = serializers.PrimaryKeyRelatedField(queryset=Course.objects.filter(is_published=True), source='training_course')
    job_title = serializers.CharField(max_length=160)
    department = serializers.CharField(max_length=100)
    start_date = serializers.DateField(validators=[validate_start_date])

    def validate_employee(self, employee):
        if Onboarding.objects.filter(employee=employee).exists():
            raise serializers.ValidationError('This employee already has an onboarding workflow.')
        return employee


class StepSerializer(serializers.ModelSerializer):
    prerequisites = serializers.SlugRelatedField(many=True, read_only=True, slug_field='key')
    state = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    completion_mode = serializers.SerializerMethodField()

    def get_description(self, obj):
        return DESCRIPTIONS.get(obj.key, obj.description)

    def get_completion_mode(self, obj):
        if obj.completed_at:
            return obj.evidence.get('_proof', {}).get('mode', 'legacy_unverified')
        return 'simulation' if obj.key in SIMULATIONS else 'assessment' if obj.key == 'training' else 'local_record'

    completed_by = serializers.CharField(source='completed_by.username', read_only=True)

    class Meta:
        model = Step
        fields = ['id', 'key', 'title', 'description', 'owner_role', 'kind', 'due_at', 'completed_at', 'completed_by', 'prerequisites', 'state', 'completion_mode']

    def get_state(self, obj):
        return step_state(obj)


class TaskStepSerializer(StepSerializer):
    form_fields = serializers.SerializerMethodField()

    class Meta(StepSerializer.Meta):
        fields = StepSerializer.Meta.fields + ['evidence', 'form_fields']

    def get_form_fields(self, obj):
        return FORMS.get(obj.key, [])


class EventSerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source='actor.username', read_only=True)
    step = serializers.CharField(source='step.key', read_only=True)

    class Meta:
        model = Event
        fields = ['id', 'actor', 'step', 'action', 'detail', 'created_at']


class OnboardingSerializer(serializers.ModelSerializer):
    employee = serializers.CharField(source='employee.username')
    manager = serializers.CharField(source='manager.username')
    training_course_id = serializers.IntegerField(read_only=True)
    steps = StepSerializer(many=True, read_only=True)
    events = EventSerializer(many=True, read_only=True)
    date_needs_correction = serializers.SerializerMethodField()

    def get_date_needs_correction(self, obj):
        return not valid_start_date(obj.start_date)


    class Meta:
        model = Onboarding
        fields = ['id', 'employee', 'manager', 'job_title', 'department', 'start_date', 'training_course_id', 'created_at', 'steps', 'events', 'date_needs_correction']
