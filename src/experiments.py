"""
Health Experiment module — Phase 2 core feature.

Design contract
───────────────
• All output uses correlation language, never causation.
  Wrong:  "散步导致血糖降低"
  Right:  "执行实验的5天里，血糖均值比未执行日低 17 mg/dL"

• SeekDB delivers two layers of comparison in one pipeline:
    SQL layer   → avg risk_score / glucose per executed/skipped day
    Vector layer → cosine distance between embedding centroids of the two groups
                   (quantifies "did your subjective experience actually differ?")

• Minimum evidence threshold: 3 execution days + 2 skip days before any conclusion
  is shown. Below that, only a progress summary is returned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .db import get_connection
from .baseline import cosine_distance


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class Experiment:
    id: int
    name: str
    variable: str
    hypothesis: str
    status: str        # "active" | "completed" | "abandoned"
    start_date: str
    target_days: int
    days_logged: int
    created_at: str

    @property
    def progress_pct(self) -> int:
        return min(100, int(self.days_logged / max(self.target_days, 1) * 100))


@dataclass
class DayLog:
    log_date: str
    executed: bool
    note: str


@dataclass
class ExperimentResult:
    experiment: Experiment
    executed_days: int
    skipped_days: int
    avg_risk_executed: float
    avg_risk_skipped: float
    avg_glucose_executed: Optional[float]
    avg_glucose_skipped: Optional[float]
    semantic_distance: Optional[float]   # cosine dist between embedding centroids
    day_logs: list[DayLog]
    conclusion: str
    is_significant: bool                 # enough data for meaningful comparison


# ── CRUD ────────────────────────────────────────────────────────────────────

def create_experiment(
    name: str,
    variable: str,
    hypothesis: str,
    target_days: int = 7,
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO experiments
            (name, variable, hypothesis, status, start_date, target_days)
        VALUES (%s, %s, %s, 'active', %s, %s)
        """,
        (name, variable, hypothesis, date.today().isoformat(), target_days),
    )
    conn.commit()
    exp_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return exp_id


def get_active_experiments() -> list[Experiment]:
    return _fetch_experiments(status_filter="active")


def get_all_experiments() -> list[Experiment]:
    return _fetch_experiments(status_filter=None)


def _fetch_experiments(status_filter: Optional[str]) -> list[Experiment]:
    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        SELECT e.id, e.name, e.variable, e.hypothesis, e.status,
               e.start_date, e.target_days, e.created_at,
               COUNT(el.id) AS days_logged
        FROM experiments e
        LEFT JOIN experiment_logs el ON e.id = el.experiment_id
    """
    params: tuple = ()
    if status_filter:
        sql += " WHERE e.status = %s"
        params = (status_filter,)
    sql += " GROUP BY e.id ORDER BY e.created_at DESC"

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return [
        Experiment(
            id=int(r[0]), name=r[1], variable=r[2] or "",
            hypothesis=r[3] or "", status=r[4],
            start_date=str(r[5]), target_days=int(r[6]),
            created_at=str(r[7]), days_logged=int(r[8]),
        )
        for r in rows
    ]


def log_day(experiment_id: int, executed: bool, note: str = "") -> None:
    """
    Record today's execution status for an experiment.
    • Upserts (one log per experiment per day).
    • Auto-completes the experiment when target_days is reached.
    • Links to today's user_diaries entry if one exists.
    """
    conn = get_connection()
    cursor = conn.cursor()

    today = date.today().isoformat()

    # Find today's diary entry (most recent)
    cursor.execute(
        "SELECT id FROM user_diaries WHERE diary_date = %s ORDER BY id DESC LIMIT 1",
        (today,),
    )
    row = cursor.fetchone()
    diary_id = row[0] if row else None

    # Upsert
    cursor.execute(
        """
        INSERT INTO experiment_logs
            (experiment_id, log_date, executed, diary_id, note)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            executed = VALUES(executed),
            diary_id = VALUES(diary_id),
            note     = VALUES(note)
        """,
        (experiment_id, today, int(executed), diary_id, note),
    )

    # Auto-complete if target reached
    cursor.execute(
        "SELECT target_days FROM experiments WHERE id = %s", (experiment_id,)
    )
    target = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COUNT(*) FROM experiment_logs WHERE experiment_id = %s",
        (experiment_id,),
    )
    if cursor.fetchone()[0] >= target:
        cursor.execute(
            "UPDATE experiments SET status = 'completed' WHERE id = %s",
            (experiment_id,),
        )

    conn.commit()
    cursor.close()
    conn.close()


def abandon_experiment(experiment_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE experiments SET status = 'abandoned' WHERE id = %s",
        (experiment_id,),
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_day_logs(experiment_id: int) -> list[DayLog]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT log_date, executed, note FROM experiment_logs "
        "WHERE experiment_id = %s ORDER BY log_date",
        (experiment_id,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [DayLog(log_date=str(r[0]), executed=bool(r[1]), note=r[2] or "") for r in rows]


# ── Analysis ────────────────────────────────────────────────────────────────

def analyze_experiment(experiment_id: int) -> Optional[ExperimentResult]:
    """
    Two-layer analysis using SeekDB data:

    SQL layer:   JOIN experiment_logs → user_diaries
                 → compare avg risk_score and avg glucose_level
                   between executed (1) and skipped (0) days

    Vector layer: load diary_embedding for each day
                  → compute centroid per group
                  → cosine_distance(exec_centroid, skip_centroid)
                  → "did your subjective experience actually differ?"
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Experiment meta
    cursor.execute(
        "SELECT id, name, variable, hypothesis, status, "
        "start_date, target_days, created_at FROM experiments WHERE id = %s",
        (experiment_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    cursor.execute(
        "SELECT COUNT(*) FROM experiment_logs WHERE experiment_id = %s",
        (experiment_id,),
    )
    days_logged = cursor.fetchone()[0]

    exp = Experiment(
        id=int(row[0]), name=row[1], variable=row[2] or "",
        hypothesis=row[3] or "", status=row[4],
        start_date=str(row[5]), target_days=int(row[6]),
        created_at=str(row[7]), days_logged=days_logged,
    )

    # Join with user_diaries for metrics + embeddings
    cursor.execute(
        """
        SELECT el.executed, el.log_date,
               ud.risk_score, ud.glucose_level, ud.diary_embedding
        FROM experiment_logs el
        LEFT JOIN user_diaries ud ON el.diary_id = ud.id
        WHERE el.experiment_id = %s
        ORDER BY el.log_date
        """,
        (experiment_id,),
    )
    logs = cursor.fetchall()
    cursor.close()
    conn.close()

    day_logs = get_day_logs(experiment_id)

    if len(logs) < 2:
        return ExperimentResult(
            experiment=exp,
            executed_days=0, skipped_days=0,
            avg_risk_executed=0.0, avg_risk_skipped=0.0,
            avg_glucose_executed=None, avg_glucose_skipped=None,
            semantic_distance=None, day_logs=day_logs,
            conclusion="数据不足，需至少 2 天记录才能开始分析。",
            is_significant=False,
        )

    # Split into two groups
    exec_risks, skip_risks = [], []
    exec_glucose, skip_glucose = [], []
    exec_embs, skip_embs = [], []

    for executed, log_date, risk_score, glucose, embedding in logs:
        bucket_risks   = exec_risks   if executed else skip_risks
        bucket_glucose = exec_glucose if executed else skip_glucose
        bucket_embs    = exec_embs    if executed else skip_embs

        if risk_score is not None:
            bucket_risks.append(float(risk_score))
        if glucose is not None:
            bucket_glucose.append(float(glucose))
        if embedding:
            bucket_embs.append(json.loads(embedding))

    n_exec = sum(1 for r in logs if r[0])
    n_skip = len(logs) - n_exec

    avg_r_exec = sum(exec_risks)   / len(exec_risks)   if exec_risks   else 0.0
    avg_r_skip = sum(skip_risks)   / len(skip_risks)   if skip_risks   else 0.0
    avg_g_exec = sum(exec_glucose) / len(exec_glucose) if exec_glucose else None
    avg_g_skip = sum(skip_glucose) / len(skip_glucose) if skip_glucose else None

    # Vector-layer: centroid cosine distance
    sem_dist: Optional[float] = None
    if exec_embs and skip_embs:
        dim = len(exec_embs[0])
        ne, ns = len(exec_embs), len(skip_embs)
        exec_c = [sum(exec_embs[i][d] for i in range(ne)) / ne for d in range(dim)]
        skip_c = [sum(skip_embs[i][d] for i in range(ns)) / ns for d in range(dim)]
        sem_dist = cosine_distance(exec_c, skip_c)

    is_significant = n_exec >= 3 and n_skip >= 2

    conclusion = _build_conclusion(
        exp, n_exec, n_skip, avg_r_exec, avg_r_skip,
        avg_g_exec, avg_g_skip, sem_dist, is_significant,
    )

    return ExperimentResult(
        experiment=exp,
        executed_days=n_exec, skipped_days=n_skip,
        avg_risk_executed=avg_r_exec, avg_risk_skipped=avg_r_skip,
        avg_glucose_executed=avg_g_exec, avg_glucose_skipped=avg_g_skip,
        semantic_distance=sem_dist, day_logs=day_logs,
        conclusion=conclusion, is_significant=is_significant,
    )


def _build_conclusion(
    exp: Experiment,
    n_exec: int, n_skip: int,
    avg_r_exec: float, avg_r_skip: float,
    avg_g_exec: Optional[float], avg_g_skip: Optional[float],
    sem_dist: Optional[float],
    is_significant: bool,
) -> str:
    if not is_significant:
        return (
            f"实验进行中（已记录 {n_exec + n_skip} 天，"
            f"执行 {n_exec} 天 / 跳过 {n_skip} 天）。\n"
            "需至少 3 天执行 + 2 天对照才能得出有意义的对比结论，继续坚持！"
        )

    parts: list[str] = [f"**「{exp.name}」实验结果**\n"]

    # Risk comparison
    r_diff = avg_r_skip - avg_r_exec
    if abs(r_diff) > 5:
        direction = "低" if r_diff > 0 else "高"
        parts.append(
            f"✅ **风险评分**：执行日（{avg_r_exec:.0f}分）比未执行日"
            f"（{avg_r_skip:.0f}分）{direction} {abs(r_diff):.0f} 分。"
        )
    else:
        parts.append(
            f"📊 **风险评分**：执行日（{avg_r_exec:.0f}分）与未执行日"
            f"（{avg_r_skip:.0f}分）差异不显著。"
        )

    # Glucose comparison
    if avg_g_exec is not None and avg_g_skip is not None:
        g_diff = avg_g_skip - avg_g_exec
        g_dir = "低" if g_diff > 0 else "高"
        parts.append(
            f"🩸 **血糖均值**：执行日 {avg_g_exec:.0f} mg/dL vs "
            f"未执行日 {avg_g_skip:.0f} mg/dL（{g_dir} {abs(g_diff):.0f} mg/dL）。"
        )

    # Semantic layer
    if sem_dist is not None:
        if sem_dist > 0.15:
            parts.append(
                "💬 **感受对比**：执行实验的日子与未执行的日子，"
                "你的整体描述有明显不同——说明两种状态下你的感受确实存在差异。"
            )
        elif sem_dist > 0.05:
            parts.append(
                "💬 **感受对比**：执行日与未执行日的描述有一定区别，"
                "主观感受有所不同。"
            )
        else:
            parts.append(
                "💬 **感受对比**：执行日与未执行日的描述非常接近，"
                "主观感受上的差异不太明显。"
            )

    parts.append(
        "\n*⚠️ 以上为相关性分析，基于有限样本，不代表因果关系，"
        "请以专业医生建议为最终参考。*"
    )

    return "\n".join(parts)
