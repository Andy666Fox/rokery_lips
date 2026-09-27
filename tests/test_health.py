import unittest

from fastapi import Response

from app.routers.health import health


class HealthTests(unittest.TestCase):
    def test_health_response_is_not_cached(self) -> None:
        response = Response()

        status = health(response)

        self.assertEqual(status.status, "ok")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
