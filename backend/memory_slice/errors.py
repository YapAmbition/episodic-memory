class MemorySliceError(Exception):
    """Domain failure; the HTTP adapter translates status_code to a response."""
    diagnostic_code = "http_error"

    def __init__(self, status_code, detail, headers=None):
        self.status_code = status_code
        self.detail = detail
        self.headers = headers
        super().__init__(str(detail))


# Compatibility for integrations that imported the original domain exception.
MemoryError = MemorySliceError


class FocusEvidenceError(MemorySliceError):
    def __init__(self, diagnostic_code, detail):
        super().__init__(502, detail)
        self.diagnostic_code = diagnostic_code
