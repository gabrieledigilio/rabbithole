-- Rabbit Hole: the tables. Needs MariaDB 11.8 or later (VECTOR, system_versioning_insert_history).
-- Run by pipeline/load.py. Every statement ends with a semicolon on its own line end.

-- ============ The graph, kept by MariaDB as history ==========================================

-- One row per article. row_start is the moment the article was created on Wikipedia, so
-- FOR SYSTEM_TIME AS OF '2008-01-01' returns the articles that existed that day.
CREATE TABLE IF NOT EXISTS page (
  page_id   INT UNSIGNED NOT NULL,           -- Wikipedia's own id: https://fi.wikipedia.org/?curid=<page_id>
  title     VARCHAR(255) NOT NULL,
  row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START INVISIBLE,
  row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END INVISIBLE,
  PERIOD FOR SYSTEM_TIME (row_start, row_end),
  PRIMARY KEY (page_id),
  KEY (title)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin WITH SYSTEM VERSIONING;

-- One row per link. The table holds the links of today; every link that was ever removed is a
-- history row with the real times it was added and removed. Nothing in the application keeps
-- history: MariaDB does.
CREATE TABLE IF NOT EXISTS link (
  src       INT UNSIGNED NOT NULL,           -- the article the link is in
  target    VARCHAR(255) NOT NULL,           -- the title it points to; may not exist (a red link)
  dst       INT UNSIGNED,                    -- page_id of the target when it is one of our articles
  start_rev INT UNSIGNED,                    -- the version that added it:  https://fi.wikipedia.org/?oldid=<start_rev>
  end_rev   INT UNSIGNED,                    -- the version that removed it; NULL while the link is alive
  row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START INVISIBLE,
  row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END INVISIBLE,
  PERIOD FOR SYSTEM_TIME (row_start, row_end),
  PRIMARY KEY (src, target),
  KEY (dst),
  KEY (target)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin WITH SYSTEM VERSIONING;

-- The opening paragraphs of every article, every version of them: how the article introduced
-- its subject on any day since 2003. Plain text, cut out of the wikitext by rabbithole/wikitext.py.
CREATE TABLE IF NOT EXISTS lead (
  page_id   INT UNSIGNED NOT NULL,
  rev_id    INT UNSIGNED NOT NULL,           -- the version that changed the lead to this text
  text      TEXT COMPRESSED NOT NULL,
  row_start TIMESTAMP(6) GENERATED ALWAYS AS ROW START INVISIBLE,
  row_end   TIMESTAMP(6) GENERATED ALWAYS AS ROW END INVISIBLE,
  PERIOD FOR SYSTEM_TIME (row_start, row_end),
  PRIMARY KEY (page_id),
  KEY (page_id, row_start)                   -- "as of an old date" from the other end (see the versioning demo's README)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin WITH SYSTEM VERSIONING;

-- ============ Around the graph: ordinary tables =============================================

-- The sentence around each link when it was added, with the linked words between ⟦ and ⟧.
CREATE TABLE IF NOT EXISTS link_context (
  src       INT UNSIGNED NOT NULL,
  target    VARCHAR(255) NOT NULL,
  start_rev INT UNSIGNED NOT NULL,
  context   VARCHAR(700) NOT NULL,
  PRIMARY KEY (src, target, start_rev)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- The edit log: one row per saved version, without the text.
CREATE TABLE IF NOT EXISTS revision (
  rev_id    INT UNSIGNED NOT NULL,
  page_id   INT UNSIGNED NOT NULL,
  ts        DATETIME(3) NOT NULL,            -- UTC
  user      VARCHAR(255),
  anonymous BOOL NOT NULL,                   -- an IP address or, since 2025, a temporary account
  minor     BOOL NOT NULL,
  comment   VARCHAR(600),                    -- the edit summary
  bytes     INT UNSIGNED,
  n_links   SMALLINT UNSIGNED NOT NULL,      -- links in this version
  n_added   SMALLINT UNSIGNED NOT NULL,      -- links this version added ...
  n_removed SMALLINT UNSIGNED NOT NULL,      -- ... and removed
  is_revert BOOL NOT NULL,                   -- its text is identical to an earlier version's
  PRIMARY KEY (rev_id),
  KEY (page_id, ts)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- ============ Dataset S: today's text in three languages, and a vector per article ==========
-- Same tables as mariadb-vector-wikipedia-rag, so its findings and settings carry over.

CREATE TABLE IF NOT EXISTS article (
  lang          CHAR(2)      NOT NULL,       -- fi, en, sv
  page_id       INT UNSIGNED NOT NULL,       -- per language
  rev_id        INT UNSIGNED NOT NULL,
  wikidata_id   VARCHAR(20),                 -- the same topic across languages
  title         VARCHAR(255) NOT NULL,
  url           VARCHAR(500) NOT NULL,
  date_modified DATETIME,
  text          MEDIUMTEXT   NOT NULL,       -- clean Markdown (FineWiki, August 2025)
  PRIMARY KEY (lang, page_id),
  KEY (wikidata_id),
  KEY (lang, title),
  FULLTEXT (title, text)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_uca1400_ai_ci;

CREATE TABLE IF NOT EXISTS article_vector (
  lang       CHAR(2)      NOT NULL,
  page_id    INT UNSIGNED NOT NULL,
  model      VARCHAR(50)  NOT NULL,
  truncated  BOOL         NOT NULL,
  embedding  VECTOR(1024) NOT NULL,          -- bge-m3
  PRIMARY KEY (lang, page_id),
  VECTOR INDEX (embedding) M=16 DISTANCE=cosine
) CHARACTER SET utf8mb4 COLLATE utf8mb4_uca1400_ai_ci;

-- Every article title of Finnish Wikipedia (dataset L, optionally Wikimedia's all-titles file):
-- a link whose target is not here points at an article nobody has written.
CREATE TABLE IF NOT EXISTS fiwiki_title (
  title       VARCHAR(255) NOT NULL,
  is_article  BOOL NOT NULL,                 -- 1: an article in dataset L. 0: only in all-titles (a redirect, or newer)
  PRIMARY KEY (title)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
