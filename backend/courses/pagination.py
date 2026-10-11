from rest_framework.pagination import PageNumberPagination


class StandardResultsPagination(PageNumberPagination):
    """Shared pagination for list endpoints that can grow without bound
    (e.g. the public course catalog). page_size=12 divides evenly into the
    course-grid layouts already used by the frontend (3 or 4 columns).

    Clients may request a different page size with ?page_size=, capped at
    max_page_size to prevent abuse.
    """

    page_size = 12
    page_size_query_param = 'page_size'
    max_page_size = 100
