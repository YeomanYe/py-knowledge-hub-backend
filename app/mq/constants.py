"""RabbitMQ 拓扑常量。"""

# 交换机（topic）
RAG_REINDEX_EXCHANGE = "rag.reindex.exchange"
SEARCH_INDEX_EXCHANGE = "search.index.exchange"
KG_GRAPH_EXCHANGE = "kg.graph.exchange"

# 本服务消费的队列
RAG_REINDEX_QUEUE = "kh.rag.reindex.queue"
SEARCH_INDEX_QUEUE = "kh.search.index.queue"
KG_GRAPH_QUEUE = "kh.kg.graph.queue"

# 路由键
RAG_RK_BY_IDS = "rag.reindex.by_ids"
RAG_RK_DELETE = "rag.reindex.delete"
SEARCH_RK_INDEX = "search.index.document"
SEARCH_RK_DELETE = "search.index.delete"
KG_RK_BUILD_BY_IDS = "kg.graph.build.by_ids"
KG_RK_DELETE = "kg.graph.delete"
