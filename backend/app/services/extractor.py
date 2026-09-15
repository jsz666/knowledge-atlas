"""实体关系抽取。

分工:AI 负责从文本中抽取实体与关系,程序负责校验、去重与兜底。
- 模型可用:走 prompt,输出必须落在关系类型白名单内;
- 模型不可用:走规则启发式(术语 + 共现),标记为 heuristic,便于人工确认时一眼看出。
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import config
from .dedupe import normalize_name
from .llm import LLMClient

logger = logging.getLogger(__name__)

ENTITY_TYPES = [
    "method", "model", "paper", "person",
    "tool", "dataset", "concept", "org",
]
DEFAULT_ENTITY_TYPE = "concept"

# 单块的抽取上限(比旧版 6 更宽松,长段落不再被截断;由 validate 兜底)
MAX_ENTITIES_PER_CHUNK = 12
MAX_RELATIONS_PER_CHUNK = 12

# 模型常见的中英/近义类型写法 → 白名单类型(避免一律落到 concept)
TYPE_ALIASES = {
    "algorithm": "method", "technique": "method", "approach": "method", "strategy": "method",
    "方法": "method", "算法": "method", "技术": "method", "机制": "method", "模型方法": "method",
    "network": "model", "architecture": "model", "llm": "model", "model_series": "model",
    "模型": "model", "架构": "model", "神经网络": "model",
    "论文": "paper", "文献": "paper", "著作": "paper", "article": "paper",
    "publication": "paper", "report": "paper", "preprint": "paper",
    "人物": "person", "researcher": "person", "author": "person", "scholar": "person", "学者": "person",
    "工具": "tool", "框架": "tool", "库": "tool", "系统": "tool", "平台": "tool",
    "framework": "tool", "library": "tool", "platform": "tool", "software": "tool", "toolkit": "tool",
    "数据集": "dataset", "语料库": "dataset", "评测集": "dataset", "corpus": "dataset",
    "benchmark": "dataset", "datasets": "dataset",
    "概念": "concept", "术语": "concept", "任务": "concept", "指标": "concept",
    "task": "concept", "metric": "concept", "technology": "concept", "term": "concept",
    "组织": "org", "机构": "org", "公司": "org", "大学": "org",
    "organization": "org", "institution": "org", "company": "org", "university": "org",
}

# 关系类型的近义写法 → 白名单(camelCase / 中文 / 反义统一)
RELATION_ALIASES = {
    "use": "uses", "used_by": "uses", "依赖": "uses", "使用": "uses", "基于": "uses",
    "depends_on": "uses", "depend_on": "uses", "based_on": "uses", "requires": "uses",
    "improve": "improves_on", "improves": "improves_on", "改进": "improves_on", "优化": "improves_on",
    "derive_from": "derives_from", "derives": "derives_from", "derived_from": "derives_from",
    "演化": "derives_from", "继承": "derives_from", "起源于": "derives_from",
    "partof": "part_of", "contains": "part_of", "包含": "part_of", "属于": "part_of",
    "component_of": "part_of", "submodule_of": "part_of",
    "compare": "compared_with", "compared_to": "compared_with", "对比": "compared_with",
    "contrast_with": "compared_with",
    "proposed_by": "proposed_by", "propose": "proposed_by", "提出": "proposed_by",
    "created_by": "proposed_by", "author_of": "proposed_by",
    "related": "related_to", "relatedto": "related_to", "关联": "related_to", "相关": "related_to",
    "关联关系": "related_to",
}

# 脱离语境就没有意义的泛词,不作为实体收录
GENERIC_ENTITY_NAMES = {
    "模型", "方法", "技术", "算法", "系统", "框架", "架构", "数据", "数据集", "概念",
    "任务", "结果", "结论", "问题", "内容", "过程", "结构", "模型结构", "研究",
    "model", "method", "technique", "system", "framework", "data", "dataset", "concept",
    "task", "result", "results", "approach", "problem", "process", "structure",
}

EXTRACTION_SYSTEM = """你是知识图谱构建助手,从技术文档片段中抽取实体与关系,用于构建一张"知识星图"。

【抽取原则】
1. 只抽取片段中明确出现、或可由片段直接推断的内容,不要用你自己的背景知识补充。
2. 优先抽取具体、可命名的实体;不要抽取「模型」「方法」「数据」这类脱离语境就没有意义的泛词。
3. 实体名一律使用片段中的原文写法,不要翻译、不要改写、不要加书名号或引号。
4. 每条关系的 source / target 必须与你给出的实体名完全一致。
5. 关系有方向,system → target。请按语义判断方向,例如「BERT 基于 Transformer」应输出 source=BERT、target=Transformer、type=uses。
6. 宁缺毋滥:片段没有明确关系时 relations 可以是空数组,不要为了凑数编造关系。

【实体类型】(必须从下列取值中选一个)
- method:方法、算法、技术路线(如 自注意力机制、fine-tuning、掩码语言模型)
- model:具体的模型或模型系列(如 Transformer、BERT、GPT-4)
- paper:论文、著作、技术报告
- person:人物
- tool:工具、框架、库、平台、系统(如 PyTorch、FAISS)
- dataset:数据集、语料库、评测集
- concept:概念、任务、指标、术语(如 检索增强生成、幻觉、多任务学习)
- org:组织、机构、公司、学校

【关系类型】(必须从下列取值中选一个)
{relation_types}

【别名】
若同一实体在本片段中还有别的写法(英文名、缩写、全称、同义说法),写进 "aliases" 数组(没有则空数组)。
例如 {"name":"自注意力机制","aliases":["self-attention"]}。

【证据】
每个实体与关系都要给出 "evidence":片段中支撑它的原文短句(照抄原文,不超过 60 字)。

【示例】
片段:BERT 基于 Transformer 的编码器堆叠,在大规模无标注语料上做掩码语言模型预训练。
输出:
{"entities":[{"name":"BERT","type":"model","description":"深层双向预训练语言模型","aliases":[],"evidence":"BERT 基于 Transformer 的编码器堆叠"},{"name":"Transformer","type":"model","description":"基于自注意力的序列模型","aliases":[],"evidence":"BERT 基于 Transformer 的编码器堆叠"},{"name":"掩码语言模型","type":"method","description":"通过预测被掩码的词来预训练","aliases":["masked language model"],"evidence":"在大规模无标注语料上做掩码语言模型预训练"}],"relations":[{"source":"BERT","target":"Transformer","type":"uses","description":"BERT 以 Transformer 编码器为基础结构","evidence":"BERT 基于 Transformer 的编码器堆叠"}]}

【输出要求】
只输出一个 JSON 对象,不要任何解释文字、不要 Markdown 代码块,格式:
{"entities":[{"name":"...","type":"...","description":"一句话说明","aliases":[],"evidence":"原文短句"}],"relations":[{"source":"...","target":"...","type":"...","description":"一句话说明","evidence":"原文短句"}]}
一个片段最多抽取 {max_entities} 个实体、{max_relations} 条关系。
"""

EXTRACTION_USER = """文档标题:{title}
文档片段(来源:{locator}):
\"\"\"
{text}
\"\"\"
"""

CONCEPT_SYSTEM = """你是信息抽取助手。从用户问题中识别出 3 到 6 个用于检索知识图谱的关键概念。
只输出 JSON:{{"concepts":["概念1","概念2"]}}
概念应当尽量接近图谱中可能出现的实体名,不要输出完整句子。
"""

# ---------------- 规则兜底 ----------------
_EN_TERM = re.compile(r"\b[A-Z][A-Za-z0-9+#.\-]{2,}\b|\b[A-Z]{2,}\b")
_CN_SUFFIX = re.compile(
    r"[一-鿿A-Za-z0-9]{2,10}(?:模型|网络|算法|机制|方法|架构|框架|系统|技术|"
    r"注意力|学习|检索|数据库|引擎|协议|优化器|损失函数|嵌入|图谱|向量)"
)
_QUOTED = re.compile(r"[《\"'“]([^《\"'”]{2,20})[》\"'”]")
_STOPWORDS = {
    "the", "this", "that", "these", "those", "with", "from", "for", "and", "but",
    "not", "are", "was", "were", "has", "have", "had", "can", "may", "our", "we",
    "they", "their", "its", "his", "her", "all", "any", "one", "two", "fig",
    "figure", "table", "et", "al", "using", "used", "use", "based", "however",
    "therefore", "thus", "also", "more", "most", "than", "then", "when", "where",
    "which", "while", "who", "whom", "will", "would", "could", "should", "note",
    "example", "such", "each", "other", "both", "same", "new", "proposed",
    "paper", "work", "approach", "result", "results", "method", "model",
}


@dataclass
class DraftEntity:
    name: str
    type: str = DEFAULT_ENTITY_TYPE
    description: str = ""
    aliases: List[str] = field(default_factory=list)
    evidence: str = ""


@dataclass
class DraftRelation:
    source: str
    target: str
    relation_type: str = config.DEFAULT_RELATION_TYPE
    description: str = ""
    evidence: str = ""


@dataclass
class ExtractionResult:
    entities: List[DraftEntity] = field(default_factory=list)
    relations: List[DraftRelation] = field(default_factory=list)
    source: str = "model"  # model / heuristic
    reason: str = ""       # 降级原因(仅降级时填写),便于排查与展示


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).strip("《》\"'“”.,;:()[]")


def _normalize_type(
    value: str,
    allowed: List[str],
    default: str,
    aliases: Optional[Dict[str, str]] = None,
) -> str:
    """把模型输出的类型收敛到白名单:先查近义别名表(camelCase / 中文 / 近义词),再按原值匹配。"""
    text = (value or "").strip().lower().replace("-", "_")
    text = re.sub(r"\s+", "_", text)
    text = (aliases or {}).get(text, text)
    return text if text in allowed else default


def _clean_aliases(name: str, aliases) -> List[str]:
    """清洗别名:兼容模型返回字符串或数组的情况,去重、去主名、最多保留 3 个。"""
    if isinstance(aliases, str):
        aliases = [aliases]
    result: List[str] = []
    seen = {name.lower()}
    for raw in aliases or []:
        alias = _clean_name(str(raw))
        if not alias or len(alias) > 60:
            continue
        key = alias.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(alias)
        if len(result) >= 3:
            break
    return result


def _clean_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:200]


def validate(
    entities: List[DraftEntity],
    relations: List[DraftRelation],
    entity_types: Optional[List[str]] = None,
    relation_types: Optional[List[str]] = None,
) -> Tuple[List[DraftEntity], List[DraftRelation]]:
    """校验 + 去重 + 按用户关注的维度过滤 + 丢弃无法落库的关系。

    entity_types / relation_types 非空时,不在其中的维度会被丢弃(自定义抽取维度)。
    """
    allowed_entities = entity_types or ENTITY_TYPES
    allowed_relations = relation_types or config.RELATION_TYPES

    clean_entities: List[DraftEntity] = []
    seen = set()
    for entity in entities:
        name = _clean_name(entity.name)
        if not name or len(name) > 60:
            continue
        # 泛词单独出现时没有信息量,丢弃(如整段只抽出一个「模型」)
        if name.lower() in GENERIC_ENTITY_NAMES:
            continue
        # 与消歧模块共用归一化:避免 "Transformer" / "Transformer 模型" 在同一次抽取里变成两个实体
        key = normalize_name(name) or name.lower()
        if key in seen:
            continue
        etype = _normalize_type(entity.type, ENTITY_TYPES, DEFAULT_ENTITY_TYPE, TYPE_ALIASES)
        if etype not in allowed_entities:
            continue                       # 用户没勾选这个维度
        seen.add(key)
        clean_entities.append(
            DraftEntity(
                name=name,
                type=etype,
                description=(entity.description or "")[:200],
                aliases=_clean_aliases(name, getattr(entity, "aliases", []) or []),
                evidence=_clean_evidence(getattr(entity, "evidence", "")),
            )
        )

    # 实体名 / 别名 → 规范名:模型用别名指代时也能正确接到实体上
    name_map: Dict[str, str] = {}
    for entity in clean_entities:
        name_map[normalize_name(entity.name) or entity.name.lower()] = entity.name
        for alias in entity.aliases:
            name_map.setdefault(normalize_name(alias) or alias.lower(), entity.name)

    def _resolve(raw: str) -> Optional[str]:
        cleaned = _clean_name(raw)
        if not cleaned:
            return None
        return name_map.get(normalize_name(cleaned) or cleaned.lower())

    clean_relations: List[DraftRelation] = []
    seen_pairs = set()
    for rel in relations:
        source = _resolve(rel.source)
        target = _resolve(rel.target)
        if not source or not target or source == target:
            continue
        rtype = _normalize_type(
            rel.relation_type,
            config.RELATION_TYPES,
            config.DEFAULT_RELATION_TYPE,
            RELATION_ALIASES,
        )
        if rtype not in allowed_relations:
            continue                       # 不是用户关心的关系类型
        # 保留方向:A uses B 与 B uses A 是两条不同的关系(旧实现 sorted 后会误合并)
        pair = (source.lower(), target.lower(), rtype)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        clean_relations.append(
            DraftRelation(
                source=source,
                target=target,
                relation_type=rtype,
                description=(rel.description or "")[:200],
                evidence=_clean_evidence(getattr(rel, "evidence", "")),
            )
        )

    return clean_entities[:MAX_ENTITIES_PER_CHUNK], clean_relations[:MAX_RELATIONS_PER_CHUNK]


def _heuristic(text: str) -> ExtractionResult:
    """无模型时的兜底:抽取术语,按共现建立泛化关联。"""
    candidates: Counter = Counter()

    for match in _QUOTED.finditer(text):
        candidates[_clean_name(match.group(1))] += 3

    for match in _EN_TERM.finditer(text):
        word = match.group(0).strip(".-")
        if word.lower() in _STOPWORDS or len(word) < 3:
            continue
        candidates[word] += 2

    for match in _CN_SUFFIX.finditer(text):
        word = _clean_name(match.group(0))
        if len(word) < 3:
            continue
        candidates[word] += 2

    top = [name for name, _ in candidates.most_common(8)]
    entities = [DraftEntity(name=name, type=DEFAULT_ENTITY_TYPE, description="规则抽取,待确认") for name in top]

    relations: List[DraftRelation] = []
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            if len(relations) >= 6:
                break
            relations.append(
                DraftRelation(
                    source=top[i],
                    target=top[j],
                    relation_type=config.DEFAULT_RELATION_TYPE,
                    description="同段落共现,待确认",
                )
            )
    return ExtractionResult(entities=entities, relations=relations, source="heuristic")


def extract_from_chunk(
    text: str,
    locator: str,
    llm: Optional[LLMClient] = None,
    entity_types: Optional[List[str]] = None,
    relation_types: Optional[List[str]] = None,
    focus: str = "",
    title: str = "",
) -> ExtractionResult:
    """对单个文本块抽取实体与关系,优先模型,失败自动降级。

    entity_types / relation_types 非空时只保留这些维度(自定义抽取维度);
    focus 是用户的关注领域描述,会注入 prompt 引导模型优先抽什么;
    title 是文档标题,作为上下文帮助模型判断实体重要性与消歧。
    """
    allowed_entities = [t for t in (entity_types or []) if t in ENTITY_TYPES] or None
    allowed_relations = [t for t in (relation_types or []) if t in config.RELATION_TYPES] or None
    degrade_reason = "未配置可用的模型,使用规则抽取"

    if llm is not None and llm.available:
        system = (
            EXTRACTION_SYSTEM.replace(
                "{relation_types}", "、".join(allowed_relations or config.RELATION_TYPES)
            )
            .replace("{max_entities}", str(MAX_ENTITIES_PER_CHUNK))
            .replace("{max_relations}", str(MAX_RELATIONS_PER_CHUNK))
        )
        if allowed_entities:
            system += "\n本次只关注这些实体类型:" + "、".join(allowed_entities) + ",其他类型不要输出。"
        if focus:
            system += f"\n用户的关注领域是「{focus}」,优先抽取与该领域相关的实体与关系。"
        payload = llm.chat_json(
            system=system,
            user=EXTRACTION_USER.format(
                title=title or "未知",
                locator=locator or "未知",
                text=text[:3000],
            ),
            temperature=0,  # 抽取任务要稳定,关闭随机性
            # 推理模型的思考也吃这份预算:一次给足,避免「空正文 → 翻倍重试」
            # 白白多花一次网络往返(这是抽取慢的一大原因)。
            max_tokens=config.LLM_EXTRACT_MAX_TOKENS,
        )
        if payload:
            raw_entities = [
                DraftEntity(
                    name=str(item.get("name", "")),
                    type=str(item.get("type", DEFAULT_ENTITY_TYPE)),
                    description=str(item.get("description", "")),
                    aliases=item.get("aliases") if isinstance(item.get("aliases"), list) else [],
                    evidence=str(item.get("evidence", "")),
                )
                for item in payload.get("entities", [])
                if isinstance(item, dict)
            ]
            raw_relations = [
                DraftRelation(
                    source=str(item.get("source", "")),
                    target=str(item.get("target", "")),
                    relation_type=str(item.get("type", config.DEFAULT_RELATION_TYPE)),
                    description=str(item.get("description", "")),
                    evidence=str(item.get("evidence", "")),
                )
                for item in payload.get("relations", [])
                if isinstance(item, dict)
            ]
            entities, relations = validate(
                raw_entities, raw_relations, allowed_entities, allowed_relations
            )
            # 模型已正常应答就以它为准,判空也认 —— 规则兜底是给「模型不可用」准备的。
            # 否则模型对代码/表格片段判空后,正则会把「Error」「POST」这类噪声当成
            # 实体写进图谱(实测发生过),还会把整篇文档误标成「已降级为规则抽取」。
            return ExtractionResult(entities=entities, relations=relations, source="model")
        else:
            # 把模型的真实报错带到界面,否则用户只看到「额度不足/网络异常」这种无法排查的提示
            detail = (getattr(llm, "last_error", "") or "").strip()
            if detail:
                degrade_reason = f"模型调用失败:{detail}(已降级为规则抽取)"
            else:
                degrade_reason = (
                    "模型调用失败或输出无法解析为 JSON(常见原因:额度不足 / 网络异常 / 端点不支持 JSON 模式),已降级为规则抽取"
                )
            logger.warning("模型无有效输出,本次降级为规则抽取:%s", detail or "原因未知")

    fallback = _heuristic(text)
    entities, relations = validate(
        fallback.entities, fallback.relations, allowed_entities, allowed_relations
    )
    return ExtractionResult(
        entities=entities,
        relations=relations,
        source="heuristic",
        reason=degrade_reason,
    )


def extract_concepts(question: str, llm: Optional[LLMClient] = None) -> List[str]:
    """识别问题中的关键概念;模型不可用时返回空列表,由检索层用实体名匹配兜底。"""
    if llm is not None and llm.available:
        payload = llm.chat_json(system=CONCEPT_SYSTEM, user=question, temperature=0)
        if payload:
            concepts = [_clean_name(str(c)) for c in payload.get("concepts", []) if str(c).strip()]
            return [c for c in concepts if c][:6]
    return []
