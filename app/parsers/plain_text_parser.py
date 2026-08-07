"""TXT / MD → 文本。"""


def parse_plain_text(buffer: bytes) -> str:
    return buffer.decode("utf-8", errors="replace")
