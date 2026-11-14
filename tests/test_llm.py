"""The language model side: what is shown of a streamed answer, and how the app copes with
endpoints that differ. Needs no database and no real model: the endpoint is tests/fake_models.py,
started here on a free port.

    python -m unittest tests.test_llm
"""
import logging
import os
import sys
import threading
import unittest

from werkzeug.serving import make_server

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_models  # noqa: E402
from rabbithole import config, llm  # noqa: E402
from rabbithole.llm import without_thinking  # noqa: E402

logging.getLogger("werkzeug").setLevel(logging.ERROR)       # the stand-in endpoint's request log


def said(*pieces):
    return "".join(without_thinking(pieces))


class Thinking(unittest.TestCase):
    def test_plain_answer_passes_untouched(self):
        self.assertEqual(said("Talvi", "sota alkoi ", "1939 [1]."), "Talvisota alkoi 1939 [1].")

    def test_think_block_is_dropped(self):
        self.assertEqual(said("<think>The user asks about", " a war.</think>\n\nIt began in 1939."), "It began in 1939.")

    def test_marks_cut_across_pieces(self):
        self.assertEqual(said("<th", "ink>hm", "m</th", "ink>", "Yes", "."), "Yes.")

    def test_gemma_empty_thought_block(self):
        self.assertEqual(said("<|channel>thought\n<channel|>", "The link was removed."), "The link was removed.")

    def test_answer_that_begins_with_an_angle_bracket(self):
        self.assertEqual(said("<", "1 % of links"), "<1 % of links")

    def test_later_marks_are_left_alone(self):
        self.assertEqual(said("In HTML, ", "<think> is not a tag."), "In HTML, <think> is not a tag.")

    def test_stream_that_ends_inside_thinking_says_nothing(self):
        self.assertEqual(said("<think>still going"), "")

    def test_empty_stream(self):
        self.assertEqual(said(), "")


class Endpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0, fake_models.app, threaded=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.saved = (config.LLM_BASE_URL, config.LLM_MODEL)
        config.LLM_BASE_URL, config.LLM_MODEL = f"http://127.0.0.1:{cls.server.server_port}/v1", ""

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        config.LLM_BASE_URL, config.LLM_MODEL = cls.saved
        llm._seen.clear()

    def setUp(self):
        llm._seen.clear()
        fake_models.STRICT = fake_models.THINKS = False

    def answer(self):
        return "".join(llm.chat("You answer from the sources.", "What happened?"))

    def test_picks_the_gemma_model_and_not_the_embedding_model(self):
        self.assertEqual(llm.model(), "gemma-test")

    def test_answer_streams_through(self):
        self.assertTrue(self.answer().startswith("This is a canned test answer"))

    def test_endpoint_that_refuses_an_unknown_setting(self):
        fake_models.STRICT = True
        self.assertTrue(self.answer().startswith("This is a canned test answer"))
        self.assertEqual(llm._seen.get("plain"), "gemma-test")       # and it does not ask that way again
        self.assertTrue(self.answer().startswith("This is a canned test answer"))

    def test_thinking_in_the_text_is_not_shown(self):
        fake_models.THINKS = True
        text = self.answer()
        self.assertNotIn("think", text)
        self.assertTrue(text.startswith("This is a canned test answer"))

    def test_no_endpoint_says_so_instead_of_failing(self):
        config.LLM_BASE_URL = "http://127.0.0.1:9/v1"
        try:
            self.assertTrue(self.answer().startswith("⚠ No language model"))
            config.LLM_MODEL = "some-model"
            self.assertTrue(self.answer().startswith("⚠ The language model could not be reached"))
        finally:
            config.LLM_BASE_URL, config.LLM_MODEL = f"http://127.0.0.1:{self.server.server_port}/v1", ""


if __name__ == "__main__":
    unittest.main()
