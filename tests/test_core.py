from __future__ import annotations

import unittest

from app.chunking import chunk_parts
from app.document_loader import DocumentPart
from app.rag_service import is_realtime_question
from app.vector_store import cosine_similarity, lexical_score, normalize


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


if __name__ == "__main__":
    unittest.main()
