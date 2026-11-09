from bson import ObjectId
from django.urls import path, register_converter

from . import views


class ObjectIdConverter:
    regex = '[0-9a-f]{24}'

    def to_python(self, value):
        return ObjectId(value)

    def to_url(self, value):
        return str(value)


class StepKeyConverter:
    regex = '[a-z_]{1,40}'

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(ObjectIdConverter, 'oid')
register_converter(StepKeyConverter, 'stepkey')

urlpatterns = [
    path('workflows/', views.workflows),
    path('workflows/<oid:workflow_id>/', views.detail),
    path('workflows/<oid:workflow_id>/steps/<stepkey:key>/', views.step_detail),
    path('workflows/<oid:workflow_id>/steps/<stepkey:key>/complete/', views.complete),
    path('workflows/<oid:workflow_id>/issues/<oid:issue_id>/resolve/', views.resolve_issue),
    path('workflows/<oid:workflow_id>/timeline/', views.timeline),
    path('workflows/<oid:workflow_id>/provisioning/<str:connector>/retry/', views.retry_job),
    path('workflows/<oid:workflow_id>/provisioning/<str:connector>/override/', views.override_job),
    path('directory/', views.directory),
]
