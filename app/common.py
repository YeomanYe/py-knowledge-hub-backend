"""公共工具：雪花 ID、角色/权限常量、HTTP 异常映射。"""
from __future__ import annotations

import os
import time

# ---------------------------------------------------------------- Snowflake
# mid（worker 0-1023）+ offset（自定义纪元），
# 返回 64 位以内的大整数（字符串），对应 Postgres BIGINT。
# 位分配（与 snowflake-id 一致）：41 位时间戳 | 10 位 worker | 12 位序列


class SnowflakeId:
    def __init__(
        self,
        mid: int = 1,
        offset: int = 1704067200000,
        mid_bits: int = 10,
        seq_bits: int = 12,
    ) -> None:
        self.mid = mid & ((1 << mid_bits) - 1)
        self.offset = offset
        self.mid_bits = mid_bits
        self.seq_bits = seq_bits
        self._last_ts = -1
        self._seq = 0

    def generate(self) -> str:
        ts = int(time.time() * 1000) - self.offset
        if ts < 0:
            raise ValueError("当前时间早于 Snowflake 纪元偏移")
        if ts == self._last_ts:
            self._seq = (self._seq + 1) & ((1 << self.seq_bits) - 1)
            if self._seq == 0:
                # 序列耗尽：等待下一毫秒
                while ts <= self._last_ts:
                    ts = int(time.time() * 1000) - self.offset
        else:
            self._seq = 0
        self._last_ts = ts
        value = (ts << (self.mid_bits + self.seq_bits)) | (
            self.mid << self.seq_bits
        ) | self._seq
        return str(value)


_snowflake = SnowflakeId(
    mid=int(os.environ.get("SNOWFLAKE_WORKER_ID", "1")),
    offset=int(os.environ.get("SNOWFLAKE_OFFSET", "1704067200000")),
)


def next_snowflake_id() -> str:
    """生成雪花 ID（str），对应 Postgres BIGINT。"""
    return _snowflake.generate()


# ---------------------------------------------------------------- 角色
class RoleCode:
    ADMIN = "ROLE_ADMIN"
    REVIEWER = "ROLE_REVIEWER"
    USER = "ROLE_USER"


# ---------------------------------------------------------------- 权限
class PermissionCode:
    documentList = "document:list"
    documentCreate = "document:create"
    documentEdit = "document:edit"
    documentDelete = "document:delete"
    documentReview = "document:review"
    search = "search"


ADMIN_OPERATION_PERMISSIONS = [
    "document:list",
    "document:create",
    "document:edit",
    "document:delete",
    "document:review",
    "document:category",
    "document:category:query",
    "document:tag",
    "document:version",
    "search",
    "system:user",
    "system:role",
    "system:permission",
    "system:permission:create",
    "system:permission:edit",
    "system:permission:delete",
    "system:team",
    "system:statistics",
    "system:settings",
]

ADMIN_ROLES = [RoleCode.ADMIN]


class PermissionType:
    Menu = 1
    Button = 2
    Api = 3



