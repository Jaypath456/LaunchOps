from rest_framework import serializers

from .models import Break, Meeting, SavedSchedule, Section, Term


class TermSerializer(serializers.ModelSerializer):
    class Meta:
        model = Term
        fields = ['id', 'name', 'start_date', 'end_date']


class MeetingSerializer(serializers.ModelSerializer):
    section = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Meeting
        fields = ['id', 'section', 'day_of_week', 'start_time', 'end_time']

    def validate(self, attrs):
        start = attrs.get('start_time', getattr(self.instance, 'start_time', None))
        end = attrs.get('end_time', getattr(self.instance, 'end_time', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'end_time': 'end_time must be after start_time.'})
        return attrs


class SectionSerializer(serializers.ModelSerializer):
    """Writable, with nested meetings. On update, meetings are fully
    replaced with whatever list is provided - simpler and safer than
    diffing individual meeting rows against arbitrary edits from the
    instructor-facing section form.
    """

    course = serializers.PrimaryKeyRelatedField(read_only=True)
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())
    meetings = MeetingSerializer(many=True, required=False)

    class Meta:
        model = Section
        fields = [
            'id',
            'course',
            'term',
            'section_code',
            'location',
            'capacity',
            'meetings',
            'created_at',
        ]

    def create(self, validated_data):
        meetings_data = validated_data.pop('meetings', [])
        section = Section.objects.create(**validated_data)
        for meeting_data in meetings_data:
            Meeting.objects.create(section=section, **meeting_data)
        return section

    def update(self, instance, validated_data):
        meetings_data = validated_data.pop('meetings', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if meetings_data is not None:
            instance.meetings.all().delete()
            for meeting_data in meetings_data:
                Meeting.objects.create(section=instance, **meeting_data)

        return instance


class SavedScheduleSerializer(serializers.ModelSerializer):
    student = serializers.PrimaryKeyRelatedField(read_only=True)
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())
    sections = serializers.PrimaryKeyRelatedField(queryset=Section.objects.all(), many=True)
    section_details = SectionSerializer(source='sections', many=True, read_only=True)

    class Meta:
        model = SavedSchedule
        fields = [
            'id',
            'student',
            'term',
            'sections',
            'section_details',
            'confirmed_at',
            'created_at',
        ]

    def validate_sections(self, sections):
        if not sections:
            raise serializers.ValidationError('sections must be a non-empty list.')
        return sections


class BreakSerializer(serializers.ModelSerializer):
    student = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Break
        fields = ['id', 'student', 'day_of_week', 'start_time', 'end_time', 'label']

    def validate(self, attrs):
        start = attrs.get('start_time', getattr(self.instance, 'start_time', None))
        end = attrs.get('end_time', getattr(self.instance, 'end_time', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'end_time': 'end_time must be after start_time.'})
        return attrs
