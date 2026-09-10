"""鹈鹕骑自行车单页（pelican-bike.html）的契约测试。

这是一个与后端无关的纯静态单文件：纯 SVG 作画、原生 SMIL 驱动场景动画、
零 JavaScript。下面每条断言都对应它对外承诺的一条性质，改页面时若把其中
任何一条改没了（比如手滑加了 script 标签、删了 SMIL），这里会变红。

刻意只用标准库读文件：不经过 FastAPI、不碰数据库，在 SQLite / 真 PG /
CI 任一环境下行为一致。
"""
import re
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "pelican-bike.html"


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_page_exists_and_is_self_contained_svg():
    assert PAGE.is_file(), "pelican-bike.html 应位于仓库根目录"
    html = _html()
    assert "<svg" in html and "</svg>" in html
    assert 'viewBox="0 0 1200 675"' in html
    assert "鹈鹕" in html


def test_zero_javascript_and_zero_external_requests():
    """零脚本 + 零外链：断网双击也能看是本页的核心卖点。"""
    low = _html().lower()
    assert "<script" not in low, "不得含有 script 标签"
    assert "javascript:" not in low
    assert not re.search(r"\son(click|load|error|mouseover|focus|change|input)\s*=", low)
    # xmlns 命名空间声明里的 http 不算外链请求，只查资源引用属性
    assert not re.search(r'(?:src|href)="http', _html()), "不得引用外部资源"


def test_smil_trio_covers_the_scene():
    """SMIL 三件套必须都在：位移/旋转、属性动画、路径运动。"""
    html = _html()
    assert html.count("<animateTransform") >= 20, "位移/旋转动画不应少于 20 处"
    assert html.count("<animate ") >= 30, "属性动画不应少于 30 处"
    assert html.count("<animateMotion") >= 3, "双脚 + 飞鱼至少需要 3 处路径运动"
    # 关键戏眼：腿部形变、车身起伏、脚踏圆周、路面流动
    assert 'attributeName="d"' in html
    assert 'stroke-dashoffset' in html
    assert 'id="ride"' in html


def test_theme_switch_is_pure_css_radio():
    """主题切换只许用原生单选框 + CSS，不得为此加脚本。"""
    html = _html()
    for tid in ("t-day", "t-sunset", "t-night"):
        assert f'id="{tid}"' in html
        assert f'for="{tid}"' in html
    assert ":checked" in html
