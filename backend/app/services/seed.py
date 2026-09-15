"""Mock 演示数据(无需模型密钥)。

在空库上灌入一份主题相关、关系连通的示例知识星图,
便于离线展示:文档列表、知识图谱、节点详情、搜索、人工确认与跨文档问答。
调用方式:POST /api/dev/seed (空库才生效);POST /api/dev/reset 可清空。
"""

from ..models import Chunk, Document, Entity, Evidence, QARecord, Relation, Trash


def _flush(db, obj):
    db.add(obj)
    db.flush()
    return obj


def _owner_clause(model, owner_id):
    if owner_id is None:
        return model.owner_id.is_(None)
    return model.owner_id == owner_id


def _evidence(db, source_type, source_id, document_id, chunk_id, quote, owner_id=None):
    _flush(
        db,
        Evidence(
            source_type=source_type,
            source_id=source_id,
            document_id=document_id,
            chunk_id=chunk_id,
            quote=quote,
            owner_id=owner_id,
        ),
    )


def seed_mock_data(db, owner_id=None):
    existing = db.query(Document).filter(_owner_clause(Document, owner_id))
    if existing.count() > 0:
        return {
            "seeded": False,
            "reason": "数据库已有数据,已跳过;如需重新载入请先调用 /api/dev/reset",
        }

    # 1) 文档与原文片段 ----------------------------------------------------
    doc_specs = [
        {
            "key": "transformer",
            "title": "Attention Is All You Need",
            "filename": "transformer.pdf",
            "file_type": "pdf",
            "chunks": [
                ("第 3 页", "Transformer 完全基于自注意力机制(self-attention),摒弃了循环与卷积结构。"),
                ("第 4 页", "多头注意力(multi-head attention)让模型在不同子空间并行关注不同位置的信息,显著增强了表达能力。"),
                ("第 5 页", "位置编码(positional encoding)为序列注入顺序信息,弥补了注意力机制本身对位置的不可知性。"),
            ],
        },
        {
            "key": "rag",
            "title": "Retrieval-Augmented Generation for NLP",
            "filename": "rag.pdf",
            "file_type": "pdf",
            "chunks": [
                ("摘要", "RAG 先由检索器(retriever)从外部语料召回相关片段,再交由生成模型组织答案。"),
                ("第 2 章", "检索阶段常借助向量数据库进行近似最近邻搜索,查询与文档都被编码为嵌入向量。"),
                ("第 4 章", "相比纯生成,RAG 借助检索到的外部证据降低幻觉;RAG 与知识图谱是两种互补的外部知识引入方式。"),
            ],
        },
        {
            "key": "kg",
            "title": "知识图谱构建综述",
            "filename": "knowledge-graph.md",
            "file_type": "markdown",
            "chunks": [
                ("引言", "知识图谱通常以实体抽取(entity extraction)得到节点与关系边,再用图结构组织领域知识。"),
                ("应用", "知识图谱可为问答系统提供可追踪的关系路径,与检索增强生成形成互补。"),
            ],
        },
        {
            "key": "bert",
            "title": "BERT: Pre-training of Deep Bidirectional Transformers",
            "filename": "bert.pdf",
            "file_type": "pdf",
            "chunks": [
                ("第 1 节", "BERT 基于 Transformer 的编码器堆叠,是一种深层双向预训练语言模型。"),
                ("第 3 节", "BERT 在大规模无标注语料上做掩码语言模型预训练,学习通用的上下文表示。"),
                ("第 5 节", "预训练之后,BERT 在下游任务上微调(fine-tuning)即可适配具体场景。"),
            ],
        },
    ]
    docs, chunks = {}, {}
    for spec in doc_specs:
        doc = _flush(
            db,
            Document(
                title=spec["title"],
                filename=spec["filename"],
                file_type=spec["file_type"],
                storage_path=f"mock://{spec['filename']}",
                status="extracted",
                owner_id=owner_id,
            ),
        )
        docs[spec["key"]] = doc
        chunks[spec["key"]] = []
        for idx, (locator, content) in enumerate(spec["chunks"]):
            chunks[spec["key"]].append(
                _flush(
                    db,
                    Chunk(
                        document_id=doc.id,
                        chunk_index=idx,
                        locator=locator,
                        content=content,
                    ),
                )
            )

    # 2) 实体 (name, type, description, status, doc_key, chunk_index, quote) --
    entity_specs = [
        ("Transformer", "method", "完全基于注意力的序列转换模型", "confirmed", "transformer", 0,
         "Transformer 完全基于自注意力机制(self-attention)。"),
        ("自注意力", "concept", "Attention Is All You Need 提出的注意力机制", "confirmed", "transformer", 0,
         "Transformer 完全基于自注意力机制(self-attention),摒弃了循环结构。"),
        ("多头注意力", "concept", "并行多个注意力头的机制", "confirmed", "transformer", 1,
         "多头注意力让模型在不同子空间并行关注不同位置的信息。"),
        ("位置编码", "concept", "为序列注入顺序信息的编码", "confirmed", "transformer", 2,
         "位置编码为序列注入顺序信息,弥补注意力对位置的不可知性。"),
        ("Vaswani 等", "person", "Transformer 论文作者团队", "confirmed", "transformer", 0,
         "Attention Is All You Need 由 Google 团队( Vaswani 等)提出。"),
        ("RAG", "method", "检索增强生成(Retrieval-Augmented Generation)", "confirmed", "rag", 0,
         "RAG 先由检索器从外部语料召回相关片段,再交由生成模型组织答案。"),
        ("检索器", "tool", "从外部知识库检索相关片段的模块", "confirmed", "rag", 0,
         "RAG 先由检索器(retriever)从外部语料召回相关片段。"),
        ("向量数据库", "tool", "存储与检索嵌入向量的数据库", "confirmed", "rag", 1,
         "检索阶段常借助向量数据库进行近似最近邻搜索。"),
        ("知识图谱", "concept", "以实体与关系组织知识的图结构", "confirmed", "kg", 0,
         "知识图谱通常以实体抽取得到节点与关系边,再用图结构组织领域知识。"),
        ("实体抽取", "method", "从文本中识别实体与关系的技术", "pending", "kg", 0,
         "知识图谱通常先通过实体抽取(entity extraction)得到节点与边。"),
        ("BERT", "method", "深层双向预训练语言模型", "confirmed", "bert", 0,
         "BERT 基于 Transformer 的编码器堆叠,是一种深层双向预训练语言模型。"),
        ("预训练", "concept", "在大规模语料上学习通用表示", "confirmed", "bert", 1,
         "BERT 在大规模无标注语料上做掩码语言模型预训练。"),
        ("微调", "method", "在下游任务上调整预训练模型", "confirmed", "bert", 2,
         "预训练之后,BERT 在下游任务上微调即可适配具体场景。"),
        ("嵌入", "concept", "将文本映射为稠密向量的表示", "confirmed", "rag", 1,
         "查询与文档都被编码为嵌入向量。"),
        ("幻觉", "concept", "模型生成与事实不符的内容", "confirmed", "rag", 2,
         "相比纯生成,RAG 借助检索到的外部证据降低幻觉。"),
        ("涌现能力", "concept", "大模型在规模增大后表现出的新能力", "pending", "bert", 1,
         "大模型在规模增大后可能表现出训练目标未显式规定的涌现能力。"),
    ]
    entities = {}
    for name, etype, desc, status, dk, ci, quote in entity_specs:
        entity = _flush(
            db,
            Entity(name=name, type=etype, description=desc, status=status, owner_id=owner_id),
        )
        entities[name] = entity
        _evidence(db, "entity", entity.id, docs[dk].id, chunks[dk][ci].id, quote, owner_id)

    # 3) 关系 (source, target, relation_type, description, status, doc_key, chunk_index, quote)
    relation_specs = [
        ("Transformer", "自注意力", "uses", "Transformer 完全依赖自注意力机制", "confirmed", "transformer", 0,
         "Transformer 完全基于自注意力机制(self-attention)。"),
        ("自注意力", "Transformer", "part_of", "自注意力是 Transformer 的核心组成", "confirmed", "transformer", 0,
         "自注意力机制是 Transformer 的核心组件。"),
        ("多头注意力", "自注意力", "improves_on", "多头注意力在自注意力基础上增强表达", "confirmed", "transformer", 1,
         "多头注意力让模型在不同子空间并行关注不同位置,增强表达。"),
        ("Transformer", "位置编码", "uses", "Transformer 使用位置编码补充顺序信息", "confirmed", "transformer", 2,
         "位置编码为序列注入顺序信息。"),
        ("Transformer", "Vaswani 等", "proposed_by", "由 Vaswani 等人提出", "confirmed", "transformer", 0,
         "Attention Is All You Need 由 Vaswani 等提出。"),
        ("RAG", "检索器", "uses", "RAG 依赖检索器获取外部证据", "confirmed", "rag", 0,
         "RAG 先由检索器从外部语料召回相关片段。"),
        ("RAG", "向量数据库", "uses", "RAG 用向量数据库存储与检索嵌入", "confirmed", "rag", 1,
         "检索阶段常借助向量数据库进行近似最近邻搜索。"),
        ("RAG", "知识图谱", "compared_with", "RAG 与知识图谱是两种互补的外部知识引入方式", "pending", "rag", 2,
         "RAG 与知识图谱是两种互补的外部知识引入方式。"),
        ("知识图谱", "实体抽取", "uses", "构建知识图谱需要先做实体抽取", "confirmed", "kg", 0,
         "知识图谱通常先通过实体抽取得到节点与边。"),
        ("BERT", "Transformer", "derives_from", "BERT 以 Transformer 编码器为骨架", "confirmed", "bert", 0,
         "BERT 基于 Transformer 的编码器堆叠。"),
        ("BERT", "预训练", "uses", "BERT 采用大规模预训练", "confirmed", "bert", 1,
         "BERT 在大规模无标注语料上做掩码语言模型预训练。"),
        ("BERT", "微调", "uses", "BERT 在下游任务上微调", "confirmed", "bert", 2,
         "预训练之后,BERT 在下游任务上微调。"),
        ("预训练", "BERT", "part_of", "预训练是 BERT 流程的一部分", "confirmed", "bert", 1,
         "预训练构成 BERT 流程的首阶段。"),
        ("RAG", "嵌入", "uses", "RAG 用嵌入表示查询与文档", "confirmed", "rag", 1,
         "查询与文档都被编码为嵌入向量。"),
        ("向量数据库", "嵌入", "uses", "向量数据库存储嵌入向量", "confirmed", "rag", 1,
         "向量数据库以嵌入向量为索引。"),
        ("RAG", "幻觉", "compared_with", "RAG 借助检索证据缓解幻觉", "confirmed", "rag", 2,
         "相比纯生成,RAG 借助检索到的外部证据降低幻觉。"),
        ("知识图谱", "RAG", "compared_with", "知识图谱与 RAG 互补", "confirmed", "kg", 1,
         "知识图谱可为问答提供可追踪的关系路径,与检索增强生成形成互补。"),
        ("RAG", "BERT", "uses", "RAG 的生成器常采用 BERT 类预训练模型", "confirmed", "rag", 0,
         "RAG 由检索器与生成式语言模型协作完成。"),
    ]
    for src, tgt, rtype, desc, status, dk, ci, quote in relation_specs:
        relation = _flush(
            db,
            Relation(
                source_id=entities[src].id,
                target_id=entities[tgt].id,
                relation_type=rtype,
                description=desc,
                status=status,
                owner_id=owner_id,
            ),
        )
        _evidence(db, "relation", relation.id, docs[dk].id, chunks[dk][ci].id, quote, owner_id)

    # 跨文档补充:让「Transformer」同时出现在 BERT 论文里,
    # 使其成为"跨文档实体",在图谱上以紫色描边突出显示。
    _evidence(db, "entity", entities["Transformer"].id, docs["bert"].id, chunks["bert"][0].id,
              "BERT 基于 Transformer 的编码器堆叠。", owner_id)

    db.commit()

    # 示例数据是直接写库的,补一次热度统计,保证图谱节点大小与证据量一致
    from .graph_store import recalc_mention_counts

    recalc_mention_counts(db, owner_id=owner_id)
    db.commit()

    return {
        "seeded": True,
        "documents": len(doc_specs),
        "entities": len(entity_specs),
        "relations": len(relation_specs),
    }


def reset_mock_data(db, owner_id=None):
    """清空某个用户的全部数据(保留表结构),其他账号不受影响。"""
    # 片段没有 owner 字段,通过所属文档间接定位
    doc_ids = [
        row[0]
        for row in db.query(Document.id).filter(_owner_clause(Document, owner_id)).all()
    ]

    for model in (Evidence, Relation, QARecord, Trash):
        db.query(model).filter(_owner_clause(model, owner_id)).delete(
            synchronize_session=False
        )
    db.query(Entity).filter(_owner_clause(Entity, owner_id)).delete(
        synchronize_session=False
    )
    if doc_ids:
        db.query(Chunk).filter(Chunk.document_id.in_(doc_ids)).delete(
            synchronize_session=False
        )
    db.query(Document).filter(_owner_clause(Document, owner_id)).delete(
        synchronize_session=False
    )
    db.commit()
