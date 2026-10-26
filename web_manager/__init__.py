"""宿主应用的节奏型模板桥接包。

独立的 Web CRUD/试听管理器（``web_manager.server``）已移除；模板编辑现在只走
宿主应用的运营控制台（``/api/admin/rhythm/*``）。本包只保留 ``adapter.py``：
把「模板 + 和弦 + 拍数 + BPM」展开成浏览器可合成的音符列表，并转出
``rhythm_pattern`` 的公开数据源类型。``web_manager`` 仍可整体替换/删除，核心
``rhythm_pattern`` 不依赖它。
"""
