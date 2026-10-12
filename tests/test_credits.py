import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.routers import credits


class CreditsTests(unittest.TestCase):
    def test_rejects_incomplete_and_extra_columns(self) -> None:
        for row in ("Title", "Title,Artist,Unexpected", ",Artist", "Title,"):
            with self.subTest(row=row), tempfile.TemporaryDirectory() as directory:
                data_path = Path(directory) / "credits.csv"
                data_path.write_text(f"title,artist\n{row}\n", encoding="utf-8")
                with (
                    patch.object(credits, "DATA_PATH", data_path),
                    self.assertRaisesRegex(RuntimeError, "line 2"),
                ):
                    credits._load()

    def test_loads_title_and_artist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "credits.csv"
            data_path.write_text("title,artist\nR U Mine?,Arctic Monkeys\n", encoding="utf-8")

            with patch.object(credits, "DATA_PATH", data_path):
                result = credits._load()

        self.assertEqual(result[0].title, "R U Mine?")
        self.assertEqual(result[0].artist, "Arctic Monkeys")

    def test_rejects_the_old_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "credits.csv"
            data_path.write_text("author,track\nArctic Monkeys,R U Mine?\n", encoding="utf-8")

            with (
                patch.object(credits, "DATA_PATH", data_path),
                self.assertRaisesRegex(RuntimeError, "title, artist"),
            ):
                credits._load()
