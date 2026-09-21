from __future__ import annotations

import unittest

from app.chunking import chunk_parts
from app.config import Settings
from app.document_loader import DocumentPart
from app.rag_service import RAGService, SYSTEM_PROMPT, is_realtime_question
from app.vector_store import SearchResult, cosine_similarity, lexical_score, normalize


class FakeChatClient:
    def __init__(self) -> None:
        self.calls = []

    def chat(self, *args):
        self.calls.append(args)
        return "依据证据作答 [S1]"


class FakeStore:
    def __init__(self, results, chunk_count=1) -> None:
        self.results = results
        self.chunk_count = chunk_count

    def search(self, question, top_k):
        return self.results[:top_k]


class ChunkingTests(unittest.TestCase):
    def test_chunking_keeps_source_and_size(self) -> None:
        parts = [DocumentPart("资料/标准.docx", "正文", "航道标准。" * 100)]
        chunks = chunk_parts(parts, chunk_size=120, overlap=20)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.source == "资料/标准.docx" for chunk in chunks))
        self.assertTrue(all(len(chunk.text) <= 120 for chunk in chunks))

    def test_chunk_ids_are_stable(self) -> None:
        parts = [DocumentPart("a.txt", "正文", "相同的内容")]
        self.assertEqual(chunk_parts(parts)[0].chunk_id, chunk_parts(parts)[0].chunk_id)

    def test_tabular_chunks_repeat_header_and_keep_rows_intact(self) -> None:
        rows = ["序号 | 名称"] + [f"{number} | 第{number}份航道资料" for number in range(1, 67)]
        part = DocumentPart("目录.xlsx", "工作表：文件清单", "\n".join(rows))

        chunks = chunk_parts([part], chunk_size=240, overlap=20)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.text.startswith("序号 | 名称\n") for chunk in chunks))
        self.assertEqual(sum("61 | 第61份航道资料" in chunk.text for chunk in chunks), 1)
        self.assertTrue(all(len(chunk.text) <= 240 for chunk in chunks))


class RetrievalTests(unittest.TestCase):
    def test_normalized_cosine_similarity(self) -> None:
        left = normalize([3.0, 4.0])
        self.assertAlmostEqual(cosine_similarity(left, left), 1.0)
        self.assertAlmostEqual(cosine_similarity(left, normalize([-4.0, 3.0])), 0.0)

    def test_chinese_lexical_overlap(self) -> None:
        related = lexical_score("航道水深标准", "本文件规定航道维护水深标准")
        unrelated = lexical_score("航道水深标准", "企业税务办理指南")
        self.assertGreater(related, unrelated)


class BoundaryTests(unittest.TestCase):
    def test_realtime_question_is_blocked(self) -> None:
        self.assertTrue(is_realtime_question("今天这个航段的实时水深是多少？"))
        self.assertTrue(is_realtime_question("目前审批进度到哪一步了？"))

    def test_historical_or_policy_question_is_not_blocked(self) -> None:
        self.assertFalse(is_realtime_question("2024 年航道水深标准如何规定？"))
        self.assertFalse(is_realtime_question("为什么实时水深必须接权威接口？"))

    def test_live_api_value_is_still_blocked(self) -> None:
        self.assertTrue(is_realtime_question("当前水深接口返回的数据是多少？"))


class RAGServiceTests(unittest.TestCase):
    def make_service(self, results, *, chunk_count=1, min_score=0.45):
        client = FakeChatClient()
        settings = Settings(rag_min_score=min_score)
        store = FakeStore(results, chunk_count=chunk_count)
        return RAGService(settings, client, store), client

    def test_low_score_results_are_rejected_without_calling_chat_model(self) -> None:
        result = SearchResult("c1", "资料.md", "正文", "无关内容", 0.44)
        service, client = self.make_service([result])

        response = service.answer("管理员手机号是什么？")

        self.assertIn("现有知识库无法确认", response["answer"])
        self.assertEqual(response["sources"], [])
        self.assertEqual(client.calls, [])

    def test_only_results_meeting_threshold_are_sent_to_chat_model(self) -> None:
        relevant = SearchResult("c1", "资料.md", "正文", "维护水深为 3.5 米", 0.7)
        weak = SearchResult("c2", "目录.md", "正文", "文件目录", 0.3)
        service, client = self.make_service([relevant, weak])

        response = service.answer("维护水深是多少？")

        self.assertEqual([source["chunk_id"] for source in response["sources"]], ["c1"])
        self.assertEqual(len(client.calls), 1)
        self.assertIn("维护水深为 3.5 米", client.calls[0][2])
        self.assertNotIn("文件目录", client.calls[0][2])

    def test_empty_index_keeps_setup_guidance(self) -> None:
        service, client = self.make_service([], chunk_count=0)

        response = service.answer("任意问题")

        self.assertIn("请先运行文档导入命令", response["answer"])
        self.assertEqual(client.calls, [])

    def test_prompt_forbids_unsupported_general_advice(self) -> None:
        self.assertIn("不得补充证据未直接支持", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
