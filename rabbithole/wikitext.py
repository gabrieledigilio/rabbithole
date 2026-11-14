"""Wikitext helpers: find links, clean text, cut out the lead section and a link's context.

Not MediaWiki's parser. Regular expressions, fast enough for 862,076 versions in a few minutes.
Links that a template generates are not seen; that is the price of not running the parser.
"""
import html
import re

# [[Target]], [[Target|label]], [[Target#Section|label]]. Not [[#Section]] and not nested links.
LINK = re.compile(r"\[\[([^\[\]|#\n]+)(?:#[^\[\]|\n]*)?(?:\|([^\[\]\n]*))?\]\]")

# Prefixes that are not articles: files, categories, other namespaces, sister projects.
NAMESPACES = {
    "tiedosto", "kuva", "luokka", "file", "image", "category", "media", "wikipedia", "malline",
    "template", "ohje", "teemasivu", "toiminnot", "käyttäjä", "keskustelu", "commons", "wikt",
    "wiktionary", "wikisource", "wikiquote", "special", "user", "talk", "portal", "project",
    "help", "järjestelmäviesti", "moduuli", "module", "wp", "doi", "meta", "mw", "species",
    "wikibooks", "wikinews", "wikiversity", "wikidata", "metawikipedia", "simple", "wikiaineisto",
    "wikisanakirja", "wikisitaatit", "wikikirjasto", "wikiuutiset", "wikiopisto", "wikimatkat",
    "s", "q", "b", "n", "v", "m", "d", "c",
}
_norm_cache = {}


def normalize_title(raw):
    """A link target as Wikipedia would resolve it: spaces for underscores, first letter upper
    case. None for anything that is not an article (file, category, another language, ...)."""
    if raw in _norm_cache:
        return _norm_cache[raw]
    s = " ".join(raw.replace("_", " ").replace("‎", "").split())
    title = None
    if s and len(s) <= 200 and not any(c in s for c in "{}<>"):
        if s[0] == ":":
            s = s[1:].lstrip()
        colon = s.find(":")
        prefix = s[:colon].strip().lower() if colon > 0 else ""
        other_language = prefix.isascii() and prefix.replace("-", "").isalpha() and (
            2 <= len(prefix) <= 3 or ("-" in prefix and len(prefix) <= 12))
        if s and not (prefix in NAMESPACES or other_language):
            title = s[0].upper() + s[1:]
    if len(_norm_cache) > 500_000:
        _norm_cache.clear()
    _norm_cache[raw] = title
    return title


_TARGET = re.compile(r"\[\[([^\[\]|#\n]+)")     # the fast path: targets only, no match objects


def link_set(text):
    """The set of article titles a text links to."""
    return frozenset(t for t in map(normalize_title, _TARGET.findall(text)) if t is not None)


def find_links(text):
    """{title: first regex match} for every article link in the text."""
    found = {}
    for m in LINK.finditer(text):
        title = normalize_title(m.group(1))
        if title is not None and title not in found:
            found[title] = m
    return found


# --- Cleaning: wikitext -> text a person can read -------------------------------------------

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_REF_EMPTY = re.compile(r"<ref[^>/]*/>", re.I)
_REF = re.compile(r"<ref[^>]*>.*?</ref>", re.S | re.I)
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")           # innermost first; repeated until none is left
_TABLE = re.compile(r"\{\|.*?\|\}", re.S)
_FILE = re.compile(r"\[\[\s*(?:Tiedosto|Kuva|File|Image|Luokka|Category|Media)\s*:"
                   r"[^\[\]]*(?:\[\[[^\[\]]*\]\][^\[\]]*)*\]\]", re.I)
_LINK_TEXT = re.compile(r"\[\[(?:[^\[\]|]*\|)?([^\[\]|]*)\]\]")
_EXTERNAL = re.compile(r"\[(?:https?:)?//\S+\s+([^\]]*)\]")
_EXTERNAL_BARE = re.compile(r"\[(?:https?:)?//[^\]\s]*\]")
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t ]+")


def clean(wikitext):
    """Drop comments, references, templates, tables and files; turn links into their words."""
    t = _COMMENT.sub("", wikitext)
    t = _REF_EMPTY.sub("", t)
    t = _REF.sub("", t)
    for _ in range(12):
        t, n = _TEMPLATE.subn("", t)
        if not n:
            break
    t = _TABLE.sub("", t)
    t = _FILE.sub("", t)
    t = _LINK_TEXT.sub(r"\1", t)
    t = _EXTERNAL.sub(r"\1", t)
    t = _EXTERNAL_BARE.sub("", t)
    t = _TAG.sub("", t)
    t = t.replace("'''", "").replace("''", "")
    if "&" in t:
        t = html.unescape(t)
    return _SPACES.sub(" ", t)


_HEADING = re.compile(r"^==", re.M)
LEAD_MAX = 1500


def lead_source(wikitext):
    """The raw wikitext before the first heading: what lead() works on."""
    m = _HEADING.search(wikitext)
    return (wikitext[:m.start()] if m else wikitext)[:60_000]


def lead(raw_lead):
    """The article's opening paragraphs as plain text, at most LEAD_MAX characters.
    Empty for redirects and for pages that open with nothing but an infobox."""
    lines = []
    for line in clean(raw_lead).split("\n"):
        line = line.strip()
        if not line or line[0] in "|!{}" or line.upper().startswith(("#OHJAUS", "#REDIRECT")):
            continue
        lines.append(line.lstrip("*#:; "))
    text = "\n".join(l for l in lines if l)
    if len(text) > LEAD_MAX:
        cut = text.rfind(". ", 0, LEAD_MAX)
        text = text[:cut + 1] if cut > LEAD_MAX // 2 else text[:LEAD_MAX].rsplit(" ", 1)[0] + " …"
    return text


_OPEN, _CLOSE = "", ""      # private-use marks that survive cleaning
CONTEXT_BEFORE, CONTEXT_AFTER = 170, 190


def context(text, match):
    """The sentence around a link, cleaned, with the link's words between ⟦ and ⟧.
    Starts with '⌂ ' when the link sits inside a template, table or reference."""
    a, b = match.span()
    label = (match.group(2) or match.group(1)).strip() or match.group(1).strip()
    raw = text[max(0, a - 400):a] + _OPEN + label + _CLOSE + text[b:b + 400]
    t = clean(raw)
    boxed = _OPEN not in t
    if boxed:                              # the cleaner removed the link: it was inside {{ }} or <ref>
        t = _LINK_TEXT.sub(r"\1", raw)
        t = re.sub(r"<[^>]+>|\{\{|\}\}|\{\||\|\}|'''?|\|", " ", t)
    before, _, rest = t.partition(_OPEN)
    word, _, after = rest.partition(_CLOSE)
    # A fragment can start or end in the middle of a template or reference: cut the debris.
    for mark in ("}}", "</ref>", "|}", "\n\n"):
        if mark in before:
            before = before.rsplit(mark, 1)[1]
    for mark in ("{{", "<ref", "{|", "\n\n"):
        if mark in after:
            after = after.split(mark, 1)[0]
    glued = after[:1].isalpha()            # [[Karjala]]ssa: the ending belongs to the linked word
    before = " ".join(before.split())[-CONTEXT_BEFORE:]
    after = " ".join(after.split())[:CONTEXT_AFTER]
    if len(before) == CONTEXT_BEFORE and " " in before:
        before = "…" + before.split(" ", 1)[1]
    if len(after) == CONTEXT_AFTER and " " in after:
        after = after.rsplit(" ", 1)[0] + "…"
    out = f"{before} ⟦{' '.join(word.split())}⟧{'' if glued else ' '}{after}".strip()
    if out.count("|") >= 3:                # a table row: cells apart, not a wall of pipes
        boxed = True
        out = re.sub(r"(?:\s*\|[-+}]?\s*)+", " · ", out).strip(" ·")
    return ("⌂ " if boxed else "") + re.sub(r"\s+([,.;:)])", r"\1", out)
