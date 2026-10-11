from django.utils import timezone
from rest_framework import serializers
from .models import Course, Chapter, Enrollment
from users.serializers import UserSerializer


class ChapterSerializer(serializers.ModelSerializer):
    course = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Chapter
        fields = [
            'id',
            'course',
            'title',
            'content',
            'visibility',
            'order_index'
        ]




class CourseSerializer(serializers.ModelSerializer):
    instructor = UserSerializer(read_only=True)
    chapter_count = serializers.SerializerMethodField()
    enrolled_count = serializers.SerializerMethodField()
    chat_open = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = [
            'id',
            'title',
            'description',
            'instructor',
            'is_published',
            'chapter_count',
            'enrolled_count',
            'chat_open',
        ]

    def get_chapter_count(self, obj):
        # Views that list many courses at once (course_list,
        # instructor_courses) annotate chapter_count via .annotate() to
        # avoid one COUNT query per course. Views that only ever serialize
        # a single course, or nested cases like my_courses's
        # select_related('course'), don't annotate - fall back to a direct
        # count query so those paths stay correct either way.
        annotated = getattr(obj, 'chapter_count', None)
        if annotated is not None:
            return annotated
        return obj.chapters.count()

    def get_enrolled_count(self, obj):
        annotated = getattr(obj, 'enrolled_count', None)
        if annotated is not None:
            return annotated
        return obj.enrollments.count()

    def get_chat_open(self, obj):
        # No term assigned -> no tenure concept -> chat never closes.
        # Once a term is assigned, chat closes the day after term.end_date
        # (see messaging/consumers.py for the matching write-lock enforced
        # server-side, and messaging/tasks.py for the purge job that
        # actually resets the room's history).
        if obj.term_id is None:
            return True
        return obj.term.end_date >= timezone.now().date()


class EnrollmentSerializer(serializers.ModelSerializer):
    course = CourseSerializer(read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            'id',
            'course',
            'enrolled_at'
        ]
