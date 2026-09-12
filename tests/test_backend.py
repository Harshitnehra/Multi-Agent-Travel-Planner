import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage

from backend import final_agent, itinerary_agent, truncate_for_prompt


def make_state(**overrides):
    state = {
        "messages": [],
        "user_query": "Tokyo trip",
        "flight_results": "Flight data",
        "hotel_results": "Hotel data",
        "itinerary": "Complete plan",
        "llm_calls": 0,
    }
    state.update(overrides)
    return state


class BackendTokenBudgetTests(unittest.TestCase):
    def test_truncate_for_prompt_caps_large_values(self):
        result = truncate_for_prompt("word " * 5000, 1000)

        self.assertLessEqual(len(result), 1031)
        self.assertIn("Additional results omitted", result)

    @patch("backend.get_llm")
    def test_itinerary_uses_one_bounded_llm_request(self, get_llm):
        get_llm.return_value.invoke.return_value = AIMessage(content="Generated plan")
        state = make_state(
            user_query="Q" * 5000,
            flight_results="F" * 20000,
            hotel_results="H" * 20000,
        )

        result = itinerary_agent(state)
        prompt = get_llm.return_value.invoke.call_args.args[0][1].content

        self.assertEqual(get_llm.return_value.invoke.call_count, 1)
        self.assertLess(len(prompt), 12000)
        self.assertEqual(result["llm_calls"], 1)

    @patch("backend.get_llm")
    def test_final_agent_does_not_make_a_second_llm_request(self, get_llm):
        result = final_agent(make_state(llm_calls=1))

        get_llm.assert_not_called()
        self.assertEqual(result["messages"][0].content, "Complete plan")
        self.assertEqual(result["llm_calls"], 1)


if __name__ == "__main__":
    unittest.main()
