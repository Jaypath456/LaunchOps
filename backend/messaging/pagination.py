from rest_framework.pagination import PageNumberPagination


class MessagePagination(PageNumberPagination):
    """Message history is the first real consumer of pagination in this
    project's history (the courses app's own pagination lands in a
    separate, not-yet-merged milestone) - kept local to this app rather
    than depending on that branch.
    """

    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100
