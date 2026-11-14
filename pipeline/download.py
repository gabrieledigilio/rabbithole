"""Fetch the challenge datasets into data/. Resumes where it stopped; checks every file's SHA-256.

    python pipeline/download.py             # S (text + vectors, 151 MB) and M (every version, 301 MB)
    python pipeline/download.py --titles    # also every title of Finnish Wikipedia, redirects included:
                                            # Wikimedia's all-titles file (about 10 MB). It tells a link
                                            # to an unwritten article from a link out of our topic.
    python pipeline/download.py --l         # also dataset L (495 MB): a second-best title list, for when
                                            # dumps.wikimedia.org cannot be reached

Source: https://github.com/MariaDB/mariadb-wikipedia-datasets (releases s-2026-10, m-2026-10, l-2026-10).
"""
import hashlib
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rabbithole import config  # noqa: E402

RELEASES = "https://github.com/MariaDB/mariadb-wikipedia-datasets/releases/download"
SETS = {
    "s": ("s-2026-10", [f"{kind}-{lang}.parquet" for kind in ("articles", "vectors-bge-m3") for lang in ("fi", "sv", "en")]),
    "m": ("m-2026-10", [f"versions-{n:02d}.parquet" for n in range(1, 17)]),
    "l": ("l-2026-10", ["articles-fi.parquet"]),
}
QUESTIONS = "https://raw.githubusercontent.com/MariaDB/mariadb-wikipedia-datasets/main/lists/s-test-questions.tsv"
AGENT = {"User-Agent": "RabbitHole/1.0 (a Junction 2026 hackathon project on the MariaDB Wikipedia datasets)"}   # Wikimedia asks for one
ALL_TITLES = ["https://dumps.wikimedia.org/fiwiki/20261001/fiwiki-20261001-all-titles-in-ns0.gz",
              "https://dumps.wikimedia.org/fiwiki/latest/fiwiki-latest-all-titles-in-ns0.gz"]


def fetch(url, path):
    part = path + ".part"
    done = os.path.getsize(part) if os.path.exists(part) else 0
    headers = dict(AGENT, Range=f"bytes={done}-") if done else AGENT
    with requests.get(url, stream=True, timeout=60, headers=headers) as r:
        if done and r.status_code != 206:                  # the server ignored the range: start again
            done = 0
        r.raise_for_status()
        with open(part, "ab" if done else "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.rename(part, path)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dataset(name):
    tag, files = SETS[name]
    folder = os.path.join(config.DATA, name)
    os.makedirs(folder, exist_ok=True)
    sums = dict(reversed(line.split()) for line in requests.get(f"{RELEASES}/{tag}/SHA256SUMS", timeout=60, headers=AGENT).text.splitlines() if line.strip())
    for file in files:
        path = os.path.join(folder, file)
        if not os.path.exists(path):
            print(f"  {name}/{file} ...", flush=True)
            fetch(f"{RELEASES}/{tag}/{file}", path)
            if file in sums and sha256(path) != sums[file]:
                os.remove(path)
                sys.exit(f"{file}: checksum does not match the release's SHA256SUMS. Run again.")
    print(f"{name.upper()}: {len(files)} files in {folder}")


def titles():
    folder = os.path.join(config.DATA, "titles")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "all-titles-in-ns0.gz")
    if os.path.exists(path):
        return print("Titles: already here")
    for url in ALL_TITLES:
        try:
            fetch(url, path)
            return print(f"Titles: {url}")
        except requests.RequestException as e:
            print(f"  could not get {url}: {type(e).__name__}")
    print("Titles: not downloaded. Everything works without them except the list of articles nobody has written.\n"
          "        For a second-best list from dataset L (495 MB):  python pipeline/download.py --l")


def questions():
    """The 37 test questions that come with dataset S, for pipeline/evaluate.py. Nothing else needs them."""
    path = os.path.join(config.DATA, "s", "test-questions.tsv")
    if not os.path.exists(path):
        try:
            fetch(QUESTIONS, path)
        except requests.RequestException as e:
            print(f"  could not get the test questions ({type(e).__name__}); only pipeline/evaluate.py needs them")


if __name__ == "__main__":
    dataset("s")
    questions()
    dataset("m")
    if "--titles" in sys.argv:
        titles()
    if "--l" in sys.argv:
        dataset("l")
