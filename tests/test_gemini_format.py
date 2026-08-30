"""The one OpenAI-messages -> Gemini-contents conversion both Gemini wires share."""
import json
import unittest

from arity.gemini_format import parse_parts, to_contents, tool_declarations, usage_from


class TestGeminiFormat(unittest.TestCase):
    def test_tool_call_and_result_round_trip_with_ids_and_signatures(self):
        parts = [{"functionCall": {"id": "toolu_1", "name": "write_file", "args": {"path": "a.py"}}, "thoughtSignature": "sig"}]
        text, calls = parse_parts(parts)
        self.assertIsNone(text)
        self.assertEqual(calls[0]["id"], "toolu_1")
        self.assertEqual(calls[0]["thought_signature"], "sig")
        msgs = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": None, "tool_calls": calls},
            {"role": "tool", "tool_call_id": "toolu_1", "content": "ok"},
        ]
        contents, system_text = to_contents(msgs)
        self.assertEqual(system_text, "SYS")
        self.assertEqual([c["role"] for c in contents], ["user", "model", "user"])
        fc = contents[1]["parts"][0]
        self.assertEqual(fc["functionCall"]["id"], "toolu_1")
        self.assertEqual(fc["thoughtSignature"], "sig")
        self.assertEqual(fc["functionCall"]["args"], {"path": "a.py"})
        fr = contents[2]["parts"][0]["functionResponse"]
        self.assertEqual((fr["id"], fr["name"], fr["response"]["output"]), ("toolu_1", "write_file", "ok"))

    def test_never_ends_with_a_model_turn_and_never_sends_empty_text(self):
        contents, _ = to_contents([{"role": "user", "content": "hi"}, {"role": "assistant", "content": ""}])
        self.assertEqual([c["role"] for c in contents], ["user"])
        contents, _ = to_contents([{"role": "assistant", "content": None}])
        self.assertEqual(contents[-1]["role"], "user")  # a placeholder user turn, not an empty model turn
        for c in contents:
            for p in c["parts"]:
                self.assertNotEqual(p.get("text"), "")

    def test_declarations_and_usage(self):
        decls = tool_declarations([{"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object"}}}, {"function": {}}])
        self.assertEqual([d["name"] for d in decls], ["f"])
        u = usage_from({"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 7})
        self.assertEqual((u["prompt_tokens"], u["completion_tokens"], u["thought_tokens"]), (10, 12, 7))


if __name__ == "__main__":
    unittest.main()
