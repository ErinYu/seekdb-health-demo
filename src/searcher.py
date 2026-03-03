"""
Hybrid search engine — the core SeekDB showcase.

SeekDB's DBMS_HYBRID_SEARCH.SEARCH combines in one query:
  • BM25 full-text search  (keyword tokens via IK tokenizer)
  • HNSW cosine vector search  (semantic embedding similarity)

Why hybrid beats either alone in this clinical setting:
  ─────────────────────────────────────────────────────────
  Patient writes: "最近总觉得眼前发花，晚上总是要起来喝水"

  Pure keyword:  misses "多尿"/"口渴" (words not written, but implied)
  Pure vector:   may rank general tiredness diaries equally high
  Hybrid:        catches semantic drift ("眼前发花"≈"视力模糊") AND
                 anchors on specific clinical tokens ("起来喝水"≈"多饮")
"""

import json
from dataclasses import dataclass

from .db import get_connection
from .embedder import embed, vec_sql


@dataclass
class SearchHit:
    patient_id: int
    diary_date: str
    diary_text: str
    glucose_level: float
    is_pre_danger: bool
    days_to_danger: int
    keyword_score: float
    semantic_score: float
    combined_score: float


@dataclass
class RiskAssessment:
    risk_score: float           # 0–100
    risk_level: str             # "low" | "medium" | "high"
    pre_danger_hits: int        # matched historical pre-danger records
    total_hits: int
    top_hits: list[SearchHit]
    # Score breakdowns for visualisation
    keyword_only_pre_danger_ratio: float
    vector_only_pre_danger_ratio: float
    hybrid_pre_danger_ratio: float


def hybrid_search(
    query_text: str,
    k: int = 15,
    boost_keywords: list[str] | None = None,
) -> list[SearchHit]:
    """
    Run DBMS_HYBRID_SEARCH.SEARCH on patient_diaries.
    Returns up to k ranked hits with individual scores.

    boost_keywords: personal trigger symptoms (from user_profile) that get
    a higher BM25 weight (boost=2.5) in the bool.should clause.
    """
    query_vec = embed(query_text)

    should_clauses = [
        {"match": {"diary_text": query_text}},
        {"match": {"symptoms_keywords": query_text}},
    ]

    # Inject personalised keyword boosts
    if boost_keywords:
        for kw in boost_keywords:
            should_clauses.append({
                "match": {"diary_text": {"query": kw, "boost": 2.5}}
            })

    parm = {
        "query": {"bool": {"should": should_clauses}},
        "knn": {
            "field": "diary_embedding",
            "k": k,
            "query_vector": query_vec,
        },
        "_source": [
            "patient_id",
            "diary_date",
            "diary_text",
            "glucose_level",
            "is_pre_danger",
            "days_to_danger",
            "_keyword_score",
            "_semantic_score",
        ],
    }

    conn = get_connection()
    cursor = conn.cursor()

    parm_json = json.dumps(parm, ensure_ascii=False)
    cursor.execute(f"SET @parm = %s", (parm_json,))
    cursor.execute(
        "SELECT DBMS_HYBRID_SEARCH.SEARCH('patient_diaries', @parm)"
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row or not row[0]:
        return []

    raw = json.loads(row[0])
    hits_raw = raw if isinstance(raw, list) else raw.get("hits", [])

    hits: list[SearchHit] = []
    for h in hits_raw:
        kw = float(h.get("_keyword_score") or 0)
        sem = float(h.get("_semantic_score") or 0)
        hits.append(
            SearchHit(
                patient_id=int(h.get("patient_id", 0)),
                diary_date=str(h.get("diary_date", "")),
                diary_text=str(h.get("diary_text", "")),
                glucose_level=float(h.get("glucose_level") or 0),
                is_pre_danger=bool(int(h.get("is_pre_danger", 0))),
                days_to_danger=int(h.get("days_to_danger", -1)),
                keyword_score=kw,
                semantic_score=sem,
                combined_score=kw * 0.4 + sem * 0.6,
            )
        )

    return hits


def _vector_only_search(query_text: str, k: int = 15) -> list[dict]:
    """Pure vector search via approximated ORDER BY vector distance."""
    query_vec = vec_sql(embed(query_text))

    conn = get_connection()
    cursor = conn.cursor()
    sql = f"""
        SELECT patient_id, diary_text, glucose_level, is_pre_danger, days_to_danger,
               COSINE_DISTANCE(diary_embedding, '{query_vec}') AS dist
        FROM patient_diaries
        ORDER BY dist ASC
        LIMIT {k}
    """
    cursor.execute(sql)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return [
        {
            "patient_id": r[0],
            "diary_text": r[1],
            "glucose_level": r[2],
            "is_pre_danger": bool(r[3]),
            "days_to_danger": r[4],
        }
        for r in rows
    ]


def _keyword_only_search(query_text: str, k: int = 15) -> list[dict]:
    """Pure full-text (BM25) search via combined FULLTEXT index."""
    conn = get_connection()
    cursor = conn.cursor()
    # idx_combined_fts covers (diary_text, symptoms_keywords) in one index
    sql = f"""
        SELECT patient_id, diary_text, glucose_level, is_pre_danger, days_to_danger,
               MATCH(diary_text, symptoms_keywords) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score
        FROM patient_diaries
        WHERE MATCH(diary_text, symptoms_keywords) AGAINST (%s IN NATURAL LANGUAGE MODE)
        ORDER BY score DESC
        LIMIT {k}
    """
    cursor.execute(sql, (query_text, query_text))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return [
        {
            "patient_id": r[0],
            "diary_text": r[1],
            "glucose_level": r[2],
            "is_pre_danger": bool(r[3]),
            "days_to_danger": r[4],
        }
        for r in rows
    ]


def assess_risk(
    query_text: str,
    k: int = 15,
    boost_keywords: list[str] | None = None,
) -> RiskAssessment:
    """
    Full risk assessment:
      1. Hybrid search (main result, with optional personal keyword boosts)
      2. Vector-only & keyword-only (for comparison visualisation)
      3. Compute risk score from % of pre-danger hits in hybrid results
    """
    hybrid_hits = hybrid_search(query_text, k=k, boost_keywords=boost_keywords)

    # ── Comparison searches (best-effort; fall back gracefully) ─────────────
    try:
        vec_hits = _vector_only_search(query_text, k=k)
        vec_pre_ratio = (
            sum(1 for h in vec_hits if h["is_pre_danger"]) / len(vec_hits)
            if vec_hits else 0.0
        )
    except Exception:
        vec_pre_ratio = 0.0

    try:
        kw_hits = _keyword_only_search(query_text, k=k)
        kw_pre_ratio = (
            sum(1 for h in kw_hits if h["is_pre_danger"]) / len(kw_hits)
            if kw_hits else 0.0
        )
    except Exception:
        kw_pre_ratio = 0.0

    # ── Risk score from hybrid hits ─────────────────────────────────────────
    total = len(hybrid_hits)
    pre_danger_hits = [h for h in hybrid_hits if h.is_pre_danger]
    n_pre = len(pre_danger_hits)

    if total == 0:
        raw_ratio = 0.0
    else:
        # Weight each hit by its combined score
        weighted_pre = sum(h.combined_score for h in hybrid_hits if h.is_pre_danger)
        weighted_total = sum(h.combined_score for h in hybrid_hits) or 1e-9
        raw_ratio = weighted_pre / weighted_total

    risk_score = round(raw_ratio * 100, 1)

    if risk_score < 30:
        level = "low"
    elif risk_score < 60:
        level = "medium"
    else:
        level = "high"

    hybrid_ratio = n_pre / total if total else 0.0

    return RiskAssessment(
        risk_score=risk_score,
        risk_level=level,
        pre_danger_hits=n_pre,
        total_hits=total,
        top_hits=hybrid_hits[:10],
        keyword_only_pre_danger_ratio=kw_pre_ratio,
        vector_only_pre_danger_ratio=vec_pre_ratio,
        hybrid_pre_danger_ratio=hybrid_ratio,
    )
