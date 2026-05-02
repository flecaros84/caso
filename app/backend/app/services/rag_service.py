from __future__ import annotations

import re
from dataclasses import dataclass
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover
    SentenceTransformer = None


@dataclass
class SearchResult:
    text: str
    source: str
    score: float


class SimpleRAGIndex:
    def __init__(self, candidate_id: str, candidate_name: str, text: str) -> None:
        self.candidate_id = candidate_id
        self.candidate_name = candidate_name
        self.chunks = self._chunk_text(text)
        self.embedding_model = None
        self.chunk_embeddings = None
        self.vectorizer = None
        self.tfidf_matrix = None
        self._build_index()

    def search(self, query: str, top_k: int = 4) -> list[SearchResult]:
        if not self.chunks:
            return []

        if self.embedding_model is not None and self.chunk_embeddings is not None:
            query_embedding = self.embedding_model.encode([query], normalize_embeddings=True)
            scores = np.dot(self.chunk_embeddings, query_embedding[0])
        else:
            query_vector = self.vectorizer.transform([query])
            scores = cosine_similarity(self.tfidf_matrix, query_vector).ravel()

        top_indexes = scores.argsort()[::-1][:top_k]
        return [
            SearchResult(
                text=self.chunks[i],
                source=self.candidate_name,
                score=float(max(scores[i], 0)),
            )
            for i in top_indexes
            if scores[i] > 0
        ]

    def _build_index(self) -> None:
        if SentenceTransformer is not None:
            try:
                self.embedding_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
                self.chunk_embeddings = self.embedding_model.encode(self.chunks, normalize_embeddings=True)
                return
            except Exception:
                self.embedding_model = None
                self.chunk_embeddings = None

        self.vectorizer = TfidfVectorizer(stop_words=None, ngram_range=(1, 2))
        self.tfidf_matrix = self.vectorizer.fit_transform(self.chunks)

    def _chunk_text(self, text: str, chunk_size: int = 850, overlap: int = 120) -> list[str]:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end == len(text):
                break
            start = max(end - overlap, start + 1)
        return chunks
