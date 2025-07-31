import os
from typing import Dict
import logging_conf

logger = logging_conf.logger.getChild("font_map_util")


def build_font_map(user_dir: str) -> Dict[str, str]:
    """
    Строит словарь {название_шрифта: путь_к_ttf} для всех шрифтов в директории пользователя.
    """
    font_map = {}
    for fname in os.listdir(user_dir):
        if fname.lower().endswith((".ttf", ".otf")):
            font_name = os.path.splitext(fname)[0]
            font_map[font_name] = os.path.join(user_dir, fname)
    # Подстраховка: default
    if "default" not in font_map and font_map:
        font_map["default"] = list(font_map.values())[0]
    return font_map