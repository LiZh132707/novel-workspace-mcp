"""示例插件：章节统计——自动统计每章字数和 token 消耗。"""
from core.plugin_manager import BasePlugin


class ChapterStatsPlugin(BasePlugin):
    """每章保存后自动统计字数变化并输出报告。"""

    def on_init(self):
        self.logger.info("插件初始化: ChapterStatsPlugin")

    def on_after_chapter_save(self, chapter_number: int, content: str, summary: dict, **kwargs):
        """章节保存后自动统计。"""
        total_chars = len(content)
        chinese_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
        paras = len([p for p in content.split("\n") if p.strip()])
        sents = len([s for s in content.replace("!", "。").replace("?", "。").split("。") if s.strip()])

        stats = {
            "chapter": chapter_number,
            "total_chars": total_chars,
            "chinese_chars": chinese_chars,
            "paragraphs": paras,
            "sentences": sents,
            "avg_sentence_len": round(chinese_chars / sents, 1) if sents else 0,
            "estimated_tokens": int(chinese_chars / 1.8),
        }

        self.logger.info(
            "[ChapterStats] 第%d章 | %d字 | %d段 | %d句 | 平均%.1f字/句 | 约%d tokens",
            stats["chapter"], stats["chinese_chars"], stats["paragraphs"],
            stats["sentences"], stats["avg_sentence_len"], stats["estimated_tokens"],
        )
        return stats
