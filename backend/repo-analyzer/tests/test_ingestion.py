"""Offline, stdlib-only tests for ingestion.py.

Builds tarballs entirely in memory (no fixture files, no network) so these
pass whether or not httpx is installed.
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ingestion as ing  # noqa: E402


def make_tarball(files: dict[str, bytes], prefix: str = "owner-repo-abc123") -> io.BytesIO:
    """Build an in-memory gzipped tarball with all entries under one prefix."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(name=f"{prefix}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


class ParseRepoUrlTests(unittest.TestCase):
    def test_https_basic(self):
        self.assertEqual(
            ing.parse_repo_url("https://github.com/owner/repo"),
            ("owner", "repo", None),
        )

    def test_http_trailing_slash(self):
        self.assertEqual(
            ing.parse_repo_url("http://github.com/owner/repo/"),
            ("owner", "repo", None),
        )

    def test_www_and_git_suffix(self):
        self.assertEqual(
            ing.parse_repo_url("https://www.github.com/owner/repo.git"),
            ("owner", "repo", None),
        )

    def test_tree_ref_multi_segment(self):
        self.assertEqual(
            ing.parse_repo_url("https://github.com/owner/repo/tree/some/branch/name"),
            ("owner", "repo", "some/branch/name"),
        )

    def test_commit_ref(self):
        self.assertEqual(
            ing.parse_repo_url("https://github.com/owner/repo/commit/deadbeef"),
            ("owner", "repo", "deadbeef"),
        )

    def test_scp_style(self):
        self.assertEqual(
            ing.parse_repo_url("git@github.com:owner/repo.git"),
            ("owner", "repo", None),
        )

    def test_bare_shorthand(self):
        self.assertEqual(
            ing.parse_repo_url("owner/repo"),
            ("owner", "repo", None),
        )

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            ing.parse_repo_url("")
        with self.assertRaises(ValueError):
            ing.parse_repo_url(None)

    def test_non_github_host_raises(self):
        with self.assertRaises(ValueError):
            ing.parse_repo_url("https://gitlab.com/owner/repo")

    def test_no_repo_component_raises(self):
        with self.assertRaises(ValueError):
            ing.parse_repo_url("https://github.com/owner")
        with self.assertRaises(ValueError):
            ing.parse_repo_url("https://github.com/")


class ExcludeReasonTests(unittest.TestCase):
    def test_node_modules(self):
        self.assertEqual(ing.exclude_reason("node_modules/foo/index.js"), "excluded_dir")

    def test_venv_dir(self):
        self.assertEqual(ing.exclude_reason(".venv/lib/foo.py"), "excluded_dir")

    def test_png(self):
        self.assertEqual(ing.exclude_reason("assets/logo.png"), "excluded_extension")

    def test_package_lock(self):
        self.assertEqual(ing.exclude_reason("package-lock.json"), "excluded_filename")

    def test_min_js_suffix(self):
        self.assertEqual(ing.exclude_reason("static/app.min.js"), "excluded_suffix")

    def test_dotfile_excluded(self):
        self.assertEqual(ing.exclude_reason(".eslintrc.json"), "hidden_file")

    def test_gitignore_included(self):
        self.assertIsNone(ing.exclude_reason(".gitignore"))

    def test_env_example_included(self):
        self.assertIsNone(ing.exclude_reason(".env.example"))

    def test_oversized(self):
        self.assertEqual(
            ing.exclude_reason("src/main.py", size=ing.MAX_FILE_BYTES + 1), "too_large"
        )

    def test_normal_source_file(self):
        self.assertIsNone(ing.exclude_reason("src/main.py", size=100))

    def test_directory_entry(self):
        self.assertEqual(ing.exclude_reason("src/"), "excluded_dir")
        self.assertEqual(ing.exclude_reason(""), "excluded_dir")

    def test_ci_workflow_not_excluded_dir(self):
        # .github/workflows must NOT be excluded (per module contract).
        self.assertIsNone(ing.exclude_reason(".github/workflows/ci.yml"))

    def test_migrations_not_excluded_dir(self):
        self.assertIsNone(ing.exclude_reason("migrations/0001_init.py"))


class LooksGeneratedTests(unittest.TestCase):
    def test_normal_source_false(self):
        text = "\n".join(f"line {i}" for i in range(50))
        self.assertFalse(ing.looks_generated(text))

    def test_long_line_true(self):
        text = "x" * 6000
        self.assertTrue(ing.looks_generated(text))

    def test_empty_false(self):
        self.assertFalse(ing.looks_generated(""))

    def test_high_mean_line_length_true(self):
        text = "\n".join("y" * 600 for _ in range(10))
        self.assertTrue(ing.looks_generated(text))


class IterTarballFilesTests(unittest.TestCase):
    def _sample_files(self):
        return {
            "README.md": b"# Hello\nThis is a readme.",
            "src/main.py": b"print('hello world')\n",
            "node_modules/pkg/index.js": b"module.exports = {};",
            "assets/logo.png": b"\x89PNG\r\n\x1a\nnotarealpng",
            "package-lock.json": b'{"lockfileVersion": 1}',
            "bin/data.so": b"\xff\xfe\x00\x01binarydata\x80\x81",
            "big.txt": b"a" * (ing.MAX_FILE_BYTES + 10),
        }

    def test_end_to_end_filtering_and_prefix_strip(self):
        tb = make_tarball(self._sample_files())
        results = {f.path: f.text for f in ing.iter_tarball_files(tb)}
        self.assertEqual(
            set(results.keys()),
            {"README.md", "src/main.py"},
        )
        self.assertIn("hello world", results["src/main.py"])
        self.assertTrue(results["README.md"].startswith("# Hello"))

    def test_max_files_cap(self):
        files = {f"file{i}.py": f"x = {i}\n".encode() for i in range(10)}
        tb = make_tarball(files)
        results = list(ing.iter_tarball_files(tb, max_files=3))
        self.assertEqual(len(results), 3)

    def test_max_total_bytes_cap(self):
        files = {
            "a.py": b"a" * 1000,
            "b.py": b"b" * 1000,
            "c.py": b"c" * 1000,
        }
        tb = make_tarball(files)
        results = list(ing.iter_tarball_files(tb, max_total_bytes=1500))
        total = sum(f.size for f in results)
        self.assertLessEqual(total, 1500)
        self.assertLess(len(results), 3)

    def test_invalid_utf8_skipped(self):
        tb = make_tarball({"bad.py": b"\xff\xfe\x00\x01broken"})
        results = list(ing.iter_tarball_files(tb))
        self.assertEqual(results, [])


class SortFilesTests(unittest.TestCase):
    def test_readme_first_ci_before_source_tests_last(self):
        files = [
            ing.RepoFile(path="tests/test_foo.py", text="x"),
            ing.RepoFile(path="src/main.py", text="x"),
            ing.RepoFile(path=".github/workflows/ci.yml", text="x"),
            ing.RepoFile(path="README.md", text="x"),
        ]
        ordered = ing.sort_files(files)
        paths = [f.path for f in ordered]
        self.assertEqual(paths[0], "README.md")
        self.assertLess(paths.index(".github/workflows/ci.yml"), paths.index("src/main.py"))
        self.assertEqual(paths[-1], "tests/test_foo.py")


class FormatFileBlockTests(unittest.TestCase):
    def test_no_trailing_newline(self):
        f = ing.RepoFile(path="a.py", text="hello")
        self.assertEqual(ing.format_file_block(f), "===== a.py =====\nhello\n")

    def test_already_trailing_newline(self):
        f = ing.RepoFile(path="a.py", text="hello\n")
        self.assertEqual(ing.format_file_block(f), "===== a.py =====\nhello\n")

    def test_empty_text(self):
        f = ing.RepoFile(path="a.py", text="")
        self.assertEqual(ing.format_file_block(f), "===== a.py =====\n\n")


class PackFilesTests(unittest.TestCase):
    def test_stays_within_budget_and_split(self):
        files = [ing.RepoFile(path=f"f{i}.py", text="x" * 50) for i in range(10)]
        block_len = len(ing.format_file_block(files[0]))
        budget = block_len * 3
        bundle, included, omitted = ing.pack_files(files, budget=budget)
        self.assertLessEqual(len(bundle), budget)
        self.assertEqual(len(included), 3)
        self.assertEqual(len(omitted), 7)
        self.assertEqual(included + omitted, [f.path for f in files])

    def test_never_splits_a_file(self):
        files = [ing.RepoFile(path="a.py", text="x" * 100)]
        bundle, included, omitted = ing.pack_files(files, budget=10)
        self.assertEqual(included, [])
        self.assertEqual(omitted, ["a.py"])
        self.assertEqual(bundle, "")

    def test_skips_oversized_to_include_later_small_file(self):
        big = ing.RepoFile(path="big.py", text="x" * 1000)
        small = ing.RepoFile(path="small.py", text="y" * 5)
        small_block_len = len(ing.format_file_block(small))
        budget = small_block_len  # too small for big, fits small exactly
        bundle, included, omitted = ing.pack_files([big, small], budget=budget)
        self.assertEqual(included, ["small.py"])
        self.assertEqual(omitted, ["big.py"])

    def test_header_always_included(self):
        header = "H" * 100
        files = [ing.RepoFile(path="a.py", text="x" * 100)]
        bundle, included, omitted = ing.pack_files(files, budget=10, header=header)
        self.assertTrue(bundle.startswith(header))
        self.assertEqual(included, [])
        self.assertEqual(omitted, ["a.py"])


class ChunkFilesTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(ing.chunk_files([]), [])

    def test_everything_fits_in_one_chunk(self):
        files = [
            ing.RepoFile(path="a.py", text="x"),
            ing.RepoFile(path="dir/b.py", text="y"),
        ]
        chunks = ing.chunk_files(files, budget=10_000)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(
            [f.path for f in chunks[0]], ["a.py", "dir/b.py"]
        )

    def test_splits_along_top_level_dirs(self):
        files = [
            ing.RepoFile(path="dirA/a1.py", text="x" * 100),
            ing.RepoFile(path="dirA/a2.py", text="x" * 100),
            ing.RepoFile(path="dirB/b1.py", text="x" * 100),
        ]
        block_len = len(ing.format_file_block(files[0]))
        # budget fits exactly one dirA-sized group (2 files) but not both groups
        budget = block_len * 2
        chunks = ing.chunk_files(files, budget=budget)
        self.assertEqual(len(chunks), 2)
        all_paths = [f.path for chunk in chunks for f in chunk]
        self.assertEqual(all_paths, ["dirA/a1.py", "dirA/a2.py", "dirB/b1.py"])

    def test_oversized_single_file_gets_own_chunk(self):
        small = ing.RepoFile(path="a.py", text="x" * 10)
        huge = ing.RepoFile(path="huge.py", text="y" * 10_000)
        chunks = ing.chunk_files([small, huge], budget=1000)
        # huge.py must appear alone in its own chunk somewhere
        huge_chunks = [c for c in chunks if any(f.path == "huge.py" for f in c)]
        self.assertEqual(len(huge_chunks), 1)
        self.assertEqual(len(huge_chunks[0]), 1)
        all_paths = [f.path for chunk in chunks for f in chunk]
        self.assertEqual(all_paths, ["a.py", "huge.py"])

    def test_never_splits_a_file_across_chunks(self):
        files = [ing.RepoFile(path=f"grp/f{i}.py", text="x" * 200) for i in range(5)]
        chunks = ing.chunk_files(files, budget=300)
        seen = set()
        for chunk in chunks:
            for f in chunk:
                self.assertNotIn(f.path, seen)
                seen.add(f.path)
        self.assertEqual(seen, {f.path for f in files})


if __name__ == "__main__":
    unittest.main()
