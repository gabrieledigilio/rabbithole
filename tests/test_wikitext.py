"""python -m unittest tests.test_wikitext    (no database needed)"""
import unittest

from rabbithole import wikitext as w

SAMPLE = """{{Sota
| nimi = Talvisota
| osapuoli1 = [[Suomi]]
}}
[[Tiedosto:Winter war.jpg|thumb|Suomalaisia [[hiihtojoukot|hiihtäjiä]] rintamalla.]]
'''Talvisota''' oli [[Suomi|Suomen]] ja [[Neuvostoliitto|Neuvostoliiton]] välillä käyty sota,<ref>Lähde {{Kirjaviite|nimeke=X}}</ref>
joka alkoi [[30. marraskuuta]] [[1939]].<!-- piilossa [[Salainen]] --> Sota päättyi [[Moskovan rauhansopimus (1940)#Ehdot|Moskovan rauhaan]].

== Tausta ==
Taustalla oli [[Molotov–Ribbentrop-sopimus]]. [[en:Winter War]] [[Luokka:Talvisota]] [[:Luokka:Sodat]]
"""


class Links(unittest.TestCase):
    def test_finds_article_links_only(self):
        found = set(w.find_links(SAMPLE))
        self.assertEqual(found, {"Suomi", "Hiihtojoukot", "Neuvostoliitto", "30. marraskuuta", "1939",
                                 "Salainen", "Moskovan rauhansopimus (1940)", "Molotov–Ribbentrop-sopimus"})

    def test_link_set_agrees_with_find_links(self):
        self.assertEqual(w.link_set(SAMPLE), frozenset(w.find_links(SAMPLE)))

    def test_normalize(self):
        self.assertEqual(w.normalize_title("suomen_sisällissota "), "Suomen sisällissota")
        self.assertEqual(w.normalize_title(":Talvisota"), "Talvisota")
        self.assertEqual(w.normalize_title("Star Wars: Episodi IV"), "Star Wars: Episodi IV")
        for not_article in ("Tiedosto:X.jpg", "en:Winter War", "zh-min-nan:X", "Luokka:Sodat", "wikt:sota", "{{{1}}}"):
            self.assertIsNone(w.normalize_title(not_article), not_article)


class Cleaning(unittest.TestCase):
    def test_lead(self):
        lead = w.lead(w.lead_source(SAMPLE))
        self.assertTrue(lead.startswith("Talvisota oli Suomen ja Neuvostoliiton välillä käyty sota,"))
        self.assertIn("Sota päättyi Moskovan rauhaan.", lead)
        for gone in ("{{", "[[", "<ref", "Lähde", "piilossa", "Tausta", "hiihtäjiä", "thumb"):
            self.assertNotIn(gone, lead)

    def test_lead_of_redirect_is_empty(self):
        self.assertEqual(w.lead(w.lead_source("#OHJAUS [[Talvisota]]")), "")

    def test_lead_is_capped(self):
        self.assertLessEqual(len(w.lead("Sana. " * 2000)), w.LEAD_MAX)

    def test_nested_templates(self):
        self.assertEqual(w.clean("a {{x|{{y|{{z}}}}|w}} b").split(), ["a", "b"])


class Context(unittest.TestCase):
    def test_marks_the_link(self):
        c = w.context(SAMPLE, w.find_links(SAMPLE)["Neuvostoliitto"])
        self.assertIn("⟦Neuvostoliiton⟧", c)
        self.assertIn("Talvisota oli Suomen ja", c)
        self.assertNotIn("[[", c)
        self.assertFalse(c.startswith("⌂"))

    def test_link_inside_template_is_flagged(self):
        c = w.context(SAMPLE, w.find_links(SAMPLE)["Suomi"])
        self.assertTrue(c.startswith("⌂ "), c)
        self.assertIn("⟦Suomi⟧", c)

    def test_no_template_debris(self):
        text = "| a = 1\n}}\nKaupunki sijaitsee [[Karjala]]ssa lähellä rajaa.{{Viite|x"
        c = w.context(text, w.find_links(text)["Karjala"])
        self.assertEqual(c, "Kaupunki sijaitsee ⟦Karjala⟧ssa lähellä rajaa.")


if __name__ == "__main__":
    unittest.main()
