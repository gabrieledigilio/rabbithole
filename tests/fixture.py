"""A four-article Wikipedia, small enough to know every answer by heart.

    build(folder)   writes the same files the real datasets come as:
                    m/versions-01.parquet          every version of every article (dataset M)
                    s/articles-*.parquet           today's text (dataset S)
                    s/vectors-bge-m3-*.parquet     one vector per article
                    titles/all-titles-in-ns0.gz    every title on the wiki, redirects too

The story, which the tests in test_pipeline.py check against:

  Suomi             written 2 Feb 2004. Links to Eurooppa, Mannerheim-linja and "Tuntematon aihe"
                    from the start, and to Talvisota from 1 Feb 2005.
  Saksa             written 1 Jun 2004 with a link to Eurooppa. Since 4 Apr 2011 also Talvisota and
                    "Tuntematon aihe".
  Talvisota         written 10 Jan 2005: Suomi, Neuvostoliitto, Mannerheim-linja.
                    The link to Saksa is disputed: added 1 Mar 2006 (Bertil), removed 5 May 2010
                    (Cecilia), added 7 Jul 2012 (Bertil), removed 1 Jan 2014 (David), added
                    2 Feb 2016 (Eeva) and still there. On 1 Mar 2006 the page was blanked at 05:00
                    and restored at 06:00: that must not break any link's period.
  Mannerheim-linja  written 9 Sep 2009, after Suomi and Talvisota had linked to it for years.

  Nobody has written "Tuntematon aihe" (two articles link to it). Eurooppa, Neuvostoliitto and
  Karjalankannas exist on the wiki but are outside the four articles.
"""
import gzip
import hashlib
import os
from datetime import datetime, timezone

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

SUOMI, SAKSA, TALVISOTA, MANNERHEIM = 4, 2, 1, 3

T_LEAD = "'''Talvisota''' oli [[Suomi|Suomen]] ja [[Neuvostoliitto|Neuvostoliiton]] välinen sota vuosina 1939–1940."
T_BODY = "\n\n== Taistelut ==\nPääpuolustuslinja oli [[Mannerheim-linja]]."
T_SAKSA = " [[Saksa]] tuki Neuvostoliittoa sodan aikana."
S_LEAD = "'''Suomi''' on tasavalta Pohjois-[[Eurooppa|Euroopassa]]."
S_BODY = "\n\n== Historia ==\n[[Mannerheim-linja]] rakennettiin 1920- ja 1930-luvuilla. Katso myös [[Tuntematon aihe]]."
D_LEAD = "'''Saksa''' on valtio Keski-[[Eurooppa|Euroopassa]]."

#          page        title               rev  when                 user          comment                          text
VERSIONS = [
    (SUOMI, "Suomi", 401, "2004-02-02 10:00", "Aino", "uusi artikkeli", S_LEAD + S_BODY),
    (SUOMI, "Suomi", 402, "2005-02-01 09:00", "Aino", "talvisota", S_LEAD + S_BODY + " [[Talvisota]] käytiin vuosina 1939–1940."),
    (SAKSA, "Saksa", 201, "2004-06-01 12:00", "Bertil", "uusi artikkeli", D_LEAD),
    (SAKSA, "Saksa", 202, "2011-04-04 08:00", "Cecilia", "historiaa",
     D_LEAD + "\n\n== Historia ==\nSaksa ei osallistunut [[Talvisota|talvisotaan]]. Katso [[Tuntematon aihe]]."),
    (TALVISOTA, "Talvisota", 101, "2005-01-10 12:00", "Aino", "uusi artikkeli", T_LEAD + T_BODY),
    (TALVISOTA, "Talvisota", 102, "2006-03-01 00:00", "Bertil", "Saksan osuus", T_LEAD + T_SAKSA + T_BODY),
    (TALVISOTA, "Talvisota", 103, "2006-03-01 05:00", "192.0.2.7", "", ""),                                   # blanked
    (TALVISOTA, "Talvisota", 104, "2006-03-01 06:00", "Aino", "kumottu vandalismi", T_LEAD + T_SAKSA + T_BODY),   # = 102
    (TALVISOTA, "Talvisota", 105, "2010-05-05 10:00", "Cecilia", "ei lähdettä", T_LEAD + T_BODY + " Sota päättyi Moskovan rauhaan."),
    (TALVISOTA, "Talvisota", 106, "2012-07-07 10:00", "Bertil", "palautettu, lähde lisätty",
     T_LEAD + T_SAKSA + "<ref>Lähde</ref>" + T_BODY + " Sota päättyi Moskovan rauhaan."),
    (TALVISOTA, "Talvisota", 107, "2014-01-01 10:00", "David", "poistettu kiistanalainen väite", T_LEAD + T_BODY + " Sota päättyi Moskovan rauhaan vuonna 1940."),
    (TALVISOTA, "Talvisota", 108, "2016-02-02 10:00", "Eeva", "lisätty lähteineen",
     T_LEAD + " [[Saksa]] oli solminut Neuvostoliiton kanssa hyökkäämättömyyssopimuksen." + T_BODY + " Sota päättyi Moskovan rauhaan vuonna 1940."),
    (MANNERHEIM, "Mannerheim-linja", 301, "2009-09-09 09:00", "David", "uusi artikkeli",
     "'''Mannerheim-linja''' oli puolustuslinja [[Karjalankannas|Karjalankannaksella]] [[Talvisota|talvisodassa]]."),
]
ANONYMOUS = {103}
WIKI_TITLES = ["Suomi", "Saksa", "Talvisota", "Mannerheim-linja", "Eurooppa", "Neuvostoliitto", "Karjalankannas"]

#           lang  page  wikidata  title               text
ARTICLES = [
    ("fi", SUOMI, "Q33", "Suomi", "# Suomi\n\nSuomi on tasavalta Pohjois-Euroopassa."),
    ("fi", SAKSA, "Q183", "Saksa", "# Saksa\n\nSaksa on valtio Keski-Euroopassa."),
    ("fi", TALVISOTA, "Q190549", "Talvisota", "# Talvisota\n\nTalvisota oli Suomen ja Neuvostoliiton välinen sota vuosina 1939–1940."),
    ("fi", MANNERHEIM, "Q152411", "Mannerheim-linja", "# Mannerheim-linja\n\nMannerheim-linja oli puolustuslinja Karjalankannaksella talvisodassa."),
    ("en", 9001, "Q190549", "Winter War", "# Winter War\n\nThe Winter War was a war between the Soviet Union and Finland in 1939–1940."),
    ("en", 9002, "Q33", "Finland", "# Finland\n\nFinland is a republic in Northern Europe."),
]


def vectors():
    """Unit vectors with one known shape: Talvisota, Mannerheim-linja and Winter War lie close together."""
    rng = np.random.default_rng(1)
    war, line, country, other, english = (v / np.linalg.norm(v) for v in rng.standard_normal((5, 1024)))
    made = {("fi", TALVISOTA): war, ("fi", MANNERHEIM): war + 0.5 * line, ("en", 9001): war + 0.2 * english,
            ("fi", SUOMI): country, ("en", 9002): country + 0.2 * english, ("fi", SAKSA): other}
    return {key: (v / np.linalg.norm(v)).astype("float32") for key, v in made.items()}


def build(folder):
    for sub in ("m", "s", "titles"):
        os.makedirs(os.path.join(folder, sub), exist_ok=True)

    when = [datetime.strptime(v[3], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc) for v in VERSIONS]
    pq.write_table(pa.table({
        "page_id": pa.array([v[0] for v in VERSIONS], pa.uint32()),
        "title": [v[1] for v in VERSIONS],
        "rev_id": pa.array([v[2] for v in VERSIONS], pa.uint32()),
        "parent_id": pa.array([None] * len(VERSIONS), pa.uint32()),
        "timestamp": pa.array(when, pa.timestamp("ms", tz="UTC")),
        "user": [v[4] for v in VERSIONS],
        "anonymous": [v[2] in ANONYMOUS for v in VERSIONS],
        "comment": [v[5] for v in VERSIONS],
        "minor": [False] * len(VERSIONS),
        "sha1": [hashlib.sha1(v[6].encode()).hexdigest() for v in VERSIONS],
        "bytes": pa.array([len(v[6].encode()) for v in VERSIONS], pa.uint32()),
        "text": [v[6] for v in VERSIONS],
    }), os.path.join(folder, "m", "versions-01.parquet"))

    vecs = vectors()
    for lang in ("fi", "en"):
        rows = [a for a in ARTICLES if a[0] == lang]
        pq.write_table(pa.table({
            "lang": [lang] * len(rows),
            "page_id": pa.array([a[1] for a in rows], pa.int64()),
            "rev_id": pa.array([1000 + a[1] for a in rows], pa.int64()),
            "wikidata_id": [a[2] for a in rows],
            "title": [a[3] for a in rows],
            "url": [f"https://{lang}.wikipedia.org/wiki/{a[3].replace(' ', '_')}" for a in rows],
            "date_modified": ["2026-09-30T12:00:00Z"] * len(rows),
            "text": [a[4] for a in rows],
        }), os.path.join(folder, "s", f"articles-{lang}.parquet"))
        pq.write_table(pa.table({
            "lang": [lang] * len(rows),
            "page_id": pa.array([a[1] for a in rows], pa.uint32()),
            "model": ["bge-m3"] * len(rows),
            "truncated": [False] * len(rows),
            "embedding": pa.FixedSizeListArray.from_arrays(pa.array(np.concatenate([vecs[(lang, a[1])] for a in rows]), pa.float32()), 1024),
        }), os.path.join(folder, "s", f"vectors-bge-m3-{lang}.parquet"))

    with gzip.open(os.path.join(folder, "titles", "all-titles-in-ns0.gz"), "wt", encoding="utf-8") as f:
        f.write("page_title\n" + "".join(t.replace(" ", "_") + "\n" for t in WIKI_TITLES))
    return folder
