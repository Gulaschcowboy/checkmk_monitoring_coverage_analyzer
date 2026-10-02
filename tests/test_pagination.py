"""Pagination and search filter of the host table on the GUI page."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

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


def _host(name: str, findings: str) -> SimpleNamespace:
    return SimpleNamespace(host_name=name, findings=findings)


HOSTS = [
    _host("web01.example.test", "No open findings."),
    _host("db01.example.test", "MySQL / MariaDB: running, not monitored (deploy mk_mysql)"),
    _host("db02.example.test", "No open findings."),
    _host("files01.example.test", "Samba: running, not monitored; NFS Server: running, not monitored"),
]


def names(results: list[SimpleNamespace]) -> list[str]:
    return [r.host_name for r in results]


class SearchFilterTest(unittest.TestCase):
    def test_empty_search_keeps_everything(self) -> None:
        self.assertEqual(names(m._filtered_results(HOSTS, "")), names(HOSTS))

    def test_hostname_and_findings_case_insensitive(self) -> None:
        self.assertEqual(names(m._filtered_results(HOSTS, "DB0")), ["db01.example.test", "db02.example.test"])
        self.assertEqual(names(m._filtered_results(HOSTS, "mysql")), ["db01.example.test"])

    def test_all_words_must_match(self) -> None:
        self.assertEqual(names(m._filtered_results(HOSTS, "db not monitored")), ["db01.example.test"])
        self.assertEqual(names(m._filtered_results(HOSTS, "web mysql")), [])

    def test_regex_and_invalid_regex(self) -> None:
        self.assertEqual(names(m._filtered_results(HOSTS, "^db0[2-9]")), ["db02.example.test"])
        # not a valid regex: matched literally instead of failing
        self.assertEqual(names(m._filtered_results(HOSTS, "(deploy")), ["db01.example.test"])


if __name__ == "__main__":
    unittest.main()
