"""Turn a GitHub repository URL into a filtered, packed text bundle.

Everything here is deterministic Python -- no model is involved in deciding
what gets included or how it is packed.  The pipeline is: resolve a repo URL
(:func:`parse_repo_url`), stream its tarball (:func:`fetch_repository_files`
-> :func:`iter_tarball_files`), filter out noise (:func:`exclude_reason`,
:func:`looks_generated`), order it by signal (:func:`sort_files`), and pack it
into one or more character-budgeted bundles (:func:`pack_files`,
:func:`chunk_files`) that a long-context model can read.

``httpx`` is imported lazily inside :func:`fetch_repository_files` only, so
every other function in this module is importable and testable without it
installed.
"""

from __future__ import annotations

import asyncio
import re
import tarfile
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import urlparse

GITHUB_WEB_ROOT = "https://github.com"

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

#: Directory basenames whose entire subtree is dropped. Note ".github" is
#: intentionally NOT here (CI workflow files are useful signal) and
#: "migrations" is intentionally NOT here (schema history is useful signal).
EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        "venv",
        ".venv",
        "env",
        "target",
        "dist",
        "build",
        "vendor",
        "__pycache__",
        ".next",
        ".nuxt",
        ".cache",
        "coverage",
        "site-packages",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        ".idea",
        ".vscode",
        "bin",
        "obj",
        "Pods",
        ".terraform",
    }
)

EXCLUDED_EXTENSIONS: frozenset[str] = frozenset(
    {
        # images
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
        # fonts
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        # media
        ".mp4", ".mp3", ".wav", ".mov", ".avi", ".webm",
        # archives
        ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
        # binaries
        ".so", ".dylib", ".dll", ".exe", ".o", ".a", ".class", ".jar",
        ".pyc", ".pyo", ".wasm", ".bin", ".dat", ".pdb",
        # data blobs
        ".csv", ".tsv", ".parquet", ".sqlite", ".db", ".pkl", ".npy", ".npz",
        ".h5", ".onnx", ".pt", ".pth", ".ckpt",
        # docs
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    }
)

EXCLUDED_FILENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lock",
        "bun.lockb",
        "poetry.lock",
        "pdm.lock",
        "uv.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
        "packages.lock.json",
        ".ds_store",
        "pipfile.lock",
    }
)

EXCLUDED_FILENAME_SUFFIXES: tuple[str, ...] = (
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
    ".snap",
    ".generated.go",
    "_pb2.py",
    ".pb.go",
)

#: Dotfiles that are excluded by name unless explicitly allow-listed.
_ALLOWED_DOTFILES: frozenset[str] = frozenset(
    {".env.example", ".gitignore", ".dockerignore"}
)

MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 8_000_000
MAX_FILES = 3_000
DEFAULT_CHAR_BUDGET = 600_000

#: Basenames (lowercase) that sort first, in this priority order.
PRIORITY_FILENAMES: tuple[str, ...] = (
    "readme.md",
    "readme.rst",
    "readme.txt",
    "readme",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "go.mod",
    "cargo.toml",
    "pom.xml",
    "build.gradle",
    "gemfile",
    "composer.json",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "makefile",
)

#: Path components identifying a test directory, for sort_files' test tier.
_TEST_DIR_COMPONENTS: frozenset[str] = frozenset({"test", "tests", "spec", "__tests__"})
_TEST_BASENAME_SUFFIXES: tuple[str, ...] = (
    "_test.py",
    ".test.ts",
    ".test.js",
    ".spec.ts",
    ".spec.js",
)
_INFRA_YAML_KEYWORDS: tuple[str, ...] = ("k8s", "kube", "deploy", "helm")


@dataclass(frozen=True)
class RepoFile:
    """One decoded text file from a repository, relative to its root."""

    path: str
    text: str

    @property
    def size(self) -> int:
        """UTF-8 byte length of ``text``."""
        return len(self.text.encode("utf-8"))


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

_SCP_STYLE_RE = re.compile(r"^git@([^:/]+):(.+)$")


def _finish_parse(host: str, raw_path: str) -> tuple[str, str, str | None]:
    """Shared tail of parse_repo_url once host and raw path are known."""
    if host != "github.com":
        raise ValueError(f"Not a GitHub URL (host={host!r})")
    trimmed = raw_path.strip("/")
    if not trimmed:
        raise ValueError("URL has no repository (owner/repo) component")
    segments = trimmed.split("/")
    if len(segments) < 2 or not segments[0] or not segments[1]:
        raise ValueError("URL is missing an owner or repo segment")
    owner, repo = segments[0], segments[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    for seg in (owner, repo):
        if seg in (".", "..") or "/" in seg or "\\" in seg:
            raise ValueError(f"Invalid owner/repo segment: {seg!r}")
    ref: str | None = None
    if len(segments) > 2:
        kind = segments[2]
        rest = segments[3:]
        if kind in ("tree", "commit") and rest:
            ref = "/".join(rest)
    return owner, repo, ref


def canonical_repo_url(owner: str, repo: str) -> str:
    """The one spelling of a repository's URL used as its stored identity.

    ``parse_repo_url`` accepts many spellings of the same repository -- a bare
    ``owner/repo``, a ``.git`` suffix, a ``/tree/<branch>`` link, a ``www.``
    prefix.  Persisting whichever one the caller happened to type would defeat
    the unique constraint on ``projects.github_repo_url`` and create a duplicate
    row per spelling, so every write goes through this instead.
    """
    return f"{GITHUB_WEB_ROOT}/{owner}/{repo}"


def parse_repo_url(url: str) -> tuple[str, str, str | None]:
    """Parse a GitHub repo reference into ``(owner, repo, ref)``.

    Accepts full https/http URLs (with or without ``.git``, ``/tree/<ref>``,
    ``/commit/<sha>``, a trailing slash, or a ``www.`` prefix), the
    ``git@github.com:owner/repo.git`` SCP-style form, and the bare
    ``owner/repo`` shorthand. Raises ``ValueError`` for empty input, a
    non-GitHub host, a URL with no repo component, or an owner/repo segment
    that is ``.``/``..`` or contains a path separator.
    """
    if url is None or not str(url).strip():
        raise ValueError("Repository URL is empty")
    raw = str(url).strip()

    scp_match = _SCP_STYLE_RE.match(raw)
    if scp_match:
        host, rest = scp_match.groups()
        return _finish_parse(host.lower(), rest)

    if "://" not in raw:
        # No scheme and no "git@host:" form -- only the bare "owner/repo"
        # shorthand is accepted here.
        parts = raw.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            return _finish_parse("github.com", raw)
        raise ValueError(f"Cannot parse repository URL: {raw!r}")

    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[len("www.") :]
    return _finish_parse(host, parsed.path)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def exclude_reason(path: str, size: int | None = None) -> str | None:
    """Return why ``path`` should be excluded, or ``None`` to include it.

    Checked in order: excluded directory component, excluded exact filename,
    excluded filename suffix, excluded extension, hidden (dot) file not on
    the allow-list, then an oversized file. A path with an empty basename
    (a directory entry, or the empty string) is always ``excluded_dir``.
    """
    normalized = str(path).replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    if basename == "":
        return "excluded_dir"

    parts = [p for p in normalized.strip("/").split("/") if p]
    for part in parts:
        if part.lower() in EXCLUDED_DIRS:
            return "excluded_dir"

    lower_basename = basename.lower()
    if lower_basename in EXCLUDED_FILENAMES:
        return "excluded_filename"

    for suffix in EXCLUDED_FILENAME_SUFFIXES:
        if lower_basename.endswith(suffix):
            return "excluded_suffix"

    ext = ""
    if "." in basename:
        ext = "." + basename.rsplit(".", 1)[1]
    if ext.lower() in EXCLUDED_EXTENSIONS:
        return "excluded_extension"

    if lower_basename.startswith(".") and lower_basename not in _ALLOWED_DOTFILES:
        return "hidden_file"

    if size is not None and size > MAX_FILE_BYTES:
        return "too_large"

    return None


def looks_generated(text: str) -> bool:
    """Heuristic flag for minified/machine-generated text that slipped past
    the name-based filters.

    Looks only at a bounded sample (the first 200k characters) so a huge file
    cannot make this check itself expensive. A single very long line (e.g. a
    minified bundle on one line) or a generally high average line length
    (e.g. a generated data table) both count as "generated".
    """
    if not text:
        return False
    sample = text[:200_000]
    lines = sample.splitlines()
    if not lines:
        return False
    longest = max(len(line) for line in lines)
    if longest > 5_000:
        return True
    if len(sample) > 2_000:
        mean_len = sum(len(line) for line in lines) / len(lines)
        if mean_len > 500:
            return True
    return False


def strip_archive_prefix(name: str) -> str:
    """Drop the leading ``owner-repo-<sha>/`` component of a tarball entry."""
    normalized = name.replace("\\", "/").lstrip("/")
    parts = normalized.split("/", 1)
    if len(parts) < 2:
        return ""
    return parts[1]


def iter_tarball_files(
    fileobj: Any,
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_files: int = MAX_FILES,
) -> Iterator[RepoFile]:
    """Stream a gzipped tarball, yielding included, decoded :class:`RepoFile`.

    Uses "r|gz" (sequential streaming) rather than "r:gz" (random access)
    because the source is often a live HTTP response body, not a seekable
    file -- streaming mode never needs to seek backwards. The byte/file caps
    exist because a compressed tarball can decompress into something far
    larger than its transfer size ("zip bomb" style blowup); enforcing them
    here means callers never have to trust the remote size claims.
    """
    total_bytes = 0
    count = 0
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:
        for member in tar:
            if count >= max_files:
                break
            if not member.isfile():
                continue

            raw_path = strip_archive_prefix(member.name)
            if not raw_path:
                continue
            posix_path = raw_path.replace("\\", "/")
            if posix_path.startswith("/") or any(
                part == ".." for part in posix_path.split("/")
            ):
                continue  # path traversal guard

            if exclude_reason(posix_path) is not None:
                continue
            if member.size > max_file_bytes:
                continue

            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            data = extracted.read()

            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue  # binary content that slipped past the extension filter

            if looks_generated(text):
                continue

            size = len(data)
            if total_bytes + size > max_total_bytes:
                break  # cumulative cap reached; stop rather than skip-and-continue

            total_bytes += size
            count += 1
            yield RepoFile(path=posix_path, text=text)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def _tier(file: "RepoFile") -> tuple[int, int]:
    path = file.path
    basename = path.rsplit("/", 1)[-1]
    lower_basename = basename.lower()

    if lower_basename in PRIORITY_FILENAMES:
        return (0, PRIORITY_FILENAMES.index(lower_basename))

    lower_parts = [p.lower() for p in path.split("/")]

    if ".github" in lower_parts:
        return (1, 0)
    if lower_basename.startswith("dockerfile") or "docker-compose" in lower_basename:
        return (1, 0)
    ext = ""
    if "." in basename:
        ext = "." + basename.rsplit(".", 1)[1]
    if ext.lower() in (".tf", ".yaml", ".yml"):
        lower_path = path.lower()
        if any(keyword in lower_path for keyword in _INFRA_YAML_KEYWORDS):
            return (1, 0)

    if any(part in _TEST_DIR_COMPONENTS for part in lower_parts):
        return (3, 0)
    if lower_basename.startswith("test_") or lower_basename.endswith(_TEST_BASENAME_SUFFIXES):
        return (3, 0)

    return (2, 0)


def sort_files(files: Iterable[RepoFile]) -> list[RepoFile]:
    """Order files so a budget truncation drops the least informative tail.

    Tiers: (0) known key filenames (README, manifests, ...), (1) CI/infra
    files, (2) ordinary source, (3) test files. Within a tier, shallower
    paths sort first, then lexicographically -- deterministic regardless of
    input order.
    """

    def key(file: RepoFile) -> tuple[int, int, int, str]:
        tier, tier_rank = _tier(file)
        depth = file.path.count("/")
        return (tier, tier_rank, depth, file.path)

    return sorted(files, key=key)


# ---------------------------------------------------------------------------
# Formatting and packing
# ---------------------------------------------------------------------------


def format_file_block(file: RepoFile) -> str:
    """Render one file as ``===== path =====\\n{text}\\n`` (one trailing newline)."""
    text = file.text.rstrip("\n") + "\n"
    return f"===== {file.path} =====\n{text}"


def build_global_header(
    *,
    owner: str,
    repo: str,
    ref: str | None,
    paths: Sequence[str],
    priority_files: Sequence[RepoFile],
    max_paths: int = 400,
) -> str:
    """Build a compact preamble prepended to every chunk.

    Includes the repo identity, a (possibly truncated) file tree, and the
    full text of a handful of priority files, so no individual chunk is ever
    read by the model without at least this much orientation.
    """
    lines = [f"# Repository: {owner}/{repo}"]
    if ref:
        lines.append(f"Ref: {ref}")
    lines.append("")
    lines.append("## File tree")
    shown = list(paths)[:max_paths]
    omitted_count = len(paths) - len(shown)
    lines.extend(shown)
    if omitted_count > 0:
        lines.append(f"... ({omitted_count} more paths omitted)")
    lines.append("")
    lines.append("## Key files")
    header = "\n".join(lines) + "\n"
    for file in priority_files:
        header += format_file_block(file)
    return header


def pack_files(
    files: Sequence[RepoFile], *, budget: int = DEFAULT_CHAR_BUDGET, header: str = ""
) -> tuple[str, list[str], list[str]]:
    """Concatenate ``header`` + formatted files while staying within ``budget``.

    A file that alone would exceed the remaining budget is omitted whole --
    never split mid-file, since a half-file is worse than no file for a model
    trying to reason about it -- but later, smaller files are still tried.
    The header always goes in, even if it alone exceeds budget.
    """
    bundle = header
    included: list[str] = []
    omitted: list[str] = []
    remaining = budget - len(header)

    for file in files:
        block = format_file_block(file)
        block_len = len(block)
        if block_len <= remaining:
            bundle += block
            included.append(file.path)
            remaining -= block_len
        else:
            omitted.append(file.path)

    return bundle, included, omitted


def chunk_files(
    files: Sequence[RepoFile], *, budget: int = DEFAULT_CHAR_BUDGET
) -> list[list[RepoFile]]:
    """Group files into budget-fitting chunks, splitting along module boundaries.

    Files are grouped by top-level path component (a root-level file is its
    own group of one) preserving incoming order. A group that fits is kept
    together; an oversized group is split across consecutive chunks without
    ever splitting an individual file, and a single file bigger than the
    whole budget still gets a dedicated one-file chunk rather than being
    dropped.
    """
    files = list(files)
    if not files:
        return []

    groups: dict[str, list[RepoFile]] = {}
    order: list[str] = []
    for file in files:
        top = file.path.split("/", 1)[0] if "/" in file.path else file.path
        if top not in groups:
            groups[top] = []
            order.append(top)
        groups[top].append(file)

    def block_size(file: RepoFile) -> int:
        return len(format_file_block(file))

    chunks: list[list[RepoFile]] = []
    current: list[RepoFile] = []
    current_size = 0

    for key in order:
        group = groups[key]
        group_size = sum(block_size(f) for f in group)

        if group_size <= budget:
            if current and current_size + group_size > budget:
                chunks.append(current)
                current, current_size = [], 0
            current.extend(group)
            current_size += group_size
            continue

        # Oversized group: split file-by-file, never splitting a single file.
        for file in group:
            fsize = block_size(file)
            if fsize > budget:
                if current:
                    chunks.append(current)
                    current, current_size = [], 0
                chunks.append([file])
                continue
            if current and current_size + fsize > budget:
                chunks.append(current)
                current, current_size = [], 0
            current.append(file)
            current_size += fsize

    if current:
        chunks.append(current)

    return chunks


# ---------------------------------------------------------------------------
# Fetching (the only piece that needs httpx)
# ---------------------------------------------------------------------------


class _BufferedByteReader:
    """Adapts a sync byte-chunk iterator into a buffering file-like ``read(n)``.

    ``tarfile``'s streaming mode ("r|gz") only ever calls ``.read(n)``
    sequentially, but an HTTP response hands out arbitrarily sized chunks;
    this buffers whatever comes back until enough bytes are available (or the
    stream ends) so tarfile always gets exactly the amount it asked for.
    """

    def __init__(self, byte_iter: Iterable[bytes]) -> None:
        self._iter = iter(byte_iter)
        self._buf = b""

    def read(self, n: int = -1) -> bytes:
        while n is None or n < 0 or len(self._buf) < n:
            try:
                chunk = next(self._iter)
            except StopIteration:
                break
            if chunk:
                self._buf += chunk
        if n is None or n < 0:
            result, self._buf = self._buf, b""
        else:
            result, self._buf = self._buf[:n], self._buf[n:]
        return result


def _fetch_repository_files_blocking(
    owner: str,
    repo: str,
    *,
    ref: str | None = None,
    token: str | None = None,
    timeout: float = 120.0,
    **caps: Any,
) -> list[RepoFile]:
    """Blocking implementation of :func:`fetch_repository_files`.

    Deliberately synchronous: ``tarfile``'s streaming mode drives the download
    by calling ``read(n)`` itself, which cannot be satisfied from an async
    iterator without buffering the whole archive first.  Keeping it sync and
    running it in a worker thread preserves true streaming; see the async
    wrapper below.

    ``httpx`` is imported lazily so this is the only function in the module
    that requires it installed. The response body is streamed straight into
    :func:`iter_tarball_files` via a small buffering adapter -- never
    ``.read()``/``.content`` in full and never written to disk -- because the
    decompressed archive can be much larger than the HTTP payload.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only without httpx
        raise RuntimeError(
            "httpx is required for fetch_repository_files; install it with "
            "`pip install httpx`."
        ) from exc

    ref_segment = ref or ""
    url = f"https://api.github.com/repos/{owner}/{repo}/tarball/{ref_segment}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as response:
            status = response.status_code
            if status == 404:
                raise RuntimeError(
                    f"GitHub tarball request failed with 404: repository "
                    f"{owner}/{repo} (ref={ref!r}) not found"
                )
            if status in (403, 429):
                raise RuntimeError(
                    f"GitHub tarball request failed with {status}: rate limited. "
                    "Pass a personal access token (PAT) via `token=` to raise "
                    "the rate limit."
                )
            if not (200 <= status < 300):
                raise RuntimeError(
                    f"GitHub tarball request failed with status {status}"
                )

            reader = _BufferedByteReader(response.iter_bytes())
            return list(iter_tarball_files(reader, **caps))


async def fetch_repository_files(
    owner: str,
    repo: str,
    *,
    ref: str | None = None,
    token: str | None = None,
    timeout: float = 120.0,
    **caps: Any,
) -> list[RepoFile]:
    """Download a repo's tarball and return its filtered files, off-loop.

    The download and decompression are blocking work, so they run in a worker
    thread.  Without this the event loop would stall for the whole transfer and
    callers could not overlap the tarball fetch with other requests (the commit
    bounds lookup, for one) even though they ``gather`` them.
    """
    return await asyncio.to_thread(
        _fetch_repository_files_blocking,
        owner,
        repo,
        ref=ref,
        token=token,
        timeout=timeout,
        **caps,
    )
