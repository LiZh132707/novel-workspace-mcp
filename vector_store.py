"""混合检索：语义向量可用时使用 Chroma，否则降级到本地全文块检索。"""
import re
import sqlite3
import uuid
from contextlib import closing

import threading
import chromadb
from chromadb.config import Settings

from config import NOVELS_ROOT, STORAGE_ROOT


class VectorStore:
    """小说语义搜索的向量存储。

    使用 LM Studio 的 text-embedding-nomic-embed-text-v1.5 生成嵌入。
    ChromaDB 持久化到 storage/vector_db/。
    """

    def __init__(self, logger, embed_func, collection_name: str = "novel_memory"):
        self.logger = logger
        self._lock = threading.Lock()
        self.embed_func = embed_func  # LMStudioClient.embed 或 embed_batch
        self.db_path = str(STORAGE_ROOT / "vector_db")
        self.lexical_path = STORAGE_ROOT / "lexical_index.db"
        self._semantic_disabled = False
        self._client = chromadb.PersistentClient(
            path=self.db_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._initialize_lexical()
        self._bootstrap_lexical()
        logger.info("向量存储初始化完成: %s", self.db_path)

    def _connect_lexical(self):
        connection = sqlite3.connect(self.lexical_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize_lexical(self):
        self.lexical_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect_lexical()) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, novel TEXT NOT NULL, chapter INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL, start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL, text TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_novel_chapter ON chunks(novel, chapter);
            """)

    def _bootstrap_lexical(self):
        """旧项目可能从未成功嵌入；首次初始化时补建无需模型的本地索引。"""
        try:
            with closing(self._connect_lexical()) as connection:
                existing = {(row["novel"], int(row["chapter"])) for row in connection.execute("SELECT DISTINCT novel,chapter FROM chunks")}
            if not NOVELS_ROOT.exists():
                return
            actual = set()
            for novel_dir in NOVELS_ROOT.iterdir():
                chapters = novel_dir / "chapters"
                if not novel_dir.is_dir() or not chapters.exists():
                    continue
                for path in sorted(chapters.glob("[0-9]*.txt")):
                    try:
                        chapter = int(path.stem)
                        actual.add((novel_dir.name, chapter))
                        if (novel_dir.name, chapter) in existing:
                            continue
                        self._replace_lexical(novel_dir.name, chapter, self.split_text(path.read_text("utf-8", errors="replace")))
                    except (OSError, ValueError):
                        continue
            stale = existing - actual
            if stale:
                with self._lock, closing(self._connect_lexical()) as connection:
                    connection.executemany("DELETE FROM chunks WHERE novel=? AND chapter=?", sorted(stale))
                    connection.commit()
                for novel, chapter in stale:
                    self._delete_semantic_document(novel, chapter)
                self.logger.info("启动索引对账清理 %d 个已不存在章节", len(stale))
        except Exception as exc:
            self.logger.warning("本地全文索引初始化失败: %s", exc)

    @staticmethod
    def split_text(text: str, chunk_size: int = 1400, overlap: int = 180) -> list[dict]:
        """按自然段切分全文，并保留少量重叠避免跨段事实丢失。"""
        clean = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not clean:
            return []
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", clean) if item.strip()]
        chunks = []
        buffer = ""
        start = 0
        cursor = 0
        for paragraph in paragraphs:
            location = clean.find(paragraph, cursor)
            location = cursor if location < 0 else location
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if buffer and len(candidate) > chunk_size:
                chunks.append({"text": buffer, "start": start, "end": start + len(buffer)})
                tail = buffer[-overlap:] if overlap else ""
                buffer = f"{tail}\n\n{paragraph}".strip()
                start = max(0, location - len(tail))
            elif len(paragraph) > chunk_size and not buffer:
                step = max(200, chunk_size - overlap)
                for offset in range(0, len(paragraph), step):
                    piece = paragraph[offset:offset + chunk_size]
                    if piece:
                        chunks.append({"text": piece, "start": location + offset, "end": location + offset + len(piece)})
                buffer = ""
            else:
                if not buffer:
                    start = location
                buffer = candidate
            cursor = location + len(paragraph)
        if buffer:
            chunks.append({"text": buffer, "start": start, "end": start + len(buffer)})
        return chunks

    def add_document(self, novel: str, chapter: int, text: str,
                     metadata: dict = None) -> str:
        """对章节全文分块建索引；重复保存章节时先替换旧索引。"""
        chunks = self.split_text(text)
        if not chunks:
            self.delete_document(novel, chapter)
            return ""
        ids = [f"{novel}_ch{chapter:06d}_{index:04d}_{uuid.uuid4().hex[:6]}" for index in range(len(chunks))]
        self._replace_lexical(novel, chapter, chunks, ids)
        self._delete_semantic_document(novel, chapter)
        if self._semantic_disabled:
            return ids[0]
        try:
            base_meta = {"novel": novel, "chapter": chapter, "type": "chapter", **(metadata or {})}
            documents = [item["text"] for item in chunks]
            owner = getattr(self.embed_func, "__self__", None)
            if owner is not None and hasattr(owner, "embed_batch"):
                embeddings = owner.embed_batch(documents)
            else:
                embeddings = [self.embed_func(document) for document in documents]
            metadatas = [{**base_meta, "chunk_index": index, "start": item["start"], "end": item["end"]}
                         for index, item in enumerate(chunks)]
            with self._lock:
                self._collection.add(
                    ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents,
                )
            self.logger.debug("向量索引添加: %s 第%d章 %d块", novel, chapter, len(chunks))
            return ids[0]
        except Exception as e:
            if "501" in str(e) or "Not Implemented" in str(e) or "没有可用嵌入接口" in str(e):
                self._semantic_disabled = True
                self.logger.warning("嵌入接口当前不可用，已自动切换本地全文检索")
            else:
                self.logger.warning("语义向量索引失败，仍保留本地全文索引: %s", e)
            return ids[0]

    def _replace_lexical(self, novel: str, chapter: int, chunks: list[dict], ids: list[str] | None = None):
        ids = ids or [f"lex_{novel}_ch{chapter:06d}_{index:04d}" for index in range(len(chunks))]
        with self._lock, closing(self._connect_lexical()) as connection:
            connection.execute("DELETE FROM chunks WHERE novel=? AND chapter=?", (novel, chapter))
            connection.executemany(
                "INSERT INTO chunks(id,novel,chapter,chunk_index,start_offset,end_offset,text) VALUES(?,?,?,?,?,?,?)",
                [(ids[index], novel, chapter, index, item["start"], item["end"], item["text"]) for index, item in enumerate(chunks)],
            )
            connection.commit()

    def delete_document(self, novel: str, chapter: int):
        """删除章节的本地与语义索引，不需要调用嵌入模型。"""
        with self._lock, closing(self._connect_lexical()) as connection:
            connection.execute("DELETE FROM chunks WHERE novel=? AND chapter=?", (novel, int(chapter)))
            connection.commit()
        try:
            self._delete_semantic_document(novel, int(chapter))
        except Exception as exc:
            self.logger.debug("删除语义章节索引失败: %s", exc)

    def _delete_semantic_document(self, novel: str, chapter: int):
        try:
            with self._lock:
                self._collection.delete(where={"$and": [{"novel": novel}, {"chapter": int(chapter)}]})
        except Exception as exc:
            self.logger.debug("删除旧语义章节索引失败: %s", exc)

    def search(self, query: str, novel: str = None, top_k: int = 5) -> list[dict]:
        """混合搜索；嵌入不可用时仍能返回本地中文块检索结果。"""
        lexical = self._lexical_search(query, novel, max(top_k * 3, top_k))
        semantic = []
        if not self._semantic_disabled:
            try:
                semantic = self._semantic_search(query, novel, max(top_k * 3, top_k))
            except Exception as exc:
                if "501" in str(exc) or "Not Implemented" in str(exc) or "没有可用嵌入接口" in str(exc):
                    self._semantic_disabled = True
                    self.logger.warning("嵌入接口不可用，后续检索使用本地全文索引")
                else:
                    self.logger.warning("语义检索失败，已降级本地全文检索: %s", exc)
        merged = {}
        for item in lexical:
            merged[(item["chapter"], item["chunk_index"])] = item
        for item in semantic:
            key = (item["chapter"], item["chunk_index"])
            if key in merged:
                local = merged[key]
                item["score"] = round(item["score"] * 0.75 + local["score"] * 0.25, 6)
                item["keyword_score"] = local.get("keyword_score", item.get("keyword_score", 0))
                item["reason"] += f"＋本地命中{local['score']:.2f}"
            merged[key] = item
        return sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]

    def _semantic_search(self, query: str, novel: str | None, top_k: int) -> list[dict]:
            query_embedding = self.embed_func(query)
            where = {"novel": novel} if novel else None
            with self._lock:
                available = self._collection.count()
                if available <= 0:
                    return []
                results = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=min(available, max(top_k, top_k * 3)),
                    where=where,
                )
            if not results or not results.get("ids") or not results["ids"][0]:
                return []
            query_terms = self._query_terms(query)
            raw_items = []
            latest_chapter = max((int(item.get("chapter", 0)) for item in results["metadatas"][0]), default=0)
            for i in range(len(results["ids"][0])):
                distance = float(results["distances"][0][i]) if results.get("distances") else 1.0
                metadata = results["metadatas"][0][i]
                document = results["documents"][0][i] or ""
                semantic = max(0.0, min(1.0, 1.0 - distance))
                lexical = sum(1 for term in query_terms if term in document.lower()) / max(1, len(query_terms))
                recency = max(0.0, 1.0 - max(0, latest_chapter - int(metadata.get("chapter", 0))) / 50)
                combined = semantic * 0.78 + lexical * 0.17 + recency * 0.05
                raw_items.append({
                    "id": results["ids"][0][i],
                    "score": round(combined, 6),
                    "semantic_score": round(semantic, 6),
                    "keyword_score": round(lexical, 6),
                    "recency_score": round(recency, 6),
                    "distance": distance,
                    "novel": metadata.get("novel", ""),
                    "chapter": metadata.get("chapter", 0),
                    "chunk_index": metadata.get("chunk_index", 0),
                    "start": metadata.get("start", 0),
                    "end": metadata.get("end", 0),
                    "snippet": document[:700],
                    "reason": f"语义{semantic:.2f}＋关键词{lexical:.2f}＋新近度{recency:.2f}；第{metadata.get('chapter', 0)}章第{int(metadata.get('chunk_index', 0)) + 1}块",
                })
            raw_items.sort(key=lambda item: item["score"], reverse=True)
            return raw_items[:top_k]

    def _lexical_search(self, query: str, novel: str | None, top_k: int) -> list[dict]:
        terms = self._query_terms(query)
        if not terms:
            return []
        with closing(self._connect_lexical()) as connection:
            if novel:
                rows = connection.execute("SELECT * FROM chunks WHERE novel=?", (novel,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM chunks").fetchall()
        latest = max((int(row["chapter"]) for row in rows), default=0)
        items = []
        for row in rows:
            text = row["text"]
            lowered = text.lower()
            matched = [term for term in terms if term in lowered]
            if not matched:
                continue
            coverage = len(matched) / len(terms)
            frequency = min(1.0, sum(lowered.count(term) for term in matched) / max(1, len(terms) * 2))
            recency = max(0.0, 1.0 - max(0, latest - int(row["chapter"])) / 80)
            score = coverage * 0.7 + frequency * 0.2 + recency * 0.1
            items.append({
                "id": row["id"], "score": round(score, 6), "semantic_score": 0.0,
                "keyword_score": round(coverage, 6), "recency_score": round(recency, 6),
                "distance": 1.0, "novel": row["novel"], "chapter": row["chapter"],
                "chunk_index": row["chunk_index"], "start": row["start_offset"], "end": row["end_offset"],
                "snippet": text[:700], "reason": f"本地全文命中：{'、'.join(matched[:6])}；第{row['chapter']}章第{row['chunk_index'] + 1}块",
            })
        return sorted(items, key=lambda item: item["score"], reverse=True)[:top_k]

    def delete_novel(self, novel: str):
        """删除某个小说的所有向量。"""
        try:
            with self._lock:
                self._collection.delete(where={"novel": novel})
                with closing(self._connect_lexical()) as connection:
                    connection.execute("DELETE FROM chunks WHERE novel=?", (novel,))
                    connection.commit()
            self.logger.info("删除向量索引: %s", novel)
        except Exception as e:
            self.logger.warning("删除向量索引失败: %s", e)

    def count(self) -> int:
        with closing(self._connect_lexical()) as connection:
            lexical = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        with self._lock:
            return max(lexical, self._collection.count())

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        text = (query or "").lower()
        words = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text)
        terms = []
        for word in words:
            candidates = [word] if not re.search(r"[\u4e00-\u9fff]", word) or len(word) <= 3 else [word[index:index + 2] for index in range(len(word) - 1)]
            for candidate in candidates:
                if candidate not in terms:
                    terms.append(candidate)
        return terms[:32]

    def evaluate(self, cases: list[dict], novel: str, top_k: int = 5) -> dict:
        """使用人工维护的少量黄金问题评估真实检索命中率。"""
        rows = []
        for case in cases:
            hits = self.search(str(case.get("query", "")), novel, top_k)
            expected = {int(item) for item in case.get("expected_chapters", [])}
            ranks = [index + 1 for index, hit in enumerate(hits) if int(hit.get("chapter", 0)) in expected]
            rows.append({"id": case.get("id", ""), "hit": bool(ranks), "rank": min(ranks) if ranks else None, "hits": hits})
        total = len(rows)
        return {
            "cases": total,
            "hit_at_k": sum(1 for row in rows if row["hit"]) / total if total else 0.0,
            "mrr": sum(1 / row["rank"] for row in rows if row["rank"]) / total if total else 0.0,
            "results": rows,
        }
