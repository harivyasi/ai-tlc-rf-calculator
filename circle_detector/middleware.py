"""Response headers that are not covered by Django's SecurityMiddleware."""


class ContentSecurityPolicyMiddleware:
    """Send a Content-Security-Policy that permits no external origins.

    Every third-party asset is vendored under static/, so the page needs
    nothing but its own origin. This is defence in depth rather than the
    primary control: the actual fix for user-supplied files being rendered as
    pages is the format check in upload_local_image.

    'unsafe-inline' is still required for scripts and styles because
    annotate.html carries its stylesheet and roughly 1500 lines of behaviour
    inline. Extracting those into static files is what would let it be dropped;
    until then the script-src rule provides no XSS protection, while the rest
    of the policy (no external origins, no framing, no plugins) still holds.
    """

    HEADER = '; '.join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        # Results are POSTed to a caller-supplied ELN host, so connect-src
        # cannot be limited to 'self' without breaking that integration.
        "connect-src *",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
    ])

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Media responses set their own, stricter, sandboxed policy.
        response.setdefault('Content-Security-Policy', self.HEADER)
        return response
