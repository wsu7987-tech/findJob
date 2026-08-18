"""FineJob 内置的 BOSS CDP 采集模块。

包入口刻意不提前导入引擎：这样既能让其他模块按需导入 ``service``，也不会在
使用 ``python -m ...boss_cdp_raw`` 调试 CLI 时重复加载长生命周期的 CDP 模块。
"""
