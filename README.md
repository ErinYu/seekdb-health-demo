# 🩺 SeekDB 慢病早期预警 Agent

基于 **SeekDB**（OceanBase AI-native 数据库）的慢性病健康风险评估 demo，核心亮点是用一次 SQL 调用同时完成 **向量搜索 + 全文检索 + SQL 过滤**。

---

## 核心思路

患者每天写健康日记（自然语言），系统将日记存入 SeekDB，同时建立：

- 📝 **全文索引**（IK 中文分词）— 捕捉精确症状词："口渴"、"头晕"、"夜尿"
- 🧠 **向量索引**（HNSW cosine 384 维）— 语义相似匹配："眼前发花" ≈ "视力模糊"
- 🗂 **结构化字段**（SQL）— 血糖值、时间窗口、危险标签

当用户输入今天的感受，系统在历史记录中寻找**最相似的"危险事件前 30 天"片段**。
如果命中大量历史预警记录，说明当前状态正在重复历史的危险轨迹。

```
用户输入："最近总觉得眼前发花，晚上总是要起来喝水"
          │
          ▼
    SeekDB DBMS_HYBRID_SEARCH.SEARCH
          │
    ┌─────┴──────────────────────────────────┐
    │  BM25 关键词：「喝水」→ 多饮相关记录    │
    │  Vector cosine：「眼前发花」≈ 「视力模糊」│
    │  SQL 过滤：is_pre_danger = 1            │
    └─────────────────────────────────────────┘
          │
    风险评分 + AI 分析报告
```

---

## 环境要求

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | 3.10+ | 运行代码 |
| Docker | 24+ | 运行 SeekDB |
| Docker Compose | v2 | 一键启动 |

---

## 快速启动

```bash
# 1. 克隆仓库
git clone https://github.com/ErinYu/seekdb-health-demo.git
cd seekdb-health-demo

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 配置环境变量（可选：填入 Anthropic API key 以启用 Claude 分析）
cp .env.example .env
# 编辑 .env，按需填入 ANTHROPIC_API_KEY

# 4. 启动 SeekDB
docker-compose up -d
# 等待约 30 秒让 SeekDB 完成初始化

# 5. 初始化数据库（约 3~5 分钟，含模型下载 + embedding）
python scripts/init_db.py

# 6. 启动 Gradio UI
python app.py
# 浏览器打开 http://localhost:7860
```

---

## 项目结构

```
seekdb-health-demo/
├── app.py                    # Gradio UI 入口
├── docker-compose.yml        # SeekDB 容器配置
├── requirements.txt
├── .env.example
├── scripts/
│   └── init_db.py           # 一键初始化数据库
└── src/
    ├── db.py                 # 数据库连接管理
    ├── schema.py             # 建表 DDL（含向量 + 全文索引）
    ├── data_generator.py     # 合成患者数据生成器
    ├── ingest.py             # Embedding + 批量写入 SeekDB
    ├── searcher.py           # 混合搜索 + 风险评分
    └── agent.py              # LLM 风险分析（Claude / 规则兜底）
```

---

## SeekDB 关键技术

### 表结构（混合索引设计）

```sql
CREATE TABLE patient_diaries (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    patient_id        INT,
    diary_date        DATE,
    diary_text        TEXT,              -- 自然语言日记
    symptoms_keywords VARCHAR(500),      -- 症状关键词（IK 分词友好）
    glucose_level     FLOAT,             -- 结构化血糖值
    is_pre_danger     TINYINT(1),        -- 危险事件前30天标签
    days_to_danger    INT,               -- 距危险事件天数
    diary_embedding   VECTOR(384),       -- 语义向量

    FULLTEXT INDEX idx_diary_fts(diary_text)     WITH PARSER ik,
    FULLTEXT INDEX idx_kw_fts(symptoms_keywords) WITH PARSER ik,
    VECTOR   INDEX idx_diary_vec(diary_embedding)
        WITH (distance=cosine, type=hnsw, lib=vsag)
);
```

### 混合搜索查询

```sql
SET @parm = '{
  "query": {
    "bool": {
      "should": [
        {"match": {"diary_text": "口渴 头晕 夜尿频繁"}},
        {"match": {"symptoms_keywords": "口渴 多尿 视力模糊"}}
      ]
    }
  },
  "knn": {
    "field": "diary_embedding",
    "k": 15,
    "query_vector": [0.021, -0.034, ...]
  },
  "_source": ["patient_id", "diary_text", "glucose_level",
              "is_pre_danger", "_keyword_score", "_semantic_score"]
}';

SELECT DBMS_HYBRID_SEARCH.SEARCH('patient_diaries', @parm);
```

---

## 数据说明

所有数据均为**完全合成数据**，由 `src/data_generator.py` 程序化生成：

- 100 名虚拟患者（40 名将在模拟期结束时出现血糖危机，60 名保持稳定）
- 每位患者 45 天的日记记录（共约 4,500 条）
- 危机患者在最后 25 天进入"预警期"（血糖值 S 曲线上升至 210–320 mg/dL）
- 日记文本基于医学文献中的症状描述模板生成，不含任何真实个人信息

血糖范围参考：ADA Standards of Medical Care in Diabetes (2024)
症状描述参考：WHO Diabetes Fact Sheet, 中国 2 型糖尿病防治指南 (2020)

---

## 演示效果

| 输入场景 | 风险评分 | 说明 |
|---------|---------|------|
| "今天状态很好，血糖控制不错" | 0–25 | 语义向量接近"稳定期"历史记录 |
| "有些疲劳口干，下午犯困" | 30–55 | 部分预警期特征，需关注 |
| "头晕目眩，大量饮水，夜间多次如厕，视力模糊" | 65–95 | 高度匹配历史预警前期轨迹 |

---

## 为什么混合搜索在此场景下优于单一方式？

| 检索方式 | 优势 | 劣势 |
|---------|------|------|
| 纯关键词（BM25） | 精确匹配医学术语 | 漏召回同义表达（"眼前发花"≠"视力模糊"） |
| 纯向量（HNSW） | 捕捉语义等价表达 | 无法区分"普通疲惫"和"高血糖疲惫" |
| **混合（SeekDB）** | **两者互补，精准识别预警信号** | — |

---

## License

MIT — 数据完全合成，可自由用于演示和研究。
