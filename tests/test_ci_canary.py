"""临时金丝雀：只为验证 CI 失败时的 PR 评论通道，验完即删。"""


def test_canary_deliberate_failure():
    raise AssertionError("这是刻意失败的金丝雀用例，用于验证 TD-194 的评论通道")
