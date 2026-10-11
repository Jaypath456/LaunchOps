from django.contrib import admin

from .models import Break, Meeting, Section, Term


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_date', 'end_date')


class MeetingInline(admin.TabularInline):
    model = Meeting
    extra = 1


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('course', 'term', 'section_code', 'location', 'capacity')
    list_filter = ('term',)
    inlines = [MeetingInline]


@admin.register(Break)
class BreakAdmin(admin.ModelAdmin):
    list_display = ('student', 'day_of_week', 'start_time', 'end_time', 'label')
