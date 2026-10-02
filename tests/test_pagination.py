"""Pagination of the host table on the GUI page."""

from __future__ import annotations

import unittest

from _harness import load_page_module

m = load_page_module()


class PageBoundsTest(unittest.TestCase):
    def test_single_page_when_everything_fits(self) -> None:
        self.assertEqual(m._page_bounds(36, 1, 100), (0, 36, 1, 1))
        self.assertEqual(m._page_bounds(100, 1, 100), (0, 100, 1, 1))
        self.assertEqual(m._page_bounds(0, 1, 100), (0, 0, 1, 1))

    def test_pages(self) -> None:
        self.assertEqual(m._page_bounds(503, 1, 100), (0, 100, 1, 6))
        self.assertEqual(m._page_bounds(503, 2, 100), (100, 200, 2, 6))
        self.assertEqual(m._page_bounds(503, 6, 100), (500, 503, 6, 6))

    def test_page_beyond_the_end_is_clamped(self) -> None:
        # e.g. after hosts were removed or the page size was raised
        self.assertEqual(m._page_bounds(503, 99, 100), (500, 503, 6, 6))
        self.assertEqual(m._page_bounds(503, 0, 100), (0, 100, 1, 6))

    def test_all_on_one_page(self) -> None:
        self.assertEqual(m._page_bounds(503, 4, 0), (0, 503, 1, 1))

    def test_default_page_size(self) -> None:
        self.assertEqual(m._DEFAULT_PAGE_SIZE, 100)
        self.assertIn(m._DEFAULT_PAGE_SIZE, m._PAGE_SIZES)


if __name__ == "__main__":
    unittest.main()
