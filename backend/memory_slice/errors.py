class MemoryError(Exception):
    """Domain failure; the HTTP adapter translates status_code to a response."""
    def __init__(self, status_code, detail, headers=None):
        self.status_code = status_code
        self.detail = detail
        self.headers = headers
        super().__init__(str(detail))
